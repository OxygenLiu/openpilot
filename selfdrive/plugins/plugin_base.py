"""Base classes for c3pilot plugins.

Plugin types:
  HookPlugin   — registers callbacks at hook points in the control loop
  ProcessPlugin — runs a separate daemon process managed by plugind
"""
from abc import ABC, abstractmethod


class PluginBase(ABC):
  """Base class for all plugins."""

  def __init__(self, manifest: dict):
    self.manifest = manifest
    self.id = manifest['id']
    self.name = manifest['name']
    self.version = manifest['version']
    self.enabled = False

  @abstractmethod
  def on_enable(self):
    """Called when plugin is enabled. Register hooks/processes here."""

  @abstractmethod
  def on_disable(self):
    """Called when plugin is disabled. Cleanup here."""

  def on_config_update(self, key: str, value):
    """Called when a plugin config param changes."""


class HookPlugin(PluginBase):
  """Plugin that registers callbacks at hook points in the control loop.

  Hook callbacks receive (current_value, *context_args) and return a new value.
  On any exception, the hook system returns the default value (fail-safe).
  """

  def __init__(self, manifest: dict):
    super().__init__(manifest)
    self._hook_callbacks = {}

  def register_hook(self, hook_name: str, callback, priority: int = 50):
    """Register a callback for a hook point.

    Args:
      hook_name: Hook point name (e.g. 'controls.curvature_correction')
      callback: Function(current_value, *args, **kwargs) -> new_value
      priority: Lower runs first (default 50)
    """
    self._hook_callbacks[hook_name] = (callback, priority)

  def get_hooks(self) -> dict:
    """Return {hook_name: (callback, priority)} for registration."""
    return self._hook_callbacks


class ProcessPlugin(PluginBase):
  """Plugin that runs a separate daemon process."""

  def __init__(self, manifest: dict):
    super().__init__(manifest)
    self._processes = []

  def get_processes(self) -> list[dict]:
    """Return list of process definitions.

    Each dict: {'name': str, 'module': str, 'condition': callable}
    """
    return self._processes
