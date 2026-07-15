#!/usr/bin/env python
"""Extract Unreal C++ symbols, includes, macros, and modules into JSONL."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from parse_build_cs import format_dependency_lines, parse_build_cs_text

SOURCE_EXTENSIONS = {".h", ".hpp", ".hh", ".cpp", ".cxx", ".cc", ".inl"}
SKIP_DIRS = {
    ".git",
    ".vs",
    "Binaries",
    "DerivedDataCache",
    "Editor",
    "Intermediate",
    "Saved",
    "ThirdParty",
}
PUBLIC_PATH_MARKERS = {"Public", "Classes"}
REFLECTION_MACRO_RE = re.compile(r"\b(UCLASS|USTRUCT|UINTERFACE|UENUM|UFUNCTION)\s*\(")
HEADER_EXTENSIONS = {".h", ".hpp", ".hh"}
INCLUDE_RE = re.compile(r'^\s*#\s*include\s+[<"]([^>"]+)[>"]', re.MULTILINE)
UCLASS_RE = re.compile(r"\b(UCLASS|USTRUCT|UINTERFACE|UENUM)\s*\((.*?)\)", re.DOTALL)
TYPE_RE = re.compile(
    r"\b(?:class|struct|enum\s+class|enum)\s+"
    r"(?:[A-Z0-9_]+_API\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:\s*:\s*(?:public|protected|private)?\s*(?P<parent>[A-Za-z_][A-Za-z0-9_:<>]*))?",
    re.DOTALL,
)
UFUNCTION_RE = re.compile(r"\bUFUNCTION\s*\((?P<meta>.*?)\)\s*(?P<decl>[^;{]+)[;{]", re.DOTALL)
UPROPERTY_RE = re.compile(r"\bUPROPERTY\s*\((?P<meta>.*?)\)\s*(?P<decl>[^;]+);", re.DOTALL)
FUNCTION_NAME_RE = re.compile(r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\([^;{}]*\)\s*(?:const\s*)?$")
PROPERTY_NAME_RE = re.compile(r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?:=.*)?$")
DEFINITION_RE = re.compile(
    r"(?P<class>[A-Za-z_][A-Za-z0-9_]*)::(?P<name>~?[A-Za-z_][A-Za-z0-9_]*)\s*\("
)

_SYMBOL_SCOPE = "engine"
_SYMBOL_PROJECT = ""


def set_symbol_context(*, scope: str = "engine", project_name: str = "") -> None:
    global _SYMBOL_SCOPE, _SYMBOL_PROJECT
    _SYMBOL_SCOPE = scope
    _SYMBOL_PROJECT = project_name


def stable_id(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def read_text(path: Path) -> str | None:
    for encoding in ("utf-8-sig", "utf-8", "cp949", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
        except OSError as exc:
            print(f"[skip] {path} ({exc})")
            return None
    return None


def should_skip(path: Path, root: Path, include_third_party: bool, include_editor: bool) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    skip_dirs = set(SKIP_DIRS)
    if include_third_party:
        skip_dirs.discard("ThirdParty")
    if include_editor:
        skip_dirs.discard("Editor")
    return any(part in skip_dirs for part in relative.parts)


def is_public_tier_header(path: Path, root: Path, text: str) -> bool:
    parts = relative_path(root, path).split("/")
    if any(part in PUBLIC_PATH_MARKERS for part in parts):
        return True
    return bool(REFLECTION_MACRO_RE.search(text))


def make_include_sidecar_item(root: Path, path: Path, text: str, module_name: str) -> dict | None:
    includes = INCLUDE_RE.findall(text)
    if not includes:
        return None
    return {
        "path": str(path),
        "relative_path": relative_path(root, path),
        "module_name": module_name,
        "includes": includes,
        "symbol_kind": "include_map",
        "path_only": True,
    }


def infer_module_name(root: Path, path: Path) -> str:
    if path.name.endswith(".Build.cs"):
        return path.name.removesuffix(".Build.cs")

    parts = list(path.relative_to(root).parts)
    lowered = [part.lower() for part in parts]
    if root.name.lower() in {"source", "runtime", "editor", "developer", "programs"} and parts:
        return parts[0]
    for marker in ("source", "runtime", "editor", "developer", "programs"):
        if marker in lowered:
            index = lowered.index(marker)
            if index + 1 < len(parts):
                return parts[index + 1]
    if "plugins" in lowered and "source" in lowered:
        index = lowered.index("source")
        if index + 1 < len(parts):
            return parts[index + 1]
    return root.name


def relative_path(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def clean_decl(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def context_lines(text: str, start: int, end: int, radius: int = 2) -> str:
    lines = text.splitlines()
    prefix = text[:start]
    line_index = prefix.count("\n")
    start_line = max(0, line_index - radius)
    end_line = min(len(lines), line_index + radius + 3)
    return "\n".join(lines[start_line:end_line]).strip()


def make_item(
    *,
    root: Path,
    path: Path,
    source: str,
    title: str,
    text: str,
    symbol_name: str,
    symbol_kind: str,
    module_name: str,
    extra: dict | None = None,
    scope: str | None = None,
    project_name: str | None = None,
) -> dict:
    resolved_scope = scope or _SYMBOL_SCOPE
    resolved_project = project_name if project_name is not None else _SYMBOL_PROJECT
    metadata = {
        "root": str(root),
        "relative_path": relative_path(root, path),
        "extension": path.suffix.lower(),
        "symbol_name": symbol_name,
        "symbol_kind": symbol_kind,
        "module_name": module_name,
        "scope": resolved_scope,
    }
    if resolved_project:
        metadata["project"] = resolved_project
    if extra:
        metadata.update(extra)
    return {
        "id": stable_id(f"{source}:{path}:{symbol_kind}:{symbol_name}:{title}"),
        "source": source,
        "path": str(path),
        "title": title,
        "text": text,
        "metadata": metadata,
    }


def collect_module(root: Path, path: Path, text: str) -> list[dict]:
    module_name = infer_module_name(root, path)
    parsed = parse_build_cs_text(text, module_name)
    dependencies = parsed.get("dependencies") or {}
    conditional = parsed.get("conditional_dependencies") or []

    lines = [
        f"Unreal module: {module_name}",
        f"Build.cs: {relative_path(root, path)}",
        "",
    ]
    dep_lines = format_dependency_lines(parsed)
    if dep_lines:
        lines.extend(dep_lines)
    else:
        lines.append(clean_decl(text[:2000]))

    extra: dict = {"dependencies": dependencies}
    if conditional:
        extra["conditional_dependencies"] = conditional

    return [
        make_item(
            root=root,
            path=path,
            source="unreal_symbol",
            title=f"{module_name} module dependencies",
            text="\n".join(lines).strip(),
            symbol_name=module_name,
            symbol_kind="module",
            module_name=module_name,
            extra=extra,
        )
    ]


def collect_includes(root: Path, path: Path, text: str, module_name: str) -> list[dict]:
    includes = INCLUDE_RE.findall(text)
    if not includes:
        return []
    title = f"{module_name}/{relative_path(root, path)} includes"
    body = "\n".join(
        [
            f"Unreal include map for {relative_path(root, path)}",
            f"Module: {module_name}",
            "Includes:",
            *[f"- {include}" for include in includes],
        ]
    )
    return [
        make_item(
            root=root,
            path=path,
            source="unreal_symbol",
            title=title,
            text=body,
            symbol_name=path.stem,
            symbol_kind="include_map",
            module_name=module_name,
            extra={"includes": includes},
        )
    ]


def collect_unreal_types(root: Path, path: Path, text: str, module_name: str) -> list[dict]:
    items: list[dict] = []
    for macro_match in UCLASS_RE.finditer(text):
        after_macro = text[macro_match.end() : macro_match.end() + 1200]
        type_match = TYPE_RE.search(after_macro)
        if not type_match:
            continue
        macro = macro_match.group(1)
        symbol_name = type_match.group("name")
        parent = type_match.group("parent") or ""
        kind_by_macro = {
            "UCLASS": "class",
            "USTRUCT": "struct",
            "UINTERFACE": "interface",
            "UENUM": "enum",
        }
        body = "\n".join(
            [
                f"Unreal {kind_by_macro[macro]} symbol: {symbol_name}",
                f"Macro: {macro}({clean_decl(macro_match.group(2))})",
                f"Parent: {parent or '(none detected)'}",
                f"Module: {module_name}",
                f"File: {relative_path(root, path)}",
                "",
                "Context:",
                context_lines(text, macro_match.start(), macro_match.end()),
            ]
        )
        items.append(
            make_item(
                root=root,
                path=path,
                source="unreal_symbol",
                title=f"{symbol_name} {kind_by_macro[macro]}",
                text=body,
                symbol_name=symbol_name,
                symbol_kind=kind_by_macro[macro],
                module_name=module_name,
                extra={"macro": macro, "parent_class": parent},
            )
        )
    return items


def collect_ufunctions(root: Path, path: Path, text: str, module_name: str) -> list[dict]:
    items: list[dict] = []
    for match in UFUNCTION_RE.finditer(text):
        decl = clean_decl(match.group("decl"))
        name_match = FUNCTION_NAME_RE.search(decl)
        if not name_match:
            continue
        symbol_name = name_match.group("name")
        body = "\n".join(
            [
                f"Unreal reflected function: {symbol_name}",
                f"Macro: UFUNCTION({clean_decl(match.group('meta'))})",
                f"Declaration: {decl}",
                f"Module: {module_name}",
                f"File: {relative_path(root, path)}",
            ]
        )
        items.append(
            make_item(
                root=root,
                path=path,
                source="unreal_symbol",
                title=f"{symbol_name} reflected function",
                text=body,
                symbol_name=symbol_name,
                symbol_kind="function",
                module_name=module_name,
                extra={"macro": "UFUNCTION", "declaration": decl},
            )
        )
    return items


def collect_uproperties(root: Path, path: Path, text: str, module_name: str) -> list[dict]:
    items: list[dict] = []
    for match in UPROPERTY_RE.finditer(text):
        decl = clean_decl(match.group("decl"))
        name_match = PROPERTY_NAME_RE.search(decl)
        if not name_match:
            continue
        symbol_name = name_match.group("name")
        body = "\n".join(
            [
                f"Unreal reflected property: {symbol_name}",
                f"Macro: UPROPERTY({clean_decl(match.group('meta'))})",
                f"Declaration: {decl}",
                f"Module: {module_name}",
                f"File: {relative_path(root, path)}",
            ]
        )
        items.append(
            make_item(
                root=root,
                path=path,
                source="unreal_symbol",
                title=f"{symbol_name} reflected property",
                text=body,
                symbol_name=symbol_name,
                symbol_kind="property",
                module_name=module_name,
                extra={"macro": "UPROPERTY", "declaration": decl},
            )
        )
    return items


def collect_definitions(root: Path, path: Path, text: str, module_name: str, max_definitions: int) -> list[dict]:
    if max_definitions <= 0 or path.suffix.lower() not in {".cpp", ".cc", ".cxx", ".inl"}:
        return []
    items: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for match in DEFINITION_RE.finditer(text):
        owner = match.group("class")
        name = match.group("name")
        if name in {"if", "for", "while", "switch"}:
            continue
        key = (owner, name)
        if key in seen:
            continue
        seen.add(key)
        symbol_name = f"{owner}::{name}"
        body = "\n".join(
            [
                f"Unreal C++ function definition: {symbol_name}",
                f"Owner: {owner}",
                f"Function: {name}",
                f"Module: {module_name}",
                f"File: {relative_path(root, path)}",
                "",
                "Context:",
                context_lines(text, match.start(), match.end(), radius=1),
            ]
        )
        items.append(
            make_item(
                root=root,
                path=path,
                source="unreal_symbol",
                title=f"{symbol_name} definition",
                text=body,
                symbol_name=symbol_name,
                symbol_kind="function_definition",
                module_name=module_name,
                extra={"owner": owner, "function": name},
            )
        )
        if len(items) >= max_definitions:
            break
    return items


def collect_file(
    root: Path,
    path: Path,
    args: argparse.Namespace,
    sidecar_items: list[dict],
) -> list[dict]:
    text = read_text(path)
    if not text or len(text.strip()) < args.min_chars:
        return []
    if path.name.endswith(".Build.cs"):
        return collect_module(root, path, text)

    module_name = infer_module_name(root, path)
    public_tier = args.tier == "public"
    if public_tier:
        if path.suffix.lower() not in HEADER_EXTENSIONS:
            return []
        if not is_public_tier_header(path, root, text):
            return []

    items: list[dict] = []
    if public_tier:
        sidecar = make_include_sidecar_item(root, path, text, module_name)
        if sidecar:
            sidecar_items.append(sidecar)
    else:
        items.extend(collect_includes(root, path, text, module_name))
    items.extend(collect_unreal_types(root, path, text, module_name))
    items.extend(collect_ufunctions(root, path, text, module_name))
    items.extend(collect_uproperties(root, path, text, module_name))
    if args.include_definitions and not public_tier:
        items.extend(collect_definitions(root, path, text, module_name, args.max_definitions_per_file))
    return items


def collect(args: argparse.Namespace) -> None:
    roots = [Path(value).expanduser().resolve() for value in args.root]
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sidecar_path = Path(args.sidecar_out) if args.sidecar_out else out_path.parent / "sidecar_symbols_meta.jsonl"
    sidecar_items: list[dict] = []
    set_symbol_context(scope=args.scope, project_name=args.project_name)

    written = 0
    scanned = 0
    open_mode = "a" if args.append else "w"
    with out_path.open(open_mode, encoding="utf-8") as handle:
        for root in roots:
            if not root.exists():
                print(f"[skip] missing root: {root}")
                continue
            for path in root.rglob("*"):
                if not path.is_file() or should_skip(
                    path, root, args.include_third_party, args.include_editor
                ):
                    continue
                if path.suffix.lower() not in SOURCE_EXTENSIONS and not path.name.endswith(".Build.cs"):
                    continue
                if path.stat().st_size > args.max_bytes:
                    continue
                scanned += 1
                for item in collect_file(root, path, args, sidecar_items):
                    handle.write(json.dumps(item, ensure_ascii=False) + "\n")
                    written += 1
                if scanned % 1000 == 0:
                    print(f"[{scanned}] scanned, wrote {written} symbols")

    if args.tier == "public" and sidecar_items and not args.append:
        sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        with sidecar_path.open("w", encoding="utf-8") as sidecar_handle:
            for item in sidecar_items:
                sidecar_handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        print(f"sidecar: wrote {len(sidecar_items)} path-only include_map records to {sidecar_path}")
    elif args.tier == "public" and sidecar_items and args.append:
        sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        with sidecar_path.open("a", encoding="utf-8") as sidecar_handle:
            for item in sidecar_items:
                sidecar_handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        print(f"sidecar: appended {len(sidecar_items)} include_map records to {sidecar_path}")

    print(
        f"done: scope={args.scope}, tier={args.tier}, scanned {scanned} files and wrote {written} symbol records to {out_path}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect Unreal C++ symbol metadata as JSONL.")
    parser.add_argument("--root", action="append", required=True)
    parser.add_argument("--out", default="data/unreal58/raw_symbols.jsonl")
    parser.add_argument(
        "--tier",
        choices=("public", "full"),
        default="public",
        help=(
            "public: Public/ or reflection-macro headers only, include maps to sidecar, "
            "no function_definition; full: legacy complete symbol harvest."
        ),
    )
    parser.add_argument(
        "--sidecar-out",
        default="",
        help="Path-only include_map sidecar for public tier (default: <out-dir>/sidecar_symbols_meta.jsonl).",
    )
    parser.add_argument("--min-chars", type=int, default=20)
    parser.add_argument("--max-bytes", type=int, default=1_000_000)
    parser.add_argument("--include-third-party", action="store_true")
    parser.add_argument(
        "--include-editor",
        action="store_true",
        help="Include Engine/Source/Editor trees (skipped by default alongside ThirdParty).",
    )
    parser.add_argument("--include-definitions", action="store_true")
    parser.add_argument("--max-definitions-per-file", type=int, default=25)
    parser.add_argument("--scope", choices=("engine", "project"), default="engine")
    parser.add_argument("--project-name", default="")
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append symbol records to --out instead of overwriting.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    collect(parse_args())
