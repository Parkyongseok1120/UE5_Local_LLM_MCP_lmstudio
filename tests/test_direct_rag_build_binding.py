#!/usr/bin/env python
"""Portable build engine-provenance regressions."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from direct_rag_build_binding import resolve_build_binding  # noqa: E402
from direct_rag_raw_provenance import validate_raw_provenance  # noqa: E402


def test_explicit_engine_binding_must_be_supplied_as_a_pair(tmp_path: Path) -> None:
    result = resolve_build_binding(tmp_path, None, "5.8", None)
    assert result["ok"] is False
    assert result["errorCode"] == "RAG_ENGINE_BINDING_INCOMPLETE"


def test_exact_project_supplies_custom_engine_provenance(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import direct_rag_project_engine

    project = tmp_path / "Game" / "Game.uproject"
    project.parent.mkdir()
    project.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        direct_rag_project_engine,
        "project_engine_version",
        lambda descriptor, workspace: {
            "ok": True,
            "engineVersion": "5.8",
            "engineAssociation": "StudioFork-A",
            "engineRoot": str(tmp_path / "UE-Studio"),
        },
    )

    result = resolve_build_binding(tmp_path, project, None, None)

    assert result["ok"] is True
    assert result["engineVersion"] == "5.8"
    assert result["engineAssociation"] == "StudioFork-A"
    assert result["projectFile"] == str(project.resolve())


def test_portable_build_forwards_exact_project_selector() -> None:
    launcher = (ROOT / "scripts" / "portable_rag.ps1").read_text(encoding="utf-8")
    assert launcher.count('@("--project", $ProjectFile)') >= 2


def test_engine_raw_root_cannot_be_relabelled_by_project_binding(
    tmp_path: Path,
) -> None:
    index = tmp_path / "index"
    index.mkdir()
    expected_engine = tmp_path / "UE_Expected"
    wrong_source = tmp_path / "UE_Wrong" / "Engine" / "Source"
    source_file = wrong_source / "Runtime" / "Core" / "Bad.cpp"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("void Bad() {}", encoding="utf-8")
    (index / "raw_source.jsonl").write_text(
        __import__("json").dumps(
            {
                "source": "unreal_source",
                "path": str(source_file),
                "metadata": {"root": str(wrong_source)},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = validate_raw_provenance(
        index_dir=index,
        workspace=tmp_path,
        engine_version="5.8",
        engine_association="StudioFork-A",
        engine_root=str(expected_engine),
    )

    assert result["ok"] is False
    assert result["errorCode"] == "RAG_RAW_ENGINE_ROOT_MISMATCH"


def test_project_raw_rows_must_match_the_target_engine_binding(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import json
    import direct_rag_project_engine

    index = tmp_path / "index"
    index.mkdir()
    project = tmp_path / "OtherGame" / "OtherGame.uproject"
    project.parent.mkdir()
    project.write_text("{}", encoding="utf-8")
    (index / "raw_projects.jsonl").write_text(
        json.dumps(
            {
                "source": "unreal_project_text",
                "metadata": {
                    "project": project.stem,
                    "project_root": str(project.parent),
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        direct_rag_project_engine,
        "project_engine_version",
        lambda *_args: {
            "ok": True,
            "engineVersion": "5.7",
            "engineAssociation": "5.7",
            "engineRoot": str(tmp_path / "UE_5.7"),
        },
    )

    result = validate_raw_provenance(
        index_dir=index,
        workspace=tmp_path,
        engine_version="5.8",
        engine_association="5.8",
    )

    assert result["ok"] is False
    assert result["errorCode"] == "RAG_RAW_PROJECT_ENGINE_MISMATCH"
