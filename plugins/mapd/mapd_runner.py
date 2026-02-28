#!/usr/bin/env python3
"""
Mapd process entry point for plugin system.
Ensures the mapd binary exists and execs it.
"""
import os
import sys

def main():
  from mapd_manager import ensure_binary, MAPD_PATH
  if ensure_binary():
    os.execv(str(MAPD_PATH), [str(MAPD_PATH)])
  else:
    print("ERROR: Failed to ensure mapd binary, exiting", file=sys.stderr)
    sys.exit(1)

if __name__ == "__main__":
  main()
