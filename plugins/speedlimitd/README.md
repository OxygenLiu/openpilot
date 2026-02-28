# speedlimitd — Speed Limit Daemon Plugin

**Type**: hybrid (process + hook)
**Dependency**: mapd plugin
**Process**: speedlimitd at 1 Hz (only_onroad)
**Hook**: `planner.v_cruise`

## What it does

Fuses multiple speed limit sources into a single `speedLimitState` message,
and enforces confirmed limits by capping cruise speed in the longitudinal planner.

### Three-tier priority

| Priority | Source | Confidence | Description |
|----------|--------|------------|-------------|
| 1 (highest) | OSM maxspeed | 0.95 | From mapdOut.speedLimit |
| 2 | YOLO detection | 0.80 | Speed sign recognition (placeholder) |
| 3 (lowest) | Road type inference | 0.50 | Highway type + lane count lookup |

### Road type fallback tables

Based on PRC Road Traffic Safety Law (GB 5768). Defaults to urban (more conservative).

**Urban** — used when roadContext is `city` or `unknown`:

| Road type | Multi-lane | Single-lane |
|-----------|-----------|-------------|
| motorway | 100 | 100 |
| trunk | 80 | 60 |
| primary | 60 | 50 |
| secondary | 60 | 50 |
| tertiary | 40 | 40 |
| residential | 30 | 30 |

**Non-urban** — used when roadContext is `freeway`:

| Road type | Multi-lane | Single-lane |
|-----------|-----------|-------------|
| motorway | 120 | 120 |
| trunk | 100 | 70 |
| primary | 80 | 70 |

### Speed limit enforcement

The `planner.v_cruise` hook caps cruise speed when a limit is confirmed:

```
v_limit = (speedLimit + offset) * KPH_TO_MS
```

Speed-dependent offset (km/h above posted limit):
- 20–60 km/h zones: +20 km/h
- 70–120 km/h zones: +10 km/h

Only enforced when `v_limit < v_cruise` (never increases cruise speed).

### Confirmation flow

1. speedlimitd publishes `speedLimitState` with current best estimate
2. UI displays the speed limit sign (via c3_compat HUD)
3. User taps to confirm → sets `SpeedLimitConfirmed=1` + `SpeedLimitValue=<limit>`
4. planner hook reads confirmed state and caps v_cruise
5. If speed limit changes, confirmation resets automatically

## Cereal messages

| Message | Direction | Frequency | Description |
|---------|-----------|-----------|-------------|
| mapdOut | subscribe | 20 Hz | OSM speed limit, road context, wayRef |
| modelV2 | subscribe | 20 Hz | Lane line probs for lane count inference |
| speedLimitState | publish | 1 Hz | Fused speed limit + source + confirmation |

## Key files

```
speedlimitd/
  plugin.json        # Plugin manifest
  speedlimitd.py     # SpeedLimitMiddleware process
  planner_hook.py    # planner.v_cruise hook
```

## Params

| Param | Type | Lifecycle | Description |
|-------|------|-----------|-------------|
| SpeedLimitConfirmed | string | CLEAR_ON_MANAGER_START | "1" when user confirmed |
| SpeedLimitValue | string | CLEAR_ON_MANAGER_START | Confirmed limit in km/h |
