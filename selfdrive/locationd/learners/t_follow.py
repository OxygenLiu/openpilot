"""
T_FOLLOW Learning Module

Learn driver's preferred T_FOLLOW_SCALE_FACTORS from manual driving behavior.

Methodology:
- Collect data during manual driving (cruise OFF)
- Measure driver's actual following distance
- Calculate preferred T_FOLLOW scale relative to standard personality (1.45s)
- Use BlockAverage for statistical stability (one per VREL_BP interval)
- Persist to Params, auto-load on boot

Data criteria:
- Cruise/openpilot NOT engaged (manual driving)
- Lead vehicle detected by vision
- Approaching slower lead (vrel > 0)
- Driver decelerating (-1.2 to 0.0 m/s²)
- Continuous for 5+ seconds
- At least 10 valid blocks per interval
"""

import numpy as np
from cereal import car, log
from openpilot.common.swaglog import cloudlog
from selfdrive.locationd.learners.base import LearnerClass, BlockAverage, Points


class PersonalizedLongitudinalLearner(LearnerClass):
    """Learn driver's preferred T_FOLLOW scales across VREL intervals"""

    inputs = {"carState", "radarState", "controlsState", "carControl"}

    # Learning parameters (matching lagd.py pattern)
    BLOCK_SIZE = 100          # 100 data points per block (5 seconds at 20Hz)
    BLOCK_NUM = 50            # 50 blocks total history per interval
    BLOCK_NUM_NEEDED = 10     # 10 valid blocks needed (50 seconds = 10 * 5s)

    # VREL_BP intervals (matching BMW values.py)
    VREL_BP_KPH = [0, 10, 20, 30, 40]  # kph
    VREL_BP = [v / 3.6 for v in VREL_BP_KPH]  # m/s
    NUM_INTERVALS = len(VREL_BP)

    # Quality filtering thresholds
    MIN_VEGO = 10.0           # m/s (36 kph) - minimum speed for valid data
    MAX_VEGO = 40.0           # m/s (144 kph) - maximum speed
    MIN_DECEL = -1.2          # m/s² - BMW DCC minus5 hold
    MAX_DECEL = 0.0           # m/s² - no acceleration
    MIN_VREL = 0.1            # m/s - approaching lead (v_ego > v_lead)
    MIN_SEGMENT_DURATION = 5.0  # seconds - minimum continuous valid segment
    MAX_LEAD_DISTANCE = 200.0   # m - BMW vision ModelV2 detection range (highway conditions)

    # T_FOLLOW baseline reference (standard personality)
    BASELINE_T_FOLLOW = 1.45  # seconds

    def __init__(self, CP: car.CarParams, dt: float = 0.05):
        super().__init__(CP, dt)

        # Initialize BlockAverage for each VREL_BP interval
        self.block_averages = [
            BlockAverage(self.BLOCK_NUM, self.BLOCK_SIZE, 0, 1.0)
            for _ in range(self.NUM_INTERVALS)
        ]

        # Data collection buffers
        window_len = int(60.0 / self.dt)  # 60 second window
        self.points = [Points(window_len) for _ in range(self.NUM_INTERVALS)]

        # Current state (from subscribed messages)
        self.v_ego = 0.0
        self.v_lead = 0.0
        self.d_lead = 0.0
        self.a_ego = 0.0
        self.gas_pressed = False
        self.brake_pressed = False
        self.cruise_enabled = False
        self.long_active = False
        self.lead_status = False

        # Segment tracking for 5-second continuity requirement
        self.segment_start_time = None
        self.current_interval = -1
        self.last_estimate_t = 0.0

    def handle_log(self, t: float, which: str, msg):
        """Update state from subscribed messages"""
        if which == "carState":
            self.v_ego = msg.vEgo
            self.a_ego = msg.aEgo
            self.gas_pressed = msg.gasPressed
            self.brake_pressed = msg.brakePressed
            self.cruise_enabled = msg.cruiseState.enabled
        elif which == "radarState":
            self.v_lead = msg.leadOne.vLead
            self.d_lead = msg.leadOne.dRel
            self.lead_status = msg.leadOne.status
        elif which == "carControl":
            self.long_active = msg.longActive
        elif which == "controlsState":
            pass  # Reserved for future use

        self.t = t

    def _get_vrel_interval(self, vrel: float) -> int:
        """
        Map vrel to VREL_BP interval index

        Uses numpy searchsorted for consistent interval mapping:
        - vrel < VREL_BP[0] → interval 0
        - VREL_BP[i] <= vrel < VREL_BP[i+1] → interval i
        - vrel >= VREL_BP[-1] → interval NUM_INTERVALS-1
        """
        # searchsorted returns index where vrel would be inserted
        # side='right' means vrel equal to breakpoint goes to next interval
        idx = np.searchsorted(self.VREL_BP, vrel, side='right')
        # Clamp to valid interval range [0, NUM_INTERVALS-1]
        return min(max(idx - 1, 0), self.NUM_INTERVALS - 1)

    def _estimate_driver_t_follow(self) -> float:
        """
        Estimate driver's preferred T_FOLLOW from current following behavior

        From MPC safe distance calculation:
        d_safe = v_ego * T_FOLLOW + (v_ego² - v_lead²) / (2 * a_comfort)

        Rearrange to solve for T_FOLLOW:
        T_FOLLOW = (d_actual - (v_ego² - v_lead²) / (2 * a_comfort)) / v_ego
        """
        if self.v_ego < 0.1:
            return self.BASELINE_T_FOLLOW

        a_comfort = -1.0  # m/s² - comfortable deceleration (MPC default)

        # Calculate deceleration compensation term
        v_squared_diff = self.v_ego ** 2 - self.v_lead ** 2
        decel_distance = v_squared_diff / (2.0 * abs(a_comfort))

        # Solve for T_FOLLOW
        time_gap_distance = self.d_lead - decel_distance
        driver_t_follow = time_gap_distance / self.v_ego

        # Sanity check: reasonable T_FOLLOW range [0.5s, 5.0s]
        driver_t_follow = np.clip(driver_t_follow, 0.5, 5.0)

        return driver_t_follow

    def update(self, t: float):
        """Collect data during manual driving (called at 20Hz)"""
        # Calculate vrel
        vrel = self.v_ego - self.v_lead

        # Determine which interval this data belongs to
        interval_idx = self._get_vrel_interval(vrel)

        # Quality filtering conditions
        valid_conditions = [
            # Manual driving (NOT cruise/openpilot)
            not self.long_active,
            not self.cruise_enabled,

            # Lead vehicle detected by vision
            self.lead_status,

            # Approaching slower lead
            vrel >= self.MIN_VREL,

            # Driver decelerating (maintaining following distance)
            self.MIN_DECEL <= self.a_ego <= self.MAX_DECEL,

            # Reasonable speeds for measurement
            self.MIN_VEGO <= self.v_ego <= self.MAX_VEGO,

            # Valid lead data
            self.v_lead > 0.1,
            0.0 < self.d_lead < self.MAX_LEAD_DISTANCE,

            # No gas input (pure manual braking/coasting)
            not self.gas_pressed,
        ]

        if all(valid_conditions):
            # Estimate driver's actual T_FOLLOW behavior
            driver_t_follow = self._estimate_driver_t_follow()

            # Calculate scale relative to baseline (standard personality = 1.45s)
            driver_scale = driver_t_follow / self.BASELINE_T_FOLLOW

            # Track segment continuity
            if self.segment_start_time is None:
                self.segment_start_time = self.t
                self.current_interval = interval_idx
            elif interval_idx != self.current_interval:
                # Interval changed - reset segment
                self.segment_start_time = self.t
                self.current_interval = interval_idx

            # Add to data collection buffer
            self.points[interval_idx].update(self.t, driver_scale, driver_scale, True)
        else:
            # Invalid data - reset segment tracking
            self.segment_start_time = None
            self.current_interval = -1

            # Still update all buffers but mark as not okay
            for idx in range(self.NUM_INTERVALS):
                self.points[idx].update(self.t, 0.0, 0.0, False)

    def update_estimate(self):
        """Update BlockAverage with new valid segments (called at 4Hz)"""
        for interval_idx in range(self.NUM_INTERVALS):
            # Get data segment
            times, desired, actual, okay = self.points[interval_idx].get()
            okay_array = np.array(okay)

            # Need at least BLOCK_SIZE valid data points
            if np.sum(okay_array) < self.BLOCK_SIZE:
                continue

            # Find longest continuous sequence of valid data (5+ seconds)
            # This ensures we have stable, continuous driver behavior
            max_consecutive = 0
            current_consecutive = 0
            for is_okay in okay_array:
                if is_okay:
                    current_consecutive += 1
                    max_consecutive = max(max_consecutive, current_consecutive)
                else:
                    current_consecutive = 0

            # Require at least BLOCK_SIZE consecutive valid points (5 seconds at 20Hz)
            if max_consecutive < self.BLOCK_SIZE:
                continue

            # Check for new data since last estimate
            if self.last_estimate_t != 0 and times[0] <= self.last_estimate_t:
                new_values_start_idx = next((-i for i, t in enumerate(reversed(times)) if t <= self.last_estimate_t), 0)
                if new_values_start_idx == 0 or not np.any(okay_array[new_values_start_idx:]):
                    continue

            # Calculate mean scale for valid data
            segment_scale = float(np.mean(np.array(desired)[okay_array]))

            # Sanity check before updating
            if 0.5 <= segment_scale <= 3.0:
                self.block_averages[interval_idx].update(segment_scale)
                self.last_estimate_t = self.t

    def get_msg_data(self):
        """Get data for liveDelay message"""
        learned_scales = self._get_learned_scales()

        return {
            'personalizedScales': learned_scales if learned_scales else [1.0] * self.NUM_INTERVALS,
            'personalizedValidBlocks': [ba.valid_blocks for ba in self.block_averages],
            'personalizedProgress': self._get_learning_progress(),
            'personalizedStatus': self._get_status(),
            'personalizedActiveInterval': self.current_interval,
        }

    def reset(self, learned_scales: list[float], valid_blocks_list: list[int], block_data: np.ndarray = None):
        """Reset with learned scales and raw block data from persistent storage"""
        if block_data is not None and len(block_data) > 0:
            # NEW: Restore BlockAverage with actual block values for proper std calculation
            # block_data is flattened array from all 5 intervals
            offset = 0
            for idx, valid_blocks in enumerate(valid_blocks_list):
                if valid_blocks > 0:
                    # Extract block data for this interval
                    interval_block_data = block_data[offset:offset + valid_blocks]
                    offset += valid_blocks

                    # Reconstruct BlockAverage with actual block values
                    self.block_averages[idx] = BlockAverage.from_block_data(
                        self.BLOCK_NUM, self.BLOCK_SIZE, valid_blocks, interval_block_data.reshape(-1, 1)
                    )
                else:
                    # No valid blocks - use default initialization
                    self.block_averages[idx] = BlockAverage(
                        self.BLOCK_NUM, self.BLOCK_SIZE, 0, learned_scales[idx]
                    )
            cloudlog.info(f"PersonalizedLongitudinalLearner initialized with {len(block_data)} raw block values")
        else:
            # Legacy path: Only scales available (no raw block data)
            for idx, (scale, valid_blocks) in enumerate(zip(learned_scales, valid_blocks_list)):
                self.block_averages[idx] = BlockAverage(
                    self.BLOCK_NUM, self.BLOCK_SIZE, valid_blocks, scale
                )
            cloudlog.info(f"PersonalizedLongitudinalLearner initialized with learned scales: {learned_scales}")

    def get_serialization_data(self):
        """Get data for serialization"""
        # Collect all block data from 5 intervals
        block_data_list = []
        for ba in self.block_averages:
            block_data = ba.get_block_data()  # Shape: (valid_blocks, 1)
            block_data_list.append(block_data.flatten())

        # Flatten all intervals into single array
        all_blocks = np.concatenate(block_data_list) if block_data_list else np.array([])

        return {
            'learned_scales': [ba.get()[0] if ba.valid_blocks > 0 else 1.0 for ba in self.block_averages],
            'valid_blocks_list': [ba.valid_blocks for ba in self.block_averages],
            'block_data': all_blocks,
        }

    def is_learned(self) -> bool:
        """Check if at least one interval has sufficient data"""
        return any(ba.valid_blocks >= self.BLOCK_NUM_NEEDED for ba in self.block_averages)

    def _get_learned_scales(self) -> list[float] | None:
        """
        Get current learned T_FOLLOW_SCALE_FACTORS (supports partial learning)

        Returns learned scales for intervals with sufficient data (≥10 blocks),
        and defaults (1.0) for intervals without enough data yet.
        This allows immediate benefit from common scenarios (low vrel) even before
        rare scenarios (high vrel) are fully learned.

        Returns None only if no intervals have been learned at all.
        """
        learned_scales = []
        has_any_learned = False

        for interval_idx in range(self.NUM_INTERVALS):
            if self.block_averages[interval_idx].valid_blocks >= self.BLOCK_NUM_NEEDED:
                # Sufficient confidence - use learned scale
                valid_mean, _, _, _ = self.block_averages[interval_idx].get()
                learned_scale = float(np.clip(valid_mean, 0.5, 3.0))
                learned_scales.append(learned_scale)
                has_any_learned = True
            else:
                # Not enough data for this interval - use default (no scaling)
                learned_scales.append(1.0)

        return learned_scales if has_any_learned else None

    def _get_learning_progress(self) -> int:
        """Calculate overall learning progress percentage (0-100)"""
        total_progress = 0
        for block_avg in self.block_averages:
            interval_progress = min(100, 100 * block_avg.valid_blocks // self.BLOCK_NUM_NEEDED)
            total_progress += interval_progress

        return total_progress // self.NUM_INTERVALS

    def _get_status(self) -> log.LiveDelayData.PersonalizedStatus:
        """
        Determine current learning status

        Returns:
        - unlearned: No data collected yet
        - learning: Collecting data, but no intervals have ≥10 blocks yet
        - learned: At least one interval has sufficient data (≥10 blocks)
        - invalid: Learned data is inconsistent (high std dev)
        """
        learned_scales = self._get_learned_scales()

        if learned_scales is None:
            # Check if any learning has started
            has_data = any(ba.valid_blocks > 0 for ba in self.block_averages)
            return log.LiveDelayData.PersonalizedStatus.learning if has_data else log.LiveDelayData.PersonalizedStatus.unlearned

        # Check consistency only for learned intervals (≥10 blocks)
        for interval_idx in range(self.NUM_INTERVALS):
            if self.block_averages[interval_idx].valid_blocks >= self.BLOCK_NUM_NEEDED:
                _, valid_std, _, _ = self.block_averages[interval_idx].get()
                if not np.isnan(valid_std) and valid_std > 0.3:
                    return log.LiveDelayData.PersonalizedStatus.invalid

        return log.LiveDelayData.PersonalizedStatus.learned
