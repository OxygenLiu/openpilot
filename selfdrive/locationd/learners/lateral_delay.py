"""
Lateral Actuator Delay Estimator

Learn lateral actuator delay (steering command → vehicle response) using cross-correlation.

Methodology:
- Compare desired lateral acceleration (from controlsState.desiredCurvature) vs actual yaw rate
- Use cross-correlation to find time lag between command and response
- Quality filtering: fast speed, turning, active steering, no driver override
- PoseCalibrator for accurate yaw rate measurement from IMU
- Store in BlockAverage for statistical stability (50 blocks * 100 samples = 5000 measurements)

Data criteria (v0.10.1 production thresholds):
- Highway speed (v_ego > 15 m/s / 33 mph) for accurate measurement
- Can learn on straights or turns (yaw_rate >= 0.0 rad/s)
- Active lateral control without driver override
- Valid IMU sensors and calibration
- High correlation (NCC > 0.95, confidence > 0.7)
- Recovery buffer after invalid conditions (2.0s)
"""

import numpy as np
from cereal import car, log
from openpilot.common.swaglog import cloudlog
from selfdrive.locationd.learners.base import LearnerClass, BlockAverage, Points, actuator_delay
from selfdrive.locationd.helpers import PoseCalibrator, Pose


class LateralLagEstimator(LearnerClass):
    """Learn lateral actuator delay using cross-correlation"""

    inputs = {"carControl", "carState", "controlsState", "liveCalibration", "livePose"}

    # Learning parameters (v0.10.1 production values)
    BLOCK_SIZE = 100          # 100 data points per block (5 seconds at 20Hz)
    BLOCK_NUM = 50            # 50 blocks total history
    BLOCK_NUM_NEEDED = 5      # 5 valid blocks needed (25 seconds = 5 * 5s) - v0.10.1: faster convergence

    # Quality filtering thresholds (v0.10.1 production values)
    MOVING_WINDOW_SEC = 60.0      # 60 second sliding window
    MIN_OKAY_WINDOW_SEC = 25.0    # Minimum 25s of valid data needed - v0.10.1: stricter quality
    MIN_RECOVERY_BUFFER_SEC = 2.0 # 2s recovery buffer after invalid conditions
    MIN_VEGO = 15.0               # m/s (33 mph) - v0.10.1: highway speeds only
    MIN_ABS_YAW_RATE = 0.0        # rad/s - v0.10.1: can learn on straights!
    MAX_YAW_RATE_SANITY_CHECK = 1.0  # rad/s - sanity check for yaw rate
    MIN_NCC = 0.95                # v0.10.1: require high correlation (was 0.8)
    MIN_CONFIDENCE = 0.7          # v0.10.1: require high confidence (was 0.2)
    MAX_LAT_ACCEL = 2.0           # m/s² - v0.10.1: conservative (was 5.0)
    MAX_LAT_ACCEL_DIFF = 0.6      # m/s² - v0.10.1: tight tolerance (was 5.0)
    MAX_LAG = 1.0                 # seconds - maximum lag to search
    MAX_LAG_STD = 0.1             # Maximum standard deviation for valid estimate

    def __init__(self, CP: car.CarParams, dt: float = 0.05):
        super().__init__(CP, dt)
        self.initial_lag = CP.steerActuatorDelay + 0.2

        # Current state (from subscribed messages)
        self.lat_active = False
        self.steering_pressed = False
        self.steering_saturated = False
        self.desired_curvature = 0.0
        self.v_ego = 0.0
        self.yaw_rate = 0.0
        self.yaw_rate_std = 0.0
        self.pose_valid = False

        # Tracking for recovery buffers
        self.last_lat_inactive_t = 0.0
        self.last_steering_pressed_t = 0.0
        self.last_steering_saturated_t = 0.0
        self.last_pose_invalid_t = 0.0
        self.last_estimate_t = 0.0

        # PoseCalibrator for accurate yaw rate from IMU
        self.calibrator = PoseCalibrator()

        # Data collection
        window_len = int(self.MOVING_WINDOW_SEC / self.dt)
        self.points = Points(window_len)
        self.block_avg = BlockAverage(self.BLOCK_NUM, self.BLOCK_SIZE, 0, self.initial_lag)

    def handle_log(self, t: float, which: str, msg):
        """Update state from subscribed messages"""
        if which == "carControl":
            self.lat_active = msg.latActive
        elif which == "carState":
            self.steering_pressed = msg.steeringPressed
            self.v_ego = msg.vEgo
        elif which == "controlsState":
            self.steering_saturated = getattr(msg.lateralControlState, msg.lateralControlState.which()).saturated
            self.desired_curvature = msg.desiredCurvature
        elif which == "liveCalibration":
            self.calibrator.feed_live_calib(msg)
        elif which == "livePose":
            device_pose = Pose.from_live_pose(msg)
            calibrated_pose = self.calibrator.build_calibrated_pose(device_pose)
            self.yaw_rate = calibrated_pose.angular_velocity.yaw
            self.yaw_rate_std = calibrated_pose.angular_velocity.yaw_std
            self.pose_valid = msg.angularVelocityDevice.valid and msg.posenetOK and msg.inputsOK

        self.t = t

    def update(self, t: float):
        """Collect data during active lateral control (called at 20Hz)"""
        # Calculate lateral accelerations
        la_desired = self.desired_curvature * self.v_ego * self.v_ego
        la_actual_pose = self.yaw_rate * self.v_ego

        # Quality filtering conditions
        fast = self.v_ego > self.MIN_VEGO
        turning = np.abs(self.yaw_rate) >= self.MIN_ABS_YAW_RATE
        sensors_valid = (self.pose_valid and
                        np.abs(self.yaw_rate) < self.MAX_YAW_RATE_SANITY_CHECK and
                        self.yaw_rate_std < self.MAX_YAW_RATE_SANITY_CHECK)
        la_valid = (np.abs(la_actual_pose) <= self.MAX_LAT_ACCEL and
                   np.abs(la_desired - la_actual_pose) <= self.MAX_LAT_ACCEL_DIFF)
        calib_valid = self.calibrator.calib_valid

        # Track invalid condition timestamps for recovery buffer
        if not self.lat_active:
            self.last_lat_inactive_t = self.t
        if self.steering_pressed:
            self.last_steering_pressed_t = self.t
        if self.steering_saturated:
            self.last_steering_saturated_t = self.t
        if not sensors_valid or not la_valid:
            self.last_pose_invalid_t = self.t

        # Check recovery buffer: wait for recovery after invalid conditions
        has_recovered = all(
            self.t - last_t >= self.MIN_RECOVERY_BUFFER_SEC
            for last_t in [self.last_lat_inactive_t, self.last_steering_pressed_t,
                          self.last_steering_saturated_t, self.last_pose_invalid_t]
        )

        # All conditions must be met for valid data
        okay = (self.lat_active and not self.steering_pressed and not self.steering_saturated and
               fast and turning and has_recovered and calib_valid and sensors_valid and la_valid)

        self.points.update(self.t, la_desired, la_actual_pose, okay)

    def update_estimate(self):
        """Update delay estimate using cross-correlation (called at 4Hz)"""
        # Need enough data points for correlation analysis
        min_points = int(self.MIN_OKAY_WINDOW_SEC / self.dt)
        if self.points.num_points < min_points:
            return

        times, desired, actual, okay = self.points.get()

        # Check if there are any new valid data points since the last update
        is_valid = self.points.num_okay >= min_points
        if self.last_estimate_t != 0 and times[0] <= self.last_estimate_t:
            new_values_start_idx = next(-i for i, t in enumerate(reversed(times)) if t <= self.last_estimate_t)
            is_valid = is_valid and not (new_values_start_idx == 0 or not np.any(okay[new_values_start_idx:]))

        # Run cross-correlation analysis
        delay, corr, confidence = actuator_delay(desired, actual, okay, self.dt, self.MAX_LAG)
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
            lateral_delay = valid_mean_lag
        else:
            lateral_delay = self.initial_lag

        if not np.isnan(current_mean_lag) and not np.isnan(current_std):
            lateral_delay_estimate = current_mean_lag
            lateral_delay_estimate_std = current_std
        else:
            lateral_delay_estimate = self.initial_lag
            lateral_delay_estimate_std = 0.0

        # Calculate calibration percentage
        lateral_cal_perc = min(100 * (self.block_avg.valid_blocks * self.BLOCK_SIZE + self.block_avg.idx) //
                              (self.BLOCK_NUM_NEEDED * self.BLOCK_SIZE), 100)

        return {
            'lateralDelay': float(lateral_delay),
            'lateralDelayEstimate': float(lateral_delay_estimate),
            'lateralDelayEstimateStd': float(lateral_delay_estimate_std),
            'validBlocks': int(self.block_avg.valid_blocks),
            'status': status,
            'calPerc': int(lateral_cal_perc),
            'points': [],  # Reserved for debug mode
        }

    def reset(self, initial_lag: float, valid_blocks: int):
        """Reset lateral lag estimator with learned delay from persistent storage"""
        window_len = int(self.MOVING_WINDOW_SEC / self.dt)
        self.points = Points(window_len)
        self.block_avg = BlockAverage(self.BLOCK_NUM, self.BLOCK_SIZE, valid_blocks, initial_lag)
        cloudlog.info(f"LateralLagEstimator initialized with learned delay: {initial_lag:.3f}s ({valid_blocks} valid blocks)")

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
                valid_std <= self.MAX_LAG_STD)
