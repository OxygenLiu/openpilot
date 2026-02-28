import numpy as np
from openpilot.common.realtime import DT_MDL
from openpilot.selfdrive.controls.lib.drive_helpers import smooth_value
from openpilot.common.params import Params


class LaneCenteringCorrection:
  """Curvature correction to center car in lane during turns.

  v9: Single-lane approach - uses higher confidence lane + estimated width.
  When both lanes are confident (>0.5), measures and updates lane width.
  Falls back to 3.5m default when no measurement available.

  Usage:
    lcc = LaneCenteringCorrection()
    correction = lcc.update(model_v2, v_ego)
  """

  # Curvature-dependent K: sharper turns need stronger correction
  K_BP = [0.002, 0.005, 0.008, 0.012, 0.020]  # Curvature breakpoints (1/m)
  K_V = [0.03, 0.35, 0.40, 0.50, 0.65]         # Corresponding K values

  MIN_PROB = 0.5           # Minimum lane detection confidence
  MIN_SPEED = 9.0          # m/s - disable at low speed
  MIN_CURVATURE = 0.002    # 1/m (~500m radius) - activate correction
  EXIT_CURVATURE = 0.001   # 1/m (~1000m radius) - deactivate on straight road
  OFFSET_THRESHOLD = 0.3   # m - activate when offset exceeds this
  OFFSET_TOLERANCE = 0.15  # m - deactivate when offset within tolerance
  SMOOTH_TAU = 0.5         # seconds - correction smoothing
  WINDDOWN_TAU = 1.0       # seconds - slower wind-down when exiting turns
  MEASUREMENT_TAU = 0.2    # seconds - lane center measurement smoothing
  MAX_JUMP = 0.3           # m - max lane center change per frame

  # Lane width estimation
  LANE_WIDTH_DEFAULT = 3.5   # m - fallback (standard in China)
  LANE_WIDTH_MIN = 2.5       # m - minimum valid for estimation
  LANE_WIDTH_MAX = 4.5       # m - maximum valid for estimation
  LANE_WIDTH_SMOOTH_TAU = 2.0  # seconds - width estimation smoothing

  def __init__(self):
    self.prev_correction = 0.0
    self.active = False
    self.prev_lane_center = None
    self.smoothed_lane_center = None
    self.estimated_lane_width = None

  def _reset_lane_tracking(self):
    self.active = False
    self.prev_lane_center = None
    self.smoothed_lane_center = None

  def _smooth_correction(self, target, winding_down=False):
    tau = self.WINDDOWN_TAU if winding_down else self.SMOOTH_TAU
    self.prev_correction = smooth_value(target, self.prev_correction, tau, DT_MDL)
    return self.prev_correction

  def update(self, model_v2, v_ego):
    # Skip if lane detection data not available
    if len(model_v2.laneLineProbs) < 3:
      self._reset_lane_tracking()
      return self._smooth_correction(0.0, winding_down=True)

    right_prob = model_v2.laneLineProbs[2]
    left_prob = model_v2.laneLineProbs[1]

    # Need at least one lane with good confidence
    if right_prob < self.MIN_PROB and left_prob < self.MIN_PROB:
      self._reset_lane_tracking()
      return self._smooth_correction(0.0, winding_down=True)

    if v_ego < self.MIN_SPEED:
      self._reset_lane_tracking()
      return self._smooth_correction(0.0, winding_down=True)

    # Check array lengths (idx=0 should always exist)
    if (len(model_v2.position.y) == 0 or
        len(model_v2.laneLines[1].y) == 0 or
        len(model_v2.laneLines[2].y) == 0):
      self._reset_lane_tracking()
      return self._smooth_correction(0.0, winding_down=True)

    curvature = model_v2.action.desiredCurvature
    path_y = model_v2.position.y[0]
    right_y = model_v2.laneLines[2].y[0]
    left_y = model_v2.laneLines[1].y[0]

    # Dynamic lane width estimation when both lanes are confident
    if right_prob >= self.MIN_PROB and left_prob >= self.MIN_PROB:
      measured_width = right_y - left_y
      if self.LANE_WIDTH_MIN <= measured_width <= self.LANE_WIDTH_MAX:
        if self.estimated_lane_width is None:
          self.estimated_lane_width = measured_width
        else:
          self.estimated_lane_width = smooth_value(measured_width, self.estimated_lane_width, self.LANE_WIDTH_SMOOTH_TAU)

    lane_width = self.estimated_lane_width if self.estimated_lane_width is not None else self.LANE_WIDTH_DEFAULT
    half_width = lane_width / 2

    # Use higher confidence lane to estimate center
    if right_prob >= left_prob and right_prob >= self.MIN_PROB:
      lane_center = right_y - half_width
    elif left_prob >= self.MIN_PROB:
      lane_center = left_y + half_width
    else:
      self.active = False
      return self._smooth_correction(0.0, winding_down=True)

    # Reject sudden lane center jumps
    if self.prev_lane_center is not None:
      if abs(lane_center - self.prev_lane_center) > self.MAX_JUMP:
        self.active = False
        self.prev_lane_center = lane_center
        self.smoothed_lane_center = None
        return self._smooth_correction(0.0, winding_down=True)
    self.prev_lane_center = lane_center

    # Smooth lane center measurement
    if self.smoothed_lane_center is None:
      self.smoothed_lane_center = lane_center
    else:
      self.smoothed_lane_center = smooth_value(lane_center, self.smoothed_lane_center, self.MEASUREMENT_TAU)

    offset = path_y - self.smoothed_lane_center

    # Hysteresis logic
    if not self.active:
      if abs(curvature) >= self.MIN_CURVATURE and abs(offset) >= self.OFFSET_THRESHOLD:
        self.active = True
    else:
      if abs(curvature) < self.EXIT_CURVATURE and abs(offset) < self.OFFSET_TOLERANCE:
        self.active = False

    if self.active:
      k = float(np.interp(abs(curvature), self.K_BP, self.K_V))
      raw_correction = -k * offset / (v_ego ** 2)
      return self._smooth_correction(raw_correction, winding_down=False)
    else:
      return self._smooth_correction(0.0, winding_down=True)


_lcc = None
_params = None

def on_curvature_correction(curvature, model_v2, v_ego, lane_changing):
  global _lcc, _params
  if _params is None:
    _params = Params()
  if not _params.get_bool("LaneCenteringCorrection"):
    return curvature
  if _lcc is None:
    _lcc = LaneCenteringCorrection()
  if lane_changing:
    return curvature
  correction = _lcc.update(model_v2, v_ego)
  return curvature + correction
