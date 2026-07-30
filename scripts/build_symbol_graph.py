#!/usr/bin/env python
"""Build a conservative, portable source-symbol graph.

The original graph was a useful Unreal symbol inventory.  It now keeps that
compatible ``symbols`` surface while adding source-backed file, definition,
include, inheritance, and *candidate* call edges.  It intentionally does not
pretend that a regex call edge proves runtime wiring or data flow.

No compiler, language server, Unreal installation, or network service is
required.  That makes the graph usable as a navigation/impact hint for other
projects too, while keeping its proof boundary explicit in the JSON contract.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

from workspace_paths import resolve_active_project_root  # noqa: E402

# C/C++ and C# keep the existing Unreal workflow working.  The remaining
# extensions are intentionally handled by the same conservative parser so the
# artifact is useful outside an Unreal project without adding runtime deps.
SOURCE_EXTS = {
    ".h", ".hpp", ".hh", ".inl", ".ipp", ".inc",
    ".cpp", ".c", ".cc", ".cxx", ".m", ".mm", ".cs",
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rs",
}
IGNORE_DIRS = {
    "binaries", "intermediate", "saved", "deriveddatacache", ".git",
    "node_modules", ".venv", "venv", "__pycache__", "dist", "build", "thirdparty",
}
LANGUAGE_BY_SUFFIX = {
    ".h": "cpp", ".hpp": "cpp", ".hh": "cpp", ".cpp": "cpp",
    ".inl": "cpp", ".ipp": "cpp", ".inc": "cpp",
    ".c": "c", ".cc": "cpp", ".cxx": "cpp", ".m": "c", ".mm": "cpp", ".cs": "csharp",
    ".py": "python", ".js": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".java": "java",
    ".go": "go", ".rs": "rust",
}

CLASS_RE = re.compile(
    r"^\s*(?:class|struct)\s+(?:(?P<api>[A-Z][A-Z0-9_]*_API)\s+)?(?P<name>[A-Za-z_]\w*)"
    r"(?:\s*:\s*(?:public|protected|private)?\s*(?P<base>[A-Za-z_]\w*))?"
)
PYTHON_CLASS_RE = re.compile(r"^\s*class\s+(?P<name>[A-Za-z_]\w*)\s*(?:\(\s*(?P<base>[A-Za-z_][\w.]*)[^)]*\))?\s*:")
JAVA_CLASS_RE = re.compile(
    r"^\s*(?:public|private|protected|abstract|final|static|\s)*\bclass\s+(?P<name>[A-Za-z_]\w*)"
    r"(?:\s+extends\s+(?P<base>[A-Za-z_][\w.]*))?"
)
ENUM_RE = re.compile(r"^\s*enum\s+(?:class\s+)?(?P<name>[A-Za-z_]\w*)")
FUNC_RE = re.compile(
    r"^\s*(?:[A-Za-z_][\w:<>,~*&\s]+\s+)+(?P<name>[A-Za-z_]\w*)\s*\([^;{}]*\)\s*(?:const\s*)?(?:;|\{)?\s*$"
)
# Like FUNC_RE, but accepts qualified definitions and both same-line and
# next-line opening braces.  It still requires a declaration-looking prefix,
# so a naked call is not promoted into a function symbol.
CPP_FUNCTION_RE = re.compile(
    r"^\s*(?:[A-Za-z_][\w:<>,~*&\s]+\s+)+(?:(?P<owner>[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*)::)?"
    r"(?P<name>[A-Za-z_]\w*)\s*\([^;{}]*\)\s*"
    r"(?:(?:const|override|final|noexcept(?:\s*\([^)]*\))?|&&?|=\s*0)\s*)*"
    r"(?:;|\{|$)"
)
CPP_CTOR_DTOR_RE = re.compile(
    r"^\s*(?P<owner>[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*)::"
    r"(?P<name>~?[A-Za-z_]\w*)\s*\([^;{}]*\)\s*"
    r"(?:noexcept(?:\s*\([^)]*\))?\s*)?(?::[^{}]*)?(?:\{|$)"
)
QUALIFIED_FUNC_RE = re.compile(
    r"\b(?P<owner>[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*)::(?P<name>[A-Za-z_]\w*)\s*\("
)
PYTHON_FUNC_RE = re.compile(r"^\s*(?:async\s+)?def\s+(?P<name>[A-Za-z_]\w*)\s*\(")
JS_FUNC_RE = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?function\s+(?P<name>[A-Za-z_$][\w$]*)\s*\("
)
GO_FUNC_RE = re.compile(r"^\s*func\s+(?:\([^)]*\)\s+)?(?P<name>[A-Za-z_]\w*)\s*\(")
RUST_FUNC_RE = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+(?P<name>[A-Za-z_]\w*)\s*\(")
INCLUDE_RE = re.compile(r"^\s*#\s*include\s+[<\"](?P<include>[^>\"]+)[>\"]")
IMPORT_RE = re.compile(
    r"^\s*(?:"
    r"from\s+(?P<py_from>[A-Za-z_][\w.]*)\s+import"
    r"|import\s+.+?\s+from\s+(?P<js_from>[\"'][^\"']+[\"'])"
    r"|import\s+(?P<plain>[A-Za-z_][\w.]*|[\"'][^\"']+[\"'])"
    r"|use\s+(?P<rust>[A-Za-z_][\w:]*)"
    r"|using\s+(?P<csharp>[A-Za-z_][\w.]*)\s*;"
    r")"
)
MODULE_RE = re.compile(r"Source[/\\](?P<module>[^/\\]+)")
CALL_RE = re.compile(r"\b(?P<name>[A-Za-z_$][\w$]*)\s*\(")
REFLECTED_TOKENS = ("UCLASS", "USTRUCT", "UENUM", "UINTERFACE", "UPROPERTY", "UFUNCTION", "GENERATED_BODY")
CALL_KEYWORDS = {
    "if", "for", "while", "switch", "catch", "return", "sizeof", "typeof", "new",
    "delete", "function", "def", "class", "struct", "enum", "do", "foreach", "lock",
}


def _iter_source_files(source_root: Path) -> list[Path]:
    files: list[Path] = []
    for current, directories, names in os.walk(source_root, followlinks=False):
        directories[:] = sorted(
            directory
            for directory in directories
            if directory.lower() not in IGNORE_DIRS
        )
        current_path = Path(current)
        for name in names:
            path = current_path / name
            if path.suffix.lower() in SOURCE_EXTS and path.is_file():
                files.append(path)
    return sorted(files)


def _language_for_path(path: Path) -> str:
    return LANGUAGE_BY_SUFFIX.get(path.suffix.lower(), "unknown")


def _module_name(path: Path) -> str:
    text = str(path).replace("\\", "/")
    match = MODULE_RE.search(text)
    return match.group("module") if match else ""


def _owner_build_cs(path: Path, source_root: Path) -> str:
    module = _module_name(path)
    if module:
        candidate = source_root / module / f"{module}.Build.cs"
        if candidate.is_file():
            return str(candidate)
    for parent in [path.parent, *path.parents]:
        for candidate in parent.glob("*.Build.cs"):
            return str(candidate)
        if parent == source_root:
            break
    return ""


def _file_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()


def _stable_id(*parts: str) -> str:
    payload = "\x1f".join(parts)
    return hashlib.sha1(payload.encode("utf-8", errors="replace")).hexdigest()[:16]


def _relative_path(path: Path, source_root: Path) -> str:
    try:
        return path.relative_to(source_root).as_posix()
    except ValueError:
        try:
            return path.resolve().relative_to(source_root.resolve()).as_posix()
        except ValueError:
            return path.as_posix()


def _nearby_reflected(lines: list[str], index: int) -> bool:
    start = max(0, index - 4)
    end = min(len(lines), index + 4)
    window = "\n".join(lines[start:end])
    return any(token in window for token in REFLECTED_TOKENS)


def _source_evidence(path: Path, line: int, digest: str) -> dict[str, Any]:
    return {
        "kind": "project_source",
        "location": f"{path}:{line}",
        "filePath": str(path),
        "lineStart": line,
        "lineEnd": line,
        "fileHash": digest,
    }


def _symbol(
    *,
    name: str,
    kind: str,
    path: Path,
    line: int,
    module: str,
    owner_build_cs: str,
    file_hash: str,
    base_class: str = "",
    api_macro: str = "",
    is_reflected: bool = False,
    includes: list[str] | None = None,
    qualified_name: str = "",
) -> dict[str, Any]:
    return {
        "symbol_name": name,
        "symbol_kind": kind,
        "file_path": str(path),
        "line_start": line,
        "line_end": line,
        "module_name": module,
        "owner_build_cs": owner_build_cs,
        "base_class": base_class,
        "api_macro": api_macro,
        "is_reflected": is_reflected,
        "includes": includes or [],
        "file_hash": file_hash,
        "qualified_name": qualified_name,
    }


def _function_match(line: str, language: str) -> tuple[str, str] | None:
    """Return (name, qualified_name) for a conservative declaration/definition."""
    patterns = {
        "python": (PYTHON_FUNC_RE,),
        "javascript": (JS_FUNC_RE,),
        "typescript": (JS_FUNC_RE,),
        "go": (GO_FUNC_RE,),
        "rust": (RUST_FUNC_RE,),
    }
    stripped = line.strip()
    starts_with_statement = re.match(
        r"^(?:if|else|for|while|do|switch|return|co_return|co_await|throw|new|delete|case|goto)\b",
        stripped,
    )
    if language in {"c", "cpp"} and not starts_with_statement:
        ctor = CPP_CTOR_DTOR_RE.match(line)
        if ctor:
            owner_name = ctor.group("owner")
            name = ctor.group("name")
            owner_leaf = owner_name.rsplit("::", 1)[-1]
            if name.lstrip("~") == owner_leaf:
                return name, f"{owner_name}::{name}"
    for pattern in patterns.get(language, (CPP_FUNCTION_RE, FUNC_RE)):
        match = pattern.match(line)
        if match and not starts_with_statement:
            name = match.group("name")
            owner_name = (match.groupdict().get("owner") or "").strip()
            qualified = f"{owner_name}::{name}" if owner_name else ""
            if not qualified:
                owner = QUALIFIED_FUNC_RE.search(line)
                if owner:
                    qualified = f"{owner.group('owner')}::{owner.group('name')}"
            return name, qualified
    return None


def _class_match(line: str, language: str) -> tuple[str, str, str, str] | None:
    """Return (kind, name, base, api_macro) without claiming semantic completeness."""
    if language == "python":
        match = PYTHON_CLASS_RE.match(line)
        if match:
            return "class", match.group("name"), (match.group("base") or "").split(".")[-1], ""
    if language == "java":
        match = JAVA_CLASS_RE.match(line)
        if match:
            return "class", match.group("name"), (match.group("base") or "").split(".")[-1], ""
    match = CLASS_RE.match(line)
    if match:
        kind = "struct" if line.lstrip().startswith("struct") else "class"
        return kind, match.group("name"), match.group("base") or "", match.group("api") or ""
    return None


def extract_symbols_from_file(
    path: Path,
    source_root: Path,
    *,
    text: str | None = None,
    owner_build_cs: str | None = None,
) -> list[dict[str, Any]]:
    """Extract source-located symbols; this does not resolve language semantics."""
    if text is None:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    lines = text.splitlines()
    digest = _file_hash(text)
    module = _module_name(path)
    owner = owner_build_cs if owner_build_cs is not None else _owner_build_cs(path, source_root)
    language = _language_for_path(path)
    symbols: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int]] = set()

    def add(row: dict[str, Any]) -> None:
        key = (str(row["symbol_kind"]), str(row["symbol_name"]), int(row["line_start"]))
        if key not in seen:
            seen.add(key)
            symbols.append(row)

    if path.name.endswith(".Build.cs"):
        add(
            _symbol(
                name=path.stem.replace(".Build", ""),
                kind="module",
                path=path,
                line=1,
                module=module or path.stem.replace(".Build", ""),
                owner_build_cs=str(path),
                file_hash=digest,
            )
        )

    for idx, line in enumerate(lines, start=1):
        include = INCLUDE_RE.match(line)
        if include:
            add(
                _symbol(
                    name=include.group("include"),
                    kind="include",
                    path=path,
                    line=idx,
                    module=module,
                    owner_build_cs=owner,
                    file_hash=digest,
                    includes=[include.group("include")],
                    is_reflected=_nearby_reflected(lines, idx - 1),
                )
            )
            continue

        imported = IMPORT_RE.match(line)
        if imported:
            name = next((value for value in imported.groupdict().values() if value), "").strip("\"'")
            if name:
                add(
                    _symbol(
                        name=name,
                        kind="import",
                        path=path,
                        line=idx,
                        module=module,
                        owner_build_cs=owner,
                        file_hash=digest,
                        includes=[name],
                    )
                )
            continue

        class_match = _class_match(line, language)
        if class_match:
            kind, name, base, api_macro = class_match
            add(
                _symbol(
                    name=name,
                    kind=kind,
                    path=path,
                    line=idx,
                    module=module,
                    owner_build_cs=owner,
                    file_hash=digest,
                    base_class=base,
                    api_macro=api_macro,
                    is_reflected=_nearby_reflected(lines, idx - 1),
                )
            )
            continue

        enum_match = ENUM_RE.match(line)
        if enum_match:
            add(
                _symbol(
                    name=enum_match.group("name"),
                    kind="enum",
                    path=path,
                    line=idx,
                    module=module,
                    owner_build_cs=owner,
                    file_hash=digest,
                    is_reflected=_nearby_reflected(lines, idx - 1),
                )
            )
            continue

        function = _function_match(line, language)
        if function:
            name, qualified_name = function
            add(
                _symbol(
                    name=name,
                    kind="function",
                    path=path,
                    line=idx,
                    module=module,
                    owner_build_cs=owner,
                    file_hash=digest,
                    is_reflected=_nearby_reflected(lines, idx - 1),
                    qualified_name=qualified_name,
                )
            )
    return symbols


def _mask_comments_and_strings(text: str) -> str:
    """Mask enough syntax to avoid turning comments/string literals into calls."""
    out: list[str] = []
    index = 0
    quote = ""
    while index < len(text):
        char = text[index]
        nxt = text[index + 1] if index + 1 < len(text) else ""
        if quote:
            if char == "\\" and index + 1 < len(text):
                out.extend("  ")
                index += 2
                continue
            out.append("\n" if char == "\n" else " ")
            if char == quote:
                quote = ""
            index += 1
            continue
        if char in {"\"", "'", "`"}:
            quote = char
            out.append(" ")
            index += 1
            continue
        if char == "/" and nxt == "/":
            while index < len(text) and text[index] != "\n":
                out.append(" ")
                index += 1
            continue
        if char == "#":
            # Python comments; a C/C++ preprocessor line cannot contain a call
            # relevant to this graph either, except include handling done earlier.
            while index < len(text) and text[index] != "\n":
                out.append(" ")
                index += 1
            continue
        if char == "/" and nxt == "*":
            out.extend("  ")
            index += 2
            while index < len(text):
                if text[index] == "*" and index + 1 < len(text) and text[index + 1] == "/":
                    out.extend("  ")
                    index += 2
                    break
                out.append("\n" if text[index] == "\n" else " ")
                index += 1
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _find_body_end(lines: list[str], start_line: int, language: str) -> int | None:
    """Find a braced body range.  Python indentation is deliberately skipped."""
    if language == "python":
        return None
    start_index = start_line - 1
    opening = None
    for index in range(start_index, min(len(lines), start_index + 4)):
        if ";" in lines[index] and "{" not in lines[index]:
            return None
        if "{" in lines[index]:
            opening = index
            break
    if opening is None:
        return None
    depth = 0
    for index in range(opening, len(lines)):
        depth += lines[index].count("{")
        depth -= lines[index].count("}")
        if depth == 0:
            return index + 1
    return None


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))


def source_inventory_signature(source_root: Path) -> str:
    """Return a cheap process-cache key for the current source inventory.

    This signature deliberately uses file identity/stat data instead of reading
    file contents.  It is suitable for invalidating read-only, in-process
    analysis caches.  Write/architecture gates must continue to use
    ``graph_is_fresh_for_root`` for content-hash verification.
    """
    root = source_root.resolve()
    digest = hashlib.sha1()
    digest.update(os.path.normcase(str(root)).encode("utf-8", errors="replace"))
    for path in _iter_source_files(root):
        try:
            stat = path.stat()
        except OSError:
            digest.update(f"\0unreadable:{_relative_path(path, root)}".encode("utf-8", errors="replace"))
            continue
        row = (
            f"\0{_relative_path(path, root)}\0{stat.st_size}\0"
            f"{stat.st_mtime_ns}\0{stat.st_ctime_ns}"
        )
        digest.update(row.encode("utf-8", errors="replace"))
    return digest.hexdigest()


def graph_is_fresh_for_root(graph: dict[str, Any], source_root: Path) -> bool:
    """Verify that a persisted graph exactly matches current source inventory/content."""
    if not isinstance(graph, dict):
        return False
    raw_graph_root = str(graph.get("sourceRoot") or "").strip()
    if not raw_graph_root or Path(raw_graph_root).resolve() != source_root.resolve():
        return False
    expected = {
        _path_key(Path(str(item.get("path") or ""))): str(item.get("fileHash") or "")
        for item in (graph.get("files") or [])
        if isinstance(item, dict) and item.get("path")
    }
    current_files = _iter_source_files(source_root)
    if len(expected) != len(current_files):
        return False
    for path in current_files:
        expected_hash = expected.get(_path_key(path))
        if not expected_hash:
            return False
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            return False
        if _file_hash(text) != expected_hash:
            return False
    return True


def _resolve_local_include(
    path: Path,
    include: str,
    source_root: Path,
    path_by_key: dict[str, Path],
    paths_by_basename: dict[str, list[Path]],
    *,
    language: str,
    relation_kind: str,
) -> Path | None:
    normalized = include.replace("\\", "/")
    candidates: list[Path] = [
        path.parent / normalized,
        source_root / normalized,
        source_root / "Public" / normalized,
        source_root / "Private" / normalized,
    ]
    if relation_kind == "import":
        module_path = normalized.lstrip("./")
        if language == "python":
            module_path = module_path.replace(".", "/")
            candidates.extend(
                [
                    path.parent / f"{module_path}.py",
                    path.parent / module_path / "__init__.py",
                    source_root / f"{module_path}.py",
                    source_root / module_path / "__init__.py",
                ]
            )
        elif language in {"javascript", "typescript"}:
            for suffix in (".js", ".jsx", ".ts", ".tsx"):
                candidates.extend(
                    [
                        path.parent / f"{module_path}{suffix}",
                        path.parent / module_path / f"index{suffix}",
                        source_root / f"{module_path}{suffix}",
                        source_root / module_path / f"index{suffix}",
                    ]
                )
    for candidate in candidates:
        resolved = path_by_key.get(_path_key(candidate))
        if resolved is not None:
            return resolved
    # A final basename match avoids pretending that an Unreal include is a
    # full path; ambiguous names intentionally remain unresolved.
    basename_matches = paths_by_basename.get(Path(normalized).name, [])
    return basename_matches[0] if len(basename_matches) == 1 else None


def _edge(
    *,
    source: str,
    target: str,
    kind: str,
    path: Path,
    line: int,
    digest: str,
    confidence: str,
    evidence_kind: str,
    proof_boundary: str,
    extraction: str,
) -> dict[str, Any]:
    evidence = _source_evidence(path, line, digest)
    if evidence_kind != "project_source":
        evidence["kind"] = evidence_kind
        evidence["sourceLocation"] = evidence.pop("location")
    return {
        "id": f"edge:{_stable_id(source, target, kind, str(path), str(line))}",
        "from": source,
        "to": target,
        "kind": kind,
        "confidence": confidence,
        "evidence": evidence,
        "extraction": extraction,
        "proofBoundary": proof_boundary,
    }


def _decorate_symbols(symbols: list[dict[str, Any]], source_root: Path) -> None:
    for row in symbols:
        path = Path(str(row.get("file_path") or ""))
        relative = _relative_path(path, source_root)
        line = int(row.get("line_start") or 1)
        name = str(row.get("symbol_name") or "")
        kind = str(row.get("symbol_kind") or "")
        row["id"] = f"symbol:{_stable_id(relative, str(line), kind, name)}"
        row["language"] = _language_for_path(path)
        row["sourceEvidence"] = _source_evidence(path, line, str(row.get("file_hash") or ""))
        row["proofBoundary"] = "source-located existence only; read source before behavioral, ownership, or runtime claims"


def build_symbol_graph(source_root: Path) -> dict[str, Any]:
    """Build graph v2 while retaining the v1 ``symbols`` compatibility field."""
    source_root = source_root.resolve()
    source_files = _iter_source_files(source_root)
    symbols: list[dict[str, Any]] = []
    file_text: dict[Path, str] = {}
    files: list[dict[str, Any]] = []
    path_by_key: dict[str, Path] = {}
    paths_by_basename: dict[str, list[Path]] = defaultdict(list)
    skipped_files: list[dict[str, str]] = []
    owner_by_directory: dict[Path, str] = {}

    for path in source_files:
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError as exc:
            skipped_files.append(
                {
                    "path": str(path),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        digest = _file_hash(text)
        relative = _relative_path(path, source_root)
        path_by_key[_path_key(path)] = path
        paths_by_basename[path.name].append(path)
        file_id = f"file:{relative}"
        files.append(
            {
                "id": file_id,
                "path": str(path),
                "relativePath": relative,
                "language": _language_for_path(path),
                "fileHash": digest,
                "sourceEvidence": _source_evidence(path, 1, digest),
                "proofBoundary": "file presence is source evidence, not behavior or architecture proof",
            }
        )
        file_text[path] = text
        if path.parent not in owner_by_directory:
            owner_by_directory[path.parent] = _owner_build_cs(path, source_root)
        owner = owner_by_directory[path.parent]
        symbols.extend(
            extract_symbols_from_file(
                path,
                source_root,
                text=text,
                owner_build_cs=owner,
            )
        )

    _decorate_symbols(symbols, source_root)
    edges: list[dict[str, Any]] = []
    edge_keys: set[tuple[str, str, str, str, int]] = set()

    def add_edge(item: dict[str, Any]) -> None:
        evidence = item.get("evidence") or {}
        raw_location = str(evidence.get("location") or evidence.get("sourceLocation") or "")
        key = (str(item["from"]), str(item["to"]), str(item["kind"]), raw_location, int(evidence.get("lineStart") or 0))
        if key not in edge_keys:
            edge_keys.add(key)
            edges.append(item)

    symbol_by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in symbols:
        symbol_by_name[str(row.get("symbol_name") or "")].append(row)
        if str(row.get("symbol_kind") or "") not in {"include", "import"}:
            path = Path(str(row["file_path"]))
            add_edge(
                _edge(
                    source=f"file:{_relative_path(path, source_root)}",
                    target=str(row["id"]),
                    kind="defines",
                    path=path,
                    line=int(row["line_start"]),
                    digest=str(row["file_hash"]),
                    confidence="direct",
                    evidence_kind="project_source",
                    proof_boundary="supports source-located declaration/existence only",
                    extraction="syntax_token",
                )
            )

    # Includes/imports and inheritance are literal source relationships.  A
    # local target is resolved only if unambiguous; unknown external targets
    # are retained as external nodes rather than fabricated project symbols.
    for row in symbols:
        kind = str(row.get("symbol_kind") or "")
        path = Path(str(row["file_path"]))
        line = int(row["line_start"])
        digest = str(row["file_hash"])
        file_id = f"file:{_relative_path(path, source_root)}"
        if kind in {"include", "import"}:
            include = str(row.get("symbol_name") or "")
            resolved = _resolve_local_include(
                path,
                include,
                source_root,
                path_by_key,
                paths_by_basename,
                language=_language_for_path(path),
                relation_kind=kind,
            )
            target = f"file:{_relative_path(resolved, source_root)}" if resolved else f"external:import:{include}"
            add_edge(
                _edge(
                    source=file_id,
                    target=target,
                    kind="includes" if kind == "include" else "imports",
                    path=path,
                    line=line,
                    digest=digest,
                    confidence="direct" if resolved else "external",
                    evidence_kind="project_source",
                    proof_boundary="supports a textual dependency only; not module-link or runtime proof",
                    extraction="directive_or_import",
                )
            )
        base = str(row.get("base_class") or "")
        if base:
            candidates = [candidate for candidate in symbol_by_name.get(base, []) if candidate.get("symbol_kind") in {"class", "struct"}]
            target = str(candidates[0]["id"]) if len(candidates) == 1 else f"external:symbol:{base}"
            add_edge(
                _edge(
                    source=str(row["id"]),
                    target=target,
                    kind="inherits",
                    path=path,
                    line=line,
                    digest=digest,
                    confidence="direct" if len(candidates) == 1 else "external",
                    evidence_kind="project_source",
                    proof_boundary="supports declared inheritance only; not framework lifecycle semantics",
                    extraction="type_declaration",
                )
            )

    unresolved_call_count = 0
    ambiguous_call_count = 0
    functions_by_path: dict[Path, list[dict[str, Any]]] = defaultdict(list)
    for row in symbols:
        if row.get("symbol_kind") == "function":
            functions_by_path[Path(str(row.get("file_path") or ""))].append(row)
    for path, raw_text in file_text.items():
        language = _language_for_path(path)
        if language == "python":
            # Proper Python scope/data-flow needs an AST.  Do not emit fake
            # function-body call edges from indentation guesses in this
            # dependency-free fallback.
            continue
        masked_lines = _mask_comments_and_strings(raw_text).splitlines()
        file_functions = functions_by_path.get(path, [])
        ranges: list[tuple[dict[str, Any], int, int]] = []
        for function in file_functions:
            start = int(function.get("line_start") or 1)
            end = _find_body_end(masked_lines, start, language)
            if end:
                ranges.append((function, start, end))
        if not ranges:
            continue
        digest = _file_hash(raw_text)
        for function, start, end in ranges:
            for line_no in range(start, end + 1):
                line = masked_lines[line_no - 1] if line_no - 1 < len(masked_lines) else ""
                # The function declaration itself contains a call-shaped token.
                # Preserve only the body suffix for compact one-line bodies.
                if line_no == start:
                    opening = line.find("{")
                    if opening < 0:
                        continue
                    line = line[opening + 1:]
                for match in CALL_RE.finditer(line):
                    name = match.group("name")
                    if name.lower() in CALL_KEYWORDS or name.isupper():
                        continue
                    candidates = [candidate for candidate in symbol_by_name.get(name, []) if candidate.get("symbol_kind") == "function"]
                    if len(candidates) == 1:
                        add_edge(
                            _edge(
                                source=str(function["id"]),
                                target=str(candidates[0]["id"]),
                                kind="calls_candidate",
                                path=path,
                                line=line_no,
                                digest=digest,
                                confidence="heuristic",
                                evidence_kind="generated_metadata",
                                proof_boundary="navigation/impact candidate only; does not prove dispatch, data flow, or runtime execution",
                                extraction="regex_call_resolution",
                            )
                        )
                    elif len(candidates) > 1:
                        ambiguous_call_count += 1
                    else:
                        unresolved_call_count += 1

    edges.sort(key=lambda row: (str(row.get("kind")), str(row.get("from")), str(row.get("to")), str((row.get("evidence") or {}).get("location") or (row.get("evidence") or {}).get("sourceLocation"))))
    return {
        "version": 2,
        "sourceRoot": str(source_root),
        "files": files,
        "symbols": symbols,
        "edges": edges,
        "analysis": {
            "engine": "regex-conservative-v2",
            "complete": not skipped_files,
            "sourceFileCount": len(files),
            "skippedFileCount": len(skipped_files),
            "skippedFiles": skipped_files[:20],
            "skippedFilesTruncated": len(skipped_files) > 20,
            "unresolvedCallCount": unresolved_call_count,
            "ambiguousCallCount": ambiguous_call_count,
            "supportedLanguages": sorted(set(file["language"] for file in files)),
            "limitations": [
                "No compiler, AST, preprocessor, build graph, generated code, or runtime trace is used.",
                "calls_candidate edges are navigation/impact hints and must not be used as proof of wiring, data flow, ownership, or runtime behavior.",
                "External include/import and base targets may be unresolved or ambiguous by design.",
            ],
        },
        "evidenceContract": {
            "directSourceEdges": ["defines", "includes", "imports", "inherits"],
            "heuristicEdges": ["calls_candidate"],
            "directSourceProof": "source-located existence or textual relation only",
            "behavioralClaimsRequire": "explicit BehaviorPath plus project/framework/static/build/test/runtime evidence as appropriate",
        },
    }


def summarize_graph(graph: dict[str, Any]) -> dict[str, Any]:
    by_module: dict[str, Counter[str]] = defaultdict(Counter)
    by_kind: Counter[str] = Counter()
    by_edge_kind: Counter[str] = Counter()
    by_edge_confidence: Counter[str] = Counter()
    for row in graph.get("symbols") or []:
        module = str(row.get("module_name") or "_unknown")
        kind = str(row.get("symbol_kind") or "_unknown")
        by_module[module][kind] += 1
        by_kind[kind] += 1
    for edge in graph.get("edges") or []:
        by_edge_kind[str(edge.get("kind") or "_unknown")] += 1
        by_edge_confidence[str(edge.get("confidence") or "_unknown")] += 1
    return {
        "version": graph.get("version", 1),
        "sourceRoot": graph.get("sourceRoot", ""),
        "totalFiles": len(graph.get("files") or []),
        "totalSymbols": len(graph.get("symbols") or []),
        "totalEdges": len(graph.get("edges") or []),
        "countsByModule": {module: dict(counter) for module, counter in sorted(by_module.items())},
        "countsBySymbolKind": dict(sorted(by_kind.items())),
        "countsByEdgeKind": dict(sorted(by_edge_kind.items())),
        "countsByEdgeConfidence": dict(sorted(by_edge_confidence.items())),
        "analysis": graph.get("analysis") or {},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build persistent conservative code-intelligence graph from source files.")
    parser.add_argument("--project-root", type=Path, default=None)
    parser.add_argument("--source-root", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "data" / "symbol_graph")
    args = parser.parse_args()

    project_root = args.project_root or resolve_active_project_root(ROOT)
    source_root = args.source_root or project_root
    if not source_root or not source_root.is_dir():
        raise SystemExit("Project/source root not found. Set active project or pass --source-root.")

    graph = build_symbol_graph(source_root)
    summary = summarize_graph(graph)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "symbol_graph.json").write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.out_dir / "symbol_graph_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
