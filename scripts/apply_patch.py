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


def _line_without_leading_ws(line: str) -> str:
    return line.lstrip(" \t").rstrip("\r\n")


def _first_nonblank_indent(lines: list[str]) -> str:
    for line in lines:
        if line.strip():
            return line[: len(line) - len(line.lstrip(" \t"))]
    return ""


def _adapt_new_text_indent(new_text: str, old_lines: list[str], actual_lines: list[str]) -> str:
    old_indent = _first_nonblank_indent(old_lines)
    actual_indent = _first_nonblank_indent(actual_lines)
    if old_indent == actual_indent:
        return new_text

    adapted_lines: list[str] = []
    for line in new_text.splitlines(keepends=True):
        if not line.strip():
            adapted_lines.append(line)
        elif old_indent and line.startswith(old_indent):
            adapted_lines.append(actual_indent + line[len(old_indent):])
        elif not old_indent and actual_indent:
            adapted_lines.append(actual_indent + line)
        else:
            adapted_lines.append(line)
    adapted = "".join(adapted_lines)

    if not new_text.endswith(("\n", "\r")) and actual_lines:
        match = re.search(r"(\r\n|\n|\r)$", actual_lines[-1])
        if match:
            adapted += match.group(1)
    return adapted


def _apply_leading_ws_normalized_patch(
    content: str,
    old_text: str,
    new_text: str,
    expected_occurrences: int,
) -> tuple[bool, str, str]:
    if expected_occurrences != 1:
        return False, "oldText not found; leading whitespace fallback supports expectedOccurrences=1 only", content
    if "\n" not in old_text and "\r" not in old_text:
        return False, "oldText not found", content

    old_lines = old_text.splitlines(keepends=True)
    content_lines = content.splitlines(keepends=True)
    if not old_lines or len(old_lines) > len(content_lines):
        return False, "oldText not found", content

    wanted = [_line_without_leading_ws(line) for line in old_lines]
    matches: list[tuple[int, list[str]]] = []
    window_size = len(old_lines)
    for start in range(0, len(content_lines) - window_size + 1):
        window = content_lines[start:start + window_size]
        if [_line_without_leading_ws(line) for line in window] == wanted:
            matches.append((start, window))

    if len(matches) != 1:
        return False, f"oldText not found; leading whitespace normalized candidates={len(matches)}", content

    start, actual_lines = matches[0]
    adapted_new_text = _adapt_new_text_indent(new_text, old_lines, actual_lines)
    updated = (
        "".join(content_lines[:start])
        + adapted_new_text
        + "".join(content_lines[start + window_size:])
    )
    return True, "ok (leading whitespace normalized)", updated


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
        return _apply_leading_ws_normalized_patch(content, old_text, new_text, expected_occurrences)
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
