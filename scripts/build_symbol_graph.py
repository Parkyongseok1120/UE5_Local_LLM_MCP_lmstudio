#!/usr/bin/env python
"""Build a persistent symbol graph from active Unreal project Source files."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

from workspace_paths import resolve_active_project_root  # noqa: E402

SOURCE_EXTS = {".h", ".hpp", ".cpp", ".c", ".cc", ".cs"}
IGNORE_DIRS = {"Binaries", "Intermediate", "Saved", "DerivedDataCache", ".git"}

CLASS_RE = re.compile(
    r"^\s*(?:class|struct)\s+(?:(?P<api>[A-Z][A-Z0-9_]*_API)\s+)?(?P<name>[A-Za-z_]\w*)"
    r"(?:\s*:\s*(?:public|protected|private)?\s*(?P<base>[A-Za-z_]\w*))?"
)
STRUCT_RE = re.compile(r"^\s*struct\s+(?:(?P<api>[A-Z][A-Z0-9_]*_API)\s+)?(?P<name>[A-Za-z_]\w*)")
ENUM_RE = re.compile(r"^\s*enum\s+(?:class\s+)?(?P<name>[A-Za-z_]\w*)")
FUNC_RE = re.compile(
    r"^\s*(?:[A-Za-z_][\w:<>,~*&\s]+\s+)+(?P<name>[A-Za-z_]\w*)\s*\([^;{}]*\)\s*(?:const\s*)?(?:;|\{)?\s*$"
)
INCLUDE_RE = re.compile(r"^\s*#\s*include\s+[<\"](?P<include>[^>\"]+)[>\"]")
MODULE_RE = re.compile(r"Source[/\\](?P<module>[^/\\]+)")
REFLECTED_TOKENS = ("UCLASS", "USTRUCT", "UENUM", "UINTERFACE", "UPROPERTY", "UFUNCTION", "GENERATED_BODY")


def _iter_source_files(source_root: Path) -> list[Path]:
    files: list[Path] = []
    for path in source_root.rglob("*"):
        if any(part in IGNORE_DIRS for part in path.parts):
            continue
        if path.is_file() and path.suffix.lower() in SOURCE_EXTS:
            files.append(path)
    return sorted(files)


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


def _nearby_reflected(lines: list[str], index: int) -> bool:
    start = max(0, index - 4)
    end = min(len(lines), index + 4)
    window = "\n".join(lines[start:end])
    return any(token in window for token in REFLECTED_TOKENS)


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
    }


def extract_symbols_from_file(path: Path, source_root: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    lines = text.splitlines()
    digest = _file_hash(text)
    module = _module_name(path)
    owner = _owner_build_cs(path, source_root)
    symbols: list[dict[str, Any]] = []

    if path.name.endswith(".Build.cs"):
        symbols.append(
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
            symbols.append(
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

        class_match = CLASS_RE.match(line)
        if class_match:
            kind = "struct" if line.lstrip().startswith("struct") else "class"
            symbols.append(
                _symbol(
                    name=class_match.group("name"),
                    kind=kind,
                    path=path,
                    line=idx,
                    module=module,
                    owner_build_cs=owner,
                    file_hash=digest,
                    base_class=class_match.group("base") or "",
                    api_macro=class_match.group("api") or "",
                    is_reflected=_nearby_reflected(lines, idx - 1),
                )
            )
            continue

        enum_match = ENUM_RE.match(line)
        if enum_match:
            symbols.append(
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

        func_match = FUNC_RE.match(line)
        if func_match and not line.strip().startswith(("if", "for", "while", "switch")):
            symbols.append(
                _symbol(
                    name=func_match.group("name"),
                    kind="function",
                    path=path,
                    line=idx,
                    module=module,
                    owner_build_cs=owner,
                    file_hash=digest,
                    is_reflected=_nearby_reflected(lines, idx - 1),
                )
            )
    return symbols


def build_symbol_graph(source_root: Path) -> dict[str, Any]:
    source_root = source_root.resolve()
    symbols: list[dict[str, Any]] = []
    for path in _iter_source_files(source_root):
        symbols.extend(extract_symbols_from_file(path, source_root))
    return {"version": 1, "sourceRoot": str(source_root), "symbols": symbols}


def summarize_graph(graph: dict[str, Any]) -> dict[str, Any]:
    by_module: dict[str, Counter[str]] = defaultdict(Counter)
    by_kind: Counter[str] = Counter()
    for row in graph.get("symbols") or []:
        module = str(row.get("module_name") or "_unknown")
        kind = str(row.get("symbol_kind") or "_unknown")
        by_module[module][kind] += 1
        by_kind[kind] += 1
    return {
        "version": graph.get("version", 1),
        "sourceRoot": graph.get("sourceRoot", ""),
        "totalSymbols": len(graph.get("symbols") or []),
        "countsByModule": {module: dict(counter) for module, counter in sorted(by_module.items())},
        "countsBySymbolKind": dict(sorted(by_kind.items())),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build persistent symbol graph from active project Source files.")
    parser.add_argument("--project-root", type=Path, default=None)
    parser.add_argument("--source-root", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "data" / "symbol_graph")
    args = parser.parse_args()

    project_root = args.project_root or resolve_active_project_root(ROOT)
    source_root = args.source_root or ((project_root / "Source") if project_root else None)
    if not source_root or not source_root.is_dir():
        raise SystemExit("Source root not found. Set active project or pass --source-root.")

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
