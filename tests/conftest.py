#!/usr/bin/env python
"""Shared pytest fixtures for multi-project tests (no Project_MJS disk dependency)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def _write_uproject(path: Path, modules: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "FileVersion": 3,
        "EngineAssociation": "5.8",
        "Category": "",
        "Description": "",
        "Modules": [{"Name": name, "Type": "Runtime", "LoadingPhase": "Default"} for name in modules],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _write_source_tree(project_dir: Path, module: str, domains: list[str]) -> None:
    for domain in domains:
        target = project_dir / "Source" / module / domain
        target.mkdir(parents=True, exist_ok=True)
        (target / f"{domain}Subsystem.h").write_text(
            f"#pragma once\nclass U{domain}Subsystem {{}};\n",
            encoding="utf-8",
        )


@pytest.fixture
def shared_config_path(tmp_path, monkeypatch):
    cfg = tmp_path / "unreal-workspace.json"
    monkeypatch.setenv("SHARED_UNREAL_CONFIG", str(cfg))
    return cfg


@pytest.fixture
def demo_game_project(tmp_path, shared_config_path):
    project_dir = tmp_path / "DemoGame"
    uproject = _write_uproject(project_dir / "DemoGame.uproject", ["DemoGame"])
    _write_source_tree(project_dir, "DemoGame", ["Combat"])
    content = project_dir / "Content" / "Shaders" / "MF_Test"
    content.mkdir(parents=True, exist_ok=True)
    shared_config_path.write_text(
        json.dumps({"activeProject": str(uproject)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "projectDir": project_dir,
        "uproject": uproject,
        "module": "DemoGame",
        "domain": "Combat",
        "folder": "MF_Test",
    }


@pytest.fixture
def lyra_style_project(tmp_path, shared_config_path):
    project_dir = tmp_path / "LyraStyleGame"
    uproject = _write_uproject(project_dir / "LyraStyleGame.uproject", ["LyraGame"])
    _write_source_tree(project_dir, "LyraGame", ["Cinematic"])
    shared_config_path.write_text(
        json.dumps({"activeProject": str(uproject)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "projectDir": project_dir,
        "uproject": uproject,
        "module": "LyraGame",
        "projectName": "LyraStyleGame",
        "domain": "Cinematic",
    }


def pytest_configure(config):
    config.addinivalue_line("markers", "smoke: optional local-disk regression against Project_MJS")
