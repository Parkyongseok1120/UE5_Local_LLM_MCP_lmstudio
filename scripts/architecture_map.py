#!/usr/bin/env python
"""Generate deterministic Unreal project architecture maps from source text."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from parse_build_cs import parse_build_cs_file
from workspace_paths import load_shared_config

SOURCE_EXTENSIONS = {".h", ".hpp", ".hh", ".cpp", ".cxx", ".cc", ".cs"}
HEADER_EXTENSIONS = {".h", ".hpp", ".hh"}
CPP_EXTENSIONS = {".cpp", ".cxx", ".cc"}
SKIP_DIRS = {".git", ".vs", "Binaries", "DerivedDataCache", "Intermediate", "Saved"}
BLUEPRINT_SPECIFIERS = {
    "BlueprintReadWrite",
    "BlueprintReadOnly",
    "BlueprintCallable",
    "BlueprintImplementableEvent",
    "BlueprintNativeEvent",
}
SERIALIZED_SPECIFIERS = {
    "EditAnywhere",
    "EditDefaultsOnly",
    "VisibleAnywhere",
    "VisibleDefaultsOnly",
    "BlueprintReadWrite",
    "BlueprintReadOnly",
}
ASSET_REFERENCE_MARKERS = (
    "UAnimMontage",
    "UDataAsset",
    "ULevelSequence",
    "UTexture",
    "USound",
    "UMaterial",
    "TSoftObjectPtr",
    "TSoftClassPtr",
)
EDITOR_ONLY_MARKERS = (
    "UnrealEd",
    "Blutility",
    "EditorUtilityWidget",
    "EditorSubsystem",
    "AssetToolsModule",
    "Kismet2/",
    "Editor/",
)

REFLECTED_TYPE_RE = re.compile(
    r"\b(?P<kind>UCLASS|USTRUCT|UINTERFACE)\s*(?:\((?P<meta>.*?)\))?\s*"
    r"(?P<decl>class|struct)\s+(?:[A-Z0-9_]+_API\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:\s*:\s*public\s+(?P<base>[A-Za-z_][A-Za-z0-9_]*))?",
    re.DOTALL,
)
REGULAR_CLASS_RE = re.compile(
    r"\bclass\s+(?:[A-Z0-9_]+_API\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:\s*:\s*public\s+(?P<base>[A-Za-z_][A-Za-z0-9_]*))?",
)
UPROPERTY_RE = re.compile(r"\bUPROPERTY\s*\((?P<spec>[^)]*)\)\s*(?P<decl>[^;{}]+);", re.DOTALL)
UFUNCTION_RE = re.compile(r"\bUFUNCTION\s*\((?P<spec>[^)]*)\)\s*(?P<decl>[^;{}]+);", re.DOTALL)


def read_text(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp949", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except (UnicodeDecodeError, OSError):
            continue
    return ""


def rel_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def should_skip(path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.parts)


def resolve_project_file(project_arg: str | None = None) -> Path | None:
    if project_arg:
        candidate = Path(project_arg).expanduser().resolve()
        if candidate.is_file() and candidate.suffix.lower() == ".uproject":
            return candidate
        if candidate.is_dir():
            found = sorted(candidate.glob("*.uproject"))
            return found[0] if found else None
        raise SystemExit(f"Invalid project path: {project_arg}")

    shared = load_shared_config()
    active = str(shared.get("activeProject") or "").strip()
    if not active:
        return None
    active_path = Path(active).expanduser().resolve()
    if active_path.is_file() and active_path.suffix.lower() == ".uproject":
        return active_path
    if active_path.is_dir():
        found = sorted(active_path.glob("*.uproject"))
        return found[0] if found else None
    return None


def project_root_from_arg(project_arg: str | None = None) -> Path:
    project_file = resolve_project_file(project_arg)
    if project_file:
        return project_file.parent
    if project_arg:
        candidate = Path(project_arg).expanduser().resolve()
        if candidate.is_dir():
            return candidate
    raise SystemExit("No .uproject found. Pass --project or configure activeProject.")


def split_specifiers(spec: str) -> list[str]:
    values: list[str] = []
    for raw in re.split(r",(?![^()]*\))", spec or ""):
        item = raw.strip()
        if not item:
            continue
        key = item.split("=", 1)[0].strip()
        if key and key not in values:
            values.append(key)
    return values


def declaration_name(decl: str, *, function: bool) -> str:
    cleaned = " ".join(str(decl or "").replace("\n", " ").split())
    if function:
        match = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(", cleaned)
        return match.group(1) if match else ""
    cleaned = cleaned.split("=", 1)[0].strip()
    names = re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", cleaned)
    return names[-1] if names else ""


def parse_reflected_members(text: str) -> dict[str, list[dict[str, Any]]]:
    properties: list[dict[str, Any]] = []
    functions: list[dict[str, Any]] = []
    for match in UPROPERTY_RE.finditer(text or ""):
        specifiers = split_specifiers(match.group("spec"))
        properties.append(
            {
                "name": declaration_name(match.group("decl"), function=False),
                "specifiers": specifiers,
            }
        )
    for match in UFUNCTION_RE.finditer(text or ""):
        specifiers = split_specifiers(match.group("spec"))
        functions.append(
            {
                "name": declaration_name(match.group("decl"), function=True),
                "specifiers": specifiers,
            }
        )
    return {"properties": [p for p in properties if p["name"]], "functions": [f for f in functions if f["name"]]}


def strip_reflection_declarations(text: str) -> str:
    """Remove reflected declarations before scanning plain C++ members."""
    cleaned = UPROPERTY_RE.sub("", text or "")
    cleaned = UFUNCTION_RE.sub("", cleaned)
    return cleaned


def is_macro_like_name(name: str) -> bool:
    return bool(name and "_" in name and name.upper() == name)


def parse_cpp_member_evidence(text: str, owner_name: str = "") -> dict[str, list[dict[str, Any]]]:
    body = strip_reflection_declarations(text)
    variables: list[dict[str, Any]] = []
    methods: list[dict[str, Any]] = []
    seen_variables: set[str] = set()
    seen_methods: set[str] = set()
    skip_prefixes = (
        "public:",
        "protected:",
        "private:",
        "using ",
        "typedef ",
        "friend ",
        "class ",
        "struct ",
        "enum ",
        "template ",
        "static_assert",
        "GENERATED_BODY",
    )
    skip_names = {
        "if",
        "for",
        "while",
        "switch",
        "return",
        "sizeof",
        "void",
        "int",
        "bool",
        "float",
        "double",
        "public",
        "private",
        "protected",
    }
    owner_without_prefix = owner_name[1:] if owner_name[:1] in {"A", "F", "I", "S", "T", "U"} else owner_name

    method_re = re.compile(
        r"^[ \t]*(?:(?:virtual|static|inline|FORCEINLINE|FORCEINLINE_DEBUGGABLE|explicit)[ \t]+)*"
        r"(?:[A-Za-z_][A-Za-z0-9_:<>,&* \t]+[ \t]+)?"
        r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)[ \t]*\([^\n;{}()]*\)[ \t]*"
        r"(?:const[ \t]*)?(?:override[ \t]*)?(?:final[ \t]*)?(?:=[ \t]*0[ \t]*)?;",
        re.MULTILINE,
    )
    for match in method_re.finditer(body):
        line = match.group(0).strip()
        name = match.group("name")
        if not name or name in skip_names or name in {owner_name, owner_without_prefix} or is_macro_like_name(name):
            continue
        if line.startswith(skip_prefixes) or line.startswith(("DECLARE_", "UCLASS", "USTRUCT", "UINTERFACE")):
            continue
        if name not in seen_methods:
            methods.append({"name": name})
            seen_methods.add(name)

    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line or not line.endswith(";") or "(" in line:
            continue
        if line.startswith(skip_prefixes) or line.startswith(("#", "//", "DECLARE_", "UCLASS", "USTRUCT", "UINTERFACE")):
            continue
        if any(token in line for token in ("::", "{", "}")):
            continue
        name = declaration_name(line, function=False)
        if not name or name in skip_names or name in seen_variables:
            continue
        variables.append({"name": name})
        seen_variables.add(name)

    return {"variables": variables, "methods": methods}


def type_body_after(text: str, offset: int) -> str:
    """Return the class/struct body following a type declaration offset."""
    open_brace = text.find("{", offset)
    if open_brace < 0:
        return ""
    depth = 0
    for index in range(open_brace, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace + 1:index]
    return text[open_brace + 1:]


def module_for_path(project_root: Path, path: Path) -> str:
    rel = path.resolve().relative_to(project_root.resolve())
    parts = list(rel.parts)
    for index, part in enumerate(parts):
        if part == "Source" and index + 1 < len(parts):
            return parts[index + 1]
    return ""


def detect_plugins(project_root: Path) -> list[dict[str, str]]:
    plugins_root = project_root / "Plugins"
    if not plugins_root.is_dir():
        return []
    plugins: list[dict[str, str]] = []
    for descriptor in sorted(plugins_root.rglob("*.uplugin")):
        if should_skip(descriptor):
            continue
        plugins.append({"name": descriptor.stem, "path": rel_path(project_root, descriptor)})
    return plugins


def source_files(project_root: Path) -> list[Path]:
    roots = [project_root / "Source", project_root / "Plugins"]
    files: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix.lower() in SOURCE_EXTENSIONS and not should_skip(path):
                files.append(path)
    return files


def classify_module(module_name: str, build_cs: Path, parsed: dict[str, Any]) -> str:
    deps = parsed.get("dependencies") or {}
    flat = {item for values in deps.values() for item in values}
    text = " ".join([module_name, build_cs.as_posix(), *sorted(flat)]).lower()
    if module_name.endswith("Editor") or any(marker.lower() in text for marker in EDITOR_ONLY_MARKERS):
        return "editor"
    return "runtime"


def detect_modules(project_root: Path, files: list[Path]) -> list[dict[str, Any]]:
    build_files = sorted(path for path in files if path.name.endswith(".Build.cs"))
    counts: dict[str, dict[str, int]] = {}
    for path in files:
        module = module_for_path(project_root, path)
        if not module:
            continue
        bucket = counts.setdefault(module, {"headers": 0, "sources": 0})
        if path.suffix.lower() in HEADER_EXTENSIONS:
            bucket["headers"] += 1
        elif path.suffix.lower() in CPP_EXTENSIONS:
            bucket["sources"] += 1

    modules: list[dict[str, Any]] = []
    for build_cs in build_files:
        module_name = build_cs.name.removesuffix(".Build.cs")
        parsed = parse_build_cs_file(build_cs)
        deps = parsed.get("dependencies") or {}
        module_root = build_cs.parent
        public_dir = module_root / "Public"
        private_dir = module_root / "Private"
        modules.append(
            {
                "name": module_name,
                "buildCs": rel_path(project_root, build_cs),
                "publicSourceFolders": [rel_path(project_root, public_dir)] if public_dir.is_dir() else [],
                "privateSourceFolders": [rel_path(project_root, private_dir)] if private_dir.is_dir() else [],
                "publicDependencies": deps.get("PublicDependencyModuleNames", []),
                "privateDependencies": deps.get("PrivateDependencyModuleNames", []),
                "classification": classify_module(module_name, build_cs, parsed),
                "headerCount": counts.get(module_name, {}).get("headers", 0),
                "sourceCount": counts.get(module_name, {}).get("sources", 0),
            }
        )
    return modules


def category_for_type(name: str, base: str, kind: str) -> str:
    if kind == "UINTERFACE" or base == "UInterface" or name.startswith("I"):
        return "Interface"
    if base in {"AActor", "APawn", "ACharacter", "APlayerController", "AGameModeBase", "AGameStateBase"}:
        return "Actor"
    if base == "USceneComponent":
        return "SceneComponent"
    if base == "UActorComponent" or name.endswith("Component"):
        return "ActorComponent"
    if base in {"UGameInstanceSubsystem", "UWorldSubsystem", "ULocalPlayerSubsystem"}:
        return base.removeprefix("U")
    if base == "UUserWidget" or name.endswith("Widget"):
        return "UserWidget"
    if base == "UDataAsset" or name.endswith("DataAsset"):
        return "DataAsset"
    if name.startswith("U"):
        return "UObject"
    return "Other"


def responsibility_hints(name: str, base: str, category: str) -> list[str]:
    lowered = name.lower()
    hints: list[str] = []
    if any(token in lowered for token in ("character", "pawn", "playercontroller")):
        hints.append("hint: input / player-facing gameplay bridge")
    if "combat" in lowered or "attack" in lowered:
        hints.append("hint: combat state / action execution candidate")
    if "input" in lowered:
        hints.append("hint: input ownership or input bridge candidate")
    if "subsystem" in lowered or category.endswith("Subsystem"):
        hints.append("hint: global or world-level service candidate")
    if category == "DataAsset":
        hints.append("hint: data definition / tuning surface")
    if category == "UserWidget":
        hints.append("hint: UI presentation surface")
    if "animinstance" in lowered or base == "UAnimInstance":
        hints.append("hint: animation state bridge")
    if category in {"ActorComponent", "SceneComponent"}:
        hints.append("hint: component-level gameplay behavior")
    return list(dict.fromkeys(hints))


def risk_flags_for_type(
    *,
    name: str,
    text: str,
    header_text: str = "",
    module_classification: str,
    cpp_path: str,
    category: str,
    reflected_surface: dict[str, list[dict[str, Any]]],
) -> list[str]:
    flags: list[str] = []
    prop_specs = {spec for prop in reflected_surface["properties"] for spec in prop.get("specifiers", [])}
    func_specs = {spec for fn in reflected_surface["functions"] for spec in fn.get("specifiers", [])}
    all_specs = prop_specs | func_specs
    if all_specs & BLUEPRINT_SPECIFIERS:
        flags.append("blueprint_facing_surface")
    if "BlueprintNativeEvent" in func_specs:
        flags.append("blueprint_native_event_surface")
        flags.append("blueprint_event_surface")
    if "BlueprintImplementableEvent" in func_specs:
        flags.append("blueprint_implementable_event_surface")
        flags.append("blueprint_event_surface")
    if prop_specs & SERIALIZED_SPECIFIERS:
        flags.append("reflected_serialized_surface")
    if any(marker in text for marker in ASSET_REFERENCE_MARKERS):
        flags.append("possible_asset_reference")
    if category not in {"DataAsset", "Interface", "Other"} and not cpp_path and name.startswith(("A", "U")):
        flags.append("missing_cpp_pair")
    editor_scan_text = f"{header_text}\n{text}"
    if "Editor" in name or any(marker in editor_scan_text for marker in EDITOR_ONLY_MARKERS):
        flags.append("editor_only_name_hint")
    if (
        module_classification == "runtime"
        and any(marker in editor_scan_text for marker in EDITOR_ONLY_MARKERS)
        and "WITH_EDITOR" not in editor_scan_text
    ):
        flags.append("runtime_editor_boundary_risk")
    return list(dict.fromkeys(flags))


def cpp_pair_index(project_root: Path, files: list[Path]) -> dict[tuple[str, str], str]:
    index: dict[tuple[str, str], str] = {}
    for path in files:
        if path.suffix.lower() not in CPP_EXTENSIONS:
            continue
        module = module_for_path(project_root, path)
        index.setdefault((module, path.stem), rel_path(project_root, path))
    return index


def detect_types(project_root: Path, files: list[Path], modules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    module_classification = {str(module["name"]): str(module.get("classification") or "runtime") for module in modules}
    cpp_index = cpp_pair_index(project_root, files)
    types: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for path in files:
        if path.suffix.lower() not in HEADER_EXTENSIONS:
            continue
        text = read_text(path)
        module = module_for_path(project_root, path)
        cpp_path = cpp_index.get((module, path.stem), "")
        reflected_names: set[str] = set()

        for match in REFLECTED_TYPE_RE.finditer(text):
            kind = match.group("kind")
            name = match.group("name")
            base = match.group("base") or ""
            body = type_body_after(text, match.end())
            reflected_names.add(name)
            category = category_for_type(name, base, kind)
            surface = parse_reflected_members(body)
            member_evidence = parse_cpp_member_evidence(body, name)
            key = (name, rel_path(project_root, path))
            if key in seen:
                continue
            seen.add(key)
            flags = risk_flags_for_type(
                name=name,
                text=body,
                header_text=text,
                module_classification=module_classification.get(module, "runtime"),
                cpp_path=cpp_path,
                category=category,
                reflected_surface=surface,
            )
            types.append(
                {
                    "name": name,
                    "kind": kind,
                    "module": module,
                    "header": rel_path(project_root, path),
                    "cpp": cpp_path,
                    "baseClass": base,
                    "category": category,
                    "responsibilityHints": responsibility_hints(name, base, category),
                    "reflectedSurface": surface,
                    "memberEvidence": member_evidence,
                    "riskFlags": flags,
                }
            )

        for match in REGULAR_CLASS_RE.finditer(text):
            name = match.group("name")
            if name in reflected_names or name.endswith("_API"):
                continue
            base = match.group("base") or ""
            body = type_body_after(text, match.end())
            key = (name, rel_path(project_root, path))
            if key in seen:
                continue
            seen.add(key)
            category = category_for_type(name, base, "class")
            surface = parse_reflected_members(body)
            member_evidence = parse_cpp_member_evidence(body, name)
            types.append(
                {
                    "name": name,
                    "kind": "class",
                    "module": module,
                    "header": rel_path(project_root, path),
                    "cpp": cpp_path,
                    "baseClass": base,
                    "category": category,
                    "responsibilityHints": responsibility_hints(name, base, category),
                    "reflectedSurface": surface,
                    "memberEvidence": member_evidence,
                    "riskFlags": risk_flags_for_type(
                        name=name,
                        text=body,
                        header_text=text,
                        module_classification=module_classification.get(module, "runtime"),
                        cpp_path=cpp_path,
                        category=category,
                        reflected_surface=surface,
                    ),
                }
            )

    return sorted(types, key=lambda row: (row.get("module", ""), row.get("name", ""), row.get("header", "")))


def risk_summary(types: list[dict[str, Any]]) -> dict[str, list[str]]:
    def names_with(flag: str) -> list[str]:
        return [str(row["name"]) for row in types if flag in row.get("riskFlags", [])]

    return {
        "blueprintFacingTypes": names_with("blueprint_facing_surface"),
        "assetReferenceCandidates": names_with("possible_asset_reference"),
        "editorRuntimeBoundaryCandidates": names_with("runtime_editor_boundary_risk"),
        "missingCppPairs": names_with("missing_cpp_pair"),
    }


def generate_architecture_map(project_arg: str | Path | None = None) -> dict[str, Any]:
    project_file = resolve_project_file(str(project_arg)) if project_arg else resolve_project_file(None)
    project_root = project_file.parent if project_file else project_root_from_arg(str(project_arg) if project_arg else None)
    files = source_files(project_root)
    modules = detect_modules(project_root, files)
    types = detect_types(project_root, files, modules)
    target_files = sorted(rel_path(project_root, path) for path in files if path.name.endswith(".Target.cs"))
    build_files = sorted(rel_path(project_root, path) for path in files if path.name.endswith(".Build.cs"))
    return {
        "schemaVersion": 1,
        "project": {
            "name": project_file.stem if project_file else project_root.name,
            "projectFile": str(project_file) if project_file else "",
            "root": str(project_root),
            "sourceRoot": str(project_root / "Source"),
            "pluginsDetected": detect_plugins(project_root),
            "targetFilesDetected": target_files,
            "buildCsFilesDetected": build_files,
        },
        "modules": modules,
        "types": types,
        "riskSummary": risk_summary(types),
    }


def markdown_report(arch: dict[str, Any]) -> str:
    project = arch.get("project") or {}
    lines = [
        "# Project Architecture Map",
        "",
        "Generated architecture hints. Review before treating as project truth.",
        "",
        "## Project",
        "",
        f"- Name: `{project.get('name', '')}`",
        f"- Project file: `{project.get('projectFile', '')}`",
        f"- Source root: `{project.get('sourceRoot', '')}`",
        "",
        "## Modules",
        "",
    ]
    for module in arch.get("modules") or []:
        lines.append(
            f"- `{module.get('name', '')}` ({module.get('classification', 'unknown')}): "
            f"public deps={module.get('publicDependencies', [])}, private deps={module.get('privateDependencies', [])}"
        )
    lines.extend(["", "## System / Type Clusters", ""])
    for category in sorted({str(row.get("category") or "Other") for row in arch.get("types") or []}):
        names = [str(row.get("name")) for row in arch.get("types") or [] if row.get("category") == category]
        lines.append(f"- {category}: {', '.join(names[:16]) if names else '(none)'}")
    lines.extend(["", "## Blueprint-Facing Contracts", ""])
    for row in arch.get("types") or []:
        if "blueprint_facing_surface" in row.get("riskFlags", []):
            lines.append(f"- `{row.get('name', '')}` in `{row.get('header', '')}`")
    lines.extend(["", "## Safe Refactor Zones", ""])
    lines.append("- Non-reflected private helpers with cpp/header pairs and no Blueprint-facing risk flags.")
    lines.append("- Internal implementation changes that preserve reflected names, signatures, and asset references.")
    lines.extend(["", "## Unsafe Refactor Zones", ""])
    lines.append("- Blueprint-facing UPROPERTY/UFUNCTION names or signatures without migration and asset validation.")
    lines.append("- Runtime module code that references editor-only APIs without explicit editor guards.")
    lines.append("- DataAsset, montage, sequence, texture, or soft object references without reference validation.")
    lines.extend(["", "## Missing / Uncertain Data", ""])
    summary = arch.get("riskSummary") or {}
    lines.append(f"- Missing cpp pairs: {', '.join(summary.get('missingCppPairs') or []) or '(none detected)'}")
    lines.append("- This report is heuristic and source-text only; it does not inspect Blueprint graphs or loaded assets.")
    lines.extend(["", "## Required Validation For Risky Changes", ""])
    lines.append("- Run UBT for C++ compile surfaces.")
    lines.append("- Validate Blueprint and asset references before reflected rename/signature changes.")
    lines.append("- Review editor/runtime boundaries before adding editor modules to runtime code.")
    return "\n".join(lines) + "\n"


def write_outputs(arch: dict[str, Any], out: Path, markdown: Path | None = None) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(arch, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {out}")
    if markdown:
        markdown.parent.mkdir(parents=True, exist_ok=True)
        markdown.write_text(markdown_report(arch), encoding="utf-8")
        print(f"Wrote {markdown}")


def semantic_graph_v1(arch: dict[str, Any]) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    for row in arch.get("types") or []:
        nodes.append(
            {
                "id": f"{row.get('module','')}::{row.get('name','')}",
                "kind": str(row.get("category") or "Class"),
                "evidenceFiles": [row.get("header") or ""],
                "confidence": "inferred",
                "status": "confirmed" if row.get("header") else "unknown",
            }
        )
        base = row.get("base")
        if base:
            edges.append(
                {
                    "from": f"{row.get('module','')}::{row.get('name','')}",
                    "to": str(base),
                    "kind": "INHERITS",
                    "confidence": "inferred",
                }
            )
    return {"version": 1, "nodes": nodes, "edges": edges}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a compact Unreal architecture map.")
    parser.add_argument("--project", default="", help="Project .uproject or project root. Falls back to activeProject.")
    parser.add_argument("--out", type=Path, required=True, help="Output architecture_map.json path.")
    parser.add_argument("--markdown", type=Path, default=None, help="Optional generated Markdown report path.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    arch = generate_architecture_map(args.project or None)
    write_outputs(arch, args.out, args.markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
