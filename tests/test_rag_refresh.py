#!/usr/bin/env python
"""Direct-only RAG refresh regressions."""

from __future__ import annotations

import json
import hashlib
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import rag_refresh  # noqa: E402


def _complete_editor_export(project: Path, export_file: Path, kind: str) -> None:
    content = export_file.read_bytes()
    row_count = sum(1 for line in content.splitlines() if line.strip())
    (export_file.parent / "export_manifest.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "complete": True,
                "runId": "test-export-run",
                "capturedAt": time.time(),
                "projectFile": str(project.resolve()),
                "scope": "all",
                "exports": [
                    {
                        "file": export_file.name,
                        "kind": kind,
                        "sizeBytes": len(content),
                        "sha256": hashlib.sha256(content).hexdigest(),
                        "rowCount": row_count,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _patch_cache_dependencies(monkeypatch) -> None:
    import direct_rag_freshness
    import project_context

    monkeypatch.setattr(project_context, "clear_project_context_cache", lambda: None)
    monkeypatch.setattr(direct_rag_freshness, "invalidate_freshness_cache", lambda: None)


def _patch_editor_dependencies(monkeypatch, tmp_path: Path) -> None:
    import workspace_paths

    monkeypatch.setattr(workspace_paths, "editor_export_dir", lambda: tmp_path / "exports")
    monkeypatch.setattr(
        workspace_paths,
        "resolve_index_path",
        lambda *_args: tmp_path / "index" / "rag.sqlite",
    )
    monkeypatch.setattr(workspace_paths, "load_shared_config", lambda: {})


def test_refresh_without_active_project(monkeypatch, tmp_path: Path) -> None:
    import workspace_paths

    monkeypatch.setattr(workspace_paths, "resolve_active_project_path", lambda *_args: None)
    payload = rag_refresh.refresh_active_project(workspace=tmp_path)

    assert payload["ok"] is False
    assert payload["errorCode"] == "NO_ACTIVE_PROJECT"


def test_refresh_rejects_nonstandard_index_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import active_project_sync

    project = tmp_path / "Demo.uproject"
    project.write_text("{}", encoding="utf-8")
    called = False

    def should_not_sync(**_kwargs):
        nonlocal called
        called = True
        return {"ok": True}

    monkeypatch.setattr(active_project_sync, "sync_active_project", should_not_sync)
    custom = tmp_path / "custom.sqlite"
    payload = rag_refresh.refresh_active_project(
        workspace=tmp_path,
        project=project,
        index_path=custom,
    )

    assert payload["ok"] is False
    assert payload["errorCode"] == "UNSUPPORTED_INDEX_FILENAME"
    assert called is False
    assert not custom.exists()


def test_project_source_refresh_is_direct_and_reports_progress(monkeypatch, tmp_path: Path) -> None:
    import active_project_sync

    project = tmp_path / "Demo.uproject"
    project.write_text("{}", encoding="utf-8")
    _patch_cache_dependencies(monkeypatch)
    observed: list[dict] = []
    messages: list[str] = []

    def _sync(**kwargs):
        observed.append(kwargs)
        return {"ok": True, "steps": [{"name": "incremental_build.py", "ok": True}]}

    monkeypatch.setattr(active_project_sync, "sync_active_project", _sync)
    payload = rag_refresh.refresh_active_project(
        scope="project_source",
        workspace=tmp_path,
        project=project,
        progress=messages.append,
    )

    assert payload["ok"] is True
    assert payload["editorLaunchAllowed"] is False
    assert observed[0]["project"] == project.resolve()
    assert "direct" not in observed[0]
    assert any("project_source" in message for message in messages)
    assert messages[-1] == "invalidating project-scoped caches"
    assert payload["cacheInvalidated"] == ["project_context", "direct_rag_freshness"]


def test_editor_refresh_without_permission_only_ingests_existing_exports(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import sync_editor_metadata

    project = tmp_path / "Demo.uproject"
    project.write_text("{}", encoding="utf-8")
    _patch_cache_dependencies(monkeypatch)
    _patch_editor_dependencies(monkeypatch, tmp_path)
    calls = {"sync": 0, "refresh": 0}

    def _sync(**kwargs):
        calls["sync"] += 1
        assert kwargs["auto_export"] is False
        assert kwargs["force_export"] is False
        return {"ok": True, "exportResult": None, "ingest": {"ok": True}}

    def _refresh(**_kwargs):
        calls["refresh"] += 1
        return {"ok": True}

    monkeypatch.setattr(sync_editor_metadata, "sync_editor_metadata", _sync)
    monkeypatch.setattr(sync_editor_metadata, "refresh_editor_metadata", _refresh)
    payload = rag_refresh.refresh_active_project(
        scope="editor_metadata",
        workspace=tmp_path,
        project=project,
    )

    assert payload["ok"] is True
    assert payload["editorLaunchAllowed"] is False
    assert calls == {"sync": 1, "refresh": 0}


def test_editor_refresh_launches_only_after_explicit_permission(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import sync_editor_metadata

    project = tmp_path / "Demo.uproject"
    project.write_text("{}", encoding="utf-8")
    _patch_cache_dependencies(monkeypatch)
    _patch_editor_dependencies(monkeypatch, tmp_path)
    calls = {"sync": 0, "refresh": 0}

    def _sync(**_kwargs):
        calls["sync"] += 1
        return {"ok": True}

    def _refresh(**_kwargs):
        calls["refresh"] += 1
        return {"ok": True, "exportResult": {"ok": True}}

    monkeypatch.setattr(sync_editor_metadata, "sync_editor_metadata", _sync)
    monkeypatch.setattr(sync_editor_metadata, "refresh_editor_metadata", _refresh)
    payload = rag_refresh.refresh_active_project(
        scope="editor_metadata",
        workspace=tmp_path,
        project=project,
        allow_editor_launch=True,
    )

    assert payload["ok"] is True
    assert payload["editorLaunchAllowed"] is True
    assert calls == {"sync": 0, "refresh": 1}


def test_all_scope_uses_one_exact_snapshot_and_one_source_generation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import active_project_sync
    import workspace_paths

    project = tmp_path / "Game" / "Game.uproject"
    project.parent.mkdir(parents=True)
    project.write_text(json.dumps({"EngineAssociation": "5.8"}), encoding="utf-8")
    exports = project.parent / "Saved" / "LmStudioMetadataExports"
    exports.mkdir(parents=True)
    (exports / "blueprints.jsonl").write_text(
        json.dumps({"asset_path": "/Game/BP_Exact", "title": "Exact"}) + "\n",
        encoding="utf-8",
    )
    _complete_editor_export(project, exports / "blueprints.jsonl", "blueprint")
    index = tmp_path / "index" / "rag.sqlite"
    index.parent.mkdir()
    index.write_bytes(b"old index")
    (index.parent / "build_manifest.json").write_text(
        json.dumps({"engineVersion": "5.8", "engineAssociation": "5.8"}),
        encoding="utf-8",
    )
    _patch_cache_dependencies(monkeypatch)
    monkeypatch.setattr(
        workspace_paths,
        "load_shared_config",
        lambda: {"editorExportDir": str(tmp_path / "shared")},
    )
    calls: list[Path] = []

    def sync_once(**kwargs):
        snapshot = Path(kwargs["editor_export_dir"])
        calls.append(snapshot)
        assert snapshot != exports
        assert snapshot.parent == index.parent.parent
        row = json.loads((snapshot / "blueprints.jsonl").read_text(encoding="utf-8"))
        assert row["title"] == "Exact"
        return {"ok": True, "stageCommitted": True, "steps": [{"name": "build", "ok": True}]}

    monkeypatch.setattr(active_project_sync, "sync_active_project", sync_once)
    result = rag_refresh.refresh_active_project(
        scope="all",
        workspace=tmp_path,
        project=project,
        index_path=index,
    )

    assert result["ok"] is True
    assert result["projectSourceSync"]["stageCommitted"] is True
    assert result["editorMetadataSetup"]["stageCommitted"] is True
    assert len(calls) == 1
    assert not calls[0].exists()


def test_all_scope_invalid_editor_snapshot_preserves_index_and_skips_source_sync(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import active_project_sync

    project = tmp_path / "Game" / "Game.uproject"
    project.parent.mkdir(parents=True)
    project.write_text(json.dumps({"EngineAssociation": "5.8"}), encoding="utf-8")
    exports = project.parent / "Saved" / "LmStudioMetadataExports"
    exports.mkdir(parents=True)
    (exports / "blueprints.jsonl").write_text('{"partial":', encoding="utf-8")
    index = tmp_path / "index" / "rag.sqlite"
    index.parent.mkdir()
    index.write_bytes(b"old index")
    (index.parent / "build_manifest.json").write_text(
        json.dumps({"engineVersion": "5.8", "engineAssociation": "5.8"}),
        encoding="utf-8",
    )
    _patch_cache_dependencies(monkeypatch)
    called = False

    def should_not_sync(**_kwargs):
        nonlocal called
        called = True
        return {"ok": True}

    monkeypatch.setattr(active_project_sync, "sync_active_project", should_not_sync)
    result = rag_refresh.refresh_active_project(
        scope="all",
        workspace=tmp_path,
        project=project,
        index_path=index,
    )

    assert result["ok"] is False
    error = result["editorMetadataSetup"]["transactionError"]
    assert error["errorCode"] == "EDITOR_EXPORT_SNAPSHOT_FAILED"
    assert called is False
    assert index.read_bytes() == b"old index"


def test_all_scope_editor_launch_receives_captured_exact_uproject(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import active_project_sync
    import editor_export_runner

    project = tmp_path / "OwnerB" / "Game" / "Game.uproject"
    project.parent.mkdir(parents=True)
    project.write_text(json.dumps({"EngineAssociation": "5.7"}), encoding="utf-8")
    index = tmp_path / "index" / "rag.sqlite"
    index.parent.mkdir()
    index.write_bytes(b"old index")
    (index.parent / "build_manifest.json").write_text(
        json.dumps({"engineVersion": "5.7", "engineAssociation": "5.7"}),
        encoding="utf-8",
    )
    _patch_cache_dependencies(monkeypatch)
    launched: list[Path] = []

    def export_exact(**kwargs):
        launched.append(Path(kwargs["uproject"]))
        assert kwargs["scope"] == "all"
        export_dir = Path(kwargs["export_dir"])
        export_dir.mkdir(parents=True, exist_ok=True)
        (export_dir / "blueprints.jsonl").write_text(
            json.dumps({"asset_path": "/Game/BP_B", "title": "B"}) + "\n",
            encoding="utf-8",
        )
        _complete_editor_export(project, export_dir / "blueprints.jsonl", "blueprint")
        return {"ok": True, "exportDir": str(export_dir)}

    monkeypatch.setattr(editor_export_runner, "run_editor_export", export_exact)
    monkeypatch.setattr(
        active_project_sync,
        "sync_active_project",
        lambda **_kwargs: {"ok": True, "stageCommitted": True},
    )
    result = rag_refresh.refresh_active_project(
        scope="all",
        workspace=tmp_path,
        project=project,
        index_path=index,
        allow_editor_launch=True,
    )

    assert result["ok"] is True
    assert launched == [project.resolve()]


def test_common_refresh_projection_removes_nested_tool_directives(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project = tmp_path / "Demo.uproject"
    project.write_text("{}", encoding="utf-8")
    _patch_cache_dependencies(monkeypatch)
    monkeypatch.setattr(
        rag_refresh,
        "_editor_metadata_refresh",
        lambda **_kwargs: {
            "ok": True,
            "projectName": "Demo",
            "nextActions": ["call unreal_run_editor_export"],
            "agentWorkflow": ["unreal_editor_metadata_status"],
            "nested": {
                "requiredReads": ["planner.md"],
                "routeDecision": "continue",
                "fact": 7,
            },
        },
    )

    payload = rag_refresh.refresh_active_project(
        scope="editor_metadata",
        workspace=tmp_path,
        project=project,
    )
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["editorMetadataSetup"]["nested"] == {"fact": 7}
    for forbidden in (
        "nextActions",
        "agentWorkflow",
        "requiredReads",
        "routeDecision",
        "unreal_run_editor_export",
        "unreal_editor_metadata_status",
    ):
        assert forbidden not in serialized


def test_editor_metadata_sync_source_generates_no_tool_order_directives() -> None:
    source = (ROOT / "scripts" / "sync_editor_metadata.py").read_text(encoding="utf-8")
    assert '"nextActions"' not in source
    assert '"agentWorkflow"' not in source


def test_refresh_rejects_unknown_scope_without_side_effects(tmp_path: Path) -> None:
    payload = rag_refresh.refresh_active_project(
        scope="workflow" ,  # type: ignore[arg-type]
        workspace=tmp_path,
    )

    assert payload["ok"] is False
    assert payload["errorCode"] == "INVALID_REFRESH_SCOPE"


def test_project_source_collector_failure_preserves_raw_inputs_and_index(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import active_project_sync
    import direct_rag_project_refresh

    workspace = tmp_path / "workspace"
    (workspace / "scripts").mkdir(parents=True)
    project = tmp_path / "Game"
    (project / "Source").mkdir(parents=True)
    uproject = project / "Game.uproject"
    uproject.write_text(json.dumps({"EngineAssociation": "5.8"}), encoding="utf-8")
    index = tmp_path / "index"
    index.mkdir()
    protected = {
        "raw_projects.jsonl": b"old projects\n",
        "raw_project_profiles.jsonl": b"old profiles\n",
        "raw_project_architecture.jsonl": b"old architecture\n",
        "raw_project_symbols.jsonl": b"old symbols\n",
        "rag.sqlite": b"old sqlite",
        "chunks.jsonl": b"old chunks\n",
        "build_manifest.json": b'{"engineVersion":"5.8","engineAssociation":"5.8"}\n',
    }
    for name, content in protected.items():
        (index / name).write_bytes(content)

    calls: list[str] = []

    def fake_run(_workspace: Path, script: str, *arguments: str) -> dict:
        calls.append(script)
        if "--out" in arguments:
            output = Path(arguments[arguments.index("--out") + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps({
                    "id": script,
                    "source": "project_profile",
                    "text": f"new from {script}",
                    "metadata": {"project": "Game", "project_root": str(project)},
                }) + "\n",
                encoding="utf-8",
            )
        if "--jsonl" in arguments:
            output = Path(arguments[arguments.index("--jsonl") + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps({
                    "id": "architecture",
                    "source": "project_architecture",
                    "text": "new architecture",
                    "metadata": {"project": "Game", "project_root": str(project)},
                }) + "\n",
                encoding="utf-8",
            )
        if "--out-dir" in arguments and script == "collect_project_architecture.py":
            output_dir = Path(arguments[arguments.index("--out-dir") + 1])
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "architecture.json").write_text("{}", encoding="utf-8")
        return {
            "ok": script != "collect_unreal_symbols.py",
            "returncode": 1 if script == "collect_unreal_symbols.py" else 0,
            "command": [script],
            "outputTail": "simulated collector failure",
        }

    import direct_rag_project_collection

    monkeypatch.setattr(direct_rag_project_collection, "run_script", fake_run)
    result = active_project_sync.sync_active_project(
        project=uproject,
        index_dir=index,
        workspace=workspace,
    )

    assert result["ok"] is False
    assert result["errorCode"] == "PROJECT_SOURCE_COLLECT_FAILED"
    assert result["stageCommitted"] is False
    assert "direct_rag_build_generation.py" not in calls
    assert {name: (index / name).read_bytes() for name in protected} == protected
    assert not list(index.parent.glob(f".{index.name}.direct-refresh-*"))


def test_project_source_success_promotes_one_staged_generation(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import active_project_sync
    import active_project_paths
    import direct_rag_project_refresh

    workspace = tmp_path / "workspace"
    (workspace / "scripts").mkdir(parents=True)
    project = tmp_path / "Game"
    (project / "Source").mkdir(parents=True)
    uproject = project / "Game.uproject"
    uproject.write_text(json.dumps({"EngineAssociation": "5.8"}), encoding="utf-8")
    index = tmp_path / "index"
    index.mkdir()
    (index / "raw_guidelines.jsonl").write_text("preserved guideline\n", encoding="utf-8")
    (index / "rag.sqlite").write_bytes(b"old sqlite")
    (index / "build_manifest.json").write_text(
        json.dumps({"engineVersion": "5.8", "engineAssociation": "5.8"}),
        encoding="utf-8",
    )
    calls: list[str] = []

    def fake_run(_workspace: Path, script: str, *arguments: str) -> dict:
        calls.append(script)
        if "--out" in arguments:
            output = Path(arguments[arguments.index("--out") + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps({
                    "id": script,
                    "source": "project_profile",
                    "text": f"new from {script}",
                    "metadata": {"project": "Game", "project_root": str(project)},
                }) + "\n",
                encoding="utf-8",
            )
        if "--jsonl" in arguments:
            output = Path(arguments[arguments.index("--jsonl") + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps({
                    "id": "architecture",
                    "source": "project_architecture",
                    "text": "new architecture",
                    "metadata": {"project": "Game", "project_root": str(project)},
                }) + "\n",
                encoding="utf-8",
            )
        if script == "collect_project_architecture.py":
            output_dir = Path(arguments[arguments.index("--out-dir") + 1])
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "architecture.json").write_text("{}", encoding="utf-8")
        if script == "direct_rag_build_generation.py":
            stage = Path(arguments[arguments.index("--out-dir") + 1])
            (stage / "rag.sqlite").write_bytes(b"new sqlite")
            (stage / "chunks.jsonl").write_text("new chunks\n", encoding="utf-8")
            staged_input = stage / "raw_project_symbols.jsonl"
            (stage / "build_manifest.json").write_text(
                json.dumps(
                    {
                        "inputs": [{"path": str(staged_input.resolve()), "exists": True}],
                        "outputs": {
                            "chunksJsonl": str((stage / "chunks.jsonl").resolve()),
                            "sqlite": str((stage / "rag.sqlite").resolve()),
                        },
                    }
                ),
                encoding="utf-8",
            )
        return {"ok": True, "returncode": 0, "command": [script], "outputTail": ""}

    import direct_rag_project_collection

    monkeypatch.setattr(active_project_paths, "indexing_tier", lambda _workspace: "lite")
    monkeypatch.setattr(direct_rag_project_collection, "run_script", fake_run)
    result = active_project_sync.sync_active_project(
        project=uproject,
        index_dir=index,
        workspace=workspace,
    )

    assert result["ok"] is True
    assert result["stageCommitted"] is True
    assert calls[-1] == "direct_rag_build_generation.py"
    assert (index / "rag.sqlite").read_bytes() == b"new sqlite"
    assert (index / "raw_guidelines.jsonl").read_text(encoding="utf-8") == "preserved guideline\n"
    manifest = json.loads((index / "build_manifest.json").read_text(encoding="utf-8"))
    assert manifest["inputs"][0]["path"] == str((index / "raw_project_symbols.jsonl").resolve())
    assert manifest["outputs"]["sqlite"] == str((index / "rag.sqlite").resolve())
    assert not list(index.parent.glob(f".{index.name}.direct-refresh-*"))


def test_project_collectors_receive_exact_descriptor_not_shared_parent(
    tmp_path: Path,
) -> None:
    from direct_rag_project_collection import collector_commands

    project_root = tmp_path / "Shared"
    project_root.mkdir()
    selected = project_root / "Selected.uproject"
    sibling = project_root / "Other.uproject"
    selected.write_text(json.dumps({"EngineAssociation": "5.8"}), encoding="utf-8")
    sibling.write_text(json.dumps({"EngineAssociation": "5.7"}), encoding="utf-8")

    commands = collector_commands(selected, tmp_path / "stage")

    for command in commands[:2]:
        assert command[command.index("--root") + 1] == str(selected)
    symbols = next(command for command in commands if command[0] == "collect_unreal_symbols.py")
    assert symbols[symbols.index("--project-name") + 1] == selected.stem
    assert symbols[symbols.index("--project-root") + 1] == str(project_root.resolve())


def test_refresh_transaction_rolls_back_partial_promotion(monkeypatch, tmp_path: Path) -> None:
    import direct_rag_generation_swap as generation
    import direct_rag_refresh_transaction as transaction

    index = tmp_path / "index"
    index.mkdir()
    protected = {
        "raw_projects.jsonl": b"old projects\n",
        "raw_project_profiles.jsonl": b"old profiles\n",
        "raw_project_architecture.jsonl": b"old architecture\n",
        "raw_project_symbols.jsonl": b"old symbols\n",
        "rag.sqlite": b"old sqlite",
        "chunks.jsonl": b"old chunks\n",
        "build_manifest.json": b'{"old":true}\n',
    }
    for name, content in protected.items():
        (index / name).write_bytes(content)
    stage = transaction.prepare_refresh_stage(index)
    try:
        for name in transaction.COLLECTOR_OUTPUTS:
            (stage / name).write_text(f"new {name}\n", encoding="utf-8")
        (stage / "rag.sqlite").write_bytes(b"new sqlite")
        (stage / "chunks.jsonl").write_bytes(b"new chunks\n")
        (stage / "build_manifest.json").write_text('{"inputs":[],"outputs":{}}\n', encoding="utf-8")

        real_promote = generation._promote
        injected = False

        def fail_during_promotion(source: Path, destination: Path) -> None:
            nonlocal injected
            source_path = Path(source)
            if not injected and source_path.parent == stage and source_path.name == "chunks.jsonl":
                injected = True
                raise OSError("simulated promotion failure")
            real_promote(source, destination)

        monkeypatch.setattr(generation, "_promote", fail_during_promotion)
        with pytest.raises(RuntimeError, match="prior index restored"):
            transaction.commit_refresh_stage(stage, index)
        assert {name: (index / name).read_bytes() for name in protected} == protected
    finally:
        transaction.discard_refresh_stage(stage)


def test_incremental_build_detects_manifest_input_disappearance(tmp_path: Path) -> None:
    import incremental_build

    index = tmp_path / "index"
    index.mkdir()
    raw = index / "raw_projects.jsonl"
    raw.write_text("{}\n", encoding="utf-8")
    sqlite = index / "rag.sqlite"
    sqlite.write_bytes(b"sqlite")
    now = time.time()
    os.utime(raw, (now - 10, now - 10))
    os.utime(sqlite, (now, now))
    manifest = index / "build_manifest.json"
    missing = index / "raw_project_symbols.jsonl"
    manifest.write_text(
        json.dumps(
            {
                "inputPolicyFingerprint": incremental_build.INDEX_INPUT_POLICY_FINGERPRINT,
                "chunkMetadataPolicy": incremental_build.chunk_metadata_policy(),
                "workspaceRoot": str(incremental_build.find_workspace_root().resolve()),
                "inputs": [
                    {
                        "path": str(raw.resolve()),
                        "exists": True,
                        "sizeBytes": raw.stat().st_size,
                        "modifiedAt": incremental_build.datetime.fromtimestamp(
                            raw.stat().st_mtime,
                            tz=incremental_build.timezone.utc,
                        ).isoformat(),
                    },
                    {"path": str(missing.resolve()), "exists": True, "sizeBytes": 100},
                ],
            }
        ),
        encoding="utf-8",
    )

    stale, reason = incremental_build.manifest_stale(index, manifest, sqlite)

    assert stale is True
    assert reason == "manifest-input-missing (raw_project_symbols.jsonl)"

    raw.unlink()
    stale_without_any_raw_inputs, no_inputs_reason = incremental_build.manifest_stale(
        index,
        manifest,
        sqlite,
    )
    assert stale_without_any_raw_inputs is True
    assert "raw_project_symbols.jsonl" in no_inputs_reason
    assert "raw_projects.jsonl" in no_inputs_reason


def test_incremental_build_rejects_old_policy_and_retired_raw_input(tmp_path: Path) -> None:
    import incremental_build

    index = tmp_path / "index"
    index.mkdir()
    raw = index / "raw_projects.jsonl"
    raw.write_text("{}\n", encoding="utf-8")
    retired = index / "raw_failure_memory.jsonl"
    retired.write_text("{}\n", encoding="utf-8")
    sqlite = index / "rag.sqlite"
    sqlite.write_bytes(b"sqlite")
    manifest = index / "build_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "inputPolicyFingerprint": "old-policy",
                "inputs": [{"path": str(retired.resolve()), "exists": True}],
            }
        ),
        encoding="utf-8",
    )

    stale, reason = incremental_build.manifest_stale(index, manifest, sqlite)

    assert stale is True
    assert reason == "input-policy-changed"

    manifest.write_text(
        json.dumps(
            {
                "inputPolicyFingerprint": incremental_build.INDEX_INPUT_POLICY_FINGERPRINT,
                "chunkMetadataPolicy": {"version": 0},
                "inputs": [{"path": str(raw.resolve()), "exists": True}],
            }
        ),
        encoding="utf-8",
    )
    stale, reason = incremental_build.manifest_stale(index, manifest, sqlite)
    assert stale is True
    assert reason == "chunk-metadata-policy-changed"


def test_refresh_lock_is_nonblocking_for_same_index(tmp_path: Path) -> None:
    from direct_rag_refresh_lock import DirectRagRefreshBusyError, index_refresh_lock

    index = tmp_path / "index" / "rag.sqlite"
    with index_refresh_lock(index):
        with pytest.raises(DirectRagRefreshBusyError):
            with index_refresh_lock(index):
                pass


def test_primary_sqlite_remains_readable_during_atomic_generation_swap(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import sqlite3
    import direct_rag_generation_swap as generation
    import direct_rag_refresh_transaction as transaction

    index = tmp_path / "index"
    index.mkdir()
    live = index / "rag.sqlite"
    connection = sqlite3.connect(live)
    try:
        connection.execute("create table generation(value text)")
        connection.execute("insert into generation values ('old')")
        connection.commit()
    finally:
        connection.close()
    stage = transaction.prepare_refresh_stage(index)
    staged = stage / "rag.sqlite"
    staged.unlink(missing_ok=True)
    connection = sqlite3.connect(staged)
    try:
        connection.execute("create table generation(value text)")
        connection.execute("insert into generation values ('new')")
        connection.commit()
    finally:
        connection.close()

    observed: list[str] = []
    real_preserve = generation.preserve_refresh_path

    def observe_after_preserve(source: Path, backup: Path) -> None:
        real_preserve(source, backup)
        if source == live:
            uri = live.resolve().as_uri() + "?mode=ro"
            connection = sqlite3.connect(uri, uri=True)
            try:
                observed.append(connection.execute("select value from generation").fetchone()[0])
            finally:
                connection.close()

    monkeypatch.setattr(generation, "preserve_refresh_path", observe_after_preserve)
    transaction.commit_refresh_stage(stage, index, required_files=("rag.sqlite",))

    connection = sqlite3.connect(live)
    try:
        current = connection.execute("select value from generation").fetchone()[0]
    finally:
        connection.close()
    assert observed == ["old"]
    assert current == "new"


def test_refresh_commit_prunes_all_retired_direct_inputs(tmp_path: Path) -> None:
    import direct_rag_refresh_transaction as transaction

    index = tmp_path / "index"
    index.mkdir()
    retired = index / "raw_failure_memory.jsonl"
    retired.write_bytes(b"retired evidence\n")
    retired_graph = index / "raw_module_graph.jsonl"
    retired_graph.write_bytes(b"retired graph\n")
    retired_report = index / "unreal_module_include_graph.md"
    retired_report.write_bytes(b"retired report\n")
    stage = transaction.prepare_refresh_stage(index)
    (stage / "rag.sqlite").write_bytes(b"new sqlite")
    try:
        transaction.commit_refresh_stage(
            stage,
            index,
            required_files=("rag.sqlite",),
            prune_files=transaction.RETIRED_MANAGED_FILES,
        )
    finally:
        transaction.discard_refresh_stage(stage)

    assert not retired.exists()
    assert not retired_graph.exists()
    assert not retired_report.exists()


def test_pruned_file_and_new_file_are_restored_or_removed_after_later_fault(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import direct_rag_generation_swap as generation
    import direct_rag_refresh_transaction as transaction

    index = tmp_path / "index"
    index.mkdir()
    retired = index / "raw_failure_memory.jsonl"
    retired.write_bytes(b"old retired\n")
    stage = transaction.prepare_refresh_stage(index)
    (stage / "raw_projects.jsonl").write_bytes(b"new project\n")
    (stage / "rag.sqlite").write_bytes(b"new sqlite")
    real_promote = generation._promote

    def fail_at_primary(source: Path, destination: Path) -> None:
        if source == stage / "rag.sqlite":
            raise OSError("primary commit point fault")
        real_promote(source, destination)

    monkeypatch.setattr(generation, "_promote", fail_at_primary)
    with pytest.raises(RuntimeError, match="prior index restored"):
        transaction.commit_refresh_stage(
            stage,
            index,
            required_files=("raw_projects.jsonl", "rag.sqlite"),
            prune_files=transaction.RETIRED_MANAGED_FILES,
        )

    assert retired.read_bytes() == b"old retired\n"
    assert not (index / "raw_projects.jsonl").exists()
    assert not list(index.parent.glob(f".{index.name}.direct-refresh-backup-*"))
    assert not (index.parent / f".{index.name}.direct-refresh-journal.json").exists()
    transaction.discard_refresh_stage(stage)


def test_prepared_recovery_replaces_primary_without_delete_window(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import direct_rag_backup_restore as backup_restore
    import direct_rag_refresh_recovery as recovery

    index = tmp_path / "index"
    index.mkdir()
    live = index / "rag.sqlite"
    live.write_bytes(b"old")
    stage = tmp_path / f".{index.name}.direct-refresh-stage"
    backup = tmp_path / f".{index.name}.direct-refresh-backup-stage"
    stage.mkdir()
    backup.mkdir()
    journal = recovery.begin_refresh_journal(
        index,
        stage,
        backup,
        ("rag.sqlite",),
        ("rag.sqlite",),
    )
    recovery.preserve_refresh_path(live, backup / "rag.sqlite")
    recovery.mark_refresh_backed_up(journal)
    promoted = stage / "rag.sqlite"
    promoted.write_bytes(b"new")
    os.replace(promoted, live)
    observed: list[bytes] = []
    real_atomic = backup_restore.atomic_replace

    def observe(source: Path, destination: Path, **kwargs) -> None:
        observed.append(destination.read_bytes())
        real_atomic(source, destination, **kwargs)

    monkeypatch.setattr(backup_restore, "atomic_replace", observe)
    result = recovery.recover_interrupted_refresh(index, ("rag.sqlite",))

    assert result == {"recovered": True, "reason": "previous_generation_restored"}
    assert observed == [b"new"]
    assert live.read_bytes() == b"old"
    assert not journal.exists()


def test_failed_prepared_recovery_keeps_journal_and_backup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import direct_rag_backup_restore as backup_restore
    import direct_rag_refresh_recovery as recovery

    index = tmp_path / "index"
    index.mkdir()
    live = index / "rag.sqlite"
    live.write_bytes(b"new")
    stage = tmp_path / f".{index.name}.direct-refresh-stage"
    backup = tmp_path / f".{index.name}.direct-refresh-backup-stage"
    stage.mkdir()
    backup.mkdir()
    (backup / "rag.sqlite").write_bytes(b"old")
    journal = recovery.begin_refresh_journal(
        index,
        stage,
        backup,
        ("rag.sqlite",),
        ("rag.sqlite",),
    )
    recovery.mark_refresh_backed_up(journal)

    def blocked(*_args, **_kwargs) -> None:
        raise PermissionError("long-lived reader")

    monkeypatch.setattr(backup_restore, "atomic_replace", blocked)
    with pytest.raises(PermissionError, match="long-lived reader"):
        recovery.recover_interrupted_refresh(index, ("rag.sqlite",))

    assert live.read_bytes() == b"new"
    assert (backup / "rag.sqlite").read_bytes() == b"old"
    assert journal.exists()


def test_v1_incomplete_backup_never_deletes_live_primary(tmp_path: Path) -> None:
    import direct_rag_refresh_recovery as recovery

    index = tmp_path / "index"
    index.mkdir()
    live = index / "rag.sqlite"
    live.write_bytes(b"old")
    stage = tmp_path / f".{index.name}.direct-refresh-stage"
    backup = tmp_path / f".{index.name}.direct-refresh-backup-stage"
    stage.mkdir()
    backup.mkdir()
    journal = recovery.begin_refresh_journal(
        index,
        stage,
        backup,
        ("rag.sqlite",),
        ("rag.sqlite",),
    )
    payload = json.loads(journal.read_text(encoding="utf-8"))
    payload.update({"version": 1, "state": "prepared"})
    journal.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuntimeError, match="backup is incomplete"):
        recovery.recover_interrupted_refresh(index, ("rag.sqlite",))

    assert live.read_bytes() == b"old"
    assert journal.exists()


def test_recovery_backup_remains_immutable_across_second_crash(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import direct_rag_backup_restore as backup_restore
    import direct_rag_refresh_recovery as recovery

    index = tmp_path / "index"
    index.mkdir()
    (index / "raw_projects.jsonl").write_bytes(b"new rows")
    (index / "rag.sqlite").write_bytes(b"new sqlite")
    stage = tmp_path / f".{index.name}.direct-refresh-stage"
    backup = tmp_path / f".{index.name}.direct-refresh-backup-stage"
    stage.mkdir()
    backup.mkdir()
    (backup / "raw_projects.jsonl").write_bytes(b"old rows")
    (backup / "rag.sqlite").write_bytes(b"old sqlite")
    journal = recovery.begin_refresh_journal(
        index,
        stage,
        backup,
        ("raw_projects.jsonl", "rag.sqlite"),
        ("raw_projects.jsonl", "rag.sqlite"),
    )
    recovery.mark_refresh_backed_up(journal)
    real_atomic = backup_restore.atomic_replace
    calls = 0

    def fail_second(source: Path, destination: Path, **kwargs) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise PermissionError("second recovery crash")
        real_atomic(source, destination, **kwargs)

    monkeypatch.setattr(backup_restore, "atomic_replace", fail_second)
    with pytest.raises(PermissionError, match="second recovery crash"):
        recovery.recover_interrupted_refresh(
            index,
            ("raw_projects.jsonl", "rag.sqlite"),
        )

    assert (backup / "raw_projects.jsonl").read_bytes() == b"old rows"
    assert (backup / "rag.sqlite").read_bytes() == b"old sqlite"
    assert journal.exists()

    monkeypatch.setattr(backup_restore, "atomic_replace", real_atomic)
    recovered = recovery.recover_interrupted_refresh(
        index,
        ("raw_projects.jsonl", "rag.sqlite"),
    )
    assert recovered == {"recovered": True, "reason": "previous_generation_restored"}
    assert (index / "raw_projects.jsonl").read_bytes() == b"old rows"
    assert (index / "rag.sqlite").read_bytes() == b"old sqlite"
    assert not backup.exists()
    assert not journal.exists()


def test_restored_journal_reentry_is_cleanup_only_after_clear_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import direct_rag_refresh_recovery as recovery

    index = tmp_path / "index"
    index.mkdir()
    live = index / "rag.sqlite"
    live.write_bytes(b"new")
    stage = tmp_path / f".{index.name}.direct-refresh-stage"
    backup = tmp_path / f".{index.name}.direct-refresh-backup-stage"
    stage.mkdir()
    backup.mkdir()
    (backup / "rag.sqlite").write_bytes(b"old")
    journal = recovery.begin_refresh_journal(
        index,
        stage,
        backup,
        ("rag.sqlite",),
        ("rag.sqlite",),
    )
    recovery.mark_refresh_backed_up(journal)
    real_clear = recovery.clear_refresh_journal

    def fail_clear(_journal: Path) -> None:
        raise OSError("journal unlink crash")

    monkeypatch.setattr(recovery, "clear_refresh_journal", fail_clear)
    with pytest.raises(OSError, match="journal unlink crash"):
        recovery.recover_interrupted_refresh(index, ("rag.sqlite",))

    assert live.read_bytes() == b"old"
    assert json.loads(journal.read_text(encoding="utf-8"))["state"] == "restored"
    assert not backup.exists()

    monkeypatch.setattr(recovery, "clear_refresh_journal", real_clear)
    recovered = recovery.recover_interrupted_refresh(index, ("rag.sqlite",))
    assert recovered == {"recovered": True, "reason": "restored_cleanup"}
    assert live.read_bytes() == b"old"
    assert not journal.exists()


def test_prepared_recovery_discards_partial_backup_without_touching_live_index(
    tmp_path: Path,
) -> None:
    import direct_rag_refresh_recovery as recovery

    index = tmp_path / "index"
    index.mkdir()
    live = index / "rag.sqlite"
    live.write_bytes(b"old")
    stage = tmp_path / f".{index.name}.direct-refresh-stage"
    backup = tmp_path / f".{index.name}.direct-refresh-backup-stage"
    stage.mkdir()
    backup.mkdir()
    (stage / "rag.sqlite").write_bytes(b"new")
    (backup / "rag.sqlite").write_bytes(b"partial backup")
    journal = recovery.begin_refresh_journal(
        index,
        stage,
        backup,
        ("rag.sqlite",),
        ("rag.sqlite",),
    )

    result = recovery.recover_interrupted_refresh(index, ("rag.sqlite",))

    assert result == {"recovered": True, "reason": "prepared_cleanup"}
    assert live.read_bytes() == b"old"
    assert not stage.exists()
    assert not backup.exists()
    assert not journal.exists()


def test_committed_cleanup_failure_keeps_recoverable_journal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import direct_rag_generation_swap as generation
    import direct_rag_refresh_transaction as transaction

    index = tmp_path / "index"
    index.mkdir()
    (index / "rag.sqlite").write_bytes(b"old")
    stage = transaction.prepare_refresh_stage(index)
    (stage / "rag.sqlite").write_bytes(b"new")
    real_rmtree = generation.shutil.rmtree

    def leave_backup(path: Path, *args, **kwargs) -> None:
        if ".direct-refresh-backup-" in Path(path).name:
            return
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(generation.shutil, "rmtree", leave_backup)
    transaction.commit_refresh_stage(stage, index, required_files=("rag.sqlite",))
    journal = index.parent / f".{index.name}.direct-refresh-journal.json"
    assert (index / "rag.sqlite").read_bytes() == b"new"
    assert journal.is_file()
    assert json.loads(journal.read_text(encoding="utf-8"))["state"] == "committed"

    monkeypatch.setattr(generation.shutil, "rmtree", real_rmtree)
    recovered = transaction.recover_interrupted_refresh(index)
    assert recovered == {"recovered": True, "reason": "committed_cleanup"}
    assert not journal.exists()


def test_readonly_query_never_creates_missing_index(tmp_path: Path) -> None:
    from direct_rag_readonly_db import connect_readonly

    missing = tmp_path / "missing.sqlite"
    with pytest.raises(Exception):
        connect_readonly(missing)
    assert not missing.exists()


def test_generation_identity_rejects_new_manifest_with_old_database(tmp_path: Path) -> None:
    import sqlite3

    from direct_rag_generation_identity import (
        RagGenerationTransitionError,
        connect_consistent_readonly,
    )
    from direct_rag_index_registry import resolve_request_index

    index_dir = tmp_path / "index"
    index_dir.mkdir()
    index = index_dir / "rag.sqlite"
    connection = sqlite3.connect(index)
    connection.execute("create table index_meta(key text primary key, value text not null)")
    connection.execute(
        "insert into index_meta(key, value) values ('generation_id', 'old-generation')"
    )
    connection.commit()
    connection.close()
    (index_dir / "build_manifest.json").write_text(
        json.dumps({"engineVersion": "5.8", "generationId": "new-generation"}),
        encoding="utf-8",
    )

    with pytest.raises(RagGenerationTransitionError, match="manifest=new-generation"):
        connect_consistent_readonly(index, attempts=1, delay_seconds=0)

    resolution = resolve_request_index(
        index,
        tmp_path,
        use_active=False,
    )
    assert resolution["ok"] is False
    assert resolution["errorCode"] == "RAG_GENERATION_TRANSITION"
    assert resolution["retryAllowed"] is True

    (index_dir / "build_manifest.json").unlink()
    connection = sqlite3.connect(index)
    connection.execute(
        "update index_meta set value = 'database-only-generation' where key = 'generation_id'"
    )
    connection.commit()
    connection.close()
    with pytest.raises(RagGenerationTransitionError, match="sqlite=database-only-generation"):
        connect_consistent_readonly(index, attempts=1, delay_seconds=0)


def test_startup_recovers_each_valid_sibling_under_its_own_fault_boundary(
    tmp_path: Path,
) -> None:
    from direct_rag_refresh_journal import (
        begin_refresh_journal,
        mark_refresh_backed_up,
    )
    from direct_rag_refresh_lock import refresh_lock_path
    from direct_rag_startup_recovery import recover_startup_refreshes

    data = tmp_path / "data"
    base = data / "unreal58" / "rag.sqlite"
    base.parent.mkdir(parents=True)
    base.write_bytes(b"base")
    healthy = data / "unreal57"
    broken = data / "unreal56"
    healthy.mkdir()
    broken.mkdir()

    healthy_stage = data / ".unreal57.direct-refresh-stage"
    healthy_backup = data / ".unreal57.direct-refresh-backup-stage"
    healthy_stage.mkdir()
    healthy_backup.mkdir()
    healthy_journal = begin_refresh_journal(
        healthy,
        healthy_stage,
        healthy_backup,
        ("rag.sqlite",),
        (),
    )

    broken_stage = data / ".unreal56.direct-refresh-stage"
    broken_backup = data / ".unreal56.direct-refresh-backup-stage"
    broken_stage.mkdir()
    broken_backup.mkdir()
    broken_journal = begin_refresh_journal(
        broken,
        broken_stage,
        broken_backup,
        ("rag.sqlite",),
        ("rag.sqlite",),
    )
    mark_refresh_backed_up(broken_journal)

    results = recover_startup_refreshes(base)
    by_dir = {Path(row["indexDir"]).name: row for row in results}

    assert by_dir["unreal56"]["reason"] == "recovery_failed"
    assert by_dir["unreal57"] == {
        "indexDir": str(healthy.resolve()),
        "recovered": True,
        "reason": "prepared_cleanup",
    }
    assert by_dir["unreal58"]["reason"] == "journal_missing"
    assert broken_journal.is_file()
    assert not healthy_journal.exists()
    assert refresh_lock_path(broken / "rag.sqlite").is_file()
    assert refresh_lock_path(healthy / "rag.sqlite").is_file()
    assert refresh_lock_path(base).is_file()


def test_direct_project_export_path_ignores_arbitrary_shared_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import editor_export_paths

    project = tmp_path / "OwnerB" / "Game" / "Game.uproject"
    project.parent.mkdir(parents=True)
    project.write_text("{}", encoding="utf-8")
    shared = tmp_path / "OwnerAExports"
    monkeypatch.setattr(
        editor_export_paths,
        "load_shared_config",
        lambda: {"editorExportDir": str(shared)},
    )

    assert editor_export_paths.editor_export_dir_for_project(project) == shared.resolve()
    assert editor_export_paths.editor_export_dir_for_project(
        project,
        use_shared_config=False,
    ) == (project.parent / "Saved" / "LmStudioMetadataExports").resolve()


def test_public_build_returns_busy_instead_of_racing_refresh(tmp_path: Path) -> None:
    import direct_rag_public_build
    from direct_rag_refresh_lock import index_refresh_lock

    workspace = tmp_path / "workspace"
    (workspace / "scripts").mkdir(parents=True)
    index = tmp_path / "index"
    index.mkdir()
    (index / "raw_projects.jsonl").write_text("{}\n", encoding="utf-8")

    with index_refresh_lock(index):
        result = direct_rag_public_build.build_public_index(
            workspace=workspace,
            index_dir=index,
            force=True,
            stale_check=lambda *_args: (True, "forced"),
        )

    assert result["ok"] is False
    assert result["errorCode"] == "RAG_REFRESH_BUSY"


def test_public_build_accepts_guidelines_without_treating_their_root_as_a_project(
    tmp_path: Path,
) -> None:
    import direct_rag_public_build

    project_root = tmp_path / "Game"
    project_root.mkdir()
    project = project_root / "Game.uproject"
    project.write_text(json.dumps({"EngineAssociation": "5.8"}), encoding="utf-8")
    guideline_root = tmp_path / "RAG_Project_Guidelines"
    guideline_root.mkdir()
    index = tmp_path / "index"
    index.mkdir()
    (index / "raw_guidelines.jsonl").write_text(
        json.dumps(
            {
                "id": "guideline",
                "source": "project_guideline",
                "title": "Portable rule",
                "text": "Use exact project identity for every project-scoped operation.",
                "metadata": {"root": str(guideline_root), "relative_path": "Rule.md"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (index / "raw_projects.jsonl").write_text(
        json.dumps(
            {
                "id": "project",
                "source": "unreal_project_text",
                "title": "Game.cpp",
                "text": "class FGameModule {};",
                "metadata": {
                    "project": "Game",
                    "project_root": str(project_root),
                    "relative_path": "Source/Game.cpp",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = direct_rag_public_build.build_public_index(
        workspace=ROOT,
        index_dir=index,
        force=True,
        stale_check=lambda *_args: (True, "forced"),
        engine_version="5.8",
        engine_association="5.8",
    )

    assert result["ok"] is True, result
    manifest = json.loads((index / "build_manifest.json").read_text(encoding="utf-8"))
    assert {Path(row["path"]).name for row in manifest["inputs"]} >= {
        "raw_guidelines.jsonl",
        "raw_projects.jsonl",
    }


def test_public_build_companion_fault_preserves_complete_old_generation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import direct_rag_generation_swap as generation
    import direct_rag_public_build

    workspace = tmp_path / "workspace"
    (workspace / "scripts").mkdir(parents=True)
    index = tmp_path / "index"
    index.mkdir()
    protected = {
        "raw_projects.jsonl": b'{"id":"old"}\n',
        "rag.sqlite": b"old sqlite",
        "chunks.jsonl": b"old chunks\n",
        "build_manifest.json": b'{"old":true}\n',
    }
    for name, content in protected.items():
        (index / name).write_bytes(content)

    def fake_build(stage: Path, _workspace: Path) -> dict:
        (stage / "rag.sqlite").write_bytes(b"new sqlite")
        (stage / "chunks.jsonl").write_bytes(b"new chunks\n")
        (stage / "build_manifest.json").write_text(
            json.dumps({"inputs": [], "outputs": {}}),
            encoding="utf-8",
        )
        return {"ok": True, "outputTail": ""}

    real_promote = generation._promote

    def fail_companion(source: Path, destination: Path) -> None:
        if source.name == "chunks.jsonl":
            raise OSError("companion fault")
        real_promote(source, destination)

    monkeypatch.setattr(direct_rag_public_build, "build_generation", fake_build)
    monkeypatch.setattr(generation, "_promote", fail_companion)
    result = direct_rag_public_build.build_public_index(
        workspace=workspace,
        index_dir=index,
        force=True,
        stale_check=lambda *_args: (True, "forced"),
    )

    assert result["ok"] is False
    assert result["errorCode"] == "RAG_PUBLIC_BUILD_TRANSACTION_FAILED"
    assert {name: (index / name).read_bytes() for name in protected} == protected
