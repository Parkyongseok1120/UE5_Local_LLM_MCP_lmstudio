#!/usr/bin/env python
"""Resolve placeholder paths in plan slices before activation."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

PLACEHOLDER_TOKEN = re.compile(r"<(\w+)>")


def _is_placeholder_path(path: str) -> bool:
    return "<" in path or ">" in path


def _stem_candidates(project_root: Path, stem: str) -> list[str]:
    matches: list[str] = []
    for suffix in (".h", ".cpp"):
        for candidate in project_root.rglob(f"{stem}{suffix}"):
            if not candidate.is_file():
                continue
            try:
                rel = candidate.resolve().relative_to(project_root.resolve()).as_posix()
            except ValueError:
                continue
            if "Intermediate/" in rel or "Binaries/" in rel:
                continue
            matches.append(rel)
    return sorted(set(matches))


def _hint_for_token(token: str, hints: dict[str, str]) -> str:
    lowered = token.lower()
    for key, value in hints.items():
        if key.lower() == lowered and value.strip():
            return value.strip()
    alias = {
        "owner": hints.get("owner") or hints.get("class") or hints.get("actor") or "",
        "actor": hints.get("actor") or hints.get("class") or hints.get("owner") or "",
        "class": hints.get("class") or hints.get("owner") or hints.get("actor") or "",
    }
    return str(alias.get(lowered) or "").strip()


def resolve_placeholder_path(path: str, *, project_root: Path, hints: dict[str, str]) -> tuple[str, str]:
    if not _is_placeholder_path(path):
        return path.replace("\\", "/"), ""
    token_match = PLACEHOLDER_TOKEN.search(path)
    if not token_match:
        return path.replace("\\", "/"), "unrecognized placeholder syntax"
    token = token_match.group(1)
    hint = _hint_for_token(token, hints)
    if not hint:
        return path.replace("\\", "/"), f"ambiguous placeholder <{token}> (no symbol hint)"
    suffix = path[token_match.end() :]
    stem = Path(hint).stem if hint.endswith((".h", ".cpp")) else hint
    if suffix.startswith("."):
        concrete = f"{stem}{suffix}"
        candidate = project_root / concrete.replace("\\", "/")
        if candidate.is_file():
            return concrete.replace("\\", "/"), ""
        matches = _stem_candidates(project_root, stem)
        filtered = [item for item in matches if item.endswith(suffix)]
        if len(filtered) == 1:
            return filtered[0], ""
        if len(filtered) > 1:
            return path.replace("\\", "/"), f"ambiguous placeholder <{token}> ({len(filtered)} matches)"
        return path.replace("\\", "/"), f"placeholder <{token}> could not be resolved"
    matches = _stem_candidates(project_root, stem)
    if len(matches) == 1:
        return matches[0], ""
    if len(matches) > 1:
        return path.replace("\\", "/"), f"ambiguous placeholder <{token}> ({len(matches)} matches)"
    return path.replace("\\", "/"), f"placeholder <{token}> could not be resolved"


def resolve_plan_slices(
    plan_slices: list[dict[str, Any]],
    *,
    project_root: Path,
    hints: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    resolved: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    hint_map = dict(hints or {})
    for item in plan_slices:
        copy = dict(item)
        files = [str(raw) for raw in (item.get("files") or [])]
        concrete_files: list[str] = []
        reasons: list[str] = []
        for raw in files:
            concrete, reason = resolve_placeholder_path(raw, project_root=project_root, hints=hint_map)
            concrete_files.append(concrete)
            if reason:
                reasons.append(reason)
        copy["files"] = concrete_files
        copy["originalFiles"] = files
        if reasons:
            copy["status"] = "blocked"
            copy["writesDisabled"] = True
            copy["slice_kind"] = "analysis"
            copy["sliceKind"] = "analysis"
            question = (
                f"Resolve plan slice '{item.get('slice_id') or item.get('sliceId')}': "
                + "; ".join(sorted(set(reasons)))
            )
            copy["clarificationQuestion"] = question
            blocked.append({"sliceId": copy.get("slice_id") or copy.get("sliceId"), "reasons": reasons, "question": question})
        resolved.append(copy)
    return resolved, blocked
