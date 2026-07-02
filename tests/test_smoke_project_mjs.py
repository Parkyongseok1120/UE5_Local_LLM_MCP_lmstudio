#!/usr/bin/env python
"""Optional smoke regressions against a real local UE project (local disk only).

Set the environment variable SMOKE_PROJECT_UPROJECT to the absolute path of a
.uproject file on your machine to enable these tests, e.g.:

    $env:SMOKE_PROJECT_UPROJECT = "C:\\path\\to\\MyGame\\MyGame.uproject"
    pytest -m smoke
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

_env_path = os.environ.get("SMOKE_PROJECT_UPROJECT", "").strip()
SMOKE_PROJECT = Path(_env_path) if _env_path else None


@pytest.mark.smoke
@pytest.mark.skipif(
    SMOKE_PROJECT is None or not SMOKE_PROJECT.is_file(),
    reason="Set SMOKE_PROJECT_UPROJECT env var to a local .uproject file to run smoke tests"
)
def test_smoke_active_project_context(monkeypatch):
    from project_context import resolve_active_project_context
    from workspace_paths import save_shared_config, load_shared_config

    assert SMOKE_PROJECT is not None
    cfg = load_shared_config()
    previous = cfg.get("activeProject")
    cfg["activeProject"] = str(SMOKE_PROJECT)
    save_shared_config(cfg)
    try:
        ctx = resolve_active_project_context()
        assert ctx["projectName"] == SMOKE_PROJECT.stem
    finally:
        cfg["activeProject"] = previous
        save_shared_config(cfg)
