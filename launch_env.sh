#!/usr/bin/env bash

export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export VECLIB_MAXIMUM_THREADS=1

# models get lower priority than ui
# - ui is ~5ms
# - modeld is 20ms
# - DM is 10ms
# in order to run ui at 60fps (16.67ms), we need to allow
# it to preempt the model workloads. we have enough
# headroom for this until ui is moved to the CPU.
export QCOM_PRIORITY=12

if [ -z "$AGNOS_VERSION" ]; then
  if [ -f /VERSION ]; then
    export AGNOS_VERSION="$(cat /VERSION)"
  else
    export AGNOS_VERSION="16"
  fi
fi

# AGNOS 12.8: venv Python has pyray/raylib dependencies
# AGNOS 16+: system Python has everything
export PATH="/usr/local/venv/bin:$PATH"

# AGNOS 12.8: Raylib uses Wayland backend via Weston compositor
# AGNOS 16+: uses DRM directly (no Weston needed)
AGNOS_MAJOR=$(echo "$AGNOS_VERSION" | cut -d. -f1)
if [ "$AGNOS_MAJOR" -lt 16 ] 2>/dev/null; then
  # Ensure Weston compositor is running for Wayland backend
  export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/var/tmp/weston}"
  export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"
  # Fix socket permissions if needed
  if [ -S "$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY" ]; then
    sudo chmod a+rw "$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY" 2>/dev/null || true
  fi
fi

export STAGING_ROOT="/data/safe_staging"
