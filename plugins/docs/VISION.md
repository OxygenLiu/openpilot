# Plugin Architecture — Vision

> This document describes the long-term goal. The architecture needs real-world
> testing and validation before any broader effort.

## The Problem

Every openpilot fork (FrogPilot, sunnypilot, DragonPilot, car-specific forks)
maintains thousands of lines of diff against upstream comma. Each upstream
release triggers a painful rebase across the main repo and forked submodules.
Features developed in one fork are inaccessible to users of another.

## The Idea

A plugin architecture that turns forks into composable, drop-in modules:

- **One upstream openpilot** with a small set of hook points
- **N independent plugins** that extend behavior without modifying core code
- **Users mix and match** — install only what they need

## What Plugins Can Replace

| Today (forked) | Tomorrow (plugin) |
|---|---|
| Fork opendbc + panda for unsupported car | Car interface plugin (BMW, Rivian, Porsche, ...) |
| Fork selfdrive for custom features | Hook plugins (lane centering, speed limits, ...) |
| Fork UI for custom HUD elements | UI overlay plugin |
| Fork manager for custom processes | Process plugin (mapd, dashcam manager, ...) |
| Maintain N diverging forks | One upstream + N plugins |

## Hook Points Needed

Current (implemented):
- `controls.curvature_correction` — curvature adjustment
- `planner.v_cruise` — cruise speed override
- `planner.accel_limits` — acceleration limit adjustment
- `desire.post_update` — lane change extensions

Additional (needed for general-purpose SDK):
- `car.register_interfaces` — register car platforms
- `car.panda_status` — monitor panda health
- `ui.render_overlay` — custom HUD elements
- `manager.register_processes` — spawn plugin processes

## Who Benefits

- **Car porters**: Ship a car plugin instead of maintaining a full fork
- **Feature developers**: Write a self-contained plugin, works on any fork that has the hook points
- **End users**: Install plugins like apps — no git knowledge required
- **Upstream comma**: Could adopt the hook points (minimal, zero-overhead) and let the community extend via plugins instead of forks

## Upstream Surface Area

For true plug-and-play, a small set of changes would need to exist in upstream
openpilot. These fall into two categories.

### 1. Plugin framework (~30 lines, one-time)

These files enable plugin discovery and hook dispatch:

| File | Lines | Purpose |
|------|-------|---------|
| `selfdrive/plugins/hooks.py` | ~20 | HookRegistry: register callbacks, dispatch with fail-safe |
| `selfdrive/plugins/loader.py` | ~10 | Scan `plugins/`, parse plugin.json, lazy-load hook callbacks |

Zero overhead when no plugins installed — `hooks.run()` returns the default
value immediately if no callbacks are registered (~50ns).

### 2. Hook call sites (~2-3 lines each)

Each hook point is a single `hooks.run()` call inserted at the right place.
These are the minimal touch points in upstream code:

| File | Hook | Lines added |
|------|------|-------------|
| `selfdrive/controls/controlsd.py` | `controls.curvature_correction` | 2 |
| `selfdrive/controls/lib/longitudinal_planner.py` | `planner.v_cruise` | 2 |
| `selfdrive/controls/lib/longitudinal_planner.py` | `planner.accel_limits` | 2 |
| `selfdrive/controls/lib/desire_helper.py` | `desire.post_update` | 2 |
| `selfdrive/ui/layouts/main.py` | `ui.render_overlay` | 2 |
| `system/manager/process_config.py` | `manager.register_processes` | 10 |
| `opendbc_repo/opendbc/car/car_helpers.py` | `car.register_interfaces` | 5 |
| `cereal/custom.capnp` | (schema definitions) | ~50 |
| `cereal/services.py` | (service registration) | ~5 |
| `common/params_keys.h` | (param registration) | ~5 |

**Total upstream diff: ~85 lines** to enable the entire plugin ecosystem.

### What stays outside upstream

Everything else lives in `plugins/` and requires zero upstream changes:

- All plugin code (car interfaces, features, UI overlays, processes)
- Plugin manifests (plugin.json)
- Plugin documentation (README.md per plugin)
- Plugin-specific params defaults and configuration

### Future: Boot-Time JIT Builder (zero upstream diff)

Instead of maintaining ~85 lines of upstream patches, a **plugin-aware build
step at boot** could reduce the upstream diff to a single line — running
`plugind` before other processes.

#### How it works

```
Boot → plugind scans enabled plugins → patch + build → start openpilot
```

1. `plugind` reads `plugins/*/plugin.json`, checks which are enabled (Params toggle)
2. **capnp schemas**: Merges field definitions from enabled plugins into
   `custom.capnp` reserved structs → `scons cereal/` to recompile
3. **services.py**: Appends service registrations from plugin manifests
4. **params**: Writes default values directly to `/data/params/d/`
5. **hook call sites**: Generates wrapper modules from plugin manifest declarations
6. Normal openpilot processes start with everything in place

Enable/disable a plugin → toggle a param → reboot → plugind rebuilds →
next drive runs with the new configuration.

#### Why this is better than runtime monkey-patching

- capnp properly compiled — no serialization hacks or raw data workarounds
- Zero runtime overhead — identical binary to hand-patched monolith
- No fragile function patching — hook sites generated from declarative manifests
- Deterministic — same plugin set always produces the same build

#### Why this is better than maintaining upstream patches

- Upstream openpilot stays 100% stock
- No rebase conflicts — ever
- Plugin loader is the only addition (~1 line in manager)
- All plugin declarations already exist in plugin.json — the builder just
  reads them and assembles the final configuration

#### What plugin.json already declares

```json
{
  "hooks": {"planner.v_cruise": {"module": "planner_hook", "function": "on_v_cruise"}},
  "processes": [{"name": "speedlimitd", "module": "speedlimitd", "condition": "only_onroad"}],
  "params": {"SpeedLimitConfirmed": {"type": "string", "default": "0"}},
  "services": {"speedLimitState": [true, 1.0, 1]}
}
```

The boot-time builder turns these declarations into concrete code — no manual
wiring needed.

### Adoption path

1. **Today**: Maintain ~85 lines as a thin patch on top of upstream
2. **After C3 validation**: Implement boot-time JIT builder to eliminate patches
3. **Proven at scale**: Propose plugind + hook call sites (~25 lines) to comma
4. **If accepted**: Zero upstream diff — `git clone commaai/openpilot && cp -r plugins/ .` and drive

The key selling point to comma: these hook points cost nothing when unused,
but eliminate the need for hundreds of forks that each carry thousands of
lines of unmaintainable diff.

---

## Status

Proven with 5 plugins on a single BMW E90 vehicle:
- bmw_e9x_e8x (car interface + safety)
- c3_compat (Comma 3 Raylib UI)
- lane_centering (curvature correction)
- mapd (OSM data)
- speedlimitd (speed limit fusion)

Next: real-world C3 testing to validate the architecture under driving conditions.
