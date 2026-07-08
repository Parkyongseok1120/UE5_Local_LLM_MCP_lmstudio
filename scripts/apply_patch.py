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


def _apply_single_line_normalized_patch(
    content: str,
    old_text: str,
    new_text: str,
    expected_occurrences: int,
) -> tuple[bool, str, str]:
    if expected_occurrences != 1:
        return False, "oldText not found; single-line fallback supports expectedOccurrences=1 only", content
    if "\n" in old_text or "\r" in old_text:
        return False, "oldText not found", content
    wanted = old_text.strip()
    if not wanted:
        return False, "oldText must not be empty", content
    lines = content.splitlines(keepends=True)
    matches: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        if wanted in line or _line_without_leading_ws(line) == _line_without_leading_ws(wanted):
            matches.append((index, line))
    if len(matches) != 1:
        return False, f"oldText not found; single-line normalized candidates={len(matches)}", content
    index, actual_line = matches[0]
    actual_lines = [actual_line]
    old_lines = [old_text if old_text.endswith(("\n", "\r")) else old_text + "\n"]
    adapted_new_text = _adapt_new_text_indent(new_text, old_lines, actual_lines)
    if not adapted_new_text.endswith(("\n", "\r")) and actual_line.endswith(("\n", "\r")):
        adapted_new_text += actual_line[len(actual_line.rstrip("\r\n")) :]
    updated = "".join(lines[:index]) + adapted_new_text + "".join(lines[index + 1 :])
    return True, "ok (single-line normalized)", updated


def patch_apply_hint(path: Path, old_text: str) -> str:
    if not path.is_file() or not old_text:
        return ""
    content = path.read_text(encoding="utf-8-sig")
    needle = old_text.strip()
    if not needle:
        return ""
    if needle in content:
        return ""
    for line_no, line in enumerate(content.splitlines(), start=1):
        if needle in line or _line_without_leading_ws(line) == _line_without_leading_ws(needle):
            return f" nearest file line {line_no}: {line.strip()[:160]}"
        if ".Broadcast(" in needle and ".Broadcast(" in line:
            return f" nearest Broadcast line {line_no}: {line.strip()[:160]}"
    return ""


def validate_patch_item(path: Path, old_text: str, new_text: str, expected_occurrences: int = 1) -> tuple[bool, str]:
    if not old_text:
        return False, "oldText must not be empty"
    if not path.is_file():
        return False, f"file not found: {path}"
    ok, msg, _ = apply_patch(path, old_text, new_text, expected_occurrences, dry_run=True)
    if ok:
        return True, ""
    hint = patch_apply_hint(path, old_text)
    return False, f"{msg}{hint}"


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


def apply_patch_content(
    content: str,
    old_text: str,
    new_text: str,
    expected_occurrences: int = 1,
) -> tuple[bool, str, str]:
    """Apply a patch to in-memory content; return (ok, message, updated_content)."""
    if not old_text:
        return False, "oldText must not be empty", content
    count = content.count(old_text)
    if count == 0:
        ok, msg, updated = _apply_leading_ws_normalized_patch(content, old_text, new_text, expected_occurrences)
        if ok:
            return ok, msg, updated
        ok2, msg2, updated2 = _apply_single_line_normalized_patch(content, old_text, new_text, expected_occurrences)
        if ok2:
            return ok2, msg2, updated2
        for candidate_msg, candidate_updated in ((msg, updated), (msg2, updated2)):
            if "candidates=" in candidate_msg:
                return False, candidate_msg, candidate_updated
        return False, msg2, updated2
    if count != expected_occurrences:
        return False, f"expected {expected_occurrences} occurrence(s), found {count}", content
    updated = content.replace(old_text, new_text, expected_occurrences)
    return True, "ok", updated


def apply_patch(
    path: Path,
    old_text: str,
    new_text: str,
    expected_occurrences: int = 1,
    *,
    dry_run: bool = False,
) -> tuple[bool, str, str]:
    """Apply patch; return (ok, message, updated_content). When dry_run=True, do not write."""
    content = path.read_text(encoding="utf-8-sig") if path.is_file() else ""
    return apply_patch_content(content, old_text, new_text, expected_occurrences)


def apply_patches(
    workspace_root: Path,
    patches: list[dict],
) -> tuple[list[Path], list[str]]:
    written: list[Path] = []
    errors: list[str] = []
    targets: list[Path] = []
    for item in patches:
        rel = str(item.get("path") or "").strip()
        if not rel:
            continue
        target = (workspace_root / rel).resolve()
        if is_allowed_path(target, workspace_root):
            targets.append(target)
    snapshot = {
        path: path.read_text(encoding="utf-8-sig") if path.is_file() else ""
        for path in targets
    }
    try:
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
            target.write_text(updated, encoding="utf-8", newline="\n")
            written.append(target)
        if errors:
            for path, content in snapshot.items():
                if content:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(content, encoding="utf-8", newline="\n")
                elif path.is_file():
                    path.unlink()
            written.clear()
    except Exception:
        for path, content in snapshot.items():
            if content:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8", newline="\n")
            elif path.is_file():
                path.unlink()
        raise
    return written, errors
