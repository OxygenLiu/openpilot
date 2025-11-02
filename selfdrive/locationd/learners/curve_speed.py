"""
Curve Speed Learning Module

Learn driver's preferred curve speed control parameters from manual driving.

Methodology:
- Collect data during manual driving through curves
- Measure driver's speed through various curvatures
- Learn personalized curve speed parameters
- Use segment-based validation (10-second minimum duration)
- Persist to Params, auto-load on boot

Data criteria:
- Model detecting curve (curvature > threshold)
- No lead vehicle (ensure not slowing for traffic)
- Speed ≥ 30 kph (minEnableSpeed for BMW E90)
- Continuous for ≥10 seconds
- Need ≥50 valid segments for confidence
"""

import numpy as np
from cereal import car, log
from openpilot.common.swaglog import cloudlog
from selfdrive.locationd.learners.base import LearnerClass, CurveSegmentBuffer


class CurveSpeedLearner(LearnerClass):
    """Learn driver's preferred curve speed control parameters"""

    inputs = {"carState", "radarState", "carControl", "modelV2"}

    # Learning parameters
    MIN_SEGMENT_DURATION = 10.0    # seconds - minimum continuous curve segment
    SEGMENTS_NEEDED = 50           # 50 valid segments for learned status

    # Data quality thresholds
    MIN_SPEED_KPH = 30.0           # kph - BMW E90 minEnableSpeed
    MIN_SPEED = MIN_SPEED_KPH / 3.6  # m/s

    # Initial parameters (from requirements doc)
    INITIAL_LOOKAHEAD_TIME = 3.0   # seconds
    INITIAL_LAT_ACCEL_LIMIT = 2.0  # m/s²
    INITIAL_SPEED_MARGIN = 0.85    # 15% safety margin
    INITIAL_MIN_CURVATURE = 0.003  # rad/m

    # Parameter bounds for learning
    LOOKAHEAD_TIME_MIN = 1.0
    LOOKAHEAD_TIME_MAX = 5.0
    LAT_ACCEL_LIMIT_MIN = 1.5
    LAT_ACCEL_LIMIT_MAX = 3.0
    SPEED_MARGIN_MIN = 0.7
    SPEED_MARGIN_MAX = 0.95
    MIN_CURVATURE_MIN = 0.001
    MIN_CURVATURE_MAX = 0.01

    def __init__(self, CP: car.CarParams, dt: float = 0.05):
        super().__init__(CP, dt)

        # Segment buffer (circular, persistent)
        self.segment_buffer = CurveSegmentBuffer()

        # Current segment accumulation
        self.segment_data = []   # Accumulate data for current segment
        self.segment_start_time = None
        self.in_valid_segment = False

        # Current state (from subscribed messages)
        self.v_ego = 0.0
        self.a_ego = 0.0
        self.cruise_enabled = False
        self.long_active = False
        self.lead_status = False
        self.curvature = 0.0  # Calculated from modelV2

    def handle_log(self, t: float, which: str, msg):
        """Update state from subscribed messages"""
        if which == "carState":
            self.v_ego = msg.vEgo
            self.a_ego = msg.aEgo
            self.cruise_enabled = msg.cruiseState.enabled
        elif which == "radarState":
            self.lead_status = msg.leadOne.status
        elif which == "carControl":
            self.long_active = msg.longActive
        elif which == "modelV2":
            # Calculate path curvature from modelV2 position trajectory
            self.curvature = self._calculate_curvature(msg)

    def _calculate_curvature(self, modelV2) -> float:
        """Calculate path curvature from modelV2 trajectory"""
        # Use position.x and position.y to calculate curvature
        # Curvature = d(heading)/ds where heading = atan2(dy, dx)

        if not hasattr(modelV2, 'position') or len(modelV2.position.x) < 3:
            return 0.0

        x = modelV2.position.x
        y = modelV2.position.y

        # Calculate curvature at near-term point (index 10 ≈ 1 second ahead at 10Hz model)
        idx = min(10, len(x) - 1)
        if idx < 2:
            return 0.0

        # Finite difference approximation of curvature
        # kappa = |x'*y'' - y'*x''| / (x'^2 + y'^2)^(3/2)
        dx = (x[idx] - x[idx-1])
        dy = (y[idx] - y[idx-1])
        ddx = (x[idx] - 2*x[idx-1] + x[idx-2])
        ddy = (y[idx] - 2*y[idx-1] + y[idx-2])

        numerator = abs(dx * ddy - dy * ddx)
        denominator = (dx**2 + dy**2) ** 1.5

        if denominator < 1e-6:
            return 0.0

        return numerator / denominator

    def update(self, t: float):
        """Main update loop - called at 20Hz"""
        self.t = t

        # Check if currently in a valid curve segment
        is_valid = self._check_segment_valid()

        if is_valid:
            if not self.in_valid_segment:
                # Starting new segment
                self.segment_start_time = t
                self.segment_data = []
                self.in_valid_segment = True

            # Accumulate data for this segment
            segment_duration = t - self.segment_start_time

            # Calculate lateral acceleration: a_lat = v^2 * curvature
            lat_accel = (self.v_ego ** 2) * self.curvature

            self.segment_data.append({
                'curvature': self.curvature,
                'v_ego': self.v_ego,
                'a_ego': self.a_ego,
                'lat_accel': lat_accel,
                'duration': segment_duration
            })

            # Check if segment is complete (≥10 seconds)
            if segment_duration >= self.MIN_SEGMENT_DURATION:
                self._process_completed_segment()
                self.in_valid_segment = False
                self.segment_start_time = None
                self.segment_data = []

        else:
            # Not in valid segment - reset
            if self.in_valid_segment:
                # Lost validity - discard partial segment
                self.in_valid_segment = False
                self.segment_start_time = None
                self.segment_data = []

    def _check_segment_valid(self) -> bool:
        """Check if current conditions meet curve learning criteria"""
        # Criteria from requirements:
        # 1. Model detecting curve
        curve_detected = self.curvature > self.INITIAL_MIN_CURVATURE

        # 2. No lead vehicle
        no_lead = not self.lead_status

        # 3. Speed ≥ 30 kph
        speed_ok = self.v_ego >= self.MIN_SPEED

        # 4. Manual driving (not cruise/openpilot engaged)
        manual_driving = not self.cruise_enabled and not self.long_active

        return curve_detected and no_lead and speed_ok and manual_driving

    def _process_completed_segment(self):
        """Process a completed 10+ second curve segment and extract learned parameters"""
        if len(self.segment_data) < 10:
            return

        # Extract observed behavior from segment
        curvatures = [d['curvature'] for d in self.segment_data]
        speeds = [d['v_ego'] for d in self.segment_data]
        accels = [d['a_ego'] for d in self.segment_data]
        lat_accels = [d['lat_accel'] for d in self.segment_data]

        # 1. Lateral acceleration limit: max comfortable lat accel observed
        max_lat_accel = max(lat_accels)

        # 2. Speed margin: Calculate theoretical safe speed vs actual speed
        max_curv = max(curvatures)
        if max_curv > 0.001:
            theoretical_safe_speed = np.sqrt(self.INITIAL_LAT_ACCEL_LIMIT / max_curv)
            actual_speed = np.mean(speeds)
            observed_margin = actual_speed / theoretical_safe_speed if theoretical_safe_speed > 0 else 0.85
            observed_margin = np.clip(observed_margin, 0.5, 1.0)
        else:
            observed_margin = 0.85

        # 3. Minimum curvature threshold: what curvatures driver responds to
        min_curv = min(curvatures)

        # 4. Lookahead time: estimate from deceleration timing
        max_curv_idx = curvatures.index(max_curv)
        decel_start_idx = next((i for i, a in enumerate(accels) if a < -0.5), max_curv_idx)
        time_before_curve = (max_curv_idx - decel_start_idx) * self.dt
        lookahead = max(0.5, time_before_curve) if time_before_curve > 0 else 3.0

        # Clip parameters to valid bounds
        lookahead = np.clip(lookahead, self.LOOKAHEAD_TIME_MIN, self.LOOKAHEAD_TIME_MAX)
        max_lat_accel = np.clip(max_lat_accel, self.LAT_ACCEL_LIMIT_MIN, self.LAT_ACCEL_LIMIT_MAX)
        observed_margin = np.clip(observed_margin, self.SPEED_MARGIN_MIN, self.SPEED_MARGIN_MAX)
        min_curv = np.clip(min_curv, self.MIN_CURVATURE_MIN, self.MIN_CURVATURE_MAX)

        # Add to circular buffer (oldest replaced if full)
        self.segment_buffer.add_segment(lookahead, max_lat_accel, observed_margin, min_curv)

        cloudlog.info(f"Curve segment #{self.segment_buffer.valid_segments} added: "
                      f"lookahead={lookahead:.2f}s, lat_accel={max_lat_accel:.2f}, "
                      f"margin={observed_margin:.2f}, min_curv={min_curv:.4f}")

    def update_estimate(self):
        """Update parameter estimates (called at 4Hz)"""
        # CurveSegmentBuffer handles all estimation internally
        # No additional work needed here
        pass

    def get_msg_data(self):
        """Get data for liveDelay message"""
        # Get learned parameters and standard deviations from buffer
        mean_params, std_params = self.segment_buffer.get_parameters()

        return {
            'curveSpeedLookaheadTime': mean_params['lookahead_time'],
            'curveSpeedLatAccelLimit': mean_params['lat_accel_limit'],
            'curveSpeedSpeedMargin': mean_params['speed_margin'],
            'curveSpeedMinCurvatureThreshold': mean_params['min_curvature_threshold'],
            'curveSpeedValidSegments': int(self.segment_buffer.valid_segments),
            'curveSpeedProgress': self._get_learning_progress(),
            'curveSpeedStatus': self._get_status(),
            # Standard deviations for confidence
            'curveSpeedLookaheadTimeStd': std_params['lookahead_time_std'],
            'curveSpeedLatAccelLimitStd': std_params['lat_accel_limit_std'],
            'curveSpeedSpeedMarginStd': std_params['speed_margin_std'],
            'curveSpeedMinCurvatureThresholdStd': std_params['min_curvature_threshold_std'],
        }

    def reset(self, segment_buffer_data: np.ndarray, valid_segments: int):
        """Reset with learned segments from persistent storage"""
        self.segment_buffer = CurveSegmentBuffer(valid_segments, segment_buffer_data)
        cloudlog.info(f"CurveSpeedLearner initialized with {valid_segments} segments")

    def get_serialization_data(self):
        """Get data for serialization"""
        return {
            'segment_buffer_data': self.segment_buffer.get_buffer_data(),
            'valid_segments': self.segment_buffer.valid_segments,
        }

    def is_learned(self) -> bool:
        """Check if learned (50+ segments)"""
        return self.segment_buffer.is_learned()

    def _get_learning_progress(self) -> int:
        """Return learning progress as percentage (0-100%)"""
        return min(100, int(100 * self.segment_buffer.valid_segments / self.SEGMENTS_NEEDED))

    def _get_status(self):
        """Return learning status enum"""
        if self.segment_buffer.valid_segments == 0:
            return log.LiveDelayData.PersonalizedStatus.unlearned
        elif self.segment_buffer.valid_segments < self.SEGMENTS_NEEDED:
            return log.LiveDelayData.PersonalizedStatus.learning
        else:
            return log.LiveDelayData.PersonalizedStatus.learned
