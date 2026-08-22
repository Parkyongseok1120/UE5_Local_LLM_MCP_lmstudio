#!/usr/bin/env python
"""Stage and transactionally promote one Direct project-source refresh."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from atomic_io import atomic_write_text
from direct_rag_generation_swap import swap_refresh_generation
from direct_rag_refresh_recovery import (
    recover_interrupted_refresh as _recover_interrupted_refresh,
)
from index_inputs import FORBIDDEN_RAW_INPUT_FILES, RAW_INPUT_FILES


COLLECTOR_OUTPUTS = (
    "raw_projects.jsonl",
    "raw_project_profiles.jsonl",
    "raw_project_architecture.jsonl",
    "raw_project_symbols.jsonl",
)
BUILD_OUTPUTS = ("rag.sqlite", "chunks.jsonl", "build_manifest.json")
AUXILIARY_FILES = ("sidecar_symbols_meta.jsonl",)
PRESERVED_STATE_FILES = ("editor_capture_state.json",)
RETIRED_MANAGED_FILES = tuple(
    sorted(
        {
            *FORBIDDEN_RAW_INPUT_FILES,
            "raw_module_graph.jsonl",
            "unreal_module_include_graph.md",
        }
    )
)
MANAGED_FILES = tuple(
    dict.fromkeys(
        (
            *RAW_INPUT_FILES,
            *BUILD_OUTPUTS,
            *AUXILIARY_FILES,
            *PRESERVED_STATE_FILES,
            *RETIRED_MANAGED_FILES,
        )
    )
)
MANAGED_DIRECTORIES = ("project_architecture",)


def prepare_refresh_stage(index_dir: Path) -> Path:
    """Create a same-volume stage containing immutable copies of prior raw inputs."""

    target = index_dir.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(
        tempfile.mkdtemp(
            prefix=f".{target.name}.direct-refresh-",
            dir=str(target.parent),
        )
    ).resolve()
    try:
        for name in (*RAW_INPUT_FILES, *PRESERVED_STATE_FILES):
            source = target / name
            if source.is_file():
                shutil.copy2(source, stage / name)
        for name in MANAGED_DIRECTORIES:
            source = target / name
            if source.is_dir():
                shutil.copytree(source, stage / name)
    except BaseException:
        discard_refresh_stage(stage)
        raise
    return stage


def rebase_stage_manifest(stage: Path, index_dir: Path) -> None:
    """Rewrite staging-only paths before the validated outputs are promoted."""

    stage = stage.resolve()
    target = index_dir.resolve()
    manifest_path = stage / "build_manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    inputs = payload.get("inputs")
    if isinstance(inputs, list):
        for row in inputs:
            if not isinstance(row, dict):
                continue
            raw_path = str(row.get("path") or "").strip()
            if not raw_path:
                continue
            try:
                relative = Path(raw_path).resolve().relative_to(stage)
            except (OSError, ValueError):
                continue
            row["path"] = str((target / relative).resolve())
    outputs = payload.get("outputs")
    if isinstance(outputs, dict):
        outputs["chunksJsonl"] = str((target / "chunks.jsonl").resolve())
        outputs["sqlite"] = str((target / "rag.sqlite").resolve())
    atomic_write_text(
        manifest_path,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )


def _required_stage_paths(stage: Path, required_files: tuple[str, ...] | None = None) -> list[Path]:
    names = required_files or (*COLLECTOR_OUTPUTS, *BUILD_OUTPUTS)
    return [stage / name for name in names]


def commit_refresh_stage(
    stage: Path,
    index_dir: Path,
    *,
    required_files: tuple[str, ...] | None = None,
    prune_files: tuple[str, ...] = (),
    prune_directories: tuple[str, ...] = (),
) -> None:
    """Promote all validated refresh files with rollback on promotion failure."""

    stage = stage.resolve()
    target = index_dir.resolve()
    if stage.parent != target.parent or not stage.name.startswith(
        f".{target.name}.direct-refresh-"
    ):
        raise ValueError("refresh stage must be a generated same-volume Direct RAG stage")
    required_names = tuple(required_files or (*COLLECTOR_OUTPUTS, *BUILD_OUTPUTS))
    invalid_required = set(required_names) - set(MANAGED_FILES)
    if invalid_required:
        raise ValueError(
            "refresh required file is not transaction-managed: "
            + ", ".join(sorted(invalid_required))
        )
    missing = [path.name for path in _required_stage_paths(stage, required_names) if not path.is_file()]
    if missing:
        raise RuntimeError(f"refresh stage is incomplete: {', '.join(sorted(missing))}")

    invalid_prunes = (set(prune_files) - set(MANAGED_FILES)) | (
        set(prune_directories) - set(MANAGED_DIRECTORIES)
    )
    if invalid_prunes:
        raise ValueError(
            "refresh prune target is not transaction-managed: "
            + ", ".join(sorted(invalid_prunes))
        )

    target.mkdir(parents=True, exist_ok=True)
    prune_names = set(prune_files) | set(prune_directories)
    planned_names = tuple(
        name
        for name in (*MANAGED_FILES, *MANAGED_DIRECTORIES)
        if (stage / name).exists() or name in prune_names
    )
    previous_names = tuple(name for name in planned_names if (target / name).exists())
    swap_refresh_generation(
        stage,
        target,
        planned_names,
        previous_names,
        prune_names,
    )


def recover_interrupted_refresh(index_dir: Path) -> dict:
    return _recover_interrupted_refresh(
        index_dir,
        (*MANAGED_FILES, *MANAGED_DIRECTORIES),
    )


def discard_refresh_stage(stage: Path | None) -> None:
    if stage is not None:
        shutil.rmtree(stage, ignore_errors=True)


__all__ = [
    "AUXILIARY_FILES",
    "BUILD_OUTPUTS",
    "MANAGED_DIRECTORIES",
    "MANAGED_FILES",
    "PRESERVED_STATE_FILES",
    "RETIRED_MANAGED_FILES",
    "commit_refresh_stage",
    "discard_refresh_stage",
    "prepare_refresh_stage",
    "recover_interrupted_refresh",
    "rebase_stage_manifest",
]
