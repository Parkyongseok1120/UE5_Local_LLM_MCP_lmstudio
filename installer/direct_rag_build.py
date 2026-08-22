#!/usr/bin/env python3
"""Compose a transactional, engine-bound Direct RAG installer build."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from collect_unreal_projects import find_projects  # noqa: E402
from direct_rag_manifest_binding import engine_bindings_match  # noqa: E402
from direct_rag_project_engine import engine_root_version, project_engine_version  # noqa: E402
from installer.direct_rag_build_model import BuildStep, DirectRagBuildPlan  # noqa: E402
from installer.direct_rag_build_scope import resolve_build_scope  # noqa: E402
from installer.direct_rag_build_stage import prepare_build_stage  # noqa: E402
from installer.direct_rag_build_steps import create_build_steps  # noqa: E402


def create_direct_rag_build_plan(
    *,
    python_executable: Path,
    index_dir: Path,
    tier: str,
    project_roots: list[Path],
    active_project: Path | None,
    engine_root: Path | None,
    root: Path = ROOT,
    guidelines_root: Path | None = None,
    game_design_root: Path | None = None,
    dry_run: bool = False,
) -> DirectRagBuildPlan:
    """Create a staged build whose project corpus matches one engine binding."""

    scope = resolve_build_scope(
        python_executable=python_executable,
        index_dir=index_dir,
        tier=tier,
        project_roots=project_roots,
        active_project=active_project,
        engine_root=engine_root,
        root=root,
        dry_run=dry_run,
        project_binding=project_engine_version,
        engine_version=engine_root_version,
        bindings_match=engine_bindings_match,
        discover_projects=find_projects,
    )
    stage = prepare_build_stage(
        target=scope.target,
        tier=scope.tier,
        has_exact_projects=bool(scope.included_projects),
        dry_run=dry_run,
    )
    try:
        steps, required = create_build_steps(
            scope,
            stage.path,
            guidelines_root=guidelines_root,
            game_design_root=game_design_root,
        )
    except BaseException:
        if not dry_run:
            from direct_rag_refresh_transaction import discard_refresh_stage

            discard_refresh_stage(stage.path)
        raise
    return DirectRagBuildPlan(
        tier=scope.tier,
        index_dir=scope.target,
        stage_dir=stage.path,
        steps=steps,
        required_files=required,
        prune_files=stage.prune_files,
        prune_directories=stage.prune_directories,
        engine_version=scope.engine_version,
        engine_association=scope.engine_association,
        included_projects=scope.included_projects,
        excluded_projects=scope.excluded_projects,
        dry_run=dry_run,
    )


__all__ = ["BuildStep", "DirectRagBuildPlan", "create_direct_rag_build_plan"]
