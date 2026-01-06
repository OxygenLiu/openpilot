#!/usr/bin/env python3
"""Async model registry update - runs in background, writes results to Params"""

import subprocess
import json
import sys
from pathlib import Path

# Add openpilot to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from openpilot.common.params import Params


def update_and_check():
    """Update model registry from GitHub and check for new models

    Writes status to Params:
    - ModelUpdateStatus: "checking" | "complete" | "error"
    - ModelUpdateResults: JSON string with update results (when complete)
    - ModelUpdateError: Error message (when error)
    """
    params = Params()
    script_path = Path(__file__).parent / "download_openpilot_models.py"

    try:
        # Set status: checking
        params.put("ModelUpdateStatus", "checking")
        params.remove("ModelUpdateResults")
        params.remove("ModelUpdateError")

        # Run update-registry (may take 10-30 seconds for GitHub API)
        result = subprocess.run(
            ["python3", str(script_path), "update-registry"],
            capture_output=True,
            timeout=30,
            text=True
        )

        if result.returncode != 0:
            params.put("ModelUpdateStatus", "error")
            params.put("ModelUpdateError", "Failed to connect to GitHub")
            return 1

        # Run check-updates
        result = subprocess.run(
            ["python3", str(script_path), "check-updates"],
            capture_output=True,
            timeout=10,
            text=True
        )

        if result.returncode == 0:
            # Parse results and store in Params
            try:
                data = json.loads(result.stdout)
                params.put("ModelUpdateStatus", "complete")
                params.put("ModelUpdateResults", data)  # Params handles JSON serialization
                return 0
            except json.JSONDecodeError:
                params.put("ModelUpdateStatus", "error")
                params.put("ModelUpdateError", "Failed to parse results")
                return 1
        else:
            params.put("ModelUpdateStatus", "error")
            params.put("ModelUpdateError", "Failed to check updates")
            return 1

    except subprocess.TimeoutExpired:
        params.put("ModelUpdateStatus", "error")
        params.put("ModelUpdateError", "GitHub connection timeout")
        return 1
    except Exception as e:
        params.put("ModelUpdateStatus", "error")
        params.put("ModelUpdateError", f"Unexpected error: {str(e)}")
        return 1


if __name__ == '__main__':
    sys.exit(update_and_check())
