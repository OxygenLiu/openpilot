from openpilot.common.constants import CV

# Speed-dependent offset: lower limits get more margin (km/h)
SPEED_LIMIT_OFFSET = {
  20: 20, 30: 20, 40: 20, 50: 20, 60: 20,
  70: 10, 80: 10, 90: 10, 100: 10, 110: 10, 120: 10,
}


def on_v_cruise(v_cruise, v_ego, sm):
  if not sm.valid.get('speedLimitState', False) and sm.recv_frame.get('speedLimitState', 0) == 0:
    return v_cruise

  sls = sm['speedLimitState']
  if sls.confirmed and sls.speedLimit > 0:
    offset_kph = SPEED_LIMIT_OFFSET.get(round(sls.speedLimit / 10) * 10, 10)
    v_limit = (sls.speedLimit + offset_kph) * CV.KPH_TO_MS
    if v_limit < v_cruise:
      return v_limit

  return v_cruise
