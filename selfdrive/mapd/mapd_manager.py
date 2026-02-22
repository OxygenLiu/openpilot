#!/usr/bin/env python3
"""
Mapd binary management utility
Handles version checking, backup, download, and update of mapd binary
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

from openpilot.common.params import Params

MAPD_PATH = Path("/data/openpilot/selfdrive/mapd/mapd")
BACKUP_DIR = Path("/data/openpilot/selfdrive/mapd/backups")
VERSION_PATH = Path("/data/media/0/osm/mapd_version")

GITHUB_API_URL = "https://api.github.com/repos/pfeiferj/mapd/releases/latest"

def get_current_version():
  """Get currently installed mapd version from Params"""
  params = Params()
  version = params.get("MapdVersion", encoding='utf-8')
  return version if version else "v2.0.2"

def get_latest_version():
  """Check GitHub API for latest release version"""
  try:
    with urllib.request.urlopen(GITHUB_API_URL, timeout=10) as response:
      data = json.loads(response.read().decode('utf-8'))
      return data.get('tag_name', '')
  except Exception as e:
    print(f"Error fetching latest version: {e}", file=sys.stderr)
    return ""

def backup_current_binary():
  """Backup current mapd binary with version suffix"""
  if not MAPD_PATH.exists():
    print("No existing binary to backup")
    return True

  try:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    current_version = get_current_version()
    backup_path = BACKUP_DIR / f"mapd_{current_version}"

    # Copy current binary to backup
    shutil.copy2(MAPD_PATH, backup_path)
    print(f"Backed up current binary to {backup_path}")
    return True
  except Exception as e:
    print(f"Backup failed: {e}", file=sys.stderr)
    return False

def download_binary(version):
  """Download mapd binary from GitHub release to temporary file"""
  download_url = f"https://github.com/pfeiferj/mapd/releases/download/{version}/mapd"

  try:
    print(f"Downloading {version} from {download_url}...")

    # Download to temporary file (don't replace binary yet)
    temp_file_path = MAPD_PATH.parent / f"mapd_{version}_temp"

    with urllib.request.urlopen(download_url) as response:
      with open(temp_file_path, "wb") as temp_file:
        shutil.copyfileobj(response, temp_file)
        os.fsync(temp_file.fileno())

    # Make executable
    os.chmod(temp_file_path, os.stat(temp_file_path).st_mode | stat.S_IEXEC)

    print(f"Successfully downloaded {version} to temporary file")
    return temp_file_path
  except Exception as e:
    print(f"Download failed: {e}", file=sys.stderr)
    if 'temp_file_path' in locals() and temp_file_path.exists():
      temp_file_path.unlink(missing_ok=True)
    return None

def stop_mapd():
  """Stop mapd daemon gracefully"""
  try:
    # Kill existing mapd process
    subprocess.run(["pkill", "mapd"], check=False)

    # Wait for process to terminate
    import time
    time.sleep(2)

    print("Mapd stopped")
    return True
  except Exception as e:
    print(f"Stop failed: {e}", file=sys.stderr)
    return False

def start_mapd():
  """Start mapd daemon in background"""
  try:
    # Start new mapd process in background
    subprocess.Popen(
      [str(MAPD_PATH)],
      cwd=MAPD_PATH.parent,
      stdout=subprocess.DEVNULL,
      stderr=subprocess.DEVNULL,
      start_new_session=True
    )

    print("Mapd started successfully")
    return True
  except Exception as e:
    print(f"Start failed: {e}", file=sys.stderr)
    return False

def replace_binary(temp_file_path):
  """Atomically replace mapd binary with new version"""
  try:
    # Atomic rename (only works if on same filesystem)
    os.rename(temp_file_path, MAPD_PATH)
    print("Binary replaced successfully")
    return True
  except Exception as e:
    print(f"Binary replacement failed: {e}", file=sys.stderr)
    return False

def update_version_param(version):
  """Update MapdVersion param to new version"""
  try:
    params = Params()
    params.put("MapdVersion", version)

    # Also write to version file
    VERSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(VERSION_PATH, "w") as f:
      f.write(version)
      os.fsync(f.fileno())

    return True
  except Exception as e:
    print(f"Failed to update version: {e}", file=sys.stderr)
    return False

def check_for_updates():
  """Check if update is available and print status"""
  current = get_current_version()
  latest = get_latest_version()

  if not latest:
    print("ERROR: Could not fetch latest version")
    return False

  if current == latest:
    print(f"UP_TO_DATE: {current}")
    return True
  else:
    print(f"UPDATE_AVAILABLE: {current} -> {latest}")
    return False

def perform_update():
  """Perform full update: backup, download, stop, replace, start"""
  current_version = get_current_version()
  latest_version = get_latest_version()

  if not latest_version:
    print("ERROR: Could not fetch latest version")
    return False

  if current_version == latest_version:
    print(f"Already up to date: {current_version}")
    return True

  print(f"Updating from {current_version} to {latest_version}...")

  # Step 1: Backup current binary
  print("Step 1/6: Backing up current binary...")
  if not backup_current_binary():
    return False

  # Step 2: Download new binary to temp file (mapd still running)
  print("Step 2/6: Downloading new binary...")
  temp_file_path = download_binary(latest_version)
  if not temp_file_path:
    return False

  # Step 3: Stop mapd daemon
  print("Step 3/6: Stopping mapd daemon...")
  if not stop_mapd():
    # Clean up temp file
    temp_file_path.unlink(missing_ok=True)
    return False

  # Step 4: Replace binary atomically
  print("Step 4/6: Replacing binary...")
  if not replace_binary(temp_file_path):
    # Try to restart old binary
    start_mapd()
    return False

  # Step 5: Update version info
  print("Step 5/6: Updating version info...")
  if not update_version_param(latest_version):
    # Version update failed but binary is replaced, continue anyway
    pass

  # Step 6: Start new mapd daemon
  print("Step 6/6: Starting new mapd daemon...")
  if not start_mapd():
    print("WARNING: Failed to start mapd daemon", file=sys.stderr)
    return False

  print(f"✓ Update complete: {current_version} -> {latest_version}")
  return True

if __name__ == "__main__":
  if len(sys.argv) < 2:
    print("Usage: mapd_manager.py [check|update]")
    sys.exit(1)

  command = sys.argv[1]

  if command == "check":
    success = check_for_updates()
    sys.exit(0 if success else 1)
  elif command == "update":
    success = perform_update()
    sys.exit(0 if success else 1)
  else:
    print(f"Unknown command: {command}")
    print("Usage: mapd_manager.py [check|update]")
    sys.exit(1)
