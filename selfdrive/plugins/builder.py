"""
Boot-time JIT builder for plugin architecture.

Scans enabled plugins, reads their cereal/services/subscription declarations,
and patches the stock cereal schemas + services at boot — before any openpilot
process imports them.  The committed repo files stay identical to upstream.

Called from manager_init() on every boot.
"""
import hashlib
import json
import os
import re
import subprocess
from collections import namedtuple

from openpilot.common.basedir import BASEDIR
from openpilot.common.swaglog import cloudlog

CEREAL_DIR = os.path.join(BASEDIR, 'cereal')
CUSTOM_CAPNP = os.path.join(CEREAL_DIR, 'custom.capnp')
LOG_CAPNP = os.path.join(CEREAL_DIR, 'log.capnp')
SERVICES_PY = os.path.join(CEREAL_DIR, 'services.py')

BUILD_HASH_FILE = '/tmp/plugin_build_hash'
SUBSCRIPTIONS_FILE = '/tmp/plugin_subscriptions.json'
PLUGINS_DIR = '/data/plugins'

# Struct IDs for each reserved custom slot (from upstream custom.capnp)
SLOT_IDS = {
  0: '0x81c2f05a394cf4af',
  1: '0xaedffd8f31e7b55d',
  2: '0xf35cc4560bbf6ec2',
  3: '0xda96579883444c35',
  4: '0x80ae746ee2596b11',
  5: '0xa5cd762cd951a455',
  6: '0xf98d843bfd7004a3',
  7: '0xb86e6369214c01c8',
  8: '0xf416ec09499d9d19',
  9: '0xa1680744031fdb2d',
  10: '0xcb9fd56c7057593a',
  11: '0xc2243c65e0340384',
  12: '0x9ccdc8676701b412',
  13: '0xcd96dafb67a082d0',
  14: '0xb057204d7deadf3f',
  15: '0xbd443b539493bc68',
  16: '0xfc6241ed8877b611',
  17: '0xa30662f84033036c',
  18: '0xc86a3d38d13eb3ef',
  19: '0xa4f1eb3323f5f582',
}

# Event field IDs in log.capnp for each custom slot
SLOT_EVENT_IDS = {
  0: 107, 1: 108, 2: 109, 3: 110, 4: 111,
  5: 112, 6: 113, 7: 114, 8: 115, 9: 116,
  10: 136, 11: 137, 12: 138, 13: 139, 14: 140,
  15: 141, 16: 142, 17: 143, 18: 144, 19: 145,
}

# Hook name → process name mapping for subscription injection
HOOK_PROCESS_MAP = {
  'planner.v_cruise': 'plannerd',
  'planner.accel_limits': 'plannerd',
  'controls.curvature_correction': 'controlsd',
  'controls.post_actuators': 'controlsd',
  'desire.post_update': 'plannerd',
}


def _load_manifest(plugin_dir: str) -> dict:
  manifest_path = os.path.join(plugin_dir, 'plugin.json')
  if not os.path.exists(manifest_path):
    return {}
  with open(manifest_path) as f:
    return json.load(f)


def _get_enabled_plugins() -> list[str]:
  """Return list of enabled plugin directory paths.

  All plugins with a valid plugin.json in PLUGINS_DIR are enabled,
  unless a .disabled marker file exists in the plugin directory.
  """
  if not os.path.isdir(PLUGINS_DIR):
    return []

  plugins = []
  for name in sorted(os.listdir(PLUGINS_DIR)):
    plugin_dir = os.path.join(PLUGINS_DIR, name)
    if not os.path.isdir(plugin_dir):
      continue
    if os.path.exists(os.path.join(plugin_dir, '.disabled')):
      continue
    manifest = _load_manifest(plugin_dir)
    if not manifest:
      continue
    plugins.append(plugin_dir)
  return plugins


def _compute_build_hash(plugin_dirs: list[str]) -> str:
  """Hash enabled plugin IDs + schema file mtimes + disabled state for rebuild detection."""
  parts = []
  # Include disabled state so toggling triggers a rebuild
  if os.path.isdir(PLUGINS_DIR):
    for name in sorted(os.listdir(PLUGINS_DIR)):
      plugin_dir = os.path.join(PLUGINS_DIR, name)
      if os.path.isdir(plugin_dir) and os.path.exists(os.path.join(plugin_dir, '.disabled')):
        parts.append(f"{name}:disabled")
  for plugin_dir in sorted(plugin_dirs):
    manifest = _load_manifest(plugin_dir)
    plugin_id = manifest.get('id', os.path.basename(plugin_dir))
    parts.append(f"{plugin_id}:{manifest.get('version', '0')}")
    for slot_def in manifest.get('cereal', {}).get('slots', {}).values():
      schema_path = os.path.join(plugin_dir, slot_def['schema_file'])
      if os.path.exists(schema_path):
        parts.append(f"{schema_path}:{os.path.getmtime(schema_path)}")
    standalone = manifest.get('cereal', {}).get('standalone_schema')
    if standalone:
      standalone_path = os.path.join(plugin_dir, standalone)
      if os.path.exists(standalone_path):
        parts.append(f"{standalone_path}:{os.path.getmtime(standalone_path)}")
  return hashlib.md5(':'.join(parts).encode()).hexdigest()


def _needs_rebuild(plugin_dirs: list[str]) -> bool:
  """Check if rebuild is needed by comparing build hash.

  Force rebuild by deleting /tmp/plugin_build_hash.
  """
  current_hash = _compute_build_hash(plugin_dirs)
  try:
    with open(BUILD_HASH_FILE) as f:
      saved_hash = f.read().strip()
    return current_hash != saved_hash
  except FileNotFoundError:
    return True


def _read_file(path: str) -> str:
  with open(path) as f:
    return f.read()


_StructMatch = namedtuple('StructMatch', ['start', 'end'])


def _match_top_level_struct(content: str, slot_id: str) -> _StructMatch | None:
  """Match a top-level struct block by its @ID, handling nested braces.

  Returns a StructMatch(start, end) or None.
  """
  decl_pattern = rf'struct \w+ @{re.escape(slot_id)} \{{'
  m = re.search(decl_pattern, content)
  if not m:
    return None

  # Walk forward counting brace depth to find the matching closing brace
  start = m.start()
  depth = 0
  i = m.end() - 1  # position of the opening {
  while i < len(content):
    if content[i] == '{':
      depth += 1
    elif content[i] == '}':
      depth -= 1
      if depth == 0:
        return _StructMatch(start, i + 1)
    i += 1
  return None


def _patch_custom_capnp(plugin_dirs: list[str]) -> None:
  """Patch custom.capnp: replace slot structs with plugin fragments, append standalone schemas.

  Idempotent: matches any struct name with the correct @ID, handles nested
  braces (enums), and strips previously-appended standalone schemas.
  """
  content = _read_file(CUSTOM_CAPNP)

  # Strip previously-appended standalone schemas (everything after the last slot struct)
  last_slot_end = 0
  for slot_id in SLOT_IDS.values():
    m = _match_top_level_struct(content, slot_id)
    if m:
      last_slot_end = max(last_slot_end, m.end)
  if last_slot_end > 0:
    content = content[:last_slot_end].rstrip() + '\n'

  for plugin_dir in plugin_dirs:
    manifest = _load_manifest(plugin_dir)
    cereal_config = manifest.get('cereal', {})

    # Process slot claims
    for slot_str, slot_def in cereal_config.get('slots', {}).items():
      slot_num = int(slot_str)
      slot_id = SLOT_IDS[slot_num]
      struct_name = slot_def['struct_name']
      schema_file = os.path.join(plugin_dir, slot_def['schema_file'])

      if not os.path.exists(schema_file):
        cloudlog.warning(f"plugin builder: schema file missing: {schema_file}")
        continue

      with open(schema_file) as f:
        fragment = f.read().strip()

      # Find and replace the struct with this slot's @ID (handles nested braces)
      m = _match_top_level_struct(content, slot_id)
      if m:
        replacement = f'struct {struct_name} @{slot_id} {{\n{fragment}\n}}'
        content = content[:m.start] + replacement + content[m.end:]
      else:
        cloudlog.warning(f"plugin builder: failed to patch slot {slot_num} for {struct_name}")

    # Append standalone schemas (supporting types)
    standalone = cereal_config.get('standalone_schema')
    if standalone:
      standalone_path = os.path.join(plugin_dir, standalone)
      if os.path.exists(standalone_path):
        with open(standalone_path) as f:
          standalone_content = f.read().strip()
        content = content.rstrip() + '\n\n' + standalone_content + '\n'

  with open(CUSTOM_CAPNP, 'w') as f:
    f.write(content)
  cloudlog.info("plugin builder: patched custom.capnp")


def _patch_log_capnp(plugin_dirs: list[str]) -> None:
  """Patch log.capnp: rename event fields to plugin names.

  Idempotent: matches any field name + struct name with the correct @ID.
  """
  content = _read_file(LOG_CAPNP)

  for plugin_dir in plugin_dirs:
    manifest = _load_manifest(plugin_dir)
    cereal_config = manifest.get('cereal', {})

    for slot_str, slot_def in cereal_config.get('slots', {}).items():
      slot_num = int(slot_str)
      event_id = SLOT_EVENT_IDS[slot_num]
      event_field = slot_def['event_field']
      struct_name = slot_def['struct_name']

      # Match ANY field name with this @ID pointing to ANY Custom.* struct
      pattern = rf'(\s+)\w+ @{event_id} :Custom\.\w+;'
      if not re.search(pattern, content):
        cloudlog.warning(f"plugin builder: event field @{event_id} not found in log.capnp")
        continue
      replacement = rf'\1{event_field} @{event_id} :Custom.{struct_name};'
      content = re.sub(pattern, replacement, content)

  with open(LOG_CAPNP, 'w') as f:
    f.write(content)
  cloudlog.info("plugin builder: patched log.capnp")


def _patch_services(plugin_dirs: list[str]) -> None:
  """Patch services.py: insert plugin service entries before closing brace of _services dict.

  Idempotent: removes any existing plugin services block before inserting.
  """
  content = _read_file(SERVICES_PY)

  # Remove any existing plugin services block (for idempotency)
  content = re.sub(r'\n  # plugin services \(auto-generated\)\n.*?\n(\})', r'\n\1',
                   content, flags=re.DOTALL)

  entries = []
  for plugin_dir in plugin_dirs:
    manifest = _load_manifest(plugin_dir)
    for service_name, service_def in manifest.get('services', {}).items():
      if isinstance(service_def, list):
        vals = service_def
      else:
        vals = [service_def.get('should_log', True), service_def.get('frequency', 0.)]
        if 'decimation' in service_def:
          vals.append(service_def['decimation'])
      entries.append(f'  "{service_name}": {tuple(vals)},')

  if entries:
    insert_text = '\n  # plugin services (auto-generated)\n' + '\n'.join(entries) + '\n'
    content = content.replace(
      '\n}\nSERVICE_LIST',
      f'{insert_text}}}\nSERVICE_LIST'
    )

  with open(SERVICES_PY, 'w') as f:
    f.write(content)
  cloudlog.info("plugin builder: patched services.py")


def _write_subscriptions(plugin_dirs: list[str]) -> None:
  """Collect hook subscriptions, group by process, write JSON."""
  process_subs: dict[str, list[str]] = {}

  for plugin_dir in plugin_dirs:
    manifest = _load_manifest(plugin_dir)
    for hook_name, hook_def in manifest.get('hooks', {}).items():
      subs = hook_def.get('subscriptions', [])
      if not subs:
        continue
      process_name = HOOK_PROCESS_MAP.get(hook_name)
      if process_name:
        if process_name not in process_subs:
          process_subs[process_name] = []
        for sub in subs:
          if sub not in process_subs[process_name]:
            process_subs[process_name].append(sub)

  with open(SUBSCRIPTIONS_FILE, 'w') as f:
    json.dump(process_subs, f, indent=2)
  cloudlog.info(f"plugin builder: wrote subscriptions {process_subs}")


def _write_params(plugin_dirs: list[str]) -> None:
  """Write plugin param defaults to /data/params/d/ directly.

  Plugin params are NOT in params_keys.h (stock), so we can't use the
  compiled Params class. Write them as plain files instead — the plugin
  framework reads them at runtime via its own param helpers.
  """
  params_dir = '/data/params/d'
  if not os.path.isdir(params_dir):
    return

  for plugin_dir in plugin_dirs:
    manifest = _load_manifest(plugin_dir)
    for param_name, param_def in manifest.get('params', {}).items():
      param_path = os.path.join(params_dir, param_name)
      if not os.path.exists(param_path):
        default = param_def.get('default', '')
        if isinstance(default, bool):
          value = '1' if default else '0'
        else:
          value = str(default)
        try:
          with open(param_path, 'w') as f:
            f.write(value)
          cloudlog.info(f"plugin builder: set default param {param_name}={value}")
        except OSError as e:
          cloudlog.warning(f"plugin builder: failed to write param {param_name}: {e}")


def build() -> None:
  """Main entry point. Called from manager_init() before processes start."""
  plugin_dirs = _get_enabled_plugins()

  if not plugin_dirs:
    cloudlog.info("plugin builder: no enabled plugins found")
    # Ensure subscription file exists (empty) so plannerd doesn't error
    if not os.path.exists(SUBSCRIPTIONS_FILE):
      with open(SUBSCRIPTIONS_FILE, 'w') as f:
        json.dump({}, f)
    return

  plugin_names = [os.path.basename(d) for d in plugin_dirs]
  cloudlog.info(f"plugin builder: enabled plugins: {plugin_names}")

  if not _needs_rebuild(plugin_dirs):
    cloudlog.info("plugin builder: plugins unchanged, skipping rebuild")
    return

  cloudlog.info("plugin builder: rebuilding plugin schemas and services")

  _patch_custom_capnp(plugin_dirs)
  _patch_log_capnp(plugin_dirs)
  _patch_services(plugin_dirs)
  _write_subscriptions(plugin_dirs)
  _write_params(plugin_dirs)

  # Save build hash
  build_hash = _compute_build_hash(plugin_dirs)
  with open(BUILD_HASH_FILE, 'w') as f:
    f.write(build_hash)

  cloudlog.info("plugin builder: rebuild complete")


def restore_stock() -> None:
  """Restore patched files to their stock upstream state.

  Finds the earliest commit that touched each file (the upstream base).
  """
  for rel_path in ('cereal/custom.capnp', 'cereal/log.capnp', 'cereal/services.py'):
    try:
      # Find the first commit that introduced this file (upstream base)
      result = subprocess.run(
        ['git', 'log', '--reverse', '--format=%H', '--diff-filter=A', '--', rel_path],
        capture_output=True, text=True, cwd=BASEDIR
      )
      base_ref = result.stdout.strip().split('\n')[0] if result.returncode == 0 and result.stdout.strip() else 'HEAD'
      subprocess.run(
        ['git', 'checkout', base_ref, '--', rel_path],
        cwd=BASEDIR, check=True
      )
    except (subprocess.CalledProcessError, IndexError):
      cloudlog.warning(f"plugin builder: failed to restore {rel_path}")

  # Clean up build artifacts
  for f in (BUILD_HASH_FILE, SUBSCRIPTIONS_FILE):
    try:
      os.remove(f)
    except FileNotFoundError:
      pass

  cloudlog.info("plugin builder: restored stock files")
