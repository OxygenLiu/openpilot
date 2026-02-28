"""Hook point registry and safe dispatcher.

The hook system is the core innovation of the plugin architecture.
It provides defined extension points in upstream control files where
plugins can inject behavior — with fail-safe guarantees.

Design principles:
  1. On ANY plugin error, immediately return default value (fail-safe)
  2. Multiple plugins on same hook run in priority order (lower first)
  3. Each callback receives current value and returns modified value (chain)
  4. Zero overhead when no plugins registered (immediate return)
  5. Lazy per-process loading: first run() call triggers plugin discovery
  6. Conflict detection: exclusive hooks reject multiple registrations

Usage in upstream files (total ~10 lines across 4 files):
  from openpilot.selfdrive.plugins.hooks import hooks

  # In control loop:
  curvature = hooks.run('controls.curvature_correction', curvature, model_v2, v_ego, lane_changing)
"""
from openpilot.common.swaglog import cloudlog

# Hooks that only allow ONE plugin to register (e.g., car interface).
# If two plugins both try to register for 'car.register_interfaces', the second
# is rejected and logged as a conflict. This prevents two car plugins fighting.
EXCLUSIVE_HOOKS = frozenset({
  'car.register_interfaces',
})


class HookRegistry:
  def __init__(self):
    # {hook_name: [(priority, plugin_name, callback), ...]} sorted by priority
    self._hooks: dict[str, list[tuple[int, str, callable]]] = {}
    # Lazy loading: each process loads plugins on first run() call
    self._loaded = False
    # {hook_name: [plugin_name]} — tracks conflicts that were rejected
    self._conflicts: dict[str, list[str]] = {}

  def _ensure_loaded(self):
    """Lazy load: discover and load enabled plugins on first use in this process.

    plugind is the lifecycle manager (enable/disable/install/API), but each
    process (controlsd, card, plannerd, etc.) has its own memory space.
    Plugins loaded in plugind are invisible to other processes.
    This method makes each process load its own plugin callbacks.
    """
    if self._loaded:
      return
    self._loaded = True
    try:
      from openpilot.selfdrive.plugins.registry import PluginRegistry
      registry = PluginRegistry()
      registry.discover()
      registry.load_enabled()
    except Exception:
      cloudlog.exception("hooks: failed to lazy-load plugins")

  def register(self, hook_name: str, plugin_name: str, callback: callable, priority: int = 50):
    """Register a callback for a hook point.

    For EXCLUSIVE_HOOKS (e.g., car.register_interfaces), only one plugin may
    register. A second registration is rejected and logged as a conflict.
    This prevents two car plugins from fighting over the same hook.
    """
    # Conflict detection for exclusive hooks
    if hook_name in EXCLUSIVE_HOOKS and hook_name in self._hooks and self._hooks[hook_name]:
      existing = self._hooks[hook_name][0][1]  # plugin_name of first registrant
      if existing != plugin_name:
        if hook_name not in self._conflicts:
          self._conflicts[hook_name] = []
        self._conflicts[hook_name].append(plugin_name)
        cloudlog.error(f"Plugin CONFLICT: '{plugin_name}' rejected for exclusive hook '{hook_name}' "
                       f"(already owned by '{existing}')")
        return

    if hook_name not in self._hooks:
      self._hooks[hook_name] = []
    self._hooks[hook_name].append((priority, plugin_name, callback))
    self._hooks[hook_name].sort(key=lambda x: x[0])
    cloudlog.info(f"Plugin '{plugin_name}' registered hook '{hook_name}' (priority {priority})")

  def unregister(self, hook_name: str, plugin_name: str):
    """Remove all callbacks for a plugin on a hook point."""
    if hook_name in self._hooks:
      self._hooks[hook_name] = [
        (p, name, cb) for p, name, cb in self._hooks[hook_name] if name != plugin_name
      ]
      if not self._hooks[hook_name]:
        del self._hooks[hook_name]

  def unregister_all(self, plugin_name: str):
    """Remove all callbacks for a plugin across all hooks."""
    for hook_name in list(self._hooks.keys()):
      self.unregister(hook_name, plugin_name)

  def run(self, hook_name: str, default, *args, **kwargs):
    """Execute hook chain. On ANY error, return default (fail-safe).

    Args:
      hook_name: The hook point identifier
      default: Value to return if no hooks registered or on error
      *args, **kwargs: Context passed to each callback

    Returns:
      Modified value from hook chain, or default on error/no hooks

    Each callback signature: callback(current_value, *args, **kwargs) -> new_value
    """
    self._ensure_loaded()

    callbacks = self._hooks.get(hook_name)
    if not callbacks:
      return default

    result = default
    for priority, plugin_name, callback in callbacks:
      try:
        result = callback(result, *args, **kwargs)
      except Exception:
        cloudlog.exception(f"Plugin '{plugin_name}' hook '{hook_name}' failed, returning default")
        return default
    return result

  def has_hooks(self, hook_name: str) -> bool:
    """Check if any callbacks registered for a hook point."""
    return bool(self._hooks.get(hook_name))

  def get_registered_hooks(self) -> dict[str, list[str]]:
    """Return {hook_name: [plugin_names]} for status reporting."""
    return {
      name: [plugin_name for _, plugin_name, _ in callbacks]
      for name, callbacks in self._hooks.items()
    }

  def get_conflicts(self) -> dict[str, list[str]]:
    """Return {hook_name: [rejected_plugin_names]} for conflict reporting."""
    return dict(self._conflicts)


# Singleton instance — imported by upstream control files
hooks = HookRegistry()
