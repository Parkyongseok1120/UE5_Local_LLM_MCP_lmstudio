#!/usr/bin/env python
"""Build Unreal include owner and module dependency graph records for RAG."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


PUBLIC_MARKERS = {"Public", "Classes"}
PRIVATE_MARKERS = {"Private"}


def stable_id(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def read_jsonl(path: Path):
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                print(f"[skip] {path}:{line_no} {exc}")


def norm_path(value: str) -> str:
    return value.replace("\\", "/").strip("/")


def include_keys(relative_path: str) -> list[str]:
    parts = norm_path(relative_path).split("/")
    keys: list[str] = []
    for marker in ("Public", "Classes", "Private"):
        if marker in parts:
            index = parts.index(marker)
            if index + 1 < len(parts):
                keys.append("/".join(parts[index + 1 :]))
    if parts:
        keys.append("/".join(parts))
        keys.append(parts[-1])
    deduped: list[str] = []
    seen: set[str] = set()
    for key in keys:
        lowered = key.lower()
        if key and lowered not in seen:
            deduped.append(key)
            seen.add(lowered)
    return deduped


def include_visibility(relative_path: str, extension: str) -> str:
    parts = norm_path(relative_path).split("/")
    if any(part in PUBLIC_MARKERS for part in parts):
        return "public_header"
    if any(part in PRIVATE_MARKERS for part in parts) or extension.lower() in {".cpp", ".cc", ".cxx"}:
        return "private_implementation"
    if extension.lower() in {".h", ".hpp", ".hh"}:
        return "public_or_private_header"
    return "implementation"


def recommended_dependency(consumer_visibility: str) -> str:
    if consumer_visibility in {"public_header", "public_or_private_header"}:
        return "PublicDependencyModuleNames"
    return "PrivateDependencyModuleNames"


def load_symbols(paths: list[Path]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, list[str]]]]:
    include_maps: list[dict[str, Any]] = []
    headers: list[dict[str, Any]] = []
    module_deps: dict[str, dict[str, list[str]]] = {}

    for path in paths:
        for item in read_jsonl(path) or []:
            metadata = item.get("metadata") or {}
            kind = str(metadata.get("symbol_kind") or "")
            module_name = str(metadata.get("module_name") or "")
            relative_path = str(metadata.get("relative_path") or "")
            extension = str(metadata.get("extension") or "").lower()

            if kind == "module":
                raw_deps = metadata.get("dependencies") or {}
                module_deps[module_name] = {str(key): [str(v) for v in values] for key, values in raw_deps.items()}
            elif kind == "include_map":
                include_maps.append(item)
                if extension in {".h", ".hpp", ".hh"}:
                    headers.append(item)
            elif relative_path and extension in {".h", ".hpp", ".hh"} and module_name:
                headers.append(item)

    return include_maps, headers, module_deps


def build_owner_index(headers: list[dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
    owners: dict[str, list[dict[str, str]]] = defaultdict(list)
    seen: set[tuple[str, str, str]] = set()
    for item in headers:
        metadata = item.get("metadata") or {}
        module_name = str(metadata.get("module_name") or "")
        relative_path = str(metadata.get("relative_path") or "")
        extension = str(metadata.get("extension") or "")
        if not module_name or not relative_path:
            continue
        visibility = include_visibility(relative_path, extension)
        for key in include_keys(relative_path):
            lowered = key.lower()
            unique = (lowered, module_name, relative_path)
            if unique in seen:
                continue
            owners[lowered].append(
                {
                    "include": key,
                    "module_name": module_name,
                    "relative_path": relative_path,
                    "visibility": visibility,
                }
            )
            seen.add(unique)
    return owners


def dependency_status(deps: dict[str, list[str]], owner_module: str) -> str:
    if owner_module in deps.get("PublicDependencyModuleNames", []):
        return "already_public_dependency"
    if owner_module in deps.get("PrivateDependencyModuleNames", []):
        return "already_private_dependency"
    if owner_module in deps.get("PublicIncludePathModuleNames", []):
        return "public_include_path_only"
    if owner_module in deps.get("PrivateIncludePathModuleNames", []):
        return "private_include_path_only"
    return "missing_or_transitive"


def make_doc(
    *,
    source_path: Path,
    title: str,
    text: str,
    symbol_name: str,
    symbol_kind: str,
    module_name: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    metadata = dict(metadata)
    metadata.update(
        {
            "symbol_name": symbol_name,
            "symbol_kind": symbol_kind,
            "module_name": module_name,
            "extension": ".graph",
        }
    )
    return {
        "id": stable_id(f"module_graph:{symbol_kind}:{module_name}:{symbol_name}:{title}"),
        "source": "module_graph",
        "path": str(source_path),
        "title": title,
        "text": text,
        "metadata": metadata,
    }


def module_docs(symbols_path: Path, module_deps: dict[str, dict[str, list[str]]]) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for module_name, deps in sorted(module_deps.items()):
        lines = [
            f"Unreal module dependency graph: {module_name}",
            f"Module: {module_name}",
            "Build.cs dependency visibility:",
        ]
        for key in (
            "PublicDependencyModuleNames",
            "PrivateDependencyModuleNames",
            "PublicIncludePathModuleNames",
            "PrivateIncludePathModuleNames",
        ):
            values = deps.get(key) or []
            lines.append(f"- {key}: {', '.join(values) if values else '(empty)'}")
        docs.append(
            make_doc(
                source_path=symbols_path,
                title=f"{module_name} module dependency graph",
                text="\n".join(lines),
                symbol_name=module_name,
                symbol_kind="module_dependency_graph",
                module_name=module_name,
                metadata={"dependencies": deps},
            )
        )
    return docs


def owner_docs(symbols_path: Path, owners: dict[str, list[dict[str, str]]]) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for key, values in sorted(owners.items()):
        if len(values) > 8:
            values = values[:8]
        modules = sorted({value["module_name"] for value in values})
        lines = [
            f"Unreal include owner graph: {values[0]['include']}",
            f"Include path key: {values[0]['include']}",
            f"Owner module candidates: {', '.join(modules)}",
            "Owners:",
        ]
        for value in values:
            lines.append(
                f"- Module: {value['module_name']}; Header: {value['relative_path']}; Visibility: {value['visibility']}"
            )
        lines.extend(
            [
                "",
                "Dependency rule:",
                "- If this include appears in a public header, prefer PublicDependencyModuleNames.",
                "- If this include appears only in a private .cpp or Private header, prefer PrivateDependencyModuleNames.",
            ]
        )
        docs.append(
            make_doc(
                source_path=symbols_path,
                title=f"{values[0]['include']} include owner",
                text="\n".join(lines),
                symbol_name=values[0]["include"],
                symbol_kind="include_owner",
                module_name=",".join(modules),
                metadata={"include_path": values[0]["include"], "owner_modules": modules},
            )
        )
    return docs


def edge_docs(
    symbols_path: Path,
    include_maps: list[dict[str, Any]],
    owners: dict[str, list[dict[str, str]]],
    module_deps: dict[str, dict[str, list[str]]],
    max_edges: int,
) -> tuple[list[dict[str, Any]], Counter[str], Counter[str]]:
    docs: list[dict[str, Any]] = []
    unresolved: Counter[str] = Counter()
    missing: Counter[str] = Counter()

    for item in include_maps:
        metadata = item.get("metadata") or {}
        consumer_module = str(metadata.get("module_name") or "")
        relative_path = str(metadata.get("relative_path") or "")
        extension = str(metadata.get("extension") or "")
        consumer_visibility = include_visibility(relative_path, extension)
        recommendation = recommended_dependency(consumer_visibility)
        deps = module_deps.get(consumer_module, {})

        for include in metadata.get("includes") or []:
            include = str(include)
            candidates = owners.get(norm_path(include).lower()) or owners.get(Path(include).name.lower()) or []
            if not candidates:
                unresolved[include] += 1
                if len(docs) < max_edges:
                    docs.append(
                        make_doc(
                            source_path=symbols_path,
                            title=f"{consumer_module} ({relative_path}) includes unresolved {include}",
                            text="\n".join(
                                [
                                    f"Unreal include dependency edge: {consumer_module} includes {include}",
                                    f"Consumer file: {relative_path}",
                                    f"Consumer visibility: {consumer_visibility}",
                                    "Owner module candidates: (not resolved from collected symbols)",
                                    f"Recommended next check: search engine source or project source for {include}.",
                                ]
                            ),
                            symbol_name=include,
                            symbol_kind="include_edge",
                            module_name=consumer_module,
                            metadata={
                                "consumer_module": consumer_module,
                                "consumer_file": relative_path,
                                "include_path": include,
                                "owner_modules": [],
                                "dependency_visibility": recommendation,
                                "dependency_status": "unresolved_owner",
                            },
                        )
                    )
                continue

            owner_modules = sorted({candidate["module_name"] for candidate in candidates})
            cross_modules = [module for module in owner_modules if module and module != consumer_module]
            if not cross_modules:
                continue
            statuses = {module: dependency_status(deps, module) for module in cross_modules}
            for module, status in statuses.items():
                if status == "missing_or_transitive":
                    missing[f"{consumer_module}->{module}"] += 1

            if len(docs) >= max_edges:
                continue
            lines = [
                f"Unreal include dependency edge: {consumer_module} includes {include}",
                f"Consumer module: {consumer_module}",
                f"Consumer file: {relative_path}",
                f"Consumer visibility: {consumer_visibility}",
                f"Owner module candidates: {', '.join(owner_modules)}",
                f"Recommended Build.cs list: {recommendation}",
                "Current dependency status:",
            ]
            for module, status in statuses.items():
                lines.append(f"- {module}: {status}")
            docs.append(
                make_doc(
                    source_path=symbols_path,
                    title=f"{consumer_module} ({relative_path}) -> {include} include edge",
                    text="\n".join(lines),
                    symbol_name=include,
                    symbol_kind="include_edge",
                    module_name=consumer_module,
                    metadata={
                        "consumer_module": consumer_module,
                        "consumer_file": relative_path,
                        "include_path": include,
                        "owner_modules": owner_modules,
                        "dependency_visibility": recommendation,
                        "dependency_status": ",".join(sorted(set(statuses.values()))),
                    },
                )
            )

    return docs, unresolved, missing


def write_report(path: Path, docs_count: int, unresolved: Counter[str], missing: Counter[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Unreal Module Include Graph Report",
        "",
        f"Generated RAG graph records: {docs_count}",
        "",
        "## Top unresolved includes",
    ]
    for include, count in unresolved.most_common(25):
        lines.append(f"- `{include}`: {count}")
    if not unresolved:
        lines.append("- none")

    lines.extend(["", "## Top cross-module includes with missing/transitive dependency status"])
    for edge, count in missing.most_common(25):
        lines.append(f"- `{edge}`: {count}")
    if not missing:
        lines.append("- none")

    lines.extend(
        [
            "",
            "## Dependency visibility rule",
            "",
            "- Include from a public header: prefer `PublicDependencyModuleNames`.",
            "- Include from a private `.cpp` or Private header: prefer `PrivateDependencyModuleNames`.",
            "- Same-module includes do not require a Build.cs dependency edge.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build(args: argparse.Namespace) -> None:
    symbol_paths = [Path(value) for value in args.symbols]
    if not symbol_paths:
        symbol_paths = [Path("data/unreal58/raw_symbols.jsonl")]
    include_maps, headers, module_deps = load_symbols(symbol_paths)
    owners = build_owner_index(headers)
    primary_symbols = symbol_paths[0]

    docs: list[dict[str, Any]] = []
    docs.extend(module_docs(primary_symbols, module_deps))
    docs.extend(owner_docs(primary_symbols, owners))
    edge_records, unresolved, missing = edge_docs(
        primary_symbols,
        include_maps,
        owners,
        module_deps,
        args.max_edges,
    )
    docs.extend(edge_records)

    deduped: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for doc in docs:
        doc_id = str(doc.get("id") or "")
        if doc_id and doc_id in seen_ids:
            continue
        if doc_id:
            seen_ids.add(doc_id)
        deduped.append(doc)
    if len(deduped) != len(docs):
        print(f"dedupe: removed {len(docs) - len(deduped)} duplicate module graph records")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        for doc in deduped:
            handle.write(json.dumps(doc, ensure_ascii=False) + "\n")

    if args.report:
        write_report(Path(args.report), len(deduped), unresolved, missing)

    print(
        "done: wrote "
        f"{len(deduped)} module/include graph records "
        f"({len(module_deps)} modules, {len(owners)} include owners, {len(edge_records)} include edges) "
        f"to {out_path}"
    )
    if args.report:
        print(f"report: {args.report}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Unreal module/include graph RAG records from raw symbols.")
    parser.add_argument("--symbols", action="append", default=[], help="Raw symbol JSONL input(s). Repeat for multiple files.")
    parser.add_argument("--out", default="data/unreal58/raw_module_graph.jsonl")
    parser.add_argument("--report", default="Reports/unreal_module_include_graph.md")
    parser.add_argument("--max-edges", type=int, default=12000)
    return parser.parse_args()


if __name__ == "__main__":
    build(parse_args())

