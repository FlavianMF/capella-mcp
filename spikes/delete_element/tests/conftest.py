"""Spike helpers are plain modules next to run_spike.py, not a package."""

import sys
from pathlib import Path

SPIKE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = SPIKE_DIR.parent.parent
sys.path.insert(0, str(SPIKE_DIR))
sys.path.insert(0, str(REPO_ROOT / "src"))
