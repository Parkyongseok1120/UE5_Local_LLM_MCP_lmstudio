#!/usr/bin/env python
"""Tests for wrapper_evidence path resolution consistency."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from wrapper_evidence import (  # noqa: E402
    _candidate_source_paths_from_text,
    project_summary_focus_paths,
)


def test_plugin_path_resolved_in_focus_and_candidate_paths(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "Plugins" / "HoldoutPlugin" / "Source" / "HoldoutPlugin" / "Public"
    plugin_dir.mkdir(parents=True)
    header = plugin_dir / "HoldoutPluginComponent.h"
    header.write_text("#pragma once\nclass UHoldoutPluginComponent {};\n", encoding="utf-8")
    rel = "Plugins/HoldoutPlugin/Source/HoldoutPlugin/Public/HoldoutPluginComponent.h"
    focus_text = f"Update {rel} and Source/HoldoutFixture/Public/HoldoutComponent.h"

    focus_paths = project_summary_focus_paths(tmp_path, focus_text)
    candidate_paths = _candidate_source_paths_from_text(tmp_path, focus_text)

    assert rel in focus_paths
    assert any(path.name == "HoldoutPluginComponent.h" for path in candidate_paths)
    assert any("Plugins" in path.parts for path in candidate_paths)


def test_absolute_and_relative_paths_share_regex(tmp_path: Path) -> None:
    header_dir = tmp_path / "Source" / "HoldoutFixture" / "Public"
    header_dir.mkdir(parents=True)
    header = header_dir / "HoldoutComponent.h"
    header.write_text("#pragma once\n", encoding="utf-8")
    rel = "Source/HoldoutFixture/Public/HoldoutComponent.h"
    abs_path = str(header.resolve())
    focus_text = f"Fix {rel} and {abs_path}"

    focus_paths = project_summary_focus_paths(tmp_path, focus_text)
    candidate_paths = _candidate_source_paths_from_text(tmp_path, focus_text)

    assert rel in focus_paths
    assert header.resolve() in candidate_paths
