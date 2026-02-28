# lane_centering — Lane Centering Correction Plugin

**Type**: hook
**Hook**: `controls.curvature_correction`
**Param toggle**: `LaneCenteringCorrection` (bool, default off)

## What it does

Applies a curvature correction to center the car in its lane during turns.
Uses the model's lane line detection to measure lateral offset from lane center,
then adds a corrective curvature proportional to the offset.

## How it works

1. Measures lane center from the higher-confidence lane line + estimated lane width
2. Computes lateral offset between the model's planned path and lane center
3. Applies curvature-dependent gain (sharper turns → stronger correction)
4. Converts offset to curvature correction: `correction = -K * offset / v_ego²`

### Activation (hysteresis)

- **Activates** when curvature > 0.002 (1/m) AND offset > 0.3m
- **Deactivates** when curvature < 0.001 (1/m) AND offset < 0.15m
- **Disabled** during lane changes, at low speed (< 9 m/s), or when lane confidence < 0.5

### Safety features

- Jump rejection: ignores lane center changes > 0.3m per frame
- Smooth wind-down (1.0s tau) when deactivating
- Dynamic lane width estimation (2.5m–4.5m range, 3.5m default)
- Fails safe: returns unmodified curvature on any data issue

## Key files

```
lane_centering/
  plugin.json      # Plugin manifest
  correction.py    # LaneCenteringCorrection class + hook callback
```

## Tuning parameters

| Parameter | Value | Description |
|-----------|-------|-------------|
| K_BP | [0.002, 0.005, 0.008, 0.012, 0.020] | Curvature breakpoints (1/m) |
| K_V | [0.03, 0.35, 0.40, 0.50, 0.65] | Gain at each breakpoint |
| MIN_CURVATURE | 0.002 | Activation threshold (~500m radius) |
| EXIT_CURVATURE | 0.001 | Deactivation threshold (~1000m radius) |
| OFFSET_THRESHOLD | 0.3m | Minimum offset to activate |
| SMOOTH_TAU | 0.5s | Correction smoothing time constant |
| WINDDOWN_TAU | 1.0s | Deactivation smoothing time constant |
