"""Panda BMW safety model status monitor.

BMW safety model (bmw.h) is compiled C on STM32 panda — it cannot be loaded
at runtime. This plugin monitors the safety model state and reports status.

Expected: safetyModel = "bmw" (ID 35), safetyParam = 0
Failure:  safetyModel = "elm327" (panda detected as unknown, firmware issue)
"""
from openpilot.common.swaglog import cloudlog

# BMW safety model constants
BMW_SAFETY_MODEL_NAME = "bmw"
BMW_SAFETY_MODEL_ID = 35
FALLBACK_MODEL_NAME = "elm327"

# Status tracking (module-level state, reset each process)
_last_status = None
_alert_count = 0


def on_panda_status(current, panda_states=None, **kwargs):
  """Hook callback: inspect panda safety state each cycle.

  Called by selfdrived or card via hooks.run('car.panda_status', None, panda_states=...).
  This is a void hook — returns None.
  """
  global _last_status, _alert_count

  if panda_states is None:
    return None

  for ps in panda_states:
    safety_model = str(ps.safetyModel) if hasattr(ps, 'safetyModel') else 'unknown'

    if safety_model != _last_status:
      _last_status = safety_model
      if safety_model == BMW_SAFETY_MODEL_NAME:
        cloudlog.info(f"panda-bmw: BMW safety model active (ID {BMW_SAFETY_MODEL_ID})")
        _alert_count = 0
      elif safety_model == FALLBACK_MODEL_NAME:
        _alert_count += 1
        cloudlog.error(f"panda-bmw: ALERT — panda fell back to ELM327 mode! "
                       f"BMW safety NOT active. Reflash panda firmware. (alert #{_alert_count})")
      else:
        cloudlog.warning(f"panda-bmw: unexpected safety model '{safety_model}'")

  return None


def get_status() -> dict:
  """Return current panda BMW safety status for COD display."""
  return {
    'safety_model': _last_status or 'unknown',
    'is_bmw_active': _last_status == BMW_SAFETY_MODEL_NAME,
    'alert_count': _alert_count,
    'expected_model': BMW_SAFETY_MODEL_NAME,
    'expected_id': BMW_SAFETY_MODEL_ID,
  }
