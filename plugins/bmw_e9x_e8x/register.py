"""BMW car interface registration hook.

Injects BMW E82/E90 into opendbc's interfaces, fingerprints, and platforms
when the plugin is enabled. When disabled, BMW is not in the system.
"""
import os
import sys

# Ensure the plugin's bmw/ package is importable
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if _PLUGIN_DIR not in sys.path:
  sys.path.insert(0, _PLUGIN_DIR)


def on_register_interfaces(interfaces):
  """Hook callback: inject BMW into the car interfaces system.

  Called by car_helpers.py via hooks.run('car.register_interfaces', interfaces).
  Modifies interfaces dict in-place AND patches fingerprints/platforms globals.
  """
  from bmw.interface import CarInterface
  from bmw.values import CAR

  # Register BMW interfaces
  interfaces[CAR.BMW_E82] = CarInterface
  interfaces[CAR.BMW_E90] = CarInterface

  # Patch global fingerprints
  try:
    from opendbc.car.fingerprints import _FINGERPRINTS, FW_VERSIONS as GLOBAL_FW
    from bmw.fingerprints import FINGERPRINTS as BMW_FP, FW_VERSIONS as BMW_FW
    _FINGERPRINTS.update({str(k): v for k, v in BMW_FP.items()})
    GLOBAL_FW.update({str(k): v for k, v in BMW_FW.items()})
  except (ImportError, AttributeError):
    pass  # fingerprints module not available in all contexts

  # Patch global platforms
  try:
    from opendbc.car.values import PLATFORMS
    PLATFORMS[str(CAR.BMW_E82)] = CAR.BMW_E82
    PLATFORMS[str(CAR.BMW_E90)] = CAR.BMW_E90
  except (ImportError, AttributeError):
    pass

  return interfaces
