#!/usr/bin/env python
"""Optional Project_MJS smoke regressions (local disk only)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

PROJECT_MJS = Path.home() / "Documents" / "Github" / "Project_MJS" / "Project_MJS.uproject"


@pytest.mark.smoke
@pytest.mark.skipif(not PROJECT_MJS.is_file(), reason="Project_MJS not installed locally")
def test_smoke_project_mjs_context(monkeypatch):
    from project_context import resolve_active_project_context
    from workspace_paths import save_shared_config, load_shared_config

    cfg = load_shared_config()
    previous = cfg.get("activeProject")
    cfg["activeProject"] = str(PROJECT_MJS)
    save_shared_config(cfg)
    try:
        ctx = resolve_active_project_context()
        assert ctx["projectName"] == "Project_MJS"
    finally:
        cfg["activeProject"] = previous
        save_shared_config(cfg)
