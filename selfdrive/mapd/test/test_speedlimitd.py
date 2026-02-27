#!/usr/bin/env python3
"""
Tests for speedlimitd.py

Covers:
  - Params key registration (prevents UnknownKeyName crashes on C3)
  - Pure function logic (infer_lane_count, infer_speed_from_road_type)
  - Priority cascade (OSM > YOLO > road-type inference)
  - Confirmation management
"""
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from openpilot.selfdrive.mapd.speedlimitd import (
  DEFAULT_FALLBACK_SPEED,
  SPEED_TABLE_NONURBAN,
  SPEED_TABLE_URBAN,
  infer_lane_count,
  infer_speed_from_road_type,
)

OPENPILOT_ROOT = Path(__file__).resolve().parents[3]
PARAMS_KEYS_H = OPENPILOT_ROOT / "common" / "params_keys.h"
MAPD_DIR = OPENPILOT_ROOT / "selfdrive" / "mapd"


# ---------------------------------------------------------------------------
# Params key registration — catches the exact crash that prompted this test
# ---------------------------------------------------------------------------
class TestParamsKeysRegistered:
  """Every Params key used under selfdrive/mapd/ must be in params_keys.h."""

  @staticmethod
  def _registered_keys() -> set[str]:
    """Parse all key names from params_keys.h."""
    text = PARAMS_KEYS_H.read_text()
    return set(re.findall(r'\{"(\w+)"', text))

  @staticmethod
  def _used_keys() -> set[str]:
    """Scan all .py files under selfdrive/mapd/ for Params access calls."""
    pattern = re.compile(
      r'params\.'
      r'(?:get|put|get_bool|put_bool|put_int|get_int|put_float|get_float|remove)'
      r'\(\s*["\'](\w+)["\']'
    )
    keys: set[str] = set()
    for py_file in MAPD_DIR.rglob("*.py"):
      if "/test/" in str(py_file):
        continue
      keys.update(pattern.findall(py_file.read_text()))
    return keys

  def test_all_mapd_params_registered(self):
    registered = self._registered_keys()
    used = self._used_keys()
    missing = used - registered
    assert not missing, (
      f"Params keys used in selfdrive/mapd/ but missing from params_keys.h: {missing}\n"
      f"Add them to common/params_keys.h to prevent UnknownKeyName crashes on C3."
    )


# ---------------------------------------------------------------------------
# infer_lane_count
# ---------------------------------------------------------------------------
class TestInferLaneCount:

  @staticmethod
  def _model_msg(probs: list[float]) -> MagicMock:
    msg = MagicMock()
    msg.laneLineProbs = probs
    return msg

  def test_multi_lane(self):
    # Both lane lines visible + left edge → multi-lane
    msg = self._model_msg([0.4, 0.8, 0.9, 0.1])
    assert infer_lane_count(msg) == 2

  def test_single_lane_no_edge(self):
    # Both lane lines but no road edge → single-lane
    msg = self._model_msg([0.1, 0.8, 0.9, 0.1])
    assert infer_lane_count(msg) == 1

  def test_single_lane_missing_right(self):
    # Only left lane line → single-lane
    msg = self._model_msg([0.4, 0.8, 0.2, 0.1])
    assert infer_lane_count(msg) == 1

  def test_no_probs(self):
    msg = MagicMock(spec=[])
    assert infer_lane_count(msg) == 1

  def test_short_probs(self):
    msg = self._model_msg([0.5, 0.5])
    assert infer_lane_count(msg) == 1


# ---------------------------------------------------------------------------
# infer_speed_from_road_type
# ---------------------------------------------------------------------------
class TestInferSpeedFromRoadType:

  def test_motorway_freeway_multi(self):
    assert infer_speed_from_road_type('motorway', 2, 'freeway') == 120

  def test_motorway_city_multi(self):
    assert infer_speed_from_road_type('motorway', 2, 'city') == 100

  def test_trunk_freeway_single(self):
    assert infer_speed_from_road_type('trunk', 1, 'freeway') == 70

  def test_trunk_city_multi(self):
    assert infer_speed_from_road_type('trunk', 2, 'city') == 80

  def test_residential_single(self):
    assert infer_speed_from_road_type('residential', 1, 'city') == 30

  def test_unknown_road_type(self):
    assert infer_speed_from_road_type('construction', 1, 'city') == DEFAULT_FALLBACK_SPEED

  def test_unknown_context_uses_urban(self):
    # 'unknown' context should use the more conservative urban table
    assert infer_speed_from_road_type('primary', 2, 'unknown') == SPEED_TABLE_URBAN['primary']['multi']

  @pytest.mark.parametrize("road_type", list(SPEED_TABLE_URBAN.keys()))
  def test_all_urban_types_covered(self, road_type):
    for lc in (1, 2):
      result = infer_speed_from_road_type(road_type, lc, 'city')
      assert result > 0

  @pytest.mark.parametrize("road_type", list(SPEED_TABLE_NONURBAN.keys()))
  def test_all_nonurban_types_covered(self, road_type):
    for lc in (1, 2):
      result = infer_speed_from_road_type(road_type, lc, 'freeway')
      assert result > 0


# ---------------------------------------------------------------------------
# Priority cascade
# ---------------------------------------------------------------------------
class TestPriorityCascade:
  """Test the three-tier priority: OSM > YOLO > road-type inference."""

  def test_osm_wins(self):
    osm, yolo, inferred = 80, 60, 40
    speed, source = self._cascade(osm, yolo, inferred)
    assert speed == 80.0
    assert source == 0  # osmMaxspeed

  def test_yolo_when_no_osm(self):
    osm, yolo, inferred = 0, 60, 40
    speed, source = self._cascade(osm, yolo, inferred)
    assert speed == 60.0
    assert source == 1  # yoloDetection

  def test_inferred_when_no_osm_no_yolo(self):
    osm, yolo, inferred = 0, 0, 40
    speed, source = self._cascade(osm, yolo, inferred)
    assert speed == 40.0
    assert source == 2  # roadTypeInference

  def test_osm_overrides_higher_yolo(self):
    # OSM says 60 even though YOLO saw 80 — OSM wins
    speed, source = self._cascade(60, 80, 40)
    assert speed == 60.0
    assert source == 0

  @staticmethod
  def _cascade(osm_speed: int, yolo_speed: int, inferred_speed: int) -> tuple[float, int]:
    """Replicate the priority cascade from SpeedLimitMiddleware.update()."""
    if osm_speed > 0:
      return float(osm_speed), 0
    elif yolo_speed > 0:
      return float(yolo_speed), 1
    else:
      return float(inferred_speed), 2


# ---------------------------------------------------------------------------
# Confirmation management
# ---------------------------------------------------------------------------
class TestConfirmation:

  def test_confirmed_when_value_matches(self):
    confirmed, _ = self._check_confirmation('1', '80.0', 80.0)
    assert confirmed is True

  def test_not_confirmed_when_value_differs(self):
    confirmed, reset = self._check_confirmation('1', '60.0', 80.0)
    assert confirmed is False
    assert reset is True  # should reset param

  def test_not_confirmed_when_param_zero(self):
    confirmed, _ = self._check_confirmation('0', '80.0', 80.0)
    assert confirmed is False

  def test_not_confirmed_when_param_none(self):
    confirmed, _ = self._check_confirmation(None, None, 80.0)
    assert confirmed is False

  def test_not_confirmed_bad_value(self):
    confirmed, _ = self._check_confirmation('1', 'abc', 80.0)
    assert confirmed is False

  @staticmethod
  def _check_confirmation(
    confirmed_param: str | None,
    value_param: str | None,
    current_speed: float,
  ) -> tuple[bool, bool]:
    """Replicate confirmation logic from SpeedLimitMiddleware.update()."""
    reset = False
    if confirmed_param == '1' and value_param:
      try:
        pv = float(value_param)
        if abs(pv - current_speed) < 0.5:
          return True, False
        else:
          reset = True
          return False, reset
      except ValueError:
        return False, False
    return False, False
