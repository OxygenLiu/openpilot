"""
Longitudinal Actuator Delay Estimator

Learn longitudinal actuator delay (vision command → vehicle response) using cross-correlation.

Methodology:
- Compare MPC delay-compensated velocity target (longitudinalPlan.vTarget) vs vehicle CAN velocity
- Use cross-correlation to find time lag between command and response
- Quality filtering: highway speeds, active acceleration, no driver override
- Vision-CAN safety validation: ModelV2 vision speed must agree with CAN within ±5 km/h
- Store in BlockAverage for statistical stability (50 blocks * 100 samples = 5000 measurements)

Data criteria:
- Highway speeds (8-40 m/s) for accurate measurement
- Active longitudinal control (|accel| > 0.2 m/s²)
- No driver override (gas/brake not pressed)
- Vision-CAN safety validation passed (±5 km/h tolerance)
- Sufficient correlation (NCC > 0.9, confidence > 0.2)
"""

import numpy as np
from cereal import car, log
from openpilot.common.swaglog import cloudlog
from selfdrive.locationd.learners.base import LearnerClass, BlockAverage, Points, actuator_delay


class LongitudinalLagEstimator(LearnerClass):
    """Learn longitudinal actuator delay using cross-correlation"""

    inputs = {"carControl", "carState", "controlsState", "modelV2", "longitudinalPlan"}

    # Learning parameters (matching lagd.py pattern)
    BLOCK_SIZE = 100          # 100 data points per block (5 seconds at 20Hz)
    BLOCK_NUM = 50            # 50 blocks total history
    BLOCK_NUM_NEEDED = 10     # 10 valid blocks needed (50 seconds = 10 * 5s)

    # Quality filtering thresholds
    MIN_VEGO = 8.0            # m/s - minimum speed for measurement
    MAX_VEGO = 40.0           # m/s - maximum speed
    MIN_ACCEL = 0.2           # m/s² - minimum active acceleration
    MIN_NCC = 0.9             # Minimum normalized cross-correlation
    MIN_CONFIDENCE = 0.2      # Minimum confidence threshold
    VISION_CAN_TOLERANCE = 1.39  # ±5 km/h safety tolerance for vision-CAN validation
    MAX_LAG_STD = 0.1         # Maximum standard deviation for valid estimate

    def __init__(self, CP: car.CarParams, dt: float = 0.05):
        super().__init__(CP, dt)
        self.initial_lag = CP.longitudinalActuatorDelay

        # Current state (from subscribed messages)
        self.long_active = False
        self.gas_pressed = False
        self.brake_pressed = False
        self.cruise_enabled = False
        self.target_velocity = 0.0    # MPC delay-compensated velocity target from longitudinalPlan.vTarget
        self.vision_velocity = 0.0     # ModelV2 vision-estimated current velocity (for safety validation)
        self.vehicle_velocity = 0.0    # Kalman-filtered CAN velocity (for delay correlation and quality filtering)
        self.accel_command = 0.0

        # Data collection
        window_len = int(60.0 / self.dt)  # 60 second window
        self.points = Points(window_len)
        self.block_avg = BlockAverage(self.BLOCK_NUM, self.BLOCK_SIZE, 0, self.initial_lag)
        self.last_estimate_t = 0.0

    def handle_log(self, t: float, which: str, msg):
        """Update state from subscribed messages"""
        if which == "carState":
            self.vehicle_velocity = msg.vEgo  # Kalman-filtered CAN velocity (for delay correlation and quality filtering)
            self.gas_pressed = msg.gasPressed
            self.brake_pressed = msg.brakePressed
            self.cruise_enabled = msg.cruiseState.enabled
        elif which == "carControl":
            self.accel_command = msg.actuators.accel
            self.long_active = msg.longActive  # longActive is in carControl, not controlsState
        elif which == "controlsState":
            pass  # No fields needed from controlsState currently
        elif which == "longitudinalPlan":
            # CRITICAL: Use MPC delay-compensated velocity target (not raw ModelV2!)
            # vTarget is extracted from MPC trajectory at action_t = longitudinalActuatorDelay + DT_MDL
            # This includes MPC optimization (physics/comfort/safety constraints) AND delay compensation
            if msg.vTarget > 0:
                self.target_velocity = msg.vTarget  # Delay-compensated MPC velocity
        elif which == "modelV2":
            if len(msg.velocity.x) > 0:
                # Vision-estimated current velocity for safety validation
                self.vision_velocity = msg.velocity.x[0]  # Current vision-estimated velocity (t=0.0s)

        self.t = t

    def update(self, t: float):
        """Collect data during active longitudinal control (called at 20Hz)"""
        # Quality filtering for longitudinal delay learning
        # CRITICAL: Compares MPC delay-compensated velocity target vs Kalman-filtered vehicle velocity
        # This measures real actuator delay: vision command → actual vehicle CAN response
        valid_conditions = [
            # Speed range: highway speeds for accurate measurement (use vehicle sensor for safety)
            self.MIN_VEGO <= self.vehicle_velocity <= self.MAX_VEGO,

            # Active longitudinal acceleration
            abs(self.accel_command) > self.MIN_ACCEL,

            # No driver override
            not self.gas_pressed,
            not self.brake_pressed,

            # Cruise control active
            self.long_active,
            self.cruise_enabled,

            # Reasonable velocity error between vision signals (avoid saturation)
            abs(self.target_velocity - self.vision_velocity) < 8.0,

            # Valid ModelV2 outputs
            self.target_velocity > 0.1,
            self.vision_velocity > 0.1,

            # CRITICAL SAFETY: Vision speed vs vehicle CAN speed validation
            # ModelV2 vision speed must agree with vehicle CAN within ±5 km/h (±1.39 m/s)
            # This prevents malfunction of openpilot vision or vehicle hardware
            abs(self.vision_velocity - self.vehicle_velocity) < self.VISION_CAN_TOLERANCE,
        ]

        if all(valid_conditions):
            # Compare MPC delay-compensated target vs Kalman-filtered vehicle velocity
            # target_velocity = longitudinalPlan.vTarget (MPC-optimized, delay-compensated from vision)
            # vehicle_velocity = carState.vEgo (Kalman-filtered CAN speed, stable and reliable)
            # This measures real actuator delay: vision command → actual vehicle CAN response
            self.points.update(self.t, self.target_velocity, self.vehicle_velocity, True)
        else:
            self.points.update(self.t, self.target_velocity, self.vehicle_velocity, False)

    def update_estimate(self):
        """Update delay estimate using cross-correlation (called at 4Hz)"""
        # Need enough data points for correlation analysis
        if self.points.num_points < self.BLOCK_SIZE or self.points.num_okay < self.BLOCK_SIZE // 2:
            return

        times, desired, actual, okay = self.points.get()

        # Check if there are any new valid data points since the last update
        is_valid = self.points.num_okay / max(1, self.points.num_points) >= 0.5
        if self.last_estimate_t != 0 and times[0] <= self.last_estimate_t:
            new_values_start_idx = next(-i for i, t in enumerate(reversed(times)) if t <= self.last_estimate_t)
            is_valid = is_valid and not (new_values_start_idx == 0 or not np.any(okay[new_values_start_idx:]))

        # Run cross-correlation analysis
        delay, corr, confidence = actuator_delay(desired, actual, okay, self.dt, 2.0)  # Max 2s delay
        if corr < self.MIN_NCC or confidence < self.MIN_CONFIDENCE or not is_valid:
            return

        # Update block average with valid delay measurement
        self.block_avg.update(delay)
        self.last_estimate_t = self.t

    def get_msg_data(self):
        """Get data for liveDelay message"""
        valid_mean_lag, valid_std, current_mean_lag, current_std = self.block_avg.get()

        # Determine status
        if self.block_avg.valid_blocks >= self.BLOCK_NUM_NEEDED and not np.isnan(valid_mean_lag) and not np.isnan(valid_std):
            if valid_std > self.MAX_LAG_STD:
                status = log.LiveDelayData.Status.invalid
            else:
                status = log.LiveDelayData.Status.estimated
        else:
            status = log.LiveDelayData.Status.unestimated

        # Set delay values
        if status == log.LiveDelayData.Status.estimated:
            longitudinal_delay = valid_mean_lag
        else:
            longitudinal_delay = self.initial_lag

        if not np.isnan(current_mean_lag) and not np.isnan(current_std):
            longitudinal_delay_estimate = current_mean_lag
            longitudinal_delay_estimate_std = current_std
        else:
            longitudinal_delay_estimate = self.initial_lag
            longitudinal_delay_estimate_std = 0.0

        # Calculate calibration percentage
        longitudinal_cal_perc = min(100 * (self.block_avg.valid_blocks * self.BLOCK_SIZE + self.block_avg.idx) //
                                   (self.BLOCK_NUM_NEEDED * self.BLOCK_SIZE), 100)

        # Vision-CAN speed safety validation
        vision_can_diff = abs(self.vision_velocity - self.vehicle_velocity)
        vision_can_safety_passed = vision_can_diff < self.VISION_CAN_TOLERANCE

        return {
            'longitudinalDelay': float(longitudinal_delay),
            'longitudinalDelayEstimate': float(longitudinal_delay_estimate),
            'longitudinalDelayEstimateStd': float(longitudinal_delay_estimate_std),
            'longitudinalValidBlocks': int(self.block_avg.valid_blocks),
            'longitudinalStatus': status,
            'longitudinalCalPerc': int(longitudinal_cal_perc),
            'visionSpeed': float(self.vision_velocity),
            'canSpeed': float(self.vehicle_velocity),
            'visionCanDiff': float(vision_can_diff),
            'visionCanSafetyPassed': bool(vision_can_safety_passed),
            'longitudinalPoints': [],  # Reserved for debug mode
        }

    def reset(self, initial_lag: float, valid_blocks: int):
        """Reset longitudinal lag estimator with learned delay from persistent storage"""
        window_len = int(60.0 / self.dt)
        self.points = Points(window_len)
        self.block_avg = BlockAverage(self.BLOCK_NUM, self.BLOCK_SIZE, valid_blocks, initial_lag)
        cloudlog.info(f"LongitudinalLagEstimator initialized with learned delay: {initial_lag:.3f}s ({valid_blocks} valid blocks)")

    def get_serialization_data(self):
        """Get data for serialization"""
        valid_mean_lag, _, _, _ = self.block_avg.get()
        return {
            'learned_delay': float(valid_mean_lag) if not np.isnan(valid_mean_lag) else self.initial_lag,
            'valid_blocks': self.block_avg.valid_blocks,
        }

    def is_learned(self) -> bool:
        """Check if delay has been learned with sufficient confidence"""
        valid_mean_lag, valid_std, _, _ = self.block_avg.get()
        return (self.block_avg.valid_blocks >= self.BLOCK_NUM_NEEDED and
                not np.isnan(valid_mean_lag) and
                valid_std <= self.MAX_LAG_STD and
                abs(valid_mean_lag - self.initial_lag) > 0.05)

    def get_learned_delay(self) -> float:
        """Get the learned delay value"""
        valid_mean_lag, _, _, _ = self.block_avg.get()
        if self.block_avg.valid_blocks >= self.BLOCK_NUM_NEEDED and not np.isnan(valid_mean_lag):
            return valid_mean_lag
        return self.initial_lag

    def get_confidence(self) -> float:
        """Get confidence score for learned delay (0.0-1.0)"""
        _, valid_std, _, _ = self.block_avg.get()
        if self.block_avg.valid_blocks < self.BLOCK_NUM_NEEDED or np.isnan(valid_std):
            return 0.0
        return max(0.0, min(1.0, 1.0 - (valid_std / self.MAX_LAG_STD)))
