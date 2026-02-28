"""Tests for lazy per-process plugin loading and conflict detection."""
import json
import os

import pytest
from unittest.mock import patch, MagicMock
from openpilot.selfdrive.plugins.hooks import HookRegistry, EXCLUSIVE_HOOKS


class TestLazyLoading:
  """Verify that _ensure_loaded triggers plugin discovery on first run() call."""

  def test_lazy_loading_not_triggered_before_run(self):
    hooks = HookRegistry()
    assert not hooks._loaded

  def test_lazy_loading_triggered_on_first_run(self):
    hooks = HookRegistry()
    with patch('openpilot.selfdrive.plugins.registry.PluginRegistry') as MockRegistry:
      mock_instance = MagicMock()
      MockRegistry.return_value = mock_instance
      hooks.run('test.hook', 42)
      assert hooks._loaded
      mock_instance.discover.assert_called_once()
      mock_instance.load_enabled.assert_called_once()

  def test_lazy_loading_only_once(self):
    hooks = HookRegistry()
    with patch('openpilot.selfdrive.plugins.registry.PluginRegistry') as MockRegistry:
      mock_instance = MagicMock()
      MockRegistry.return_value = mock_instance
      hooks.run('test.hook', 1)
      hooks.run('test.hook', 2)
      hooks.run('test.hook', 3)
      # discover() should only be called once despite 3 run() calls
      assert mock_instance.discover.call_count == 1

  def test_lazy_loading_failure_is_safe(self):
    hooks = HookRegistry()
    with patch('openpilot.selfdrive.plugins.registry.PluginRegistry', side_effect=ImportError("no plugins")):
      # Should not raise, should return default
      result = hooks.run('test.hook', 99)
      assert result == 99
      assert hooks._loaded  # Still marked as loaded to prevent retry storm

  def test_register_before_run_works(self):
    """Hooks registered directly (e.g., in tests) work without lazy loading."""
    hooks = HookRegistry()
    hooks.register('test.direct', 'test-plugin', lambda v: v + 1)
    # Set loaded to prevent lazy load attempt
    hooks._loaded = True
    result = hooks.run('test.direct', 0)
    assert result == 1


class TestConflictDetection:
  """Verify that exclusive hooks reject multiple registrations."""

  def test_exclusive_hook_rejects_second_plugin(self):
    hooks = HookRegistry()
    hooks._loaded = True  # Skip lazy loading

    def callback_a(interfaces):
      return interfaces

    def callback_b(interfaces):
      return interfaces

    # First registration succeeds
    hooks.register('car.register_interfaces', 'plugin-a', callback_a)
    assert hooks.has_hooks('car.register_interfaces')

    # Second registration from different plugin is rejected
    hooks.register('car.register_interfaces', 'plugin-b', callback_b)

    # Only plugin-a should be registered
    registered = hooks.get_registered_hooks()
    assert registered['car.register_interfaces'] == ['plugin-a']

    # Conflict should be recorded
    conflicts = hooks.get_conflicts()
    assert 'car.register_interfaces' in conflicts
    assert 'plugin-b' in conflicts['car.register_interfaces']

  def test_exclusive_hook_allows_same_plugin_reregister(self):
    hooks = HookRegistry()
    hooks._loaded = True

    def callback(interfaces):
      return interfaces

    hooks.register('car.register_interfaces', 'my-plugin', callback)
    hooks.register('car.register_interfaces', 'my-plugin', callback)

    # Same plugin can register again (for reload scenarios)
    registered = hooks.get_registered_hooks()
    assert 'my-plugin' in registered['car.register_interfaces']

  def test_non_exclusive_hook_allows_multiple(self):
    hooks = HookRegistry()
    hooks._loaded = True

    hooks.register('controls.curvature_correction', 'plugin-a', lambda v: v + 1)
    hooks.register('controls.curvature_correction', 'plugin-b', lambda v: v + 2)

    # Both should be registered
    registered = hooks.get_registered_hooks()
    assert len(registered['controls.curvature_correction']) == 2
    assert hooks.get_conflicts() == {}

  def test_car_register_interfaces_is_exclusive(self):
    assert 'car.register_interfaces' in EXCLUSIVE_HOOKS


class TestDependencyChecking:
  """Verify manifest dependency and conflict checking."""

  def test_check_dependencies_satisfied(self):
    from openpilot.selfdrive.plugins.manifest import check_dependencies
    manifest = {'dependencies': ['plugin-a', 'plugin-b']}
    loaded = {'plugin-a', 'plugin-b', 'plugin-c'}
    ok, reason = check_dependencies(manifest, loaded)
    assert ok
    assert reason == ""

  def test_check_dependencies_missing(self):
    from openpilot.selfdrive.plugins.manifest import check_dependencies
    manifest = {'dependencies': ['plugin-a', 'plugin-missing']}
    loaded = {'plugin-a'}
    ok, reason = check_dependencies(manifest, loaded)
    assert not ok
    assert 'plugin-missing' in reason

  def test_check_dependencies_empty(self):
    from openpilot.selfdrive.plugins.manifest import check_dependencies
    manifest = {}
    loaded = set()
    ok, reason = check_dependencies(manifest, loaded)
    assert ok

  def test_check_conflicts_none(self):
    from openpilot.selfdrive.plugins.manifest import check_conflicts
    manifest = {'conflicts': ['enemy-plugin']}
    loaded = {'friend-plugin'}
    ok, reason = check_conflicts(manifest, loaded)
    assert ok

  def test_check_conflicts_detected(self):
    from openpilot.selfdrive.plugins.manifest import check_conflicts
    manifest = {'conflicts': ['enemy-plugin']}
    loaded = {'enemy-plugin', 'friend-plugin'}
    ok, reason = check_conflicts(manifest, loaded)
    assert not ok
    assert 'enemy-plugin' in reason

  def test_check_conflicts_empty(self):
    from openpilot.selfdrive.plugins.manifest import check_conflicts
    manifest = {}
    loaded = {'any-plugin'}
    ok, reason = check_conflicts(manifest, loaded)
    assert ok


class TestManifestTypes:
  """Verify new plugin types are accepted."""

  def test_car_type_valid(self):
    from openpilot.selfdrive.plugins.manifest import load_manifest
    import tempfile
    with tempfile.TemporaryDirectory() as d:
      manifest = {'id': 'test', 'name': 'Test', 'version': '1.0.0', 'type': 'car'}
      with open(os.path.join(d, 'plugin.json'), 'w') as f:
        json.dump(manifest, f)
      result = load_manifest(d)
      assert result is not None
      assert result['type'] == 'car'

  def test_firmware_type_valid(self):
    from openpilot.selfdrive.plugins.manifest import load_manifest
    import tempfile
    with tempfile.TemporaryDirectory() as d:
      manifest = {'id': 'test', 'name': 'Test', 'version': '1.0.0', 'type': 'firmware'}
      with open(os.path.join(d, 'plugin.json'), 'w') as f:
        json.dump(manifest, f)
      result = load_manifest(d)
      assert result is not None
      assert result['type'] == 'firmware'

  def test_invalid_type_rejected(self):
    from openpilot.selfdrive.plugins.manifest import load_manifest
    import tempfile
    with tempfile.TemporaryDirectory() as d:
      manifest = {'id': 'test', 'name': 'Test', 'version': '1.0.0', 'type': 'invalid'}
      with open(os.path.join(d, 'plugin.json'), 'w') as f:
        json.dump(manifest, f)
      result = load_manifest(d)
      assert result is None


class TestRegistryDependencyOrdering:
  """Verify that load_enabled respects dependency ordering."""

  def test_dependency_blocks_loading(self, tmp_path):
    """Plugin with unmet dependency should not load."""
    from openpilot.selfdrive.plugins.registry import PluginRegistry
    from openpilot.selfdrive.plugins import hooks as hooks_module
    original_hooks = hooks_module.hooks
    hooks_module.hooks = HookRegistry()
    hooks_module.hooks._loaded = True

    try:
      # Create plugin with dependency
      plugin_dir = tmp_path / 'dependent'
      plugin_dir.mkdir()
      manifest = {
        'id': 'dependent',
        'name': 'Dependent Plugin',
        'version': '1.0.0',
        'type': 'hook',
        'dependencies': ['missing-plugin'],
        'hooks': {}
      }
      (plugin_dir / 'plugin.json').write_text(json.dumps(manifest))

      registry = PluginRegistry(str(tmp_path))
      registry.discover()
      result = registry.load_plugin('dependent')
      assert not result
      assert 'Dependency' in registry.plugins['dependent'].error
    finally:
      hooks_module.hooks = original_hooks

  def test_conflict_blocks_loading(self, tmp_path):
    """Plugin conflicting with loaded plugin should not load."""
    from openpilot.selfdrive.plugins.registry import PluginRegistry
    from openpilot.selfdrive.plugins import hooks as hooks_module
    original_hooks = hooks_module.hooks
    hooks_module.hooks = HookRegistry()
    hooks_module.hooks._loaded = True

    try:
      # Create "enemy" plugin (already loaded)
      enemy_dir = tmp_path / 'enemy'
      enemy_dir.mkdir()
      (enemy_dir / 'plugin.json').write_text(json.dumps({
        'id': 'enemy', 'name': 'Enemy', 'version': '1.0.0', 'type': 'hook', 'hooks': {}
      }))

      # Create "new" plugin that conflicts with enemy
      new_dir = tmp_path / 'new_plugin'
      new_dir.mkdir()
      (new_dir / 'plugin.json').write_text(json.dumps({
        'id': 'new_plugin', 'name': 'New', 'version': '1.0.0', 'type': 'hook',
        'conflicts': ['enemy'], 'hooks': {}
      }))

      registry = PluginRegistry(str(tmp_path))
      registry.discover()

      # Load enemy first
      registry.plugins['enemy'].loaded = True  # Simulate already loaded
      registry.plugins['enemy'].enabled = True

      # Try to load conflicting plugin
      result = registry.load_plugin('new_plugin')
      assert not result
      assert 'Conflict' in registry.plugins['new_plugin'].error
    finally:
      hooks_module.hooks = original_hooks


class TestProcessOverrides:
  """Verify process replacement map generation."""

  def test_get_process_overrides_empty(self, tmp_path):
    from openpilot.selfdrive.plugins.registry import PluginRegistry
    registry = PluginRegistry(str(tmp_path))
    assert registry.get_process_overrides() == {}

  def test_get_process_overrides_with_replacement(self, tmp_path):
    from openpilot.selfdrive.plugins.registry import PluginRegistry, PluginInfo
    registry = PluginRegistry(str(tmp_path))

    # Simulate a loaded process plugin
    manifest = {
      'id': 'c3-raylib-ui',
      'name': 'UI',
      'version': '1.0.0',
      'type': 'process',
      'processes': [{'name': 'ui', 'module': 'ui.ui', 'condition': 'always_run', 'replace': True}]
    }
    info = PluginInfo(manifest, str(tmp_path))
    info.loaded = True
    registry.plugins['c3-raylib-ui'] = info

    overrides = registry.get_process_overrides()
    assert 'ui' in overrides
    assert overrides['ui']['module'] == 'ui.ui'
    assert overrides['ui']['plugin_id'] == 'c3-raylib-ui'
