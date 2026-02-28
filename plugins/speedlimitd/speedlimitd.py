#!/usr/bin/env python3
"""
Speed Limit Middleware — merges OSM, YOLO, and road-type inference
into a single SpeedLimitState message at 1 Hz.

Three-tier priority:
  1. OSM maxspeed tag (highest confidence)
  2. YOLO speed sign detection (event-driven)
  3. Road type + lane count inference (always available)
"""
import time
import cereal.messaging as messaging
from openpilot.common.params import Params
from openpilot.common.realtime import Ratekeeper

# Road type -> speed limit fallback table (km/h)
# Conservative values based on PRC Road Traffic Safety Law (GB 5768)
# Default to urban rules (more conservative) when context unknown
SPEED_TABLE_URBAN = {
  # highway_type: {multi_lane: speed, single_lane: speed}
  'motorway':     {'multi': 100, 'single': 100},
  'trunk':        {'multi': 80,  'single': 60},
  'primary':      {'multi': 60,  'single': 50},
  'secondary':    {'multi': 60,  'single': 50},
  'tertiary':     {'multi': 40,  'single': 40},
  'residential':  {'multi': 30,  'single': 30},
  'unclassified': {'multi': 40,  'single': 40},
  'living_street': {'multi': 20, 'single': 20},
  'service':      {'multi': 20,  'single': 20},
}

SPEED_TABLE_NONURBAN = {
  'motorway':     {'multi': 120, 'single': 120},
  'trunk':        {'multi': 100, 'single': 70},
  'primary':      {'multi': 80,  'single': 70},
  'secondary':    {'multi': 60,  'single': 50},
  'tertiary':     {'multi': 40,  'single': 40},
  'unclassified': {'multi': 40,  'single': 30},
}

DEFAULT_FALLBACK_SPEED = 40  # km/h — conservative default when no data


def infer_lane_count(model_msg) -> int:
  """Infer lane count from modelV2 laneLineProbs.

  Returns 2 for multi-lane, 1 for single-lane.
  """
  if not hasattr(model_msg, 'laneLineProbs') or len(model_msg.laneLineProbs) < 4:
    return 1

  probs = model_msg.laneLineProbs
  left_lane = probs[1] > 0.5
  right_lane = probs[2] > 0.5
  left_edge = probs[0] > 0.3
  right_edge = probs[3] > 0.3

  # Multi-lane: both lane lines visible + at least one road edge beyond
  if left_lane and right_lane and (left_edge or right_edge):
    return 2
  return 1


def infer_speed_from_road_type(highway_type: str, lane_count: int, road_context: str) -> int:
  """Look up fallback speed from road type + lane count.

  Args:
    highway_type: OSM highway tag (e.g. 'trunk', 'primary')
    lane_count: 1 for single-lane, 2+ for multi-lane
    road_context: 'freeway', 'city', or 'unknown'
  """
  lane_key = 'multi' if lane_count >= 2 else 'single'

  # Select table based on context
  if road_context == 'freeway':
    table = SPEED_TABLE_NONURBAN
  else:
    # Default to urban (more conservative)
    table = SPEED_TABLE_URBAN

  entry = table.get(highway_type)
  if entry:
    return entry[lane_key]
  return DEFAULT_FALLBACK_SPEED


class SpeedLimitMiddleware:
  def __init__(self):
    self.params = Params()
    self.sm = messaging.SubMaster(['mapdOut', 'modelV2'])
    self.pm = messaging.PubMaster(['speedLimitState'])

    # State
    self.last_osm_speed: float = 0.0
    self.last_yolo_speed: float = 0.0
    self.last_highway_type: str = ''
    self.last_road_name: str = ''
    self.last_road_context: str = 'unknown'
    self.lane_count: int = 1
    self.lane_count_stable: int = 1
    self.lane_count_stable_since: float = 0.0

    # Confirmation state
    self.confirmed: bool = False
    self.confirmed_value: float = 0.0

    # YOLO detection state (placeholder for future integration)
    self.yolo_speed: int = 0
    self.yolo_last_seen: float = 0.0
    self.yolo_timeout: float = 120.0  # seconds before YOLO detection expires

  def update(self):
    self.sm.update(0)

    now = time.monotonic()

    # --- Read OSM data from mapdOut ---
    if self.sm.updated['mapdOut']:
      mapd = self.sm['mapdOut']
      self.last_osm_speed = mapd.speedLimit  # 0 if unavailable
      self.last_highway_type = mapd.roadName if not mapd.wayName else ''
      self.last_road_name = mapd.roadName

      # Extract highway type from wayRef or wayName
      # mapdOut doesn't have explicit highway type, but it has roadContext
      road_ctx = mapd.roadContext
      if road_ctx == 0:  # freeway
        self.last_road_context = 'freeway'
      elif road_ctx == 1:  # city
        self.last_road_context = 'city'
      else:
        self.last_road_context = 'unknown'

      # Use wayRef as highway type hint if available
      way_ref = mapd.wayRef
      if way_ref:
        # G-roads are typically trunk/primary, S-roads are primary/secondary
        if way_ref.startswith('G'):
          self.last_highway_type = 'trunk'
        elif way_ref.startswith('S'):
          self.last_highway_type = 'primary'
        elif way_ref.startswith('X'):
          self.last_highway_type = 'secondary'

    # --- Read lane data from modelV2 ---
    if self.sm.updated['modelV2']:
      raw_lane_count = infer_lane_count(self.sm['modelV2'])

      # Require stable lane detection for 2+ seconds
      if raw_lane_count != self.lane_count:
        self.lane_count = raw_lane_count
        self.lane_count_stable_since = now
      elif now - self.lane_count_stable_since > 2.0:
        self.lane_count_stable = self.lane_count

    # --- YOLO timeout ---
    if self.yolo_speed > 0 and (now - self.yolo_last_seen) > self.yolo_timeout:
      self.yolo_speed = 0

    # --- Priority cascade ---
    osm_speed = int(self.last_osm_speed) if self.last_osm_speed > 0 else 0
    yolo_speed = self.yolo_speed
    inferred_speed = infer_speed_from_road_type(
      self.last_highway_type, self.lane_count_stable, self.last_road_context
    )

    if osm_speed > 0:
      speed_limit = float(osm_speed)
      source = 0  # osmMaxspeed
      confidence = 0.95
    elif yolo_speed > 0:
      speed_limit = float(yolo_speed)
      source = 1  # yoloDetection
      confidence = 0.8
    else:
      speed_limit = float(inferred_speed)
      source = 2  # roadTypeInference
      confidence = 0.5

    # --- Confirmation management ---
    # Read confirmation state from Params (set by HUD touch handler)
    confirmed_raw = self.params.get("SpeedLimitConfirmed")
    confirmed_param = confirmed_raw.decode('utf-8') if confirmed_raw else None
    value_raw = self.params.get("SpeedLimitValue")
    confirmed_value_param = value_raw.decode('utf-8') if value_raw else None

    if confirmed_param == '1' and confirmed_value_param:
      try:
        pv = float(confirmed_value_param)
        # Only valid if confirmed value matches current speed limit
        if abs(pv - speed_limit) < 0.5:
          self.confirmed = True
          self.confirmed_value = pv
        else:
          # Speed limit changed — reset confirmation
          self.confirmed = False
          self.params.put("SpeedLimitConfirmed", "0")
      except ValueError:
        self.confirmed = False
    else:
      self.confirmed = False

    # --- Publish ---
    msg = messaging.new_message('speedLimitState')
    sls = msg.speedLimitState
    sls.speedLimit = speed_limit
    sls.source = source
    sls.confirmed = self.confirmed
    sls.confidence = confidence
    sls.osmMaxspeed = osm_speed
    sls.yoloSpeed = yolo_speed
    sls.inferredSpeed = inferred_speed
    sls.highwayType = self.last_highway_type
    sls.roadName = self.last_road_name
    sls.laneCount = self.lane_count_stable

    self.pm.send('speedLimitState', msg)


def main():
  middleware = SpeedLimitMiddleware()
  rk = Ratekeeper(1, print_delay_threshold=None)  # 1 Hz

  while True:
    middleware.update()
    rk.keep_time()


if __name__ == "__main__":
  main()
