#!/usr/bin/env python
"""Merge one project's collector outputs without deleting other project roots."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from atomic_io import atomic_write_text
from workspace_paths import filesystem_path_identity

PROJECT_RAW_FILES = (
    "raw_projects.jsonl",
    "raw_project_profiles.jsonl",
    "raw_project_architecture.jsonl",
    "raw_project_symbols.jsonl",
)


def _rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    result: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid collector JSONL {path.name}:{line_no}: {exc}") from exc
        if not isinstance(row, dict):
            raise RuntimeError(f"Collector row must be an object: {path.name}:{line_no}")
        result.append(row)
    return result


def _row_root(row: dict[str, Any]) -> str:
    metadata = row.get("metadata")
    if not isinstance(metadata, dict):
        return ""
    return filesystem_path_identity(
        metadata.get("project_root") or metadata.get("projectRoot") or "",
        strip_project_uri=False,
    )


def _row_project(row: dict[str, Any]) -> str:
    metadata = row.get("metadata")
    if not isinstance(metadata, dict):
        return ""
    return str(metadata.get("project") or "").strip().casefold()


def _exact_identity(project: Path) -> tuple[str, str]:
    descriptor = project.expanduser().resolve()
    if not descriptor.is_file() or descriptor.suffix.casefold() != ".uproject":
        raise RuntimeError(f"Project merge requires one exact .uproject: {project}")
    root = filesystem_path_identity(descriptor.parent, strip_project_uri=False)
    return root, descriptor.stem.casefold()


def _absolute_path_within(value: Any, root: Path) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        return False
    try:
        candidate.resolve().relative_to(root)
    except (OSError, ValueError):
        return False
    return True


def _legacy_symbol_belongs_to_project(row: dict[str, Any], project: Path) -> bool:
    """Recognize only the old symbol schema sufficiently to replace its own rows."""

    if _row_root(row):
        return False
    metadata = row.get("metadata")
    if not isinstance(metadata, dict):
        return False
    descriptor = project.expanduser().resolve()
    if (
        _row_project(row) != descriptor.stem.casefold()
        or str(row.get("source") or "").strip() != "unreal_symbol"
        or str(metadata.get("scope") or "").strip().casefold() != "project"
    ):
        return False
    root = descriptor.parent.resolve()
    return _absolute_path_within(metadata.get("root"), root) and _absolute_path_within(
        row.get("path"), root
    )


def merge_project_jsonl(destination: Path, incoming: Path, project: Path) -> None:
    expected = _exact_identity(project)
    new_rows = _rows(incoming)
    wrong = [
        row
        for row in new_rows
        if (_row_root(row), _row_project(row)) != expected
    ]
    if wrong:
        raise RuntimeError(
            f"Collector output {incoming.name} was not bound to the exact captured project"
        )
    retained = [
        row
        for row in _rows(destination)
        if (
            (_row_root(row), _row_project(row)) != expected
            and not (
                destination.name == "raw_project_symbols.jsonl"
                and _legacy_symbol_belongs_to_project(row, project)
            )
        )
    ]
    rendered = "".join(
        json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
        for row in (*retained, *new_rows)
    )
    atomic_write_text(destination, rendered)


def replace_project_architecture(
    destination_root: Path,
    incoming_root: Path,
    project: Path,
) -> None:
    descriptor = project.expanduser().resolve()
    _exact_identity(descriptor)
    identity = filesystem_path_identity(descriptor, strip_project_uri=False)
    key = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    destination_root.mkdir(parents=True, exist_ok=True)
    destination = destination_root / key
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(incoming_root, destination)
    for legacy_file in destination_root.iterdir():
        if legacy_file.is_file():
            legacy_file.unlink()


def merge_project_collection(stage: Path, collection: Path, project: Path) -> None:
    for name in PROJECT_RAW_FILES:
        incoming = collection / name
        if not incoming.is_file():
            raise RuntimeError(f"Collector did not produce required output: {name}")
        merge_project_jsonl(stage / name, incoming, project)
    architecture = collection / "project_architecture"
    if not architecture.is_dir():
        raise RuntimeError("Architecture collector did not produce its output directory")
    replace_project_architecture(stage / "project_architecture", architecture, project)


__all__ = [
    "PROJECT_RAW_FILES",
    "merge_project_collection",
    "merge_project_jsonl",
    "replace_project_architecture",
]
