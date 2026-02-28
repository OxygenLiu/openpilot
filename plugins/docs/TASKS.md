# Plugin Architecture — Tasks & Progress

## Overview

Plugin system for openpilot-plugins repo (`~/openpilot-plugins`, branch `plugins`).
Plugins live in `plugins/` and hook into the control loop via the hook system,
or run as managed processes.

### Available Hook Points (wired in repo)
- `controls.curvature_correction` (controlsd.py:123) — curvature adjustment
- `planner.v_cruise` (longitudinal_planner.py:107) — cruise speed override
- `planner.accel_limits` (longitudinal_planner.py:128) — accel limit adjustment
- `desire.post_update` (desire_helper.py:113) — lane change extensions

---

## Repo-Level Prerequisites ✅ COMPLETED

Commit `9c0215a` — cereal schemas, params, planner subscriptions.

- ✅ `cereal/custom.capnp` — SpeedLimitState, MapdOut, MapdIn, MapdExtendedOut structs with full fields
- ✅ `cereal/log.capnp` — Event field renames (speedLimitState @107, mapdOut @145, mapdIn @144, mapdExtendedOut @143)
- ✅ `cereal/services.py` — mapdOut (20Hz), mapdExtendedOut (20Hz), mapdIn (no-log), speedLimitState (1Hz)
- ✅ `common/params_keys.h` — LaneCenteringCorrection, MapdVersion, SpeedLimitConfirmed, SpeedLimitValue
- ✅ `selfdrive/controls/plannerd.py` — speedLimitState + mapdOut added to SubMaster

---

## Plugin: lane_centering ✅ COMPLETED

Commit `b1941cb`

**Type**: hook
**Hook**: `controls.curvature_correction`
**Param toggle**: `LaneCenteringCorrection` (bool, default off)

Files:
- `plugins/lane_centering/plugin.json`
- `plugins/lane_centering/correction.py` — LaneCenteringCorrection class + on_curvature_correction()

Features:
- Curvature-dependent K gain (sharper turns → stronger correction)
- Hysteresis activation (MIN_CURVATURE=0.002 on, EXIT_CURVATURE=0.001 off)
- Dynamic lane width estimation from both lane lines
- Jump rejection (MAX_JUMP=0.3m per frame)
- Smooth wind-down on deactivation (WINDDOWN_TAU=1.0s)
- Disabled during lane changes and at low speed (<9 m/s)

---

## Plugin: mapd ✅ COMPLETED

Commit `b1941cb`

**Type**: process
**Condition**: always_run
**Device filter**: tici, tizi, mici

Files:
- `plugins/mapd/plugin.json`
- `plugins/mapd/mapd_manager.py` — Binary lifecycle (ensure, download, backup, update, version check)
- `plugins/mapd/mapd_runner.py` — Entry point: ensure binary + os.execv()

Binary: pfeiferj/mapd (Go), publishes mapdOut at 20Hz with OSM speed limits, road names, curve speeds, hazards.

---

## Plugin: speedlimitd ✅ COMPLETED

Commit `b1941cb`

**Type**: hybrid (process + hook)
**Dependency**: mapd plugin
**Process**: speedlimitd at 1Hz (only_onroad)
**Hook**: `planner.v_cruise` — enforces confirmed speed limits

Files:
- `plugins/speedlimitd/plugin.json`
- `plugins/speedlimitd/speedlimitd.py` — SpeedLimitMiddleware (subscribes mapdOut + modelV2, publishes speedLimitState)
- `plugins/speedlimitd/planner_hook.py` — on_v_cruise() with speed-dependent offset

Three-tier priority:
1. OSM maxspeed (confidence 0.95)
2. YOLO speed sign detection (confidence 0.8)
3. Road type + lane count inference (confidence 0.5)

Speed-dependent offset (km/h above limit):
- 20-60 km/h zones: +20 km/h
- 70-120 km/h zones: +10 km/h

Road type fallback: PRC GB 5768 urban/non-urban tables by highway type + lane count.

---

## Plugin: bmw_e9x_e8x ✅ (pre-existing)

Car interface plugin with VIN-based detection, DCC control, stepper servo steering.

---

## Plugin: c3_compat ✅ (pre-existing)

Comma 3 hardware compatibility (STM32F4/AGNOS 12.8, Raylib UI).

---

## Benefits Over Monolith Fork

### No more submodule forks
The monolith approach required maintaining forked `opendbc` and `panda` submodules
with BMW-specific code. Every upstream comma update meant rebasing three repos
(openpilot + opendbc fork + panda fork) and resolving merge conflicts in all three.

With plugins, `opendbc_repo` and `panda` use upstream submodules directly. All
BMW-specific code lives in `plugins/bmw_e9x_e8x/` and loads dynamically at runtime.

| Before (monolith) | After (plugins) |
|---|---|
| Fork opendbc → add car/bmw/ | plugins/bmw_e9x_e8x/bmw/ |
| Fork panda → add safety/bmw.h | plugins/bmw_e9x_e8x/safety/bmw.h |
| Rebase 3 repos on every upstream update | `git pull upstream` — plugins untouched |

### Upstream sync is trivial
Update workflow: `git pull upstream master`. Plugins are isolated — nothing to
rebase. The only breakage risk is if comma changes a hook call site signature
(~5 lines in the repo), which is easy to spot and fix.

### Clean separation of concerns
- Official openpilot code: repo root (`selfdrive/`, `cereal/`, etc.)
- All custom features: `plugins/` directory
- Easy to enable/disable individual features via Params toggles
- Each plugin is self-contained with its own README, manifest, and code

---

## TODO

- [ ] C3 deployment testing for all three new plugins
- [ ] YOLO speed sign integration into speedlimitd (currently placeholder)
- [ ] Map download UI (Phase 2D from mapd_v2 integration)
- [ ] Interactive speed limit HUD element (tap to confirm/dismiss)
