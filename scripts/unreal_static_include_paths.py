#!/usr/bin/env python
"""Where a quoted include resolves: compiler lookup order, UBT include roots,
and which include lines cannot prove a missing header (conditional, commented).
Shared by the include existence and visibility checks."""

from __future__ import annotations

import re
from pathlib import Path

from cpp_parse_utils import (
    find_balanced_parens,
    mask_comments_and_strings,
)
from unreal_static_scan import _source_module_root


def normalize_include_path(include: str) -> str:
    """Slashes unified, empty and "." segments dropped, ".." folded where it can be."""
    parts: list[str] = []
    for part in include.replace("\\", "/").split("/"):
        if part in {"", "."}:
            continue
        if part == ".." and parts and parts[-1] != "..":
            parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


_INCLUDE_PATH_CALL = re.compile(r"\b(?:Public|Private)IncludePaths\s*\.\s*(Add|AddRange)\s*\(")
_PATH_COMBINE = re.compile(r'^Path\s*\.\s*Combine\s*\(\s*ModuleDirectory\s*((?:,\s*"[^"\\]*"\s*)*)\)$')
_STRING_LITERAL = re.compile(r'"([^"\\]*)"')
_MODULE_ROOT_CACHE: dict[str, tuple[tuple[Path, ...], bool]] = {}


def _literal_include_root(argument: str, module_root: Path) -> Path | None:
    value = argument.strip()
    if value == "ModuleDirectory":
        return module_root
    combined = _PATH_COMBINE.match(value)
    if combined:
        return module_root.joinpath(*_STRING_LITERAL.findall(combined.group(1)))
    literal = re.fullmatch(r'"([^"\\]*)"', value)
    return module_root / literal.group(1) if literal else None


def module_include_roots(path: Path, project_root: Path) -> tuple[tuple[Path, ...], bool]:
    """UBT's default include roots for the file's module plus the Build.cs
    entries written as literals. ``complete`` is False when Build.cs adds a
    path this parser cannot evaluate: a missing include is then not provable."""
    module_root = _source_module_root(path, project_root)
    if module_root is None:
        return (), True
    build_files = sorted(module_root.glob("*.Build.cs"))
    # Keyed by Build.cs versions: a long-lived importer sees edits to them.
    key = str(module_root) + "|" + "|".join(f"{item.name}:{item.stat().st_mtime_ns}" for item in build_files)
    if key in _MODULE_ROOT_CACHE:
        return _MODULE_ROOT_CACHE[key]
    roots = [module_root / "Public", module_root / "Private", module_root / "Classes", module_root.parent]
    complete = True
    for build_cs in build_files:
        try:
            text = build_cs.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            complete = False
            continue
        masked = mask_comments_and_strings(text)
        for match in _INCLUDE_PATH_CALL.finditer(masked):
            close = find_balanced_parens(text, match.end() - 1)
            argument = text[match.end():close].strip() if close > match.end() else ""
            root = _literal_include_root(argument, module_root) if match.group(1) == "Add" else None
            if root is None:
                complete = False
            else:
                roots.append(root)
    _MODULE_ROOT_CACHE[key] = (tuple(roots), complete)
    return _MODULE_ROOT_CACHE[key]


def _exact_case(candidate: Path) -> bool:
    try:
        return candidate.name in {entry.name for entry in candidate.parent.iterdir()}
    except OSError:
        return True


def resolve_quoted_include(include: str, including_file: Path, include_index: dict[str, list[str]],
                           roots: tuple[Path, ...] = ()) -> dict[str, object]:
    """Where a quoted include resolves, as compilers search it: the including
    file's directory first, then the module's include roots. Shared by the
    existence and visibility checks so both see the same headers."""
    normalized = normalize_include_path(include)
    for candidate in include_index.get(normalized, []):
        return {"found": True, "via": "index", "path": Path(candidate), "normalized": normalized, "caseMismatch": False}
    for via, base in [("file_relative", including_file.parent), *(("include_root", root) for root in roots)]:
        candidate = base / include.replace("\\", "/")
        if candidate.is_file():
            return {"found": True, "via": via, "path": candidate, "normalized": normalized,
                    "caseMismatch": not _exact_case(candidate)}
    return {"found": False, "normalized": normalized, "escapes": normalized.startswith("..")}


_CONDITIONAL_OPEN = re.compile(r"^\s*#\s*(if|ifdef|ifndef)\b\s*(\w*)")
_CONDITIONAL_CLOSE = re.compile(r"^\s*#\s*endif\b")


def conditional_include_lines(text: str) -> set[int]:
    """Lines inside a preprocessor conditional, other than a header guard."""
    lines = text.splitlines()
    stack: list[bool] = []
    inside: set[int] = set()
    seen_directive = False
    for number, line in enumerate(lines, start=1):
        opened = _CONDITIONAL_OPEN.match(line)
        if opened:
            following = next((item.strip() for item in lines[number:] if item.strip()), "")
            guard = (not seen_directive and opened.group(1) == "ifndef"
                     and re.match(rf"#\s*define\s+{re.escape(opened.group(2))}\b", following) is not None)
            stack.append(not guard)
            seen_directive = True
            continue
        if _CONDITIONAL_CLOSE.match(line):
            if stack:
                stack.pop()
            continue
        if re.match(r"^\s*#", line):
            seen_directive = True
        if any(stack):
            inside.add(number)
    return inside


def commented_lines(text: str) -> set[int]:
    """Lines whose ``#include`` sits inside a block comment."""
    masked = mask_comments_and_strings(text)  # same length; comment text, newlines included, become spaces
    inside: set[int] = set()
    offset = 0
    for number, line in enumerate(text.splitlines(keepends=True), start=1):
        if re.match(r"\s*#\s*include", line) and masked[offset + line.index("#")] != "#":
            inside.add(number)
        offset += len(line)
    return inside


__all__ = [
    "normalize_include_path",
    "module_include_roots",
    "resolve_quoted_include",
    "conditional_include_lines",
    "commented_lines",
]
