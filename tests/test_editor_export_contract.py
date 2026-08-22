#!/usr/bin/env python
"""Exact-project completion contract regressions for Editor export ingestion."""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from collect_editor_metadata import merge_export_into_raw  # noqa: E402
from editor_export_contract import (  # noqa: E402
    EditorExportContractError,
    completed_export_files,
)
from editor_capture_state import record_completed_capture  # noqa: E402
from editor_metadata_status import editor_metadata_status  # noqa: E402
from editor_sync_decision import raw_newest_mtime  # noqa: E402
from ingest_editor_exports import discover_exports  # noqa: E402


def _manifest(project: Path, export_file: Path, kind: str = "material") -> None:
    content = export_file.read_bytes()
    (export_file.parent / "export_manifest.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "complete": True,
                "runId": "completed-test-run",
                "capturedAt": time.time(),
                "projectFile": str(project.resolve()),
                "exports": [
                    {
                        "file": export_file.name,
                        "kind": kind,
                        "sizeBytes": len(content),
                        "sha256": hashlib.sha256(content).hexdigest(),
                        "rowCount": sum(1 for line in content.splitlines() if line.strip()),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _project(tmp_path: Path, name: str) -> Path:
    descriptor = tmp_path / name / f"{name}.uproject"
    descriptor.parent.mkdir()
    descriptor.write_text("{}", encoding="utf-8")
    return descriptor


def test_manifest_rejects_exports_captured_from_another_project(tmp_path: Path) -> None:
    project_a = _project(tmp_path, "ProjectA")
    project_b = _project(tmp_path, "ProjectB")
    export = tmp_path / "exports" / "materials.jsonl"
    export.parent.mkdir()
    export.write_text('{"asset_path":"/Game/M_A"}\n', encoding="utf-8")
    _manifest(project_a, export)

    with pytest.raises(EditorExportContractError, match="project identity"):
        completed_export_files(export.parent, project_b)


def test_manifest_rejects_file_changed_after_completion(tmp_path: Path) -> None:
    project = _project(tmp_path, "Project")
    export = tmp_path / "exports" / "materials.jsonl"
    export.parent.mkdir()
    export.write_text('{"asset_path":"/Game/M_A"}\n', encoding="utf-8")
    _manifest(project, export)
    export.write_text('{"asset_path":"/Game/M_Tampered"}\n', encoding="utf-8")

    with pytest.raises(EditorExportContractError, match="changed after completion"):
        completed_export_files(export.parent, project)


def test_manifest_lists_only_current_run_not_stale_neighbor_files(tmp_path: Path) -> None:
    project = _project(tmp_path, "Project")
    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    material = export_dir / "materials.jsonl"
    material.write_text("", encoding="utf-8")
    (export_dir / "blueprints.jsonl").write_text(
        '{"asset_path":"/Game/BP_FromOlderRun"}\n', encoding="utf-8"
    )
    _manifest(project, material)

    assert discover_exports(
        export_dir,
        project_file=project,
        require_manifest=True,
    ) == [(material.resolve(), "material")]


def test_completed_empty_kind_authoritatively_removes_only_selected_project_rows(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path, "Project")
    other = _project(tmp_path, "Other")
    export = tmp_path / "exports" / "materials.jsonl"
    export.parent.mkdir()
    export.write_text("", encoding="utf-8")
    _manifest(project, export)
    completed = completed_export_files(export.parent, project)[1]

    raw = tmp_path / "raw_material_metadata.jsonl"
    seed = tmp_path / "seed.jsonl"
    seed.write_text('{"asset_path":"/Game/M_Project","title":"Project"}\n', encoding="utf-8")
    merge_export_into_raw(seed, "material", project.stem, raw, project_root=str(project.parent))
    seed.write_text('{"asset_path":"/Game/M_Other","title":"Other"}\n', encoding="utf-8")
    merge_export_into_raw(seed, "material", other.stem, raw, project_root=str(other.parent))

    ingested, _replaced = merge_export_into_raw(
        completed[0][0],
        completed[0][1],
        project.stem,
        raw,
        project_root=str(project.parent),
    )
    rows = [json.loads(line) for line in raw.read_text(encoding="utf-8").splitlines()]
    assert ingested == 0
    assert [row["metadata"]["project"] for row in rows] == [other.stem]


def test_empty_completion_fact_survives_as_fresh_project_kind_state(tmp_path: Path) -> None:
    project = _project(tmp_path, "Project")
    export = tmp_path / "exports" / "materials.jsonl"
    export.parent.mkdir()
    export.write_text("", encoding="utf-8")
    _manifest(project, export)
    manifest, _files = completed_export_files(export.parent, project)
    index = tmp_path / "index"
    index.mkdir()
    (index / "raw_material_metadata.jsonl").write_text("", encoding="utf-8")
    record_completed_capture(index, manifest)

    status = editor_metadata_status(index, project, stale_after_hours=1000.0)
    material = status["files"]["material"]
    assert material["exists"] is True
    assert material["authoritativeEmpty"] is True
    assert material["captureProvenanceKnown"] is True
    assert "material" not in status["missingKinds"]
    assert raw_newest_mtime(index, ("material",), project) == pytest.approx(
        manifest["capturedAt"]
    )
