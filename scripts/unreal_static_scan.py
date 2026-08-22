#!/usr/bin/env python
"""Source discovery, indexing, and reusable C++ parsing primitives."""

from __future__ import annotations

import os
import re
from pathlib import Path

from cpp_parse_utils import (
    mask_comments_and_strings,
)
from unreal_source_extensions import (
    UNREAL_SCAN_SUFFIXES,
)
from unreal_static_model import (
    CPP_HEADER_SUFFIXES,
    CPP_SOURCE_SUFFIXES,
    IGNORED_PROJECT_DIRS,
    SOURCE_ONLY_SUFFIXES,
)


def resolve_scan_roots(root: Path) -> list[Path]:
    last_error = ""
    try:
        from plugin_project_context import resolve_scan_roots as plugin_scan_roots

        roots = plugin_scan_roots(root)
        if roots:
            return roots
        last_error = "plugin resolver returned empty roots"
    except Exception as exc:
        last_error = str(exc)
    roots: list[Path] = []
    source = root / "Source"
    if source.is_dir():
        roots.append(source)
    allowlist = os.environ.get("PLUGIN_SCAN_ALLOWLIST", "").strip()
    if allowlist:
        for name in allowlist.split(","):
            plugin = name.strip()
            if not plugin:
                continue
            plugin_source = root / "Plugins" / plugin / "Source"
            if plugin_source.is_dir():
                roots.append(plugin_source)
    if roots:
        return roots
    try:
        from plugin_project_context import fallback_scan_roots

        fallback = fallback_scan_roots(root)
        if fallback:
            return fallback
        last_error = last_error or "fallback_scan_roots returned empty"
    except Exception as exc:
        last_error = last_error or str(exc)
    resolve_scan_roots.last_diagnostic = {
        "code": "PLUGIN_SCAN_DEGRADED",
        "message": last_error or "no scan roots resolved",
    }
    return []

def read_text(path: Path, default: str = "") -> str:
    if not path.exists():
        return default
    for encoding in ("utf-8-sig", "utf-8", "cp949", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return default

def should_ignore_project_path(path: Path) -> bool:
    return any(part in IGNORED_PROJECT_DIRS for part in path.parts)

def iter_source_files(root: Path, *, scan_roots: list[Path] | None = None) -> list[Path]:
    suffixes = UNREAL_SCAN_SUFFIXES
    try:
        from plugin_project_context import iter_scan_root_files
    except Exception:
        iter_scan_root_files = None  # type: ignore[assignment]

    roots = scan_roots if scan_roots is not None else resolve_scan_roots(root)
    files: list[Path] = []
    for scan_root in roots:
        if not scan_root.is_dir():
            continue
        if iter_scan_root_files is not None:
            files.extend(iter_scan_root_files(scan_root, skip_dirs=IGNORED_PROJECT_DIRS))
            continue
        for path in scan_root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in suffixes:
                continue
            if should_ignore_project_path(path):
                continue
            files.append(path)
    return sorted(set(files))

def resolve_write_scope_paths(root: Path, write_target: str) -> list[Path]:
    """Minimal file set for validate-on-write: target, paired header/cpp, local includes."""
    root = root.resolve()
    rel = str(write_target or "").strip().replace("\\", "/")
    if not rel:
        return []
    target_path = (root / rel).resolve()
    scope: dict[Path, None] = {}
    if target_path.is_file():
        scope[target_path] = None
    parent = target_path.parent if target_path.is_file() else (root / Path(rel).parent).resolve()
    stem = target_path.stem if target_path.is_file() else Path(rel).stem
    suffix = target_path.suffix.lower() if target_path.is_file() else Path(rel).suffix.lower()
    if suffix in CPP_SOURCE_SUFFIXES:
        for ext in sorted(CPP_HEADER_SUFFIXES):
            paired = parent / f"{stem}{ext}"
            if paired.is_file():
                scope[paired.resolve()] = None
        for paired in _find_module_paired_files(
            target_path,
            root,
            stem,
            tuple(sorted(CPP_HEADER_SUFFIXES)),
        ):
            scope[paired] = None
    elif suffix in CPP_HEADER_SUFFIXES:
        for ext in sorted(CPP_SOURCE_SUFFIXES):
            paired = parent / f"{stem}{ext}"
            if paired.is_file():
                scope[paired.resolve()] = None
        for paired in _find_module_paired_files(
            target_path,
            root,
            stem,
            tuple(sorted(CPP_SOURCE_SUFFIXES)),
        ):
            scope[paired] = None
    if target_path.is_file():
        include_index = build_source_include_index(root)
        for _, include_name in include_lines(read_text(target_path)):
            normalized = include_name.replace("\\", "/")
            if normalized in include_index:
                for candidate in include_index[normalized]:
                    candidate_path = Path(candidate)
                    if candidate_path.is_file():
                        scope[candidate_path.resolve()] = None
                continue
            if "/" not in normalized and "\\" not in normalized:
                local = parent / include_name
                if local.is_file() and local.suffix.lower() in SOURCE_ONLY_SUFFIXES:
                    scope[local.resolve()] = None
    return sorted(scope.keys())

def _source_module_root(path: Path, project_root: Path) -> Path | None:
    try:
        parts = path.resolve().relative_to(project_root.resolve()).parts
    except ValueError:
        return None
    if len(parts) >= 2 and parts[0].lower() == "source":
        return (project_root / parts[0] / parts[1]).resolve()
    if len(parts) >= 4 and parts[0].lower() == "plugins" and parts[2].lower() == "source":
        return (project_root / parts[0] / parts[1] / parts[2] / parts[3]).resolve()
    return None

def module_relative_key(path: Path, project_root: Path) -> str | None:
    module_root = _source_module_root(path, project_root)
    if module_root is None:
        return None
    try:
        tail = list(path.resolve().relative_to(module_root).parts)
    except ValueError:
        return None
    if tail and tail[0].lower() in {"public", "private", "classes"}:
        tail = tail[1:]
    return "/".join(tail).replace("\\", "/") if tail else None

def _find_module_paired_files(
    path: Path,
    project_root: Path,
    stem: str,
    paired_exts: tuple[str, ...],
) -> list[Path]:
    module_root = _source_module_root(path, project_root)
    source_key = module_relative_key(path, project_root)
    if module_root is None or not module_root.is_dir() or not source_key:
        return []
    key_dir = source_key.rsplit("/", 1)[0] if "/" in source_key else ""
    found: list[Path] = []
    for candidate in module_root.rglob(f"{stem}.*"):
        if candidate.suffix.lower() not in paired_exts:
            continue
        candidate_key = module_relative_key(candidate, project_root)
        if not candidate_key or candidate.stem != stem:
            continue
        candidate_dir = candidate_key.rsplit("/", 1)[0] if "/" in candidate_key else ""
        if candidate_dir == key_dir:
            found.append(candidate.resolve())
    return found

def class_headers_from_paths(paths: list[Path], texts: dict[Path, str]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for path in paths:
        if path.suffix.lower() not in CPP_HEADER_SUFFIXES:
            continue
        text = texts.get(path, read_text(path))
        for match in re.finditer(r"\bclass\s+(?:[A-Z0-9_]+_API\s+)?([A-Za-z_][A-Za-z0-9_]*)\b", text):
            if not _is_class_definition(text, match.start()):
                continue
            headers.setdefault(match.group(1), text)
    return headers

def class_bases_from_paths(paths: list[Path], texts: dict[Path, str]) -> dict[str, str]:
    bases: dict[str, str] = {}
    for path in paths:
        if path.suffix.lower() not in CPP_HEADER_SUFFIXES:
            continue
        bases.update(class_base_names(texts.get(path, read_text(path))))
    return bases

def build_source_include_index_for_paths(paths: list[Path]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for path in paths:
        if path.suffix.lower() not in CPP_HEADER_SUFFIXES:
            continue
        parts = path.parts
        rel = path.name
        if "Public" in parts:
            rel = "/".join(parts[parts.index("Public") + 1 :])
        elif "Private" in parts:
            rel = "/".join(parts[parts.index("Private") + 1 :])
        rel = rel.replace("\\", "/")
        index.setdefault(rel, []).append(str(path))
        index.setdefault(path.name, []).append(str(path))
    return index

def _read_scope_texts(paths: list[Path]) -> dict[Path, str]:
    return {path: read_text(path) for path in paths if path.is_file()}

def line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1

def include_lines(text: str) -> list[tuple[int, str]]:
    results: list[tuple[int, str]] = []
    for index, line in enumerate(text.splitlines(), start=1):
        match = re.match(r'\s*#\s*include\s+[<"]([^>"]+)[>"]', line)
        if match:
            results.append((index, match.group(1)))
    return results

def has_include(text: str, include_path: str) -> bool:
    return any(value == include_path or value.endswith("/" + include_path) for _, value in include_lines(text))

def class_base_names(text: str) -> dict[str, str]:
    bases: dict[str, str] = {}
    pattern = re.compile(
        r"\bclass\s+(?:[A-Z0-9_]+_API\s+)?(?P<class>[A-Za-z_][A-Za-z0-9_]*)\s*:\s*public\s+(?P<base>[A-Za-z_][A-Za-z0-9_]*)"
    )
    for match in pattern.finditer(text):
        bases[match.group("class")] = match.group("base")
    return bases

def class_bases(root: Path) -> dict[str, str]:
    bases: dict[str, str] = {}
    for path in iter_source_files(root):
        if path.suffix.lower() not in CPP_HEADER_SUFFIXES:
            continue
        bases.update(class_base_names(read_text(path)))
    return bases

def _is_class_definition(text: str, class_start: int) -> bool:
    snippet = text[class_start : class_start + 800]
    brace = snippet.find("{")
    semi = snippet.find(";")
    if brace == -1:
        return False
    return semi == -1 or brace < semi

def _class_definition_span(text: str, class_start: int) -> tuple[int, int] | None:
    masked = mask_comments_and_strings(text)
    brace = masked.find("{", class_start)
    if brace < 0:
        return None
    depth = 0
    for index in range(brace, len(masked)):
        token = masked[index]
        if token == "{":
            depth += 1
        elif token == "}":
            depth -= 1
            if depth == 0:
                return class_start, index + 1
    return None

def _class_definition_text(text: str, class_name: str) -> tuple[str, int] | None:
    pattern = re.compile(rf"\bclass\s+(?:[A-Z0-9_]+_API\s+)?{re.escape(class_name)}\b")
    for match in pattern.finditer(text):
        if not _is_class_definition(text, match.start()):
            continue
        span = _class_definition_span(text, match.start())
        if span:
            start, end = span
            return text[start:end], start
    return None

def class_headers(root: Path) -> dict[str, str]:
    headers: dict[str, str] = {}
    for path in iter_source_files(root):
        if path.suffix.lower() not in CPP_HEADER_SUFFIXES:
            continue
        text = read_text(path)
        for match in re.finditer(r"\bclass\s+(?:[A-Z0-9_]+_API\s+)?([A-Za-z_][A-Za-z0-9_]*)\b", text):
            if _is_class_definition(text, match.start()):
                headers.setdefault(match.group(1), text)
    return headers

def find_matching_brace(text: str, open_index: int) -> int:
    depth = 0
    index = open_index
    in_string: str | None = None
    escape = False
    while index < len(text):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == in_string:
                in_string = None
        else:
            if char in {'"', "'"}:
                in_string = char
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return index
        index += 1
    return -1

def iter_function_blocks(text: str) -> list[tuple[str, int, str]]:
    blocks: list[tuple[str, int, str]] = []
    pattern = re.compile(
        r"(?P<header>(?:[\w:<>,~*&]+\s+)+(?P<name>[A-Za-z_][A-Za-z0-9_:~]*)\s*\([^;{}]*\)\s*(?:const\s*)?)\{",
        flags=re.MULTILINE,
    )
    for match in pattern.finditer(text):
        header = re.sub(r"\s+", " ", match.group("header")).strip()
        name = match.group("name")
        if name in {"if", "for", "while", "switch", "catch"}:
            continue
        open_index = match.end() - 1
        close_index = find_matching_brace(text, open_index)
        if close_index == -1:
            continue
        blocks.append((header, match.start(), text[open_index + 1 : close_index]))
    return blocks

def build_source_include_index(root: Path) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for path in iter_source_files(root):
        if path.suffix.lower() not in CPP_HEADER_SUFFIXES:
            continue
        if should_ignore_project_path(path):
            continue
        rel = module_relative_key(path, root) or path.name
        index.setdefault(rel, []).append(str(path))
        index.setdefault(path.name, []).append(str(path))
    return index

def _split_top_level_args(arg_text: str) -> list[str]:
    args: list[str] = []
    current: list[str] = []
    depth = 0
    for ch in arg_text:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            args.append("".join(current).strip())
            current = []
            continue
        current.append(ch)
    tail = "".join(current).strip()
    if tail:
        args.append(tail)
    return args

def normalize_timer_handle(expr: str) -> str:
    return re.sub(r"\s+", "", str(expr or "").strip())

def _find_enclosing_class_name(text: str, offset: int) -> str | None:
    matches = list(
        re.finditer(r"\bclass\s+(?:[A-Z0-9_]+_API\s+)?([A-Za-z_][A-Za-z0-9_]*)\b", text[:offset])
    )
    for match in reversed(matches):
        if _is_class_definition(text, match.start()):
            return match.group(1)
    return None

def iter_class_method_blocks(text: str) -> list[tuple[str, str, int, str]]:
    blocks: list[tuple[str, str, int, str]] = []
    seen: set[tuple[str, str, int]] = set()
    for header, start, body in iter_function_blocks(text):
        match = re.search(r"([A-Za-z_][A-Za-z0-9_]*)::([A-Za-z_][A-Za-z0-9_]*)\s*\(", header)
        if match:
            key = (match.group(1), match.group(2), start)
            if key not in seen:
                seen.add(key)
                blocks.append((match.group(1), match.group(2), start, body))
            continue
        name_match = re.search(
            r"(?:^|\s)(~?[A-Za-z_][A-Za-z0-9_]*)\s*\(",
            header,
        )
        if not name_match:
            continue
        method_name = name_match.group(1)
        if method_name in {"if", "for", "while", "switch", "catch"}:
            continue
        class_name = _find_enclosing_class_name(text, start)
        if not class_name:
            continue
        key = (class_name, method_name, start)
        if key not in seen:
            seen.add(key)
            blocks.append((class_name, method_name, start, body))
    return blocks

def lines_in_function_blocks(text: str) -> set[int]:
    """Line numbers inside function bodies (not signatures)."""
    blocked: set[int] = set()
    pattern = re.compile(
        r"(?:[\w:<>,~*&]+\s+)+(?P<name>[A-Za-z_][A-Za-z0-9_:~]*)\s*\([^;{}]*\)\s*(?:const\s*)?\{",
        flags=re.MULTILINE,
    )
    for match in pattern.finditer(text):
        name = match.group("name")
        if name in {"if", "for", "while", "switch", "catch"}:
            continue
        open_index = match.end() - 1
        close_index = find_matching_brace(text, open_index)
        if close_index == -1:
            continue
        start_line = text.count("\n", 0, open_index) + 1
        end_line = text.count("\n", 0, close_index) + 1
        blocked.update(range(start_line + 1, end_line))
    return blocked

resolve_scan_roots.last_diagnostic: dict[str, str] | None = None

__all__ = [
    'resolve_scan_roots',
    'read_text',
    'should_ignore_project_path',
    'iter_source_files',
    'resolve_write_scope_paths',
    '_source_module_root',
    'module_relative_key',
    '_find_module_paired_files',
    'class_headers_from_paths',
    'class_bases_from_paths',
    'build_source_include_index_for_paths',
    '_read_scope_texts',
    'line_number',
    'include_lines',
    'has_include',
    'class_base_names',
    'class_bases',
    '_is_class_definition',
    '_class_definition_span',
    '_class_definition_text',
    'class_headers',
    'find_matching_brace',
    'iter_function_blocks',
    'build_source_include_index',
    '_split_top_level_args',
    'normalize_timer_handle',
    '_find_enclosing_class_name',
    'iter_class_method_blocks',
    'lines_in_function_blocks',
]
