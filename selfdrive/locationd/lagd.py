#!/usr/bin/env python3
import os
import numpy as np
import capnp
from collections import deque
from functools import partial

import cereal.messaging as messaging
from cereal import car, log
from cereal.services import SERVICE_LIST
from openpilot.common.params import Params
from openpilot.common.realtime import config_realtime_process
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.locationd.helpers import PoseCalibrator, Pose, fft_next_good_size, parabolic_peak_interp

BLOCK_SIZE = 100
BLOCK_NUM = 50
BLOCK_NUM_NEEDED = 5
MOVING_WINDOW_SEC = 60.0
MIN_OKAY_WINDOW_SEC = 25.0
MIN_RECOVERY_BUFFER_SEC = 2.0
MIN_VEGO = 15.0
MIN_ABS_YAW_RATE = 0.0
MAX_YAW_RATE_SANITY_CHECK = 1.0
MIN_NCC = 0.95
MAX_LAG = 1.0
MAX_LAG_STD = 0.1
MAX_LAT_ACCEL = 2.0
MAX_LAT_ACCEL_DIFF = 0.6
MIN_CONFIDENCE = 0.7
CORR_BORDER_OFFSET = 5
LAG_CANDIDATE_CORR_THRESHOLD = 0.9


def masked_normalized_cross_correlation(expected_sig: np.ndarray, actual_sig: np.ndarray, mask: np.ndarray, n: int):
  """
  References:
    D. Padfield. "Masked FFT registration". In Proc. Computer Vision and
    Pattern Recognition, pp. 2918-2925 (2010).
    :DOI:`10.1109/CVPR.2010.5540032`
  """

  eps = np.finfo(np.float64).eps
  expected_sig = np.asarray(expected_sig, dtype=np.float64)
  actual_sig = np.asarray(actual_sig, dtype=np.float64)

  expected_sig[~mask] = 0.0
  actual_sig[~mask] = 0.0

  rotated_expected_sig = expected_sig[::-1]
  rotated_mask = mask[::-1]

  fft = partial(np.fft.fft, n=n)

  actual_sig_fft = fft(actual_sig)
  rotated_expected_sig_fft = fft(rotated_expected_sig)
  actual_mask_fft = fft(mask.astype(np.float64))
  rotated_mask_fft = fft(rotated_mask.astype(np.float64))

  number_overlap_masked_samples = np.fft.ifft(rotated_mask_fft * actual_mask_fft).real
  number_overlap_masked_samples[:] = np.round(number_overlap_masked_samples)
  number_overlap_masked_samples[:] = np.fmax(number_overlap_masked_samples, eps)
  masked_correlated_actual_fft = np.fft.ifft(rotated_mask_fft * actual_sig_fft).real
  masked_correlated_expected_fft = np.fft.ifft(actual_mask_fft * rotated_expected_sig_fft).real

  numerator = np.fft.ifft(rotated_expected_sig_fft * actual_sig_fft).real
  numerator -= masked_correlated_actual_fft * masked_correlated_expected_fft / number_overlap_masked_samples

  actual_squared_fft = fft(actual_sig ** 2)
  actual_sig_denom = np.fft.ifft(rotated_mask_fft * actual_squared_fft).real
  actual_sig_denom -= masked_correlated_actual_fft ** 2 / number_overlap_masked_samples
  actual_sig_denom[:] = np.fmax(actual_sig_denom, 0.0)

  rotated_expected_squared_fft = fft(rotated_expected_sig ** 2)
  expected_sig_denom = np.fft.ifft(actual_mask_fft * rotated_expected_squared_fft).real
  expected_sig_denom -= masked_correlated_expected_fft ** 2 / number_overlap_masked_samples
  expected_sig_denom[:] = np.fmax(expected_sig_denom, 0.0)

  denom = np.sqrt(actual_sig_denom * expected_sig_denom)

  # zero-out samples with very small denominators
  tol = 1e3 * eps * np.max(np.abs(denom), keepdims=True)
  nonzero_indices = denom > tol

  ncc = np.zeros_like(denom, dtype=np.float64)
  ncc[nonzero_indices] = numerator[nonzero_indices] / denom[nonzero_indices]
  np.clip(ncc, -1, 1, out=ncc)

  return ncc


class Points:
  def __init__(self, num_points: int):
    self.times = deque[float]([0.0] * num_points, maxlen=num_points)
    self.okay = deque[bool]([False] * num_points, maxlen=num_points)
    self.desired = deque[float]([0.0] * num_points, maxlen=num_points)
    self.actual = deque[float]([0.0] * num_points, maxlen=num_points)

  @property
  def num_points(self):
    return len(self.desired)

  @property
  def num_okay(self):
    return np.count_nonzero(self.okay)

  def update(self, t: float, desired: float, actual: float, okay: bool):
    self.times.append(t)
    self.okay.append(okay)
    self.desired.append(desired)
    self.actual.append(actual)

  def get(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    return np.array(self.times), np.array(self.desired), np.array(self.actual), np.array(self.okay)


class BlockAverage:
  def __init__(self, num_blocks: int, block_size: int, valid_blocks: int, initial_value: float):
    self.num_blocks = num_blocks
    self.block_size = block_size
    self.block_idx = valid_blocks % num_blocks
    self.idx = 0

    self.values = np.tile(initial_value, (num_blocks, 1))
    self.valid_blocks = valid_blocks

  def update(self, value: float):
    self.values[self.block_idx] = (self.idx * self.values[self.block_idx] + value) / (self.idx + 1)
    self.idx = (self.idx + 1) % self.block_size
    if self.idx == 0:
      self.block_idx = (self.block_idx + 1) % self.num_blocks
      self.valid_blocks = min(self.valid_blocks + 1, self.num_blocks)

  def get(self) -> tuple[float, float, float, float]:
    valid_block_idx = [i for i in range(self.valid_blocks) if i != self.block_idx]
    valid_and_current_idx = valid_block_idx + ([self.block_idx] if self.idx > 0 else [])

    if len(valid_block_idx) > 0:
      valid_mean = float(np.mean(self.values[valid_block_idx], axis=0).item())
      valid_std = float(np.std(self.values[valid_block_idx], axis=0).item())
    else:
      valid_mean, valid_std = float('nan'), float('nan')

    if len(valid_and_current_idx) > 0:
      current_mean = float(np.mean(self.values[valid_and_current_idx], axis=0).item())
      current_std = float(np.std(self.values[valid_and_current_idx], axis=0).item())
    else:
      current_mean, current_std = float('nan'), float('nan')

    return valid_mean, valid_std, current_mean, current_std


class LongitudinalLagEstimator:
  inputs = {"carControl", "carState", "controlsState", "modelV2", "longitudinalPlan"}

  def __init__(self, CP: car.CarParams, dt: float = 0.05,
               block_count: int = BLOCK_NUM, min_valid_block_count: int = BLOCK_NUM_NEEDED, block_size: int = BLOCK_SIZE,
               min_vego: float = 8.0, max_vego: float = 40.0, min_accel: float = 0.2, min_ncc: float = 0.9, min_confidence: float = 0.2):
    self.dt = dt
    self.initial_lag = CP.longitudinalActuatorDelay
    self.block_size = block_size
    self.block_count = block_count
    self.min_valid_block_count = min_valid_block_count
    self.min_vego = min_vego
    self.max_vego = max_vego
    self.min_accel = min_accel
    self.min_ncc = min_ncc
    self.min_confidence = min_confidence

    self.t = 0.0
    self.long_active = False
    self.gas_pressed = False
    self.brake_pressed = False
    self.cruise_enabled = False
    self.target_velocity = 0.0  # MPC delay-compensated velocity target from longitudinalPlan.vTarget
    self.vision_velocity = 0.0   # ModelV2 vision-estimated current velocity (for safety validation)
    self.vehicle_velocity = 0.0  # Kalman-filtered CAN velocity (for delay correlation and quality filtering)
    self.accel_command = 0.0

    # Add tracking for estimation updates
    self.last_estimate_t = 0.0

    window_len = int(60.0 / self.dt)  # 60 second window
    self.points = Points(window_len)
    self.block_avg = BlockAverage(self.block_count, self.block_size, 0, self.initial_lag)

  def reset(self, initial_lag: float, valid_blocks: int):
    """Reset longitudinal lag estimator with learned delay from persistent storage"""
    window_len = int(60.0 / self.dt)
    self.points = Points(window_len)
    self.block_avg = BlockAverage(self.block_count, self.block_size, valid_blocks, initial_lag)
    cloudlog.info(f"LongitudinalLagEstimator initialized with learned delay: {initial_lag:.3f}s ({valid_blocks} valid blocks)")

  def handle_log(self, t: float, which: str, sm_data):
    if which == "carState":
      self.vehicle_velocity = sm_data.vEgo  # Kalman-filtered CAN velocity (for delay correlation and quality filtering)
      self.gas_pressed = sm_data.gasPressed
      self.brake_pressed = sm_data.brakePressed
      self.cruise_enabled = sm_data.cruiseState.enabled
    elif which == "carControl":
      self.accel_command = sm_data.actuators.accel
      self.long_active = sm_data.longActive  # longActive is in carControl, not controlsState
    elif which == "controlsState":
      pass  # No fields needed from controlsState currently
    elif which == "longitudinalPlan":
      # CRITICAL: Use MPC delay-compensated velocity target (not raw ModelV2!)
      # vTarget is extracted from MPC trajectory at action_t = longitudinalActuatorDelay + DT_MDL
      # This includes MPC optimization (physics/comfort/safety constraints) AND delay compensation
      if sm_data.vTarget > 0:
        self.target_velocity = sm_data.vTarget  # Delay-compensated MPC velocity
    elif which == "modelV2":
      if len(sm_data.velocity.x) > 0:
        # Vision-estimated current velocity for safety validation
        self.vision_velocity = sm_data.velocity.x[0]  # Current vision-estimated velocity (t=0.0s)

    self.t = t

  def update_points(self):
    # Quality filtering for longitudinal delay learning
    # CRITICAL: Compares MPC delay-compensated velocity target vs Kalman-filtered vehicle velocity
    # This measures real actuator delay: vision command → actual vehicle CAN response
    valid_conditions = [
      # Speed range: highway speeds for accurate measurement (use vehicle sensor for safety)
      self.min_vego <= self.vehicle_velocity <= self.max_vego,

      # Active longitudinal acceleration
      abs(self.accel_command) > self.min_accel,

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
      abs(self.vision_velocity - self.vehicle_velocity) < 1.39,  # ±5 km/h safety tolerance
    ]

    if all(valid_conditions):
      # Compare MPC delay-compensated target vs Kalman-filtered vehicle velocity
      # target_velocity = longitudinalPlan.vTarget (MPC-optimized, delay-compensated from vision)
      # vehicle_velocity = carState.vEgo (Kalman-filtered CAN speed, stable and reliable)
      # This measures real actuator delay: vision command → actual vehicle CAN response
      self.points.update(self.t, self.target_velocity, self.vehicle_velocity, True)
    else:
      self.points.update(self.t, self.target_velocity, self.vehicle_velocity, False)

  def points_enough(self):
    """Check if we have enough data points for correlation analysis"""
    return self.points.num_points >= self.block_size and self.points.num_okay >= self.block_size // 2

  def points_valid(self):
    """Check if we have sufficient valid data points"""
    return self.points.num_okay / max(1, self.points.num_points) >= 0.5

  def update_estimate(self):
    if not self.points_enough():
      return

    times, desired, actual, okay = self.points.get()

    # Check if there are any new valid data points since the last update
    is_valid = self.points_valid()
    if hasattr(self, 'last_estimate_t') and self.last_estimate_t != 0 and times[0] <= self.last_estimate_t:
      new_values_start_idx = next(-i for i, t in enumerate(reversed(times)) if t <= self.last_estimate_t)
      is_valid = is_valid and not (new_values_start_idx == 0 or not np.any(okay[new_values_start_idx:]))

    delay, corr, confidence = self.actuator_delay(desired, actual, okay, self.dt, 2.0)  # Max 2s delay
    if corr < self.min_ncc or confidence < self.min_confidence or not is_valid:
      return

    self.block_avg.update(delay)
    self.last_estimate_t = self.t

  def get_lag(self):
    valid_mean_lag, _, _, _ = self.block_avg.get()
    if self.block_avg.valid_blocks >= self.min_valid_block_count and not np.isnan(valid_mean_lag):
      return valid_mean_lag
    return self.initial_lag

  def has_learned_delay(self):
    valid_mean_lag, valid_std, _, _ = self.block_avg.get()
    return (self.block_avg.valid_blocks >= self.min_valid_block_count and
            not np.isnan(valid_mean_lag) and
            valid_std <= 0.1 and
            abs(valid_mean_lag - self.initial_lag) > 0.05)

  def get_learned_delay(self):
    return self.get_lag()

  def get_confidence(self):
    _, valid_std, _, _ = self.block_avg.get()
    if self.block_avg.valid_blocks < self.min_valid_block_count or np.isnan(valid_std):
      return 0.0
    return max(0.0, min(1.0, 1.0 - (valid_std / 0.1)))

  def get_longitudinal_msg_data(self, debug: bool = False) -> dict:
    """Generate longitudinal delay data for liveDelay message"""
    valid_mean_lag, valid_std, current_mean_lag, current_std = self.block_avg.get()

    # Determine status
    if self.block_avg.valid_blocks >= self.min_valid_block_count and not np.isnan(valid_mean_lag) and not np.isnan(valid_std):
      if valid_std > 0.1:  # MAX_LAG_STD threshold
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
    longitudinal_cal_perc = min(100 * (self.block_avg.valid_blocks * self.block_size + self.block_avg.idx) //
                               (self.min_valid_block_count * self.block_size), 100)

    # Vision-CAN speed safety validation
    vision_can_diff = abs(self.vision_velocity - self.vehicle_velocity)
    vision_can_safety_passed = vision_can_diff < 1.39  # ±5 km/h tolerance

    data = {
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
    }

    if debug:
      data['longitudinalPoints'] = self.block_avg.values.flatten().tolist()
    else:
      data['longitudinalPoints'] = []

    return data

  @staticmethod
  def actuator_delay(expected_sig: np.ndarray, actual_sig: np.ndarray, mask: np.ndarray, dt: float, max_lag: float) -> tuple[float, float, float]:
    return LateralLagEstimator.actuator_delay(expected_sig, actual_sig, mask, dt, max_lag)


class LateralLagEstimator:
  inputs = {"carControl", "carState", "controlsState", "liveCalibration", "livePose"}

  def __init__(self, CP: car.CarParams, dt: float,
               block_count: int = BLOCK_NUM, min_valid_block_count: int = BLOCK_NUM_NEEDED, block_size: int = BLOCK_SIZE,
               window_sec: float = MOVING_WINDOW_SEC, okay_window_sec: float = MIN_OKAY_WINDOW_SEC, min_recovery_buffer_sec: float = MIN_RECOVERY_BUFFER_SEC,
               min_vego: float = MIN_VEGO, min_yr: float = MIN_ABS_YAW_RATE, min_ncc: float = MIN_NCC,
               max_lat_accel: float = MAX_LAT_ACCEL, max_lat_accel_diff: float = MAX_LAT_ACCEL_DIFF, min_confidence: float = MIN_CONFIDENCE):
    self.dt = dt
    self.window_sec = window_sec
    self.okay_window_sec = okay_window_sec
    self.min_recovery_buffer_sec = min_recovery_buffer_sec
    self.initial_lag = CP.steerActuatorDelay + 0.2
    self.block_size = block_size
    self.block_count = block_count
    self.min_valid_block_count = min_valid_block_count
    self.min_vego = min_vego
    self.min_yr = min_yr
    self.min_ncc = min_ncc
    self.min_confidence = min_confidence
    self.max_lat_accel = max_lat_accel
    self.max_lat_accel_diff = max_lat_accel_diff

    self.t = 0.0
    self.lat_active = False
    self.steering_pressed = False
    self.steering_saturated = False
    self.desired_curvature = 0.0
    self.v_ego = 0.0
    self.yaw_rate = 0.0
    self.yaw_rate_std = 0.0
    self.pose_valid = False

    self.last_lat_inactive_t = 0.0
    self.last_steering_pressed_t = 0.0
    self.last_steering_saturated_t = 0.0
    self.last_pose_invalid_t = 0.0
    self.last_estimate_t = 0.0

    self.calibrator = PoseCalibrator()

    self.reset(self.initial_lag, 0)

  def reset(self, initial_lag: float, valid_blocks: int):
    window_len = int(self.window_sec / self.dt)
    self.points = Points(window_len)
    self.block_avg = BlockAverage(self.block_count, self.block_size, valid_blocks, initial_lag)

  def get_msg(self, valid: bool, debug: bool = False) -> capnp._DynamicStructBuilder:
    msg = messaging.new_message('liveDelay')

    msg.valid = valid

    liveDelay = msg.liveDelay

    valid_mean_lag, valid_std, current_mean_lag, current_std = self.block_avg.get()
    if self.block_avg.valid_blocks >= self.min_valid_block_count and not np.isnan(valid_mean_lag) and not np.isnan(valid_std):
      if valid_std > MAX_LAG_STD:
        liveDelay.status = log.LiveDelayData.Status.invalid
      else:
        liveDelay.status = log.LiveDelayData.Status.estimated
    else:
      liveDelay.status = log.LiveDelayData.Status.unestimated

    if liveDelay.status == log.LiveDelayData.Status.estimated:
      liveDelay.lateralDelay = valid_mean_lag
    else:
      liveDelay.lateralDelay = self.initial_lag

    if not np.isnan(current_mean_lag) and not np.isnan(current_std):
      liveDelay.lateralDelayEstimate = current_mean_lag
      liveDelay.lateralDelayEstimateStd = current_std
    else:
      liveDelay.lateralDelayEstimate = self.initial_lag
      liveDelay.lateralDelayEstimateStd = 0.0

    liveDelay.validBlocks = self.block_avg.valid_blocks
    liveDelay.calPerc = min(100 * (self.block_avg.valid_blocks * self.block_size + self.block_avg.idx) //
                            (self.min_valid_block_count * self.block_size), 100)
    if debug:
      liveDelay.points = self.block_avg.values.flatten().tolist()

    return msg

  def handle_log(self, t: float, which: str, msg: capnp._DynamicStructReader):
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

  def points_enough(self):
    return self.points.num_points >= int(self.okay_window_sec / self.dt)

  def points_valid(self):
    return self.points.num_okay >= int(self.okay_window_sec / self.dt)

  def update_points(self):
    la_desired = self.desired_curvature * self.v_ego * self.v_ego
    la_actual_pose = self.yaw_rate * self.v_ego

    fast = self.v_ego > self.min_vego
    turning = np.abs(self.yaw_rate) >= self.min_yr
    sensors_valid = self.pose_valid and np.abs(self.yaw_rate) < MAX_YAW_RATE_SANITY_CHECK and self.yaw_rate_std < MAX_YAW_RATE_SANITY_CHECK
    la_valid = np.abs(la_actual_pose) <= self.max_lat_accel and np.abs(la_desired - la_actual_pose) <= self.max_lat_accel_diff
    calib_valid = self.calibrator.calib_valid

    if not self.lat_active:
      self.last_lat_inactive_t = self.t
    if self.steering_pressed:
      self.last_steering_pressed_t = self.t
    if self.steering_saturated:
      self.last_steering_saturated_t = self.t
    if not sensors_valid or not la_valid:
      self.last_pose_invalid_t = self.t

    has_recovered = all( # wait for recovery after !lat_active, steering_pressed, steering_saturated, !sensors/la_valid
      self.t - last_t >= self.min_recovery_buffer_sec
      for last_t in [self.last_lat_inactive_t, self.last_steering_pressed_t, self.last_steering_saturated_t, self.last_pose_invalid_t]
    )
    okay = self.lat_active and not self.steering_pressed and not self.steering_saturated and \
           fast and turning and has_recovered and calib_valid and sensors_valid and la_valid

    self.points.update(self.t, la_desired, la_actual_pose, okay)

  def update_estimate(self):
    if not self.points_enough():
      return

    times, desired, actual, okay = self.points.get()
    # check if there are any new valid data points since the last update
    is_valid = self.points_valid()
    if self.last_estimate_t != 0 and times[0] <= self.last_estimate_t:
      new_values_start_idx = next(-i for i, t in enumerate(reversed(times)) if t <= self.last_estimate_t)
      is_valid = is_valid and not (new_values_start_idx == 0 or not np.any(okay[new_values_start_idx:]))

    delay, corr, confidence = self.actuator_delay(desired, actual, okay, self.dt, MAX_LAG)
    if corr < self.min_ncc or confidence < self.min_confidence or not is_valid:
      return

    self.block_avg.update(delay)
    self.last_estimate_t = self.t

  @staticmethod
  def actuator_delay(expected_sig: np.ndarray, actual_sig: np.ndarray, mask: np.ndarray, dt: float, max_lag: float) -> tuple[float, float, float]:
    assert len(expected_sig) == len(actual_sig)
    max_lag_samples = int(max_lag / dt)
    padded_size = fft_next_good_size(len(expected_sig) + max_lag_samples)

    ncc = masked_normalized_cross_correlation(expected_sig, actual_sig, mask, padded_size)

    # only consider lags from 0 to max_lag
    roi = np.s_[len(expected_sig) - 1: len(expected_sig) - 1 + max_lag_samples]
    extended_roi = np.s_[roi.start - CORR_BORDER_OFFSET: roi.stop + CORR_BORDER_OFFSET]
    roi_ncc = ncc[roi]
    extended_roi_ncc = ncc[extended_roi]

    max_corr_index = np.argmax(roi_ncc)
    corr = roi_ncc[max_corr_index]
    lag = parabolic_peak_interp(roi_ncc, max_corr_index) * dt

    # to estimate lag confidence, gather all high-correlation candidates and see how spread they are
    # if e.g. 0.8 and 0.4 are both viable, this is an ambiguous case
    ncc_thresh = (roi_ncc.max() - roi_ncc.min()) * LAG_CANDIDATE_CORR_THRESHOLD + roi_ncc.min()
    good_lag_candidate_mask = extended_roi_ncc >= ncc_thresh
    good_lag_candidate_edges = np.diff(good_lag_candidate_mask.astype(int), prepend=0, append=0)
    starts, ends = np.where(good_lag_candidate_edges == 1)[0], np.where(good_lag_candidate_edges == -1)[0] - 1
    run_idx = np.searchsorted(starts, max_corr_index + CORR_BORDER_OFFSET, side='right') - 1
    width = ends[run_idx] - starts[run_idx] + 1
    confidence = np.clip(1 - width * dt, 0, 1)

    return lag, corr, confidence


def retrieve_initial_lag(params: Params, CP: car.CarParams):
  """Retrieve learned lateral and longitudinal delays from persistent storage"""
  last_lag_data = params.get("LiveDelay")
  last_carparams_data = params.get("CarParamsPrevRoute")

  if last_lag_data is not None:
    try:
      with log.Event.from_bytes(last_lag_data) as last_lag_msg, car.CarParams.from_bytes(last_carparams_data) as last_CP:
        ld = last_lag_msg.liveDelay
        if last_CP.carFingerprint != CP.carFingerprint:
          raise Exception("Car model mismatch")

        # Lateral delay (existing)
        lateral_lag = ld.lateralDelayEstimate
        lateral_valid_blocks = ld.validBlocks
        lateral_status = ld.status
        assert lateral_valid_blocks <= BLOCK_NUM, "Invalid number of lateral valid blocks"
        assert lateral_status != log.LiveDelayData.Status.invalid, "Lateral lag estimate is invalid"

        # Longitudinal delay (NEW)
        longitudinal_lag = ld.longitudinalDelayEstimate
        longitudinal_valid_blocks = ld.longitudinalValidBlocks
        longitudinal_status = ld.longitudinalStatus

        # Return dict with both lateral and longitudinal delays
        return {
          'lateral': (lateral_lag, lateral_valid_blocks) if lateral_status == log.LiveDelayData.Status.estimated else None,
          'longitudinal': (longitudinal_lag, longitudinal_valid_blocks) if longitudinal_status == log.LiveDelayData.Status.estimated else None,
        }
    except Exception as e:
      cloudlog.error(f"Failed to retrieve initial lag: {e}")
      params.remove("LiveDelay")

  return None


def apply_learned_longitudinal_delay(CP: car.CarParams, params: Params) -> car.CarParams:
  """
  Apply learned longitudinal delay to CarParams for improved MPC velocity extraction accuracy

  This function loads the learned longitudinal delay from persistent storage and updates
  CarParams.longitudinalActuatorDelay if the learned value meets confidence thresholds.

  Returns: Updated CarParams with learned delay, or original CP if no valid learned delay
  """
  initial_lag_data = retrieve_initial_lag(params, CP)

  if initial_lag_data is None or initial_lag_data.get('longitudinal') is None:
    cloudlog.info(f"No learned longitudinal delay available, using default: {CP.longitudinalActuatorDelay:.3f}s")
    return CP

  learned_delay, valid_blocks = initial_lag_data['longitudinal']

  # Sanity check: Learned delay must be reasonable (0.05s to 1.0s)
  if not (0.05 <= learned_delay <= 1.0):
    cloudlog.warning(f"Learned longitudinal delay {learned_delay:.3f}s outside valid range [0.05s, 1.0s], using default")
    return CP

  # Require minimum confidence (at least BLOCK_NUM_NEEDED valid blocks)
  if valid_blocks < BLOCK_NUM_NEEDED:
    cloudlog.info(f"Learned longitudinal delay confidence too low ({valid_blocks} blocks < {BLOCK_NUM_NEEDED} required), using default")
    return CP

  # Update CarParams with learned delay (CP is mutable)
  original_delay = CP.longitudinalActuatorDelay
  CP.longitudinalActuatorDelay = learned_delay

  cloudlog.info(f"Applied learned longitudinal delay: {original_delay:.3f}s → {learned_delay:.3f}s ({valid_blocks} valid blocks)")

  return CP


def main():
  config_realtime_process([0, 1, 2, 3], 5)

  DEBUG = bool(int(os.getenv("DEBUG", "0")))

  pm = messaging.PubMaster(['liveDelay'])
  sm = messaging.SubMaster(['livePose', 'liveCalibration', 'carState', 'controlsState', 'carControl', 'modelV2', 'longitudinalPlan'], poll='livePose')

  params = Params()
  CP = messaging.log_from_bytes(params.get("CarParams", block=True), car.CarParams)

  # Initialize lateral lag learner (existing)
  lag_learner = LateralLagEstimator(CP, 1. / SERVICE_LIST['livePose'].frequency)

  # Initialize longitudinal lag learner (NEW)
  long_lag_learner = LongitudinalLagEstimator(CP, 1. / 20.0)  # 20Hz for modelV2

  # Load learned delays from persistent storage and initialize both learners
  if (initial_lag_params := retrieve_initial_lag(params, CP)) is not None:
    if initial_lag_params.get('lateral') is not None:
      lateral_lag, lateral_valid_blocks = initial_lag_params['lateral']
      lag_learner.reset(lateral_lag, lateral_valid_blocks)
      cloudlog.info(f"Loaded learned lateral delay: {lateral_lag:.3f}s ({lateral_valid_blocks} valid blocks)")

    if initial_lag_params.get('longitudinal') is not None:
      longitudinal_lag, longitudinal_valid_blocks = initial_lag_params['longitudinal']
      long_lag_learner.reset(longitudinal_lag, longitudinal_valid_blocks)
      # cloudlog.info is already in reset() method

  while True:
    sm.update()
    if sm.all_checks():
      for which in sorted(sm.updated.keys(), key=lambda x: sm.logMonoTime[x]):
        if sm.updated[which]:
          t = sm.logMonoTime[which] * 1e-9
          lag_learner.handle_log(t, which, sm[which])

          # Update longitudinal lag learner with relevant messages
          if which in long_lag_learner.inputs:
            long_lag_learner.handle_log(t, which, sm[which])

      lag_learner.update_points()
      long_lag_learner.update_points()

    # 4Hz driven by livePose
    if sm.frame % 5 == 0:
      lag_learner.update_estimate()
      long_lag_learner.update_estimate()

      # Get lateral delay message
      lag_msg = lag_learner.get_msg(sm.all_checks(), DEBUG)

      # Add longitudinal delay data to the message
      longitudinal_data = long_lag_learner.get_longitudinal_msg_data(DEBUG)
      liveDelay = lag_msg.liveDelay

      # Set longitudinal fields
      liveDelay.longitudinalDelay = longitudinal_data['longitudinalDelay']
      liveDelay.longitudinalDelayEstimate = longitudinal_data['longitudinalDelayEstimate']
      liveDelay.longitudinalDelayEstimateStd = longitudinal_data['longitudinalDelayEstimateStd']
      liveDelay.longitudinalValidBlocks = longitudinal_data['longitudinalValidBlocks']
      liveDelay.longitudinalStatus = longitudinal_data['longitudinalStatus']
      liveDelay.longitudinalCalPerc = longitudinal_data['longitudinalCalPerc']
      liveDelay.longitudinalPoints = longitudinal_data['longitudinalPoints']

      # Set vision-CAN safety validation fields
      liveDelay.visionSpeed = longitudinal_data['visionSpeed']
      liveDelay.canSpeed = longitudinal_data['canSpeed']
      liveDelay.visionCanDiff = longitudinal_data['visionCanDiff']
      liveDelay.visionCanSafetyPassed = longitudinal_data['visionCanSafetyPassed']

      lag_msg_dat = lag_msg.to_bytes()
      pm.send('liveDelay', lag_msg_dat)

      if sm.frame % 1200 == 0: # cache every 60 seconds
        params.put_nonblocking("LiveDelay", lag_msg_dat)
