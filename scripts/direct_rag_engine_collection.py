#!/usr/bin/env python
"""Initialize or update engine evidence for one engine-bound RAG shard."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

RunScript = Callable[..., dict[str, Any]]
Emit = Callable[[str], None]


def _run(
    run_script: RunScript,
    workspace: Path,
    emit: Emit,
    name: str,
    *arguments: str,
) -> dict[str, Any]:
    emit(f"{name} (engine shard)")
    return run_script(workspace, name, *arguments)


def ensure_engine_inputs(
    *,
    workspace: Path,
    project: Path,
    stage: Path,
    engine_binding: dict[str, Any],
    run_script: RunScript,
    emit: Emit,
) -> tuple[list[dict[str, Any]], str | None]:
    from active_project_paths import indexing_tier
    from direct_rag_engine_tier import prune_engine_inputs_for_tier
    from direct_rag_project_engine import normalize_engine_version

    tier = indexing_tier(workspace)
    pruned = prune_engine_inputs_for_tier(stage, tier)
    expected_version = normalize_engine_version(engine_binding.get("engineVersion"))
    if not expected_version:
        return [{
            "name": "resolve-engine-root",
            "ok": False,
            "errorCode": "RAG_ENGINE_BINDING_INVALID",
            "error": "The engine shard has no exact major.minor engineVersion binding.",
        }], "resolve-engine-root"
    if tier == "lite":
        return [{
            "name": "engine-evidence",
            "ok": True,
            "skipped": True,
            "reason": "lite-tier",
            "pruned": list(pruned),
        }], None

    from workspace_paths import resolve_engine_root_for_association
    from unreal_engine_discovery import engine_build_version

    association = str(engine_binding.get("engineAssociation") or "").strip()
    resolution = resolve_engine_root_for_association(
        association or expected_version,
        workspace,
    )
    if resolution.get("ok") is not True:
        return [{"name": "resolve-engine-root", **resolution}], "resolve-engine-root"
    engine_root = Path(str(resolution.get("engineRoot") or "")).expanduser().resolve()
    actual_version = engine_build_version(engine_root)
    if actual_version != expected_version:
        return [{
            "name": "resolve-engine-root",
            "ok": False,
            "errorCode": "RAG_ENGINE_ROOT_VERSION_MISMATCH",
            "error": (
                f"Resolved Engine/Build/Build.version is {actual_version or 'missing'}, "
                f"but the shard is bound to Unreal {expected_version}: {engine_root}"
            ),
            "expectedEngineVersion": expected_version,
            "actualEngineVersion": actual_version or None,
        }], "resolve-engine-root"
    engine_source = engine_root / "Engine" / "Source"
    if not engine_source.is_dir():
        return [{
            "name": "resolve-engine-root",
            "ok": False,
            "errorCode": "ENGINE_SOURCE_MISSING",
            "error": f"Engine source is missing: {engine_source}",
        }], "resolve-engine-root"

    from direct_rag_raw_provenance import validate_raw_provenance

    provenance = validate_raw_provenance(
        index_dir=stage,
        workspace=workspace,
        engine_version=expected_version,
        engine_association=association,
        engine_root=str(engine_root),
    )
    steps: list[dict[str, Any]] = [{
        "name": "resolve-engine-root",
        "ok": True,
        "engineRoot": str(engine_root),
        "engineVersion": actual_version,
    }, {
        "name": "validate-engine-raw-provenance",
        **provenance,
    }]
    if provenance.get("ok") is not True:
        return steps, "validate-engine-raw-provenance"
    guidelines = workspace / "RAG_Project_Guidelines"
    if guidelines.is_dir() and not (stage / "raw_guidelines.jsonl").is_file():
        step = _run(
            run_script, workspace, emit, "collect_project_guidelines.py",
            "--root", str(guidelines), "--out", str(stage / "raw_guidelines.jsonl"),
        )
        steps.append({"name": "collect_project_guidelines.py", **step})
        if step.get("ok") is not True:
            return steps, "collect_project_guidelines.py"
    game_design = workspace / "Game_Design_Docs"
    if game_design.is_dir() and not (stage / "raw_game_design.jsonl").is_file():
        step = _run(
            run_script, workspace, emit, "collect_game_design_docs.py",
            "--root", str(game_design), "--out", str(stage / "raw_game_design.jsonl"),
        )
        steps.append({"name": "collect_game_design_docs.py", **step})
        if step.get("ok") is not True:
            return steps, "collect_game_design_docs.py"

    if not (stage / "raw_symbols.jsonl").is_file():
        step = _run(
            run_script, workspace, emit, "collect_unreal_symbols.py",
            "--root", str(engine_source),
            "--out", str(stage / "raw_symbols.jsonl"),
            "--sidecar-out", str(stage / "sidecar_symbols_meta.jsonl"),
            "--tier", "public", "--scope", "engine",
        )
        steps.append({"name": "collect-engine-public-symbols", **step})
        if step.get("ok") is not True:
            return steps, "collect_unreal_symbols.py"

    if tier == "full" and not (stage / "raw_source.jsonl").is_file():
        step = _run(
            run_script, workspace, emit, "collect_unreal_source.py",
            "--root", str(engine_source), "--out", str(stage / "raw_source.jsonl"),
        )
        steps.append({"name": "collect-engine-source", **step})
        if step.get("ok") is not True:
            return steps, "collect_unreal_source.py"
    provenance = validate_raw_provenance(
        index_dir=stage,
        workspace=workspace,
        engine_version=expected_version,
        engine_association=association,
        engine_root=str(engine_root),
    )
    steps.append({"name": "validate-collected-engine-provenance", **provenance})
    if provenance.get("ok") is not True:
        return steps, "validate-collected-engine-provenance"
    return steps, None


__all__ = ["ensure_engine_inputs"]
