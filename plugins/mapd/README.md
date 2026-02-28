# mapd — OpenStreetMap Data Plugin

**Type**: process
**Condition**: always_run
**Device filter**: tici, tizi, mici
**Upstream**: [pfeiferj/mapd](https://github.com/pfeiferj/mapd)

## What it does

Manages the mapd Go binary that provides OpenStreetMap data to openpilot:

- Speed limits (maxspeed tags)
- Road names and reference codes
- Curve speeds (map-based and vision-based)
- Hazards and advisory speeds
- Road context (freeway/city)
- Offline map tile management

## How it works

1. `mapd_runner.py` is spawned by the plugin process manager
2. It calls `ensure_binary()` to download the mapd binary if missing
3. Then `os.execv()` replaces the Python process with the native Go binary
4. The binary reads GPS from cereal, queries OSM tiles, publishes `mapdOut` at 20Hz

## Binary management

The binary lives at `/data/media/0/osm/mapd` (outside the repo, persists across updates).

```bash
# Check for updates
python mapd_manager.py check

# Download/update to latest
python mapd_manager.py update

# Ensure binary exists (download if missing)
python mapd_manager.py ensure
```

Update flow: backup → download to temp → stop daemon → atomic replace → update version → restart.

## Offline maps

Downloaded to `/data/media/0/osm/offline/`. Use `mapd interactive` TUI to download regions.

## Cereal messages

| Message | Direction | Frequency | Description |
|---------|-----------|-----------|-------------|
| mapdOut | publish | 20 Hz | Speed limits, road info, curve speeds |
| mapdExtendedOut | publish | 20 Hz | Download progress, settings, path points |
| mapdIn | subscribe | event | Download triggers, settings changes |

## Key files

```
mapd/
  plugin.json        # Plugin manifest
  mapd_manager.py    # Binary download, update, version management
  mapd_runner.py     # Process entry point (ensure + execv)
```

## Params

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| MapdVersion | string | v2.0.2 | Currently installed mapd version |
