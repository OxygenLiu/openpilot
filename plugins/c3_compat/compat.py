"""
Comma 3 compatibility module.

Monitors AGNOS version, panda MCU type, and device type at startup
and via the device.health_check hook. Logs warnings for unexpected
configurations (e.g. wrong MCU type for detected hardware).
"""

import os
import logging

logger = logging.getLogger("c3_compat")

# Expected MCU types per device
# C3 (tici / Dos board) uses STM32F4
# C3X / C4 (tres / cuatro) use STM32H7
DEVICE_MCU_EXPECTATIONS = {
  "tici": "f4",     # Comma 3 — Dos board, STM32F4
  "tizi": "h7",     # Comma 3X — Tres board, STM32H7
  "mici": "h7",     # Comma 4 — Cuatro board, STM32H7
}


def get_agnos_version() -> str:
  """Read AGNOS version from /VERSION."""
  try:
    with open("/VERSION") as f:
      return f.read().strip()
  except FileNotFoundError:
    return "unknown"


def get_device_type() -> str:
  """Detect device type from devicetree model string."""
  model_path = "/sys/firmware/devicetree/base/model"
  try:
    with open(model_path) as f:
      model = f.read().strip().rstrip("\x00")
    model_lower = model.lower()
    if "tici" in model_lower:
      return "tici"
    elif "tizi" in model_lower:
      return "tizi"
    elif "mici" in model_lower:
      return "mici"
    return model
  except FileNotFoundError:
    return "unknown"


def log_startup_info():
  """Log compatibility status on startup."""
  agnos = get_agnos_version()
  device = get_device_type()
  logger.info("C3 compat: AGNOS %s, device %s", agnos, device)

  # Warn if AGNOS version is unexpected for C3
  if device == "tici":
    try:
      major = int(agnos.split(".")[0])
      if major > 13:
        logger.warning("AGNOS %s may not be fully supported on Comma 3 (tici)", agnos)
    except (ValueError, IndexError):
      pass


def on_health_check(params=None, **kwargs):
  """
  Hook: device.health_check

  Checks panda MCU type matches expected type for the detected device.
  Called periodically by the plugin framework.
  """
  device = get_device_type()
  expected_mcu = DEVICE_MCU_EXPECTATIONS.get(device)

  result = {
    "plugin": "c3-compat",
    "agnos_version": get_agnos_version(),
    "device_type": device,
    "status": "ok",
    "warnings": [],
  }

  # Check panda MCU type from pandaStates if available
  if params is not None:
    try:
      from cereal import messaging
      sm = messaging.SubMaster(["pandaStates"])
      sm.update(0)
      if sm.valid["pandaStates"] and len(sm["pandaStates"]) > 0:
        panda_type = str(sm["pandaStates"][0].pandaType)
        result["panda_type"] = panda_type

        # Dos = F4 (C3), Tres = H7 (C3X), Cuatro = H7 (C4)
        if device == "tici" and "dos" not in panda_type.lower():
          result["warnings"].append(
            f"Expected Dos (F4) panda on C3, got {panda_type}"
          )
        elif device in ("tizi", "mici") and "dos" in panda_type.lower():
          result["warnings"].append(
            f"Unexpected Dos (F4) panda on {device}, got {panda_type}"
          )
    except Exception as e:
      result["warnings"].append(f"Could not read pandaStates: {e}")

  if result["warnings"]:
    result["status"] = "warning"
    for w in result["warnings"]:
      logger.warning("C3 compat health: %s", w)

  return result


# Log on import
log_startup_info()
