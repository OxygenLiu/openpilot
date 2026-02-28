"""Tests for the plugin hook system."""
import pytest
from openpilot.selfdrive.plugins.hooks import HookRegistry


class TestHookRegistry:
  def setup_method(self):
    self.hooks = HookRegistry()

  def test_no_hooks_returns_default(self):
    result = self.hooks.run('nonexistent.hook', 42)
    assert result == 42

  def test_single_hook_modifies_value(self):
    def double(value):
      return value * 2

    self.hooks.register('test.double', 'test-plugin', double)
    result = self.hooks.run('test.double', 5)
    assert result == 10

  def test_hook_chain_runs_in_priority_order(self):
    calls = []

    def add_ten(value):
      calls.append('add_ten')
      return value + 10

    def multiply_two(value):
      calls.append('multiply_two')
      return value * 2

    # multiply runs first (priority 10), then add (priority 50)
    self.hooks.register('test.chain', 'plugin-add', add_ten, priority=50)
    self.hooks.register('test.chain', 'plugin-mul', multiply_two, priority=10)

    result = self.hooks.run('test.chain', 5)
    assert calls == ['multiply_two', 'add_ten']
    assert result == 20  # (5 * 2) + 10

  def test_exception_returns_default(self):
    def bad_callback(value):
      raise ValueError("plugin error")

    self.hooks.register('test.fail', 'bad-plugin', bad_callback)
    result = self.hooks.run('test.fail', 42)
    assert result == 42

  def test_exception_in_chain_returns_default(self):
    def good(value):
      return value + 1

    def bad(value):
      raise RuntimeError("crash")

    # good runs first, then bad crashes — should return original default
    self.hooks.register('test.chain_fail', 'good-plugin', good, priority=10)
    self.hooks.register('test.chain_fail', 'bad-plugin', bad, priority=50)

    result = self.hooks.run('test.chain_fail', 100)
    assert result == 100  # Returns original default, not 101

  def test_hook_with_extra_args(self):
    def callback(value, model, speed):
      return value + speed

    self.hooks.register('test.args', 'plugin', callback)
    result = self.hooks.run('test.args', 0.0, 'model_data', 5.0)
    assert result == 5.0

  def test_hook_with_kwargs(self):
    def callback(value, **kwargs):
      return value + kwargs.get('offset', 0)

    self.hooks.register('test.kwargs', 'plugin', callback)
    result = self.hooks.run('test.kwargs', 10, offset=5)
    assert result == 15

  def test_unregister_plugin(self):
    def callback(value):
      return value + 1

    self.hooks.register('test.unreg', 'my-plugin', callback)
    assert self.hooks.run('test.unreg', 0) == 1

    self.hooks.unregister('test.unreg', 'my-plugin')
    assert self.hooks.run('test.unreg', 0) == 0

  def test_unregister_all(self):
    def cb1(value):
      return value + 1

    def cb2(value):
      return value + 2

    self.hooks.register('hook.a', 'my-plugin', cb1)
    self.hooks.register('hook.b', 'my-plugin', cb2)

    self.hooks.unregister_all('my-plugin')
    assert self.hooks.run('hook.a', 0) == 0
    assert self.hooks.run('hook.b', 0) == 0

  def test_has_hooks(self):
    assert not self.hooks.has_hooks('test.check')
    self.hooks.register('test.check', 'plugin', lambda v: v)
    assert self.hooks.has_hooks('test.check')

  def test_get_registered_hooks(self):
    self.hooks.register('hook.a', 'plugin-1', lambda v: v)
    self.hooks.register('hook.a', 'plugin-2', lambda v: v)
    self.hooks.register('hook.b', 'plugin-1', lambda v: v)

    registered = self.hooks.get_registered_hooks()
    assert set(registered['hook.a']) == {'plugin-1', 'plugin-2'}
    assert registered['hook.b'] == ['plugin-1']

  def test_none_default_for_void_hooks(self):
    """Void hooks (post_actuators) use None as default and don't chain values."""
    calls = []

    def side_effect(value, actuators, cs, plan):
      calls.append('called')
      return None

    self.hooks.register('controls.post_actuators', 'plugin', side_effect)
    result = self.hooks.run('controls.post_actuators', None, 'actuators', 'cs', 'plan')
    assert result is None
    assert calls == ['called']
