#!/usr/bin/env python
"""Tests for stable project identity helpers."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from project_identity import project_identity, resolve_uproject  # noqa: E402


def test_resolve_uproject_from_file(tmp_path):
    uproject = tmp_path / "DemoGame.uproject"
    uproject.write_text(json.dumps({"Modules": [{"Name": "DemoGame"}]}), encoding="utf-8")

    resolved = resolve_uproject(uproject)

    assert resolved == uproject.resolve()


def test_project_identity_is_stable_for_same_uproject(tmp_path):
    uproject = tmp_path / "DemoGame.uproject"
    uproject.write_text(json.dumps({"Modules": [{"Name": "DemoGame"}, {"Name": "DemoEditor"}]}), encoding="utf-8")

    first = project_identity(uproject, engine_version="5.8")
    second = project_identity(uproject, engine_version="5.8")

    assert first["ok"] is True
    assert first["projectName"] == "DemoGame"
    assert first["projectId"] == second["projectId"]
    assert first["modules"] == ["DemoEditor", "DemoGame"]
