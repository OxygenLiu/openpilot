#!/usr/bin/env python3
"""
Smoke-test speedlimitd against a short rlog replay.

Runs on C3 or dev machine. Starts speedlimitd in an isolated
prefix, feeds mapdOut + modelV2 messages (or lets it run with defaults),
then checks:
  1. Process stays alive (no crash / UnknownKeyName / import error)
  2. At least one speedLimitState message is published
  3. Published fields are sane (speedLimit >= 0, valid source enum)

Usage:
  # Auto-find most recent local segment
  python -m pytest selfdrive/mapd/test/test_speedlimitd_replay.py -v

  # Specify a segment via env var
  TEST_SEGMENT=/data/media/0/realdata/00000127--d714c0c0f7--0 \
    python -m pytest selfdrive/mapd/test/test_speedlimitd_replay.py -v

  # Standalone
  python selfdrive/mapd/test/test_speedlimitd_replay.py [segment_path]
"""
import argparse
import os
import signal
import sys
import time
from multiprocessing import Process
from pathlib import Path

import cereal.messaging as messaging
from openpilot.common.prefix import OpenpilotPrefix

REPLAY_DURATION = 10.0   # seconds of log to replay


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def find_segment(hint: str | None = None) -> str:
  """Find a segment with an rlog, using hint or auto-discovery."""
  if hint and os.path.isdir(hint):
    if (Path(hint) / "rlog.zst").exists() or (Path(hint) / "rlog").exists():
      return hint
    raise FileNotFoundError(f"No rlog found in {hint}")

  search_dirs = [
    Path("/data/media/0/realdata"),
    Path.home() / "driving_data" / "data",
  ]
  for search_dir in search_dirs:
    if not search_dir.exists():
      continue
    for seg in sorted(search_dir.iterdir(), key=lambda p: p.name, reverse=True):
      if not seg.is_dir() or not seg.name[0].isdigit():
        continue
      if (seg / "rlog.zst").exists() or (seg / "rlog").exists():
        return str(seg)

  raise FileNotFoundError(
    "No segment with rlog found. Provide a path via TEST_SEGMENT env var "
    "or as CLI argument."
  )


def load_messages(segment_path: str, msg_types: set[str], duration: float):
  """Load up to `duration` seconds of selected messages from an rlog."""
  from openpilot.tools.lib.logreader import LogReader

  rlog = os.path.join(segment_path, "rlog.zst")
  if not os.path.exists(rlog):
    rlog = os.path.join(segment_path, "rlog")

  msgs = []
  start_mono = None
  for msg in LogReader(rlog):
    if start_mono is None:
      start_mono = msg.logMonoTime
    if (msg.logMonoTime - start_mono) / 1e9 > duration:
      break
    if msg.which() in msg_types:
      msgs.append(msg)
  return msgs


def middleware_process():
  """Entry point for the child process."""
  from openpilot.selfdrive.mapd.speedlimitd import main
  main()


def run_replay(segment_path: str | None) -> dict:
  """
  Start speedlimitd in an isolated prefix, optionally replay
  mapdOut + modelV2 from a segment, return results dict.
  """
  segment_path = find_segment(segment_path)

  needed = {"mapdOut", "modelV2"}
  msgs = load_messages(segment_path, needed, REPLAY_DURATION)
  mapd_count = sum(1 for m in msgs if m.which() == "mapdOut")
  model_count = sum(1 for m in msgs if m.which() == "modelV2")

  results = {
    "segment": segment_path,
    "mapd_msgs_fed": mapd_count,
    "model_msgs_fed": model_count,
    "process_alive": False,
    "msgs_received": 0,
    "speed_limits": [],
    "errors": [],
  }

  with OpenpilotPrefix() as pfx:
    # Pre-create msgq directory so child SubSocket.connect() succeeds
    msgq_path = os.path.join("/dev/shm", f"msgq_{pfx.prefix}")
    os.makedirs(msgq_path, exist_ok=True)

    # Create publishers BEFORE child — socket files must exist for SubMaster
    pm = messaging.PubMaster(["mapdOut", "modelV2"])
    pm.send("mapdOut", messaging.new_message("mapdOut"))
    pm.send("modelV2", messaging.new_message("modelV2"))
    time.sleep(0.1)

    # Start middleware
    proc = Process(target=middleware_process, daemon=True)
    proc.start()
    time.sleep(2.0)

    if not proc.is_alive():
      results["errors"].append(f"Process crashed on startup (exit code {proc.exitcode})")
      return results
    results["process_alive"] = True

    # Subscribe to output
    sm = messaging.SubMaster(["speedLimitState"])

    # Replay messages at ~5x speed
    all_msgs = sorted(msgs, key=lambda m: m.logMonoTime)
    if all_msgs:
      start = time.monotonic()
      log_start = all_msgs[0].logMonoTime
      for msg in all_msgs:
        sleep_time = ((msg.logMonoTime - log_start) / 1e9 / 5.0) - (time.monotonic() - start)
        if sleep_time > 0:
          time.sleep(sleep_time)
        pm.send(msg.which(), msg.as_builder())
    else:
      # No input data — just let middleware tick a few times with defaults
      time.sleep(3.0)

    # Collect output (middleware runs at 1 Hz, wait up to 5 s)
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
      sm.update(100)
      if sm.updated["speedLimitState"]:
        sls = sm["speedLimitState"]
        results["msgs_received"] += 1
        results["speed_limits"].append({
          "speedLimit": sls.speedLimit,
          "source": sls.source.raw,
          "confidence": sls.confidence,
          "confirmed": sls.confirmed,
        })
        break
      if not proc.is_alive():
        results["errors"].append(f"Process died during replay (exit code {proc.exitcode})")
        results["process_alive"] = False
        break

    # Cleanup
    if proc.is_alive():
      os.kill(proc.pid, signal.SIGTERM)
      proc.join(timeout=3.0)
      if proc.is_alive():
        os.kill(proc.pid, signal.SIGKILL)
        proc.join(timeout=2.0)

  return results


# ---------------------------------------------------------------------------
# pytest — single replay, multiple assertions
# ---------------------------------------------------------------------------
_cached_results = None

def _get_results():
  global _cached_results
  if _cached_results is None:
    _cached_results = run_replay(os.environ.get("TEST_SEGMENT"))
  return _cached_results


class TestSpeedlimitdReplay:

  def test_process_stays_alive(self):
    """Middleware must not crash (catches UnknownKeyName, ImportError, etc.)."""
    r = _get_results()
    assert r["process_alive"], f"speedlimitd crashed: {r['errors']}"

  def test_publishes_speed_limit_state(self):
    """Middleware must publish at least one speedLimitState message."""
    r = _get_results()
    assert r["process_alive"], f"Process crashed: {r['errors']}"
    assert r["msgs_received"] > 0, (
      "No speedLimitState published. "
      f"Fed {r['mapd_msgs_fed']} mapdOut + {r['model_msgs_fed']} modelV2."
    )

  def test_speed_limit_values_sane(self):
    """Published speed limits must be non-negative with valid source."""
    r = _get_results()
    for sls in r["speed_limits"]:
      assert sls["speedLimit"] >= 0, f"Negative speed limit: {sls['speedLimit']}"
      assert sls["source"] in (0, 1, 2), f"Invalid source: {sls['source']}"
      assert 0.0 <= sls["confidence"] <= 1.0, f"Invalid confidence: {sls['confidence']}"


# ---------------------------------------------------------------------------
# Standalone
# ---------------------------------------------------------------------------
if __name__ == "__main__":
  parser = argparse.ArgumentParser(description="Smoke-test speedlimitd")
  parser.add_argument("segment", nargs="?", default=None, help="Segment directory path")
  args = parser.parse_args()

  results = run_replay(args.segment)

  print("\n" + "=" * 60)
  print(f"Segment:           {results['segment']}")
  print(f"Process alive:     {results['process_alive']}")
  print(f"Messages fed:      {results['mapd_msgs_fed']} mapdOut + {results['model_msgs_fed']} modelV2")
  print(f"Messages received: {results['msgs_received']} speedLimitState")
  if results["speed_limits"]:
    sls = results["speed_limits"][0]
    src = {0: "osmMaxspeed", 1: "yoloDetection", 2: "roadTypeInference"}
    print(f"  speedLimit={sls['speedLimit']:.0f} km/h, source={src.get(sls['source'], '?')}, "
          f"confidence={sls['confidence']:.2f}, confirmed={sls['confirmed']}")
  if results["errors"]:
    print(f"ERRORS: {results['errors']}")
  print("=" * 60)

  ok = results["process_alive"] and not results["errors"] and results["msgs_received"] > 0
  print("PASS" if ok else "FAIL")
  sys.exit(0 if ok else 1)
