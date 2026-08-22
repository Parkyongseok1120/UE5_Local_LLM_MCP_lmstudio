"""Prepare and prune the same-volume installer build stage."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from direct_rag_refresh_transaction import (
    AUXILIARY_FILES,
    RETIRED_MANAGED_FILES,
    prepare_refresh_stage,
)
from installer.direct_rag_build_model import (
    ENGINE_OUTPUTS,
    OBSOLETE_OUTPUTS,
    PROJECT_DETAIL_OUTPUTS,
)


@dataclass(frozen=True)
class BuildStage:
    path: Path
    prune_files: tuple[str, ...]
    prune_directories: tuple[str, ...]


def _remove_from_stage(stage: Path, files: set[str], directories: set[str]) -> None:
    for name in sorted(files):
        (stage / name).unlink(missing_ok=True)
    for name in sorted(directories):
        shutil.rmtree(stage / name, ignore_errors=True)


def prepare_build_stage(
    *,
    target: Path,
    tier: str,
    has_exact_projects: bool,
    dry_run: bool,
) -> BuildStage:
    stage = (
        target.parent / f".{target.name}.direct-build-dry-run"
        if dry_run
        else prepare_refresh_stage(target)
    )
    prune_files: set[str] = {
        *AUXILIARY_FILES,
        *RETIRED_MANAGED_FILES,
        *OBSOLETE_OUTPUTS,
    }
    prune_directories: set[str] = set()
    if tier == "lite":
        prune_files.update((*ENGINE_OUTPUTS, *PROJECT_DETAIL_OUTPUTS, "raw_source.jsonl"))
        prune_directories.add("project_architecture")
    elif tier == "standard":
        prune_files.add("raw_source.jsonl")
    if not has_exact_projects and tier != "lite":
        prune_files.update(PROJECT_DETAIL_OUTPUTS)
        prune_directories.add("project_architecture")
    if not dry_run:
        _remove_from_stage(stage, prune_files, prune_directories)
    return BuildStage(
        path=stage,
        prune_files=tuple(sorted(prune_files)),
        prune_directories=tuple(sorted(prune_directories)),
    )


__all__ = ["BuildStage", "prepare_build_stage"]
