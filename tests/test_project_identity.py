#!/usr/bin/env python
"""Tests for stable project identity helpers."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import project_identity as project_identity_module  # noqa: E402
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


def test_project_identity_uses_injected_host_case_rules(tmp_path, monkeypatch):
    upper = tmp_path / "Upper" / "Demo.uproject"
    lower = tmp_path / "upper" / "Demo.uproject"

    monkeypatch.setattr(project_identity_module, "resolve_uproject", lambda _value: upper)
    posix_upper = project_identity_module.project_identity("ignored", host_platform="linux")
    windows_upper = project_identity_module.project_identity("ignored", host_platform="win32")
    monkeypatch.setattr(project_identity_module, "resolve_uproject", lambda _value: lower)
    posix_lower = project_identity_module.project_identity("ignored", host_platform="linux")
    windows_lower = project_identity_module.project_identity("ignored", host_platform="win32")

    assert posix_upper["projectId"] != posix_lower["projectId"]
    assert windows_upper["projectId"] == windows_lower["projectId"]


def test_project_identity_does_not_merge_unicode_casefold_project_roots(tmp_path):
    composed = tmp_path / "\u0130Project" / "Demo.uproject"
    decomposed = tmp_path / "I\u0307Project" / "Demo.uproject"
    for project_file in (composed, decomposed):
        project_file.parent.mkdir(parents=True)
        project_file.write_text('{"Modules": [{"Name": "Demo"}]}', encoding="utf-8")
    assert str(composed).casefold() == str(decomposed).casefold()

    for host_platform in ("linux", "win32"):
        composed_id = project_identity(
            composed,
            engine_version="5.8",
            host_platform=host_platform,
        )["projectId"]
        decomposed_id = project_identity(
            decomposed,
            engine_version="5.8",
            host_platform=host_platform,
        )["projectId"]
        assert composed_id != decomposed_id
