# c3_compat — Comma 3 Compatibility Plugin

**Type**: hybrid (process + hook)
**Device filter**: tici (Comma 3)

## What it does

Provides Comma 3 (TICI) hardware compatibility for AGNOS 12.8:

- **Raylib Python UI** — Full replacement UI process using raylib instead of Qt
  - Onroad HUD with speed, alerts, model/path rendering
  - Settings panels (device, software, developer, toggles, firehose)
  - Augmented road view with driver camera dialog
  - Speed limit sign and curvature speed overlays
- **Panda health monitoring** — STM32F4/Dos health check hook
- **AGNOS 12.8 support** — Wayland backend, GPU preemption, venv paths

## Params

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| ShowSpeedLimitSign | bool | true | Show speed limit sign on HUD |
| ShowCurvatureSpeed | bool | true | Show curvature-limited speed on HUD |

## Key files

```
c3_compat/
  plugin.json          # Plugin manifest
  compat.py            # device.health_check hook
  ui.py                # UI process entry point
  ui_state.py          # UIState cereal subscriptions
  layouts/             # Raylib layout components
    main.py            # Main onroad layout
    home.py            # Home/offroad layout
    sidebar.py         # Sidebar with connectivity/temp
    settings/          # Settings panels
  onroad/              # Onroad rendering
    hud_renderer.py    # Speed, alerts, speed limit sign
    model_renderer.py  # Path/lane rendering
    cameraview.py      # Camera frame display
    alert_renderer.py  # Alert overlay
  lib/                 # Helpers
    api_helpers.py     # API/auth helpers
    prime_state.py     # Prime subscription state
```

## Notes

- Replaces the default UI process (`"replace": true` in plugin.json)
- Requires Wayland (Weston compositor) on AGNOS 12.8
- Font loading uses `.ttf` directly via raylib `load_font_ex()`
