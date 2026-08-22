#!/usr/bin/env python
"""Collect every exact project in one engine-bound installer stage."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from direct_rag_project_collection import run_project_collectors
from direct_rag_project_merge import PROJECT_RAW_FILES
from workspace_paths import canonical_absolute_path_identity


def exact_projects(values: list[str]) -> tuple[Path, ...]:
    projects: list[Path] = []
    seen: set[str] = set()
    for value in values:
        descriptor = Path(value).expanduser().resolve()
        if not descriptor.is_file() or descriptor.suffix.casefold() != ".uproject":
            raise ValueError(f"project set requires an exact existing .uproject: {value}")
        identity = canonical_absolute_path_identity(descriptor)
        if identity not in seen:
            projects.append(descriptor)
            seen.add(identity)
    if not projects:
        raise ValueError("project set requires at least one --project")
    return tuple(projects)


def reset_project_outputs(stage: Path) -> None:
    for name in PROJECT_RAW_FILES:
        (stage / name).unlink(missing_ok=True)
    shutil.rmtree(stage / "project_architecture", ignore_errors=True)


def collect_project_set(
    workspace: Path,
    stage: Path,
    projects: tuple[Path, ...],
) -> None:
    stage.mkdir(parents=True, exist_ok=True)
    reset_project_outputs(stage)
    for descriptor in projects:
        steps, failed = run_project_collectors(
            workspace,
            descriptor,
            stage,
            print,
        )
        if failed:
            detail = next(
                (str(step.get("outputTail") or "") for step in steps if step["name"] == failed),
                "",
            )
            raise RuntimeError(f"project collector failed for {descriptor}: {failed}\n{detail}")
    print(f"done: collected {len(projects)} exact Unreal projects into {stage}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect a complete engine-bound set of exact Unreal projects."
    )
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--project", action="append", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        projects = exact_projects(args.project)
        collect_project_set(
            Path(args.workspace).expanduser().resolve(),
            Path(args.out_dir).expanduser().resolve(),
            projects,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["collect_project_set", "exact_projects", "reset_project_outputs"]
