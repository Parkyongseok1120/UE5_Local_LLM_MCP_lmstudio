"""Small immutable types and output contracts for installer RAG builds."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from direct_rag_refresh_transaction import (
    commit_refresh_stage,
    discard_refresh_stage,
    rebase_stage_manifest,
)


TIERS = frozenset({"lite", "standard", "full"})
BUILD_OUTPUTS = ("rag.sqlite", "chunks.jsonl", "build_manifest.json")
PROJECT_DETAIL_OUTPUTS = (
    "raw_project_symbols.jsonl",
    "raw_project_profiles.jsonl",
    "raw_project_architecture.jsonl",
)
ENGINE_OUTPUTS = ("raw_symbols.jsonl",)
OBSOLETE_OUTPUTS = (
    "raw_module_graph.jsonl",
    "unreal_module_include_graph.md",
)


@dataclass(frozen=True)
class BuildStep:
    name: str
    command: tuple[str, ...]


@dataclass
class DirectRagBuildPlan:
    tier: str
    index_dir: Path
    stage_dir: Path
    steps: tuple[BuildStep, ...]
    required_files: tuple[str, ...]
    prune_files: tuple[str, ...]
    prune_directories: tuple[str, ...]
    engine_version: str
    engine_association: str
    included_projects: tuple[Path, ...]
    excluded_projects: tuple[Path, ...]
    dry_run: bool = False

    def commit(self) -> None:
        if self.dry_run:
            return
        rebase_stage_manifest(self.stage_dir, self.index_dir)
        commit_refresh_stage(
            self.stage_dir,
            self.index_dir,
            required_files=self.required_files,
            prune_files=self.prune_files,
            prune_directories=self.prune_directories,
        )

    def discard(self) -> None:
        if not self.dry_run:
            discard_refresh_stage(self.stage_dir)


__all__ = [
    "BUILD_OUTPUTS",
    "ENGINE_OUTPUTS",
    "OBSOLETE_OUTPUTS",
    "PROJECT_DETAIL_OUTPUTS",
    "TIERS",
    "BuildStep",
    "DirectRagBuildPlan",
]
