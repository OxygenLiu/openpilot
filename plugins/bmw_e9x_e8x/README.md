# bmw_e9x_e8x — BMW Car Interface Plugin

**Type**: car (hook-based registration)

## What it does

Full BMW E8x/E9x car interface for openpilot:

- **VIN-based detection** — Empty CAN fingerprints, pure VIN model code matching
- **Car interface** — CarState (CAN parsing), CarController (actuator commands), CarParams
- **DCC control** — Dynamic Cruise Control via tick-counted commands
- **Stepper servo steering** — Ocelot stepper servo on F-CAN/AUX-CAN
- **Panda safety model** — BMW safety rules (bmw.h, safety model ID 35)
- **Panda status monitoring** — ELM327 fallback detection, firmware health

## Supported platforms

| Platform | Description |
|----------|-------------|
| BMW_E82 | 1-Series Coupe/Convertible (2004-13) |
| BMW_E90 | 3-Series Sedan/Wagon/Coupe/Convertible (2005-11) |

## Hooks

| Hook | Module | Description |
|------|--------|-------------|
| car.register_interfaces | register.py | Register BMW platforms into openpilot car detection |
| car.panda_status | panda_status.py | Monitor panda safety model (detect ELM327 fallback) |

## Key files

```
bmw_e9x_e8x/
  plugin.json           # Plugin manifest
  register.py           # car.register_interfaces hook
  panda_status.py       # car.panda_status hook
  bmw/                  # Car interface (mirrors opendbc/car/bmw/)
    values.py           # Platform config, VIN detection, DBC mapping
    fingerprints.py     # Empty fingerprints + dummy FW versions
    interface.py        # CarInterface (get_params, _get_params)
    carstate.py         # CAN message parsing (empty parser pattern)
    carcontroller.py    # DCC commands, stepper servo, turn signals
    bmwcan.py           # CAN message construction helpers
  dbc/                  # DBC files
    bmw_e9x_e8x.dbc    # BMW PT-CAN + F-CAN definitions
    ocelot_controls.dbc # Stepper servo CAN definitions
  safety/
    bmw.h               # Panda safety model (C, compiled on STM32)
```

## CAN bus layout

| Bus | Name | Messages |
|-----|------|----------|
| 0 | PT-CAN | Engine, brakes, speed, transmission, cruise |
| 1 | F-CAN | Stepper servo (if equipped) |
| 2 | AUX-CAN | Alternative servo bus, message routing |
