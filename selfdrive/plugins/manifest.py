"""Plugin manifest parser and compatibility checker.

Each plugin has a plugin.json manifest defining:
  - Identity: id, name, version, author, description
  - Type: 'hook', 'process', 'hybrid', 'car', or 'firmware'
  - Compatibility: min_openpilot, max_openpilot
  - Device filter: restrict to specific hardware (e.g., ["tici"] for Comma 3)
  - Dependencies: other plugin IDs that must be loaded first
  - Conflicts: other plugin IDs that cannot coexist
  - Hook registrations: hook_name -> module.function
  - Params: configurable parameters exposed to UI
  - Processes: daemon processes to run (with optional replace flag)
"""
import json
import os

from openpilot.common.swaglog import cloudlog

REQUIRED_FIELDS = ('id', 'name', 'version', 'type')
VALID_TYPES = ('hook', 'process', 'hybrid', 'car', 'firmware')

# Current openpilot version for compatibility checking
OPENPILOT_VERSION = '0.10.3'


def parse_version(v: str) -> tuple[int, ...]:
  """Parse semver string to tuple for comparison."""
  try:
    return tuple(int(x) for x in v.split('.'))
  except (ValueError, AttributeError):
    return (0, 0, 0)


def load_manifest(plugin_dir: str) -> dict | None:
  """Load and validate plugin.json from a plugin directory.

  Returns parsed manifest dict, or None if invalid.
  """
  manifest_path = os.path.join(plugin_dir, 'plugin.json')
  if not os.path.exists(manifest_path):
    cloudlog.warning(f"No plugin.json in {plugin_dir}")
    return None

  try:
    with open(manifest_path) as f:
      manifest = json.load(f)
  except (json.JSONDecodeError, OSError) as e:
    cloudlog.error(f"Failed to parse {manifest_path}: {e}")
    return None

  # Validate required fields
  for field in REQUIRED_FIELDS:
    if field not in manifest:
      cloudlog.error(f"Plugin {plugin_dir} missing required field: {field}")
      return None

  if manifest['type'] not in VALID_TYPES:
    cloudlog.error(f"Plugin {manifest['id']} has invalid type: {manifest['type']}")
    return None

  # Add plugin_dir to manifest for later use
  manifest['_plugin_dir'] = plugin_dir

  return manifest


def check_compatibility(manifest: dict) -> bool:
  """Check if plugin is compatible with current openpilot version and device."""
  current = parse_version(OPENPILOT_VERSION)

  min_ver = manifest.get('min_openpilot')
  if min_ver and parse_version(min_ver) > current:
    cloudlog.warning(f"Plugin {manifest['id']} requires openpilot >= {min_ver}")
    return False

  max_ver = manifest.get('max_openpilot')
  if max_ver and parse_version(max_ver) < current:
    cloudlog.warning(f"Plugin {manifest['id']} requires openpilot <= {max_ver}")
    return False

  # Device filter: ["tici"] means Comma 3 only, ["tici3"] for comma3x, etc.
  device_filter = manifest.get('device_filter')
  if device_filter:
    from openpilot.system.hardware import HARDWARE
    device_type = HARDWARE.get_device_type()
    if device_type not in device_filter:
      cloudlog.warning(f"Plugin {manifest['id']} not compatible with device '{device_type}' "
                       f"(requires {device_filter})")
      return False

  return True


def check_dependencies(manifest: dict, loaded_plugin_ids: set[str]) -> tuple[bool, str]:
  """Check if plugin dependencies are satisfied.

  Args:
    manifest: Plugin manifest dict
    loaded_plugin_ids: Set of currently loaded plugin IDs

  Returns:
    (satisfied, reason) — True if all deps met, else False with explanation
  """
  deps = manifest.get('dependencies', [])
  for dep_id in deps:
    if dep_id not in loaded_plugin_ids:
      return False, f"missing dependency '{dep_id}'"
  return True, ""


def check_conflicts(manifest: dict, loaded_plugin_ids: set[str]) -> tuple[bool, str]:
  """Check if plugin conflicts with any loaded plugin.

  Args:
    manifest: Plugin manifest dict
    loaded_plugin_ids: Set of currently loaded plugin IDs

  Returns:
    (ok, reason) — True if no conflicts, else False with explanation
  """
  conflicts = manifest.get('conflicts', [])
  for conflict_id in conflicts:
    if conflict_id in loaded_plugin_ids:
      return False, f"conflicts with loaded plugin '{conflict_id}'"
  return True, ""


def get_plugin_params(manifest: dict) -> dict:
  """Extract configurable params from manifest.

  Returns {param_name: {type, default, label, ...}}
  """
  return manifest.get('params', {})
