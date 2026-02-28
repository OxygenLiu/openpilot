"""Tests for plugin discovery, loading, and lifecycle."""
import json
import os
import tempfile

import pytest
from unittest.mock import patch, MagicMock
from openpilot.selfdrive.plugins.registry import PluginRegistry, PluginInfo
from openpilot.selfdrive.plugins.hooks import HookRegistry


@pytest.fixture
def plugins_dir(tmp_path):
  """Create a temporary plugins directory."""
  return str(tmp_path)


@pytest.fixture
def sample_plugin(plugins_dir):
  """Create a sample hook plugin in the plugins directory."""
  plugin_dir = os.path.join(plugins_dir, 'sample_plugin')
  os.makedirs(plugin_dir)

  manifest = {
    'id': 'sample',
    'name': 'Sample Plugin',
    'version': '1.0.0',
    'type': 'hook',
    'hooks': {
      'test.hook': {
        'module': 'sample_mod',
        'function': 'on_test',
        'priority': 50,
      }
    },
    'params': {
      'SampleEnabled': {'type': 'bool', 'default': True}
    }
  }

  with open(os.path.join(plugin_dir, 'plugin.json'), 'w') as f:
    json.dump(manifest, f)

  with open(os.path.join(plugin_dir, 'sample_mod.py'), 'w') as f:
    f.write("def on_test(value):\n  return value + 100\n")

  return plugin_dir


class TestPluginDiscovery:
  def test_discover_empty(self, plugins_dir):
    registry = PluginRegistry(plugins_dir)
    assert registry.discover() == []

  def test_discover_plugin(self, plugins_dir, sample_plugin):
    registry = PluginRegistry(plugins_dir)
    discovered = registry.discover()
    assert 'sample' in discovered
    assert 'sample' in registry.plugins

  def test_discover_nonexistent_dir(self):
    registry = PluginRegistry('/nonexistent/path')
    assert registry.discover() == []

  def test_discover_skips_files(self, plugins_dir):
    # Create a file (not directory) in plugins dir
    with open(os.path.join(plugins_dir, 'not_a_plugin.txt'), 'w') as f:
      f.write('test')
    registry = PluginRegistry(plugins_dir)
    assert registry.discover() == []


class TestPluginLoading:
  @patch('openpilot.selfdrive.plugins.registry.PluginRegistry.is_enabled', return_value=True)
  def test_load_plugin_registers_hooks(self, mock_enabled, plugins_dir, sample_plugin):
    registry = PluginRegistry(plugins_dir)
    registry.discover()

    # Clear singleton hooks and use fresh one
    from openpilot.selfdrive.plugins import hooks as hooks_module
    original_hooks = hooks_module.hooks
    hooks_module.hooks = HookRegistry()

    try:
      success = registry.load_plugin('sample')
      assert success
      assert registry.plugins['sample'].loaded
      assert registry.plugins['sample'].enabled

      # Verify hook was registered
      result = hooks_module.hooks.run('test.hook', 0)
      assert result == 100
    finally:
      hooks_module.hooks = original_hooks

  def test_load_nonexistent_plugin(self, plugins_dir):
    registry = PluginRegistry(plugins_dir)
    assert not registry.load_plugin('nonexistent')

  @patch('openpilot.selfdrive.plugins.registry.PluginRegistry.is_enabled', return_value=True)
  def test_unload_plugin_removes_hooks(self, mock_enabled, plugins_dir, sample_plugin):
    registry = PluginRegistry(plugins_dir)
    registry.discover()

    from openpilot.selfdrive.plugins import hooks as hooks_module
    original_hooks = hooks_module.hooks
    hooks_module.hooks = HookRegistry()

    try:
      registry.load_plugin('sample')
      assert hooks_module.hooks.has_hooks('test.hook')

      registry.unload_plugin('sample')
      assert not hooks_module.hooks.has_hooks('test.hook')
      assert not registry.plugins['sample'].loaded
    finally:
      hooks_module.hooks = original_hooks


class TestPluginStatus:
  def test_get_status(self, plugins_dir, sample_plugin):
    registry = PluginRegistry(plugins_dir)
    registry.discover()

    status = registry.get_status()
    assert len(status) == 1
    assert status[0]['id'] == 'sample'
    assert status[0]['name'] == 'Sample Plugin'
    assert status[0]['version'] == '1.0.0'
    assert status[0]['type'] == 'hook'
    assert not status[0]['enabled']
    assert 'test.hook' in status[0]['hooks']
