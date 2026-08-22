#!/usr/bin/env python
"""Migrate only unambiguous legacy Editor rows to exact project ownership.

Old Editor exports recorded a project stem but no project root.  Asset paths
such as ``/Game/Foo`` cannot distinguish same-name clones, so the selected
project may claim those rows only when the staged ``raw_projects.jsonl``
descriptor inventory proves that the stem belongs to exactly one root.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from atomic_io import atomic_write_text
from direct_rag_raw_scope import PROJECT_SCOPED_SOURCES
from editor_metadata_catalog import METADATA_FILES
from workspace_paths import filesystem_path_identity

EDITOR_RAW_FILES = tuple(dict.fromkeys(METADATA_FILES.values()))


def _metadata(row: dict[str, Any]) -> dict[str, Any] | None:
    value = row.get("metadata")
    return value if isinstance(value, dict) else None


def _read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(
        path.read_text(encoding="utf-8-sig", errors="strict").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Invalid legacy Editor JSONL {path.name}:{line_no}: {exc}"
            ) from exc
        if not isinstance(row, dict):
            raise RuntimeError(
                f"Legacy Editor row must be an object: {path.name}:{line_no}"
            )
        rows.append(row)
    return rows


def _descriptor_inventory(raw_projects: Path) -> dict[str, set[str]]:
    """Return stem -> exact root identities from proven descriptor rows only."""

    inventory: dict[str, set[str]] = {}
    for row in _read_rows(raw_projects):
        metadata = _metadata(row)
        if metadata is None or str(row.get("source") or "") != "unreal_project_text":
            continue
        project = str(metadata.get("project") or "").strip()
        root_text = str(
            metadata.get("project_root") or metadata.get("projectRoot") or ""
        ).strip()
        row_path = str(row.get("path") or "").strip()
        if not project or not root_text or not row_path:
            continue
        root = Path(root_text).expanduser().resolve()
        descriptor = (root / f"{project}.uproject").resolve()
        supplied = Path(row_path).expanduser().resolve()
        if (
            descriptor.suffix.casefold() != ".uproject"
            or not descriptor.is_file()
            or filesystem_path_identity(supplied, strip_project_uri=False)
            != filesystem_path_identity(descriptor, strip_project_uri=False)
        ):
            continue
        stem = project.casefold()
        inventory.setdefault(stem, set()).add(
            filesystem_path_identity(root, strip_project_uri=False)
        )
    return inventory


def legacy_descriptor_roots(stage: Path, project: Path) -> frozenset[str]:
    """Capture the roots already proven by the live generation before merge."""

    descriptor = project.expanduser().resolve()
    if not descriptor.is_file() or descriptor.suffix.casefold() != ".uproject":
        raise RuntimeError(f"Editor legacy migration requires one exact .uproject: {project}")
    return frozenset(
        _descriptor_inventory(stage / "raw_projects.jsonl").get(
            descriptor.stem.casefold(), set()
        )
    )


def migrate_legacy_editor_rows(
    stage: Path,
    project: Path,
    prior_descriptor_roots: frozenset[str],
) -> dict[str, Any]:
    """Attach exact ownership only when the descriptor inventory is unique.

    Ambiguous, absent, malformed, already-rooted, or foreign rows are never
    guessed away.  They remain available for the normal provenance validator
    to reject, preserving the fail-closed boundary.
    """

    descriptor = project.expanduser().resolve()
    if not descriptor.is_file() or descriptor.suffix.casefold() != ".uproject":
        raise RuntimeError(f"Editor legacy migration requires one exact .uproject: {project}")
    expected_root = descriptor.parent.resolve()
    expected_identity = filesystem_path_identity(
        expected_root, strip_project_uri=False
    )
    candidates = set(prior_descriptor_roots)
    if candidates != {expected_identity}:
        return {
            "ok": False,
            "reason": "descriptor_inventory_not_unique",
            "project": descriptor.stem,
            "candidateRoots": sorted(candidates),
            "migratedRows": 0,
            "changedFiles": [],
        }

    migrated_rows = 0
    changed_files: list[str] = []
    for name in EDITOR_RAW_FILES:
        path = stage / name
        rows = _read_rows(path)
        changed = 0
        for row in rows:
            metadata = _metadata(row)
            if metadata is None:
                continue
            existing_root = str(
                metadata.get("project_root") or metadata.get("projectRoot") or ""
            ).strip()
            row_project = str(metadata.get("project") or "").strip()
            source = str(row.get("source") or "").strip()
            if (
                existing_root
                or row_project.casefold() != descriptor.stem.casefold()
                or source not in PROJECT_SCOPED_SOURCES
            ):
                continue
            metadata["project"] = descriptor.stem
            metadata["project_root"] = str(expected_root)
            changed += 1
        if not changed:
            continue
        rendered = "".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            for row in rows
        )
        atomic_write_text(path, rendered)
        migrated_rows += changed
        changed_files.append(name)

    return {
        "ok": True,
        "project": descriptor.stem,
        "projectRoot": str(expected_root),
        "migratedRows": migrated_rows,
        "changedFiles": changed_files,
    }


__all__ = [
    "EDITOR_RAW_FILES",
    "legacy_descriptor_roots",
    "migrate_legacy_editor_rows",
]
