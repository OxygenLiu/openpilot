#!/usr/bin/env bash
# c3_compat boot patcher — runs before openpilot build/launch on AGNOS 12.8
# Called from /data/continue.sh to apply Comma 3 compatibility fixes
#
# Patches are idempotent — safe to run multiple times.

# NOTE: Do NOT use 'set -e' here — this script is sourced by continue.sh,
# and set -e would propagate to the parent shell, killing the launch chain
# on any non-zero exit from build.py, manager.py, etc.
OPENPILOT_DIR="${1:-/data/openpilot}"

echo "[c3_compat] Applying AGNOS 12.8 patches to $OPENPILOT_DIR"

# 0. Prevent overlay swap from wiping our patches
#    launch_chffrplus.sh checks .overlay_init + finalized/.overlay_consistent
#    and swaps /data/openpilot with the staging copy (losing all our patches).
#    Remove the marker so the swap is skipped — updates go through COD instead.
if [ -f "$OPENPILOT_DIR/.overlay_init" ]; then
  rm -f "$OPENPILOT_DIR/.overlay_init"
  echo "[c3_compat] Removed .overlay_init (prevents stale overlay swap)"
fi

# 1. Amplifier: add tici config (removed in v0.10.3 which dropped C3 support)
AMP_FILE="$OPENPILOT_DIR/openpilot/system/hardware/tici/amplifier.py"
if [ -f "$AMP_FILE" ] && ! grep -q '"tici"' "$AMP_FILE"; then
  python3 - "$AMP_FILE" << 'PYEOF'
import sys
path = sys.argv[1]
with open(path) as f:
    content = f.read()

tici_config = '''  "tici": [
    AmpConfig("Right speaker output from right DAC", 0b1, 0x2C, 0, 0b11111111),
    AmpConfig("Right Speaker Mixer Gain", 0b00, 0x2D, 2, 0b00001100),
    AmpConfig("Right speaker output volume", 0x1c, 0x3E, 0, 0b00011111),
    AmpConfig("DAI2 EQ enable", 0b1, 0x49, 1, 0b00000010),

    *configs_from_eq_params(0x84, EQParams(0x274F, 0xC0FF, 0x3BF9, 0x0B3C, 0x1656)),
    *configs_from_eq_params(0x8E, EQParams(0x1009, 0xC6BF, 0x2952, 0x1C97, 0x30DF)),
    *configs_from_eq_params(0x98, EQParams(0x0F75, 0xCBE5, 0x0ED2, 0x2528, 0x3E42)),
    *configs_from_eq_params(0xA2, EQParams(0x091F, 0x3D4C, 0xCE11, 0x1266, 0x2807)),
    *configs_from_eq_params(0xAC, EQParams(0x0A9E, 0x3F20, 0xE573, 0x0A8B, 0x3A3B)),
  ],
'''
content = content.replace('CONFIGS = {\n  "tizi"', 'CONFIGS = {\n' + tici_config + '  "tizi"')
with open(path, 'w') as f:
    f.write(content)
PYEOF
  echo "[c3_compat] Patched amplifier.py with tici config"
fi

# 2. msgfmt stub: AGNOS 12.8 lacks gettext — create valid empty .mo files
#    /usr is read-only on AGNOS, so put stub in /data/plugins/c3_compat/bin and prepend to PATH
if ! command -v msgfmt &>/dev/null; then
  mkdir -p /data/plugins/c3_compat/bin
  cat > /data/plugins/c3_compat/bin/msgfmt << 'STUB'
#!/bin/sh
# Stub: c3_compat — produce valid empty .mo files (proper gettext binary format)
# scons calls: msgfmt -o output.mo input.po
while [ $# -gt 0 ]; do
  case "$1" in
    -o) shift; OUTPUT="$1" ;;
  esac
  shift
done
if [ -n "$OUTPUT" ]; then
  # Write a valid empty .mo file: magic + revision + 0 strings + offsets
  printf '\xde\x12\x04\x95\x00\x00\x00\x00\x00\x00\x00\x00\x1c\x00\x00\x00\x1c\x00\x00\x00\x00\x00\x00\x00\x1c\x00\x00\x00' > "$OUTPUT"
fi
STUB
  chmod +x /data/plugins/c3_compat/bin/msgfmt
  echo "[c3_compat] Installed msgfmt stub"
fi
export PATH="/data/plugins/c3_compat/bin:$PATH"

# 3. Multilang: patch to handle invalid/empty .mo files gracefully
#    Even with valid .mo stubs, be resilient to any translation failures
MULTILANG_FILE="$OPENPILOT_DIR/openpilot/system/ui/lib/multilang.py"
if [ -f "$MULTILANG_FILE" ] && grep -q 'except FileNotFoundError' "$MULTILANG_FILE"; then
  sed -i 's/except FileNotFoundError:/except Exception:/' "$MULTILANG_FILE"
  echo "[c3_compat] Patched multilang.py to handle invalid .mo files"
fi

# 4. Python packages: install missing deps to writable location
#    AGNOS 12.8 /usr/local/venv is read-only — install to /data/plugins/c3_compat/site-packages
C3_SITE_PACKAGES="/data/plugins/c3_compat/site-packages"
mkdir -p "$C3_SITE_PACKAGES"

# Missing Python packages: install to writable location if not already present
# These are in AGNOS 16 system Python but missing from AGNOS 12.8
C3_MISSING_PKGS="jeepney kaitaistruct"
for pkg in $C3_MISSING_PKGS; do
  if ! PYTHONPATH="$C3_SITE_PACKAGES" python3 -c "import $pkg" 2>/dev/null; then
    /usr/local/venv/bin/pip install --target "$C3_SITE_PACKAGES" "$pkg" -q 2>/dev/null || true
    echo "[c3_compat] Installed $pkg to $C3_SITE_PACKAGES"
  fi
done

# 5. Display: DRM backend (no Weston compositor)
#    DRM raylib at /data/pip_packages shadows venv's Wayland raylib
#    Must stop Weston so raylib can get DRM master on /dev/dri/card0
sudo systemctl stop weston 2>/dev/null || true

# 6. PYTHONPATH: patch launch script for c3_compat site-packages + DRM raylib
#    launch_chffrplus.sh does: export PYTHONPATH="$PWD" which clobbers any prior PYTHONPATH
#    /data/pip_packages MUST come before venv so DRM raylib shadows Wayland raylib
LAUNCH_FILE="$OPENPILOT_DIR/launch_chffrplus.sh"
if [ -f "$LAUNCH_FILE" ] && ! grep -q 'c3_compat' "$LAUNCH_FILE"; then
  sed -i "s|export PYTHONPATH=\"\$PWD\"|export PYTHONPATH=\"\$PWD:/data/pip_packages:$C3_SITE_PACKAGES\"|" "$LAUNCH_FILE"
  echo "[c3_compat] Patched launch_chffrplus.sh PYTHONPATH (DRM raylib + site-packages)"
fi

# 7. launch_env.sh: remove Wayland env vars (DRM backend uses /dev/dri/card0 directly)
LAUNCH_ENV="$OPENPILOT_DIR/launch_env.sh"
if [ -f "$LAUNCH_ENV" ] && grep -q 'WAYLAND_DISPLAY' "$LAUNCH_ENV" && ! grep -q 'c3_compat' "$LAUNCH_ENV"; then
  python3 - "$LAUNCH_ENV" << 'PYEOF'
import sys, re
path = sys.argv[1]
with open(path) as f:
    content = f.read()
# Replace the AGNOS < 16 Wayland block with Weston stop
old_block = re.search(
    r'if \[ "\$AGNOS_MAJOR" -lt 16 \].*fi\n',
    content, re.DOTALL
)
if old_block:
    new_block = """if [ "$AGNOS_MAJOR" -lt 16 ] 2>/dev/null; then
  # c3_compat: DRM backend — stop Weston so raylib gets DRM master
  sudo systemctl stop weston 2>/dev/null || true
fi
"""
    content = content[:old_block.start()] + new_block + content[old_block.end():]
    with open(path, 'w') as f:
        f.write(content)
PYEOF
  echo "[c3_compat] Patched launch_env.sh for DRM backend (removed Wayland config)"
fi

# 8. Panda: restore STM32F4 (Dos board) support for C3 internal panda
#    v0.10.3 dropped F4 support — only H7 (red panda, tres, cuatro) remains
#    C3 has HW_TYPE_DOS (0x06) with STM32F4 MCU
PANDA_INIT="$OPENPILOT_DIR/panda/python/__init__.py"
PANDA_CONST="$OPENPILOT_DIR/panda/python/constants.py"
if [ -f "$PANDA_INIT" ] && ! grep -q 'HW_TYPE_DOS' "$PANDA_INIT"; then
  python3 - "$PANDA_INIT" "$PANDA_CONST" << 'PYEOF'
import sys
init_path, const_path = sys.argv[1], sys.argv[2]

# --- Patch constants.py: add F4Config and McuType.F4 ---
with open(const_path) as f:
    const = f.read()
if 'F4Config' not in const:
    f4_config = '''
F4Config = McuConfig(
  "STM32F4",
  0x463,
  [0x4000 for _ in range(4)] + [0x10000] + [0x20000 for _ in range(7)],
  12,
  0x1FFF7A10,
  0x800,
  0x1FFF79C0,
  0x8004000,
  "panda.bin.signed",
  0x8000000,
  "bootstub.panda.bin",
)

'''
    const = const.replace('H7Config = McuConfig(', f4_config + 'H7Config = McuConfig(')
    const = const.replace(
        'class McuType(enum.Enum):\n  H7 = H7Config',
        'class McuType(enum.Enum):\n  F4 = F4Config\n  H7 = H7Config'
    )
    with open(const_path, 'w') as f:
        f.write(const)

# --- Patch __init__.py: add HW_TYPE_DOS, update get_mcu_type, devices ---
with open(init_path) as f:
    init = f.read()

# Add HW_TYPE_DOS after HW_TYPE_BLACK
init = init.replace(
    "HW_TYPE_BLACK = b'\\x03'\n",
    "HW_TYPE_BLACK = b'\\x03'\n  HW_TYPE_DOS = b'\\x06'  # C3 internal panda (STM32F4)\n"
)

# Add F4 devices list and extend SUPPORTED_DEVICES
init = init.replace(
    'H7_DEVICES = [HW_TYPE_RED_PANDA, HW_TYPE_TRES, HW_TYPE_CUATRO, HW_TYPE_BODY]\n  SUPPORTED_DEVICES = H7_DEVICES',
    'H7_DEVICES = [HW_TYPE_RED_PANDA, HW_TYPE_TRES, HW_TYPE_CUATRO, HW_TYPE_BODY]\n  F4_DEVICES = [HW_TYPE_DOS]\n  SUPPORTED_DEVICES = H7_DEVICES + F4_DEVICES'
)

# Add DOS to INTERNAL_DEVICES
init = init.replace(
    'INTERNAL_DEVICES = (HW_TYPE_TRES, HW_TYPE_CUATRO)',
    'INTERNAL_DEVICES = (HW_TYPE_TRES, HW_TYPE_CUATRO, HW_TYPE_DOS)'
)

# Patch get_mcu_type to handle F4
init = init.replace(
    '  def get_mcu_type(self) -> McuType:\n    hw_type = self.get_type()\n    if hw_type in Panda.H7_DEVICES:\n      return McuType.H7\n    raise ValueError(f"unknown HW type: {hw_type}")',
    '  def get_mcu_type(self) -> McuType:\n    hw_type = self.get_type()\n    if hw_type in Panda.H7_DEVICES:\n      return McuType.H7\n    if hw_type in Panda.F4_DEVICES:\n      return McuType.F4\n    raise ValueError(f"unknown HW type: {hw_type}")'
)

with open(init_path, 'w') as f:
    f.write(init)
PYEOF
  echo "[c3_compat] Patched panda library with STM32F4 (Dos board) support"
fi

# 9. Pandad: skip firmware flashing for F4 panda (BMW plugin handles firmware)
#    Without this, pandad would try to flash non-existent panda.bin.signed
PANDAD_FILE="$OPENPILOT_DIR/selfdrive/pandad/pandad.py"
if [ -f "$PANDAD_FILE" ] && ! grep -q 'c3_compat' "$PANDAD_FILE"; then
  python3 - "$PANDAD_FILE" << 'PYEOF'
import sys
path = sys.argv[1]
with open(path) as f:
    content = f.read()

# Replace flash_panda to skip firmware check for F4 devices
old = '''def flash_panda(panda_serial: str) -> Panda:
  try:
    panda = Panda(panda_serial)
  except PandaProtocolMismatch:'''

new = '''def flash_panda(panda_serial: str) -> Panda:
  try:
    panda = Panda(panda_serial)
  except PandaProtocolMismatch:'''

# Simpler approach: add F4 skip after firmware signature check
old_sig = '''  if panda.bootstub or panda_signature != fw_signature:
    cloudlog.info("Panda firmware out of date, update required")
    panda.flash()'''

new_sig = '''  # c3_compat: skip firmware flashing for F4 panda (BMW plugin handles firmware)
  from panda.python.constants import McuType
  if panda.get_mcu_type() == McuType.F4:
    cloudlog.warning("c3_compat: F4 panda detected, skipping firmware flash")
    return panda
  if panda.bootstub or panda_signature != fw_signature:
    cloudlog.info("Panda firmware out of date, update required")
    panda.flash()'''

if old_sig in content:
    content = content.replace(old_sig, new_sig)
    with open(path, 'w') as f:
        f.write(content)
PYEOF
  echo "[c3_compat] Patched pandad.py to skip F4 firmware flashing"
fi

echo "[c3_compat] Boot patches applied successfully"
