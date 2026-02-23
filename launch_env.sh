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
  export AGNOS_VERSION="12.8"
fi

export STAGING_ROOT="/data/safe_staging"

# AGNOS 12.8: Use venv Python which has pyray and other raylib UI dependencies
# AGNOS 16+ has these in the system Python, but 12.8 keeps them in the venv
export PATH="/usr/local/venv/bin:$PATH"

# AGNOS 12.8: Raylib uses Wayland backend (via Weston compositor, not DRM)
# Weston creates its socket at /var/tmp/weston/ (per weston.service config)
export XDG_RUNTIME_DIR="/var/tmp/weston"
export WAYLAND_DISPLAY="wayland-0"
# Fix socket permissions — weston creates it as root but raylib runs as comma
sudo chmod a+rw /var/tmp/weston/wayland-0 2>/dev/null || true
