from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from editor_sync_capture import EditorSyncCapture  # noqa: E402
from editor_sync_context import EditorSyncContext, resolve_editor_sync_context  # noqa: E402
from editor_sync_coordinator import sync_editor_context  # noqa: E402
from sync_editor_metadata import refresh_editor_metadata  # noqa: E402


def _project(path: Path, version: str = "5.8") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"EngineAssociation": version}), encoding="utf-8")
    return path


def _completed_export(export_dir: Path, project: Path, *, manifest_project: Path | None = None) -> None:
    export_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"asset_path": "/Game/BP_Direct", "title": "Direct"}) + "\n"
    exported = export_dir / "blueprints.jsonl"
    exported.write_text(payload, encoding="utf-8")
    content = exported.read_bytes()
    (export_dir / "export_manifest.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "complete": True,
                "runId": "direct-sync-test",
                "capturedAt": time.time(),
                "projectFile": str((manifest_project or project).resolve()),
                "scope": "all",
                "exports": [
                    {
                        "file": exported.name,
                        "kind": "blueprint",
                        "sizeBytes": len(content),
                        "sha256": hashlib.sha256(content).hexdigest(),
                        "rowCount": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _context(tmp_path: Path, project: Path, export_dir: Path) -> EditorSyncContext:
    index_dir = tmp_path / "data" / "unreal58"
    index_dir.mkdir(parents=True, exist_ok=True)
    return EditorSyncContext(
        workspace=tmp_path.resolve(),
        index_dir=index_dir.resolve(),
        project_file=project.resolve(),
        project_root=project.parent.resolve(),
        project_name=project.stem,
        export_dir=export_dir.resolve(),
    )


def test_context_resolves_exact_project_index_and_local_export_without_shared_state(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path / "Game" / "Game.uproject")
    context = resolve_editor_sync_context(
        workspace=tmp_path,
        index_dir=tmp_path / "data" / "unreal58",
        project_file=project,
        project_name="game",
    )

    assert context.project_file == project.resolve()
    assert context.project_name == "Game"
    assert context.index_dir == (tmp_path / "data" / "unreal58").resolve()
    assert context.export_dir == (
        project.parent / "Saved" / "LmStudioMetadataExports"
    ).resolve()


def test_validated_capture_is_immutable_and_removed_at_lifecycle_exit(tmp_path: Path) -> None:
    project = _project(tmp_path / "Game" / "Game.uproject")
    export_dir = tmp_path / "exports"
    _completed_export(export_dir, project)
    context = _context(tmp_path, project, export_dir)

    with EditorSyncCapture.for_context(context) as capture:
        snapshot = capture.ensure_snapshot(expected_scope="all")
        assert snapshot is not None and snapshot.is_dir()
        original = (snapshot / "blueprints.jsonl").read_bytes()
        (export_dir / "blueprints.jsonl").write_text("changed after capture\n", encoding="utf-8")
        assert (snapshot / "blueprints.jsonl").read_bytes() == original

    assert not snapshot.exists()


def test_capture_rejects_cross_project_manifest_and_never_launches_without_permission(
    tmp_path: Path,
) -> None:
    selected = _project(tmp_path / "Selected" / "Selected.uproject")
    foreign = _project(tmp_path / "Foreign" / "Foreign.uproject")
    export_dir = tmp_path / "exports"
    _completed_export(export_dir, selected, manifest_project=foreign)
    capture = EditorSyncCapture.for_context(_context(tmp_path, selected, export_dir))

    assert capture.ensure_snapshot() is None
    assert capture.error and capture.error["errorCode"] == "EDITOR_EXPORT_CONTRACT_INVALID"
    denied = capture.run_export(
        launch_authorized=False,
        content_path=None,
        export_scope=None,
        export_mode="auto",
    )
    assert denied["errorCode"] == "EDITOR_LAUNCH_NOT_AUTHORIZED"
    assert not list(tmp_path.glob(".direct-editor-snapshot-*"))


def test_coordinator_fails_closed_on_foreign_manifest_without_index_mutation(
    tmp_path: Path,
) -> None:
    selected = _project(tmp_path / "Selected" / "Selected.uproject")
    foreign = _project(tmp_path / "Foreign" / "Foreign.uproject")
    export_dir = tmp_path / "exports"
    _completed_export(export_dir, selected, manifest_project=foreign)
    context = _context(tmp_path, selected, export_dir)

    result = sync_editor_context(
        context,
        rebuild_index=False,
        force_ingest=False,
        auto_export=False,
        force_export=False,
        content_path=None,
        export_scope=None,
        export_mode="auto",
    )

    assert result["ok"] is False
    assert result["exportResult"] is None
    assert result["transactionError"]["errorCode"] == "EDITOR_EXPORT_CONTRACT_INVALID"
    assert not (context.index_dir / "rag.sqlite").exists()


def test_coordinator_runs_real_snapshot_ingest_and_atomic_stage_commit(tmp_path: Path) -> None:
    project = _project(tmp_path / "Game" / "Game.uproject")
    export_dir = tmp_path / "exports"
    _completed_export(export_dir, project)
    context = _context(tmp_path, project, export_dir)
    context = EditorSyncContext(
        workspace=ROOT,
        index_dir=context.index_dir,
        project_file=context.project_file,
        project_root=context.project_root,
        project_name=context.project_name,
        export_dir=context.export_dir,
    )

    result = sync_editor_context(
        context,
        rebuild_index=False,
        force_ingest=True,
        auto_export=False,
        force_export=False,
        content_path=None,
        export_scope="all",
        export_mode="auto",
    )

    assert result["ok"] is True, result
    assert result["stageCommitted"] is True
    assert result["ingest"]["ok"] is True
    rows = [
        json.loads(line)
        for line in (context.index_dir / "raw_blueprint_metadata.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert len(rows) == 1
    assert rows[0]["metadata"]["project"] == project.stem
    assert Path(rows[0]["metadata"]["project_root"]).resolve() == project.parent.resolve()
    assert not list(context.index_dir.parent.glob(".direct-editor-snapshot-*"))


def test_explicit_refresh_rejects_missing_project_before_editor_launch(tmp_path: Path) -> None:
    result = refresh_editor_metadata(
        workspace=tmp_path,
        project_file=tmp_path / "Missing.uproject",
        force=True,
    )

    assert result["ok"] is False
    assert result["errorCode"] == "PROJECT_SELECTOR_REQUIRED"
    assert result["stageCommitted"] is False
