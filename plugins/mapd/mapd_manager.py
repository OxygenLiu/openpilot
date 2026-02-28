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
import urllib.request
from pathlib import Path

MAPD_PATH = Path("/data/media/0/osm/mapd")
BACKUP_DIR = Path("/data/media/0/osm/mapd_backups")
VERSION_PATH = Path("/data/media/0/osm/mapd_version")
PARAMS_DIR = Path("/data/params/d")

GITHUB_API_URL = "https://api.github.com/repos/pfeiferj/mapd/releases/latest"

def ensure_binary():
  """Ensure mapd binary exists at MAPD_PATH, downloading if needed"""
  if MAPD_PATH.exists():
    return True

  MAPD_PATH.parent.mkdir(parents=True, exist_ok=True)

  # No binary — download latest
  print("No mapd binary found, downloading latest...")
  latest_version, _ = get_latest_version()
  if latest_version:
    temp = download_binary(latest_version)
    if temp:
      os.rename(temp, MAPD_PATH)
      update_version_param(latest_version)
      return True

  print("ERROR: Could not obtain mapd binary", file=sys.stderr)
  return False

def get_current_version():
  """Get currently installed mapd version from params file"""
  try:
    return (PARAMS_DIR / "MapdVersion").read_text().strip() or "v2.0.2"
  except FileNotFoundError:
    return "v2.0.2"

def get_latest_version():
  """Check GitHub API for latest release version and date"""
  try:
    with urllib.request.urlopen(GITHUB_API_URL, timeout=10) as response:
      data = json.loads(response.read().decode('utf-8'))
      version = data.get('tag_name', '')
      published = data.get('published_at', '')  # e.g. "2026-01-31T03:28:20Z"
      date = published[:10] if published else ''  # "2026-01-31"
      return version, date
  except Exception as e:
    print(f"Error fetching latest version: {e}", file=sys.stderr)
    return "", ""

def backup_current_binary():
  """Backup current mapd binary with version suffix"""
  if not MAPD_PATH.exists():
    print("No existing binary to backup")
    return True

  try:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    current_version = get_current_version()
    backup_path = BACKUP_DIR / f"mapd_{current_version}"

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

    temp_file_path = MAPD_PATH.parent / f"mapd_{version}_temp"

    result = subprocess.run(
      ["curl", "-fSL", "--max-time", "60", "-o", str(temp_file_path), download_url],
      capture_output=True, text=True,
    )
    if result.returncode != 0:
      raise RuntimeError(f"curl failed: {result.stderr.strip()}")

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
    subprocess.run(["pkill", "mapd"], check=False)

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
    ensure_binary()
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
    os.rename(temp_file_path, MAPD_PATH)
    print("Binary replaced successfully")
    return True
  except Exception as e:
    print(f"Binary replacement failed: {e}", file=sys.stderr)
    return False

def update_version_param(version):
  """Update MapdVersion param to new version"""
  try:
    PARAMS_DIR.mkdir(parents=True, exist_ok=True)
    param_path = PARAMS_DIR / "MapdVersion"
    with open(param_path, "w") as f:
      f.write(version)
      os.fsync(f.fileno())

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
  latest, date = get_latest_version()

  if not latest:
    print("ERROR: Could not fetch latest version")
    return False

  date_str = f" ({date})" if date else ""
  if current == latest:
    print(f"UP_TO_DATE: {current}{date_str}")
    return True
  else:
    print(f"UPDATE_AVAILABLE: {current} -> {latest}{date_str}")
    return False

def perform_update():
  """Perform full update: backup, download, stop, replace, start"""
  current_version = get_current_version()
  latest_version, _ = get_latest_version()

  if not latest_version:
    print("ERROR: Could not fetch latest version")
    return False

  if current_version == latest_version:
    print(f"Already up to date: {current_version}")
    return True

  print(f"Updating from {current_version} to {latest_version}...")

  print("Step 1/6: Backing up current binary...")
  if not backup_current_binary():
    return False

  print("Step 2/6: Downloading new binary...")
  temp_file_path = download_binary(latest_version)
  if not temp_file_path:
    return False

  print("Step 3/6: Stopping mapd daemon...")
  if not stop_mapd():
    temp_file_path.unlink(missing_ok=True)
    return False

  print("Step 4/6: Replacing binary...")
  if not replace_binary(temp_file_path):
    start_mapd()
    return False

  print("Step 5/6: Updating version info...")
  if not update_version_param(latest_version):
    pass

  print("Step 6/6: Starting new mapd daemon...")
  if not start_mapd():
    print("WARNING: Failed to start mapd daemon", file=sys.stderr)
    return False

  print(f"Update complete: {current_version} -> {latest_version}")
  return True

if __name__ == "__main__":
  if len(sys.argv) < 2:
    print("Usage: mapd_manager.py [check|update|ensure]")
    sys.exit(1)

  command = sys.argv[1]

  if command == "check":
    success = check_for_updates()
    sys.exit(0 if success else 1)
  elif command == "update":
    success = perform_update()
    sys.exit(0 if success else 1)
  elif command == "ensure":
    success = ensure_binary()
    sys.exit(0 if success else 1)
  else:
    print(f"Unknown command: {command}")
    print("Usage: mapd_manager.py [check|update|ensure]")
    sys.exit(1)
