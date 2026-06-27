#!/usr/bin/env python
"""Safe exact-match file patch application."""

from __future__ import annotations

import re
from pathlib import Path

ALLOWED_EXTENSIONS = {".h", ".hpp", ".hh", ".cpp", ".cxx", ".cc", ".cs", ".ini", ".json", ".uplugin", ".uproject", ".md", ".txt", ".Build.cs", ".Target.cs"}


def is_allowed_path(path: Path, workspace_root: Path) -> bool:
    resolved = path.resolve()
    root = workspace_root.resolve()
    if root not in resolved.parents and resolved != root:
        return False
    name = path.name
    if name.endswith(".Build.cs") or name.endswith(".Target.cs"):
        return True
    if path.suffix.lower() not in ALLOWED_EXTENSIONS and path.suffix:
        return False
    return True


def apply_patch(
    path: Path,
    old_text: str,
    new_text: str,
    expected_occurrences: int = 1,
) -> tuple[bool, str, str]:
    """Apply patch; return (ok, message, updated_content)."""
    if not old_text:
        return False, "oldText must not be empty", ""
    content = path.read_text(encoding="utf-8")
    count = content.count(old_text)
    if count == 0:
        return False, "oldText not found", content
    if count != expected_occurrences:
        return False, f"expected {expected_occurrences} occurrence(s), found {count}", content
    updated = content.replace(old_text, new_text, expected_occurrences)
    return True, "ok", updated


def apply_patches(
    workspace_root: Path,
    patches: list[dict],
) -> tuple[list[Path], list[str]]:
    written: list[Path] = []
    errors: list[str] = []
    for item in patches:
        rel = str(item.get("path") or "").strip()
        if not rel:
            errors.append("patch missing path")
            continue
        target = (workspace_root / rel).resolve()
        if not is_allowed_path(target, workspace_root):
            errors.append(f"path not allowed: {rel}")
            continue
        ok, msg, updated = apply_patch(
            target,
            str(item.get("oldText") or ""),
            str(item.get("newText") or ""),
            int(item.get("expectedOccurrences") or 1),
        )
        if not ok:
            errors.append(f"{rel}: {msg}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(updated, encoding="utf-8")
        written.append(target)
    return written, errors
