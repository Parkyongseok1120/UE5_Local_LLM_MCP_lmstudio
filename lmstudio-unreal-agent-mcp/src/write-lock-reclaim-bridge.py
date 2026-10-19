"""Compatibility entry point for the shared stale-lock helper."""
from pathlib import Path
import runpy

globals().update(runpy.run_path(str(Path(__file__).resolve().parents[2] / "shared-tool-core" / "write-lock-reclaim-bridge.py"), run_name=__name__))
