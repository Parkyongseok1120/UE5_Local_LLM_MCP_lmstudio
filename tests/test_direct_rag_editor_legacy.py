from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from direct_rag_editor_legacy import (  # noqa: E402
    legacy_descriptor_roots,
    migrate_legacy_editor_rows,
)


def _project(root: Path, name: str = "Demo") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    descriptor = root / f"{name}.uproject"
    descriptor.write_text("{}", encoding="utf-8")
    return descriptor


def _descriptor_row(project: Path) -> dict:
    return {
        "id": str(project),
        "source": "unreal_project_text",
        "path": str(project.resolve()),
        "metadata": {
            "project": project.stem,
            "project_root": str(project.parent.resolve()),
            "relative_path": project.name,
            "extension": ".uproject",
        },
    }


def _editor_row(project: str = "Demo", *, root: str = "") -> dict:
    metadata = {"project": project, "asset_path": "/Game/BP_Demo"}
    if root:
        metadata["project_root"] = root
    return {
        "id": "asset",
        "source": "unreal_blueprint_metadata",
        "path": "/Game/BP_Demo",
        "metadata": metadata,
    }


def _write_jsonl(path: Path, *rows: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _read_one(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8").strip())


def test_unique_descriptor_inventory_migrates_legacy_editor_row(tmp_path: Path) -> None:
    project = _project(tmp_path / "Owner")
    _write_jsonl(tmp_path / "raw_projects.jsonl", _descriptor_row(project))
    raw = tmp_path / "raw_blueprint_metadata.jsonl"
    _write_jsonl(raw, _editor_row())

    proof = legacy_descriptor_roots(tmp_path, project)
    result = migrate_legacy_editor_rows(tmp_path, project, proof)

    assert result["ok"] is True
    assert result["migratedRows"] == 1
    assert result["changedFiles"] == [raw.name]
    assert _read_one(raw)["metadata"] == {
        "project": "Demo",
        "asset_path": "/Game/BP_Demo",
        "project_root": str(project.parent.resolve()),
    }


def test_same_name_clone_inventory_preserves_ambiguous_legacy_row(tmp_path: Path) -> None:
    selected = _project(tmp_path / "Selected")
    clone = _project(tmp_path / "Clone")
    _write_jsonl(
        tmp_path / "raw_projects.jsonl",
        _descriptor_row(selected),
        _descriptor_row(clone),
    )
    raw = tmp_path / "raw_blueprint_metadata.jsonl"
    _write_jsonl(raw, _editor_row())
    before = raw.read_bytes()

    proof = legacy_descriptor_roots(tmp_path, selected)
    result = migrate_legacy_editor_rows(tmp_path, selected, proof)

    assert result["ok"] is False
    assert result["reason"] == "descriptor_inventory_not_unique"
    assert raw.read_bytes() == before


def test_absent_descriptor_inventory_preserves_legacy_row(tmp_path: Path) -> None:
    project = _project(tmp_path / "Owner")
    raw = tmp_path / "raw_blueprint_metadata.jsonl"
    _write_jsonl(raw, _editor_row())
    before = raw.read_bytes()

    proof = legacy_descriptor_roots(tmp_path, project)
    result = migrate_legacy_editor_rows(tmp_path, project, proof)

    assert result["ok"] is False
    assert raw.read_bytes() == before


def test_rooted_and_foreign_rows_are_unchanged(tmp_path: Path) -> None:
    project = _project(tmp_path / "Owner")
    _write_jsonl(tmp_path / "raw_projects.jsonl", _descriptor_row(project))
    rooted = _editor_row(root=str(project.parent.resolve()))
    foreign = _editor_row("Another")
    raw = tmp_path / "raw_blueprint_metadata.jsonl"
    _write_jsonl(raw, rooted, foreign)
    before = raw.read_bytes()

    proof = legacy_descriptor_roots(tmp_path, project)
    result = migrate_legacy_editor_rows(tmp_path, project, proof)

    assert result["ok"] is True
    assert result["migratedRows"] == 0
    assert raw.read_bytes() == before


def test_malformed_editor_jsonl_fails_without_replacing_file(tmp_path: Path) -> None:
    project = _project(tmp_path / "Owner")
    _write_jsonl(tmp_path / "raw_projects.jsonl", _descriptor_row(project))
    raw = tmp_path / "raw_blueprint_metadata.jsonl"
    raw.write_text('{"broken":\n', encoding="utf-8")
    before = raw.read_bytes()

    with pytest.raises(RuntimeError, match="Invalid legacy Editor JSONL"):
        proof = legacy_descriptor_roots(tmp_path, project)
        migrate_legacy_editor_rows(tmp_path, project, proof)

    assert raw.read_bytes() == before


def test_incoming_descriptor_cannot_retroactively_authorize_legacy_rows(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path / "Owner")
    raw = tmp_path / "raw_blueprint_metadata.jsonl"
    _write_jsonl(raw, _editor_row())
    before = raw.read_bytes()
    proof = legacy_descriptor_roots(tmp_path, project)

    _write_jsonl(tmp_path / "raw_projects.jsonl", _descriptor_row(project))
    result = migrate_legacy_editor_rows(tmp_path, project, proof)

    assert result["ok"] is False
    assert result["reason"] == "descriptor_inventory_not_unique"
    assert raw.read_bytes() == before
