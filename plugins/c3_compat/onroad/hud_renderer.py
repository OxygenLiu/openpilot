import math
import pyray as rl
from dataclasses import dataclass
from openpilot.common.constants import CV
from openpilot.common.params import Params
from openpilot.selfdrive.ui.onroad.exp_button import ExpButton
from openpilot.selfdrive.ui.ui_state import ui_state, UIStatus
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget

# Constants
SET_SPEED_NA = 255
KM_TO_MILE = 0.621371
CRUISE_DISABLED_CHAR = '–'

# Speed limit sign config
SPEED_SIGN_RADIUS = 60  # px
SPEED_SIGN_BORDER = 8   # red ring thickness
SPEED_SIGN_X = 120      # center x (below MAX box)
SPEED_SIGN_Y = 330      # center y
SPEED_SIGN_FONT_SIZE = 56
SOURCE_LABELS = {0: "OSM", 1: "SIGN", 2: "~"}


@dataclass(frozen=True)
class UIConfig:
  header_height: int = 300
  border_size: int = 30
  button_size: int = 192
  set_speed_width_metric: int = 200
  set_speed_width_imperial: int = 172
  set_speed_height: int = 204
  wheel_icon_size: int = 144


@dataclass(frozen=True)
class FontSizes:
  current_speed: int = 176
  speed_unit: int = 66
  max_speed: int = 40
  set_speed: int = 90


@dataclass(frozen=True)
class Colors:
  WHITE = rl.WHITE
  DISENGAGED = rl.Color(145, 155, 149, 255)
  OVERRIDE = rl.Color(145, 155, 149, 255)  # Added
  ENGAGED = rl.Color(128, 216, 166, 255)
  DISENGAGED_BG = rl.Color(0, 0, 0, 153)
  OVERRIDE_BG = rl.Color(145, 155, 149, 204)
  ENGAGED_BG = rl.Color(128, 216, 166, 204)
  GREY = rl.Color(166, 166, 166, 255)
  DARK_GREY = rl.Color(114, 114, 114, 255)
  BLACK_TRANSLUCENT = rl.Color(0, 0, 0, 166)
  WHITE_TRANSLUCENT = rl.Color(255, 255, 255, 200)
  BORDER_TRANSLUCENT = rl.Color(255, 255, 255, 75)
  HEADER_GRADIENT_START = rl.Color(0, 0, 0, 114)
  HEADER_GRADIENT_END = rl.BLANK


UI_CONFIG = UIConfig()
FONT_SIZES = FontSizes()
COLORS = Colors()


class HudRenderer(Widget):
  def __init__(self):
    super().__init__()
    """Initialize the HUD renderer."""
    self.is_cruise_set: bool = False
    self.is_cruise_available: bool = True
    self.set_speed: float = SET_SPEED_NA
    self.speed: float = 0.0
    self.v_ego_cluster_seen: bool = False

    self._font_semi_bold: rl.Font = gui_app.font(FontWeight.SEMI_BOLD)
    self._font_bold: rl.Font = gui_app.font(FontWeight.BOLD)
    self._font_medium: rl.Font = gui_app.font(FontWeight.MEDIUM)

    self._exp_button: ExpButton = ExpButton(UI_CONFIG.button_size, UI_CONFIG.wheel_icon_size)

    # Speed limit sign state
    self._params = Params()
    self._speed_limit: float = 0.0
    self._speed_limit_source: int = 2  # roadTypeInference default
    self._speed_limit_confirmed: bool = False
    self._speed_limit_rect = rl.Rectangle(
      SPEED_SIGN_X - SPEED_SIGN_RADIUS,
      SPEED_SIGN_Y - SPEED_SIGN_RADIUS,
      SPEED_SIGN_RADIUS * 2,
      SPEED_SIGN_RADIUS * 2,
    )

  def _update_state(self) -> None:
    """Update HUD state based on car state and controls state."""
    sm = ui_state.sm
    if sm.recv_frame["carState"] < ui_state.started_frame:
      self.is_cruise_set = False
      self.set_speed = SET_SPEED_NA
      self.speed = 0.0
      return

    controls_state = sm['controlsState']
    car_state = sm['carState']

    v_cruise_cluster = car_state.vCruiseCluster
    self.set_speed = (
      controls_state.vCruiseDEPRECATED if v_cruise_cluster == 0.0 else v_cruise_cluster
    )
    self.is_cruise_set = 0 < self.set_speed < SET_SPEED_NA
    self.is_cruise_available = self.set_speed != -1

    if self.is_cruise_set and not ui_state.is_metric:
      self.set_speed *= KM_TO_MILE

    v_ego_cluster = car_state.vEgoCluster
    self.v_ego_cluster_seen = self.v_ego_cluster_seen or v_ego_cluster != 0.0
    v_ego = v_ego_cluster if self.v_ego_cluster_seen else car_state.vEgo
    speed_conversion = CV.MS_TO_KPH if ui_state.is_metric else CV.MS_TO_MPH
    self.speed = max(0.0, v_ego * speed_conversion)

    # Speed limit state
    if sm.recv_frame.get("speedLimitState", 0) > 0:
      sls = sm['speedLimitState']
      self._speed_limit = sls.speedLimit
      self._speed_limit_source = sls.source.raw if hasattr(sls.source, 'raw') else int(sls.source)
      self._speed_limit_confirmed = sls.confirmed

  def _render(self, rect: rl.Rectangle) -> None:
    """Render HUD elements to the screen."""
    # Draw the header background
    rl.draw_rectangle_gradient_v(
      int(rect.x),
      int(rect.y),
      int(rect.width),
      UI_CONFIG.header_height,
      COLORS.HEADER_GRADIENT_START,
      COLORS.HEADER_GRADIENT_END,
    )

    if self.is_cruise_available:
      self._draw_set_speed(rect)

    self._draw_current_speed(rect)

    if self._speed_limit > 0:
      self._draw_speed_limit_sign(rect)

    button_x = rect.x + rect.width - UI_CONFIG.border_size - UI_CONFIG.button_size
    button_y = rect.y + UI_CONFIG.border_size
    self._exp_button.render(rl.Rectangle(button_x, button_y, UI_CONFIG.button_size, UI_CONFIG.button_size))

  def user_interacting(self) -> bool:
    return self._exp_button.is_pressed

  def _handle_mouse_release(self, mouse_pos) -> None:
    # Speed limit sign tap — toggle confirmation
    if self._speed_limit > 0:
      dx = mouse_pos.x - (self._rect.x + SPEED_SIGN_X)
      dy = mouse_pos.y - (self._rect.y + SPEED_SIGN_Y)
      if math.sqrt(dx * dx + dy * dy) <= SPEED_SIGN_RADIUS:
        new_confirmed = not self._speed_limit_confirmed
        self._speed_limit_confirmed = new_confirmed
        self._params.put("SpeedLimitConfirmed", "1" if new_confirmed else "0")
        self._params.put("SpeedLimitValue", str(self._speed_limit))
        return
    super()._handle_mouse_release(mouse_pos)

  def _draw_set_speed(self, rect: rl.Rectangle) -> None:
    """Draw the MAX speed indicator box."""
    set_speed_width = UI_CONFIG.set_speed_width_metric if ui_state.is_metric else UI_CONFIG.set_speed_width_imperial
    x = rect.x + 60 + (UI_CONFIG.set_speed_width_imperial - set_speed_width) // 2
    y = rect.y + 45

    set_speed_rect = rl.Rectangle(x, y, set_speed_width, UI_CONFIG.set_speed_height)
    rl.draw_rectangle_rounded(set_speed_rect, 0.35, 10, COLORS.BLACK_TRANSLUCENT)
    rl.draw_rectangle_rounded_lines_ex(set_speed_rect, 0.35, 10, 6, COLORS.BORDER_TRANSLUCENT)

    max_color = COLORS.GREY
    set_speed_color = COLORS.DARK_GREY
    if self.is_cruise_set:
      set_speed_color = COLORS.WHITE
      if ui_state.status == UIStatus.ENGAGED:
        max_color = COLORS.ENGAGED
      elif ui_state.status == UIStatus.DISENGAGED:
        max_color = COLORS.DISENGAGED
      elif ui_state.status == UIStatus.OVERRIDE:
        max_color = COLORS.OVERRIDE

    max_text = tr("MAX")
    max_text_width = measure_text_cached(self._font_semi_bold, max_text, FONT_SIZES.max_speed).x
    rl.draw_text_ex(
      self._font_semi_bold,
      max_text,
      rl.Vector2(x + (set_speed_width - max_text_width) / 2, y + 27),
      FONT_SIZES.max_speed,
      0,
      max_color,
    )

    set_speed_text = CRUISE_DISABLED_CHAR if not self.is_cruise_set else str(round(self.set_speed))
    speed_text_width = measure_text_cached(self._font_bold, set_speed_text, FONT_SIZES.set_speed).x
    rl.draw_text_ex(
      self._font_bold,
      set_speed_text,
      rl.Vector2(x + (set_speed_width - speed_text_width) / 2, y + 77),
      FONT_SIZES.set_speed,
      0,
      set_speed_color,
    )

  def _draw_current_speed(self, rect: rl.Rectangle) -> None:
    """Draw the current vehicle speed and unit. Yellow when curvature-limited."""
    speed_text = str(round(self.speed))
    speed_text_size = measure_text_cached(self._font_bold, speed_text, FONT_SIZES.current_speed)
    speed_pos = rl.Vector2(rect.x + rect.width / 2 - speed_text_size.x / 2, 180 - speed_text_size.y / 2)
    speed_color = rl.Color(255, 215, 0, 255) if ui_state.curvature_speed_limited else COLORS.WHITE
    rl.draw_text_ex(self._font_bold, speed_text, speed_pos, FONT_SIZES.current_speed, 0, speed_color)

    unit_text = tr("km/h") if ui_state.is_metric else tr("mph")
    unit_text_size = measure_text_cached(self._font_medium, unit_text, FONT_SIZES.speed_unit)
    unit_pos = rl.Vector2(rect.x + rect.width / 2 - unit_text_size.x / 2, 290 - unit_text_size.y / 2)
    rl.draw_text_ex(self._font_medium, unit_text, unit_pos, FONT_SIZES.speed_unit, 0, COLORS.WHITE_TRANSLUCENT)

  def _draw_speed_limit_sign(self, rect: rl.Rectangle) -> None:
    """Draw Vienna-style speed limit sign (red circle, white fill, black number).

    50% opacity when unconfirmed (suggestion), 100% when confirmed (active).
    Small source indicator below: "OSM" / "SIGN" / "~"
    """
    cx = int(rect.x) + SPEED_SIGN_X
    cy = int(rect.y) + SPEED_SIGN_Y
    r = SPEED_SIGN_RADIUS
    alpha = 255 if self._speed_limit_confirmed else 128

    # Red outer ring
    red_ring = rl.Color(220, 30, 30, alpha)
    rl.draw_circle(cx, cy, r, red_ring)

    # White inner fill
    white_fill = rl.Color(255, 255, 255, alpha)
    rl.draw_circle(cx, cy, r - SPEED_SIGN_BORDER, white_fill)

    # Speed number (black)
    speed_text = str(round(self._speed_limit))
    text_color = rl.Color(0, 0, 0, alpha)
    text_size = measure_text_cached(self._font_bold, speed_text, SPEED_SIGN_FONT_SIZE)
    rl.draw_text_ex(
      self._font_bold,
      speed_text,
      rl.Vector2(cx - text_size.x / 2, cy - text_size.y / 2),
      SPEED_SIGN_FONT_SIZE,
      0,
      text_color,
    )

    # Source indicator below the sign
    source_label = SOURCE_LABELS.get(self._speed_limit_source, "?")
    source_size = measure_text_cached(self._font_medium, source_label, 28)
    source_color = rl.Color(200, 200, 200, alpha)
    rl.draw_text_ex(
      self._font_medium,
      source_label,
      rl.Vector2(cx - source_size.x / 2, cy + r + 8),
      28,
      0,
      source_color,
    )
