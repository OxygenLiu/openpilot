#!/usr/bin/env python3
"""Plugin manager daemon — discovers, loads, and monitors plugins.

Runs as an always_run process. Responsibilities:
  1. Scan /data/plugins/ for installed plugins
  2. Validate compatibility (min/max openpilot version)
  3. Load enabled plugins, register hooks + processes
  4. Health monitoring — detect crashed plugins
  5. Serve REST API on localhost for COD
  6. Poll for enable/disable changes
"""
import time

from openpilot.common.realtime import Ratekeeper
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.plugins.registry import PluginRegistry
from openpilot.selfdrive.plugins.api import set_registry, start_api_server

POLL_INTERVAL = 5.0  # seconds between checking for config changes


def main():
  cloudlog.info("plugind starting")

  registry = PluginRegistry()

  # Initial discovery
  discovered = registry.discover()
  cloudlog.info(f"plugind discovered {len(discovered)} plugins: {discovered}")

  # Load enabled plugins
  registry.load_enabled()

  # Start REST API for COD
  set_registry(registry)
  api_server = start_api_server()

  # Main loop: poll for config changes, health monitoring
  rk = Ratekeeper(1.0 / POLL_INTERVAL, print_delay_threshold=None)
  while True:
    try:
      # Re-check enabled state (user may toggle via COD or Params)
      registry.load_enabled()
    except Exception:
      cloudlog.exception("plugind poll error")

    rk.keep_time()


if __name__ == "__main__":
  main()
