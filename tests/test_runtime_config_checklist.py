#!/usr/bin/env python
"""Tests for Unreal runtime/config readiness checks."""

from __future__ import annotations

import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]
SCRIPTS = WORKSPACE / "scripts"
sys.path.insert(0, str(SCRIPTS))

from runtime_config_checklist import check_runtime_config  # noqa: E402


def _make_project(tmp_path: Path) -> Path:
    root = tmp_path / "Demo"
    (root / "Config").mkdir(parents=True)
    (root / "Source" / "Demo").mkdir(parents=True)
    (root / "Content").mkdir()
    return root


def test_missing_gamemode_is_warning_without_custom_gamemode(tmp_path):
    root = _make_project(tmp_path)
    (root / "Source" / "Demo" / "PlainActor.cpp").write_text(
        "void APlainActor::BeginPlay() {}\n",
        encoding="utf-8",
    )

    result = check_runtime_config(root)

    assert result["ok"] is True
    assert result["details"]["customGameModeDetected"] is False
    assert not result["issues"]
    assert any("No default GameMode configured" in warning for warning in result["warnings"])


def test_missing_gamemode_is_issue_with_custom_gamemode(tmp_path):
    root = _make_project(tmp_path)
    (root / "Source" / "Demo" / "DemoGameMode.cpp").write_text(
        "ADemoGameMode::ADemoGameMode() {}\n",
        encoding="utf-8",
    )

    result = check_runtime_config(root)

    assert result["ok"] is False
    assert result["details"]["customGameModeDetected"] is True
    assert any("No GlobalDefaultGameMode" in issue for issue in result["issues"])


def test_configured_gamemode_passes_with_custom_gamemode(tmp_path):
    root = _make_project(tmp_path)
    (root / "Source" / "Demo" / "DemoGameMode.h").write_text(
        "class ADemoGameMode : public AGameModeBase {};\n",
        encoding="utf-8",
    )
    (root / "Config" / "DefaultEngine.ini").write_text(
        "[/Script/EngineSettings.GameMapsSettings]\n"
        "GlobalDefaultGameMode=/Script/Demo.DemoGameMode\n",
        encoding="utf-8",
    )

    result = check_runtime_config(root)

    assert result["ok"] is True
    assert any("GameMode configured" in item for item in result["passed"])
