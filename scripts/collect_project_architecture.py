#!/usr/bin/env python
"""Scan active Unreal project Source/ for architecture inventory (PAB)."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from workspace_paths import load_shared_config
from parse_build_cs import parse_build_cs_file

SOURCE_EXTENSIONS = {".h", ".hpp", ".hh", ".cpp", ".cxx", ".cc", ".cs"}
SKIP_DIRS = {".git", ".vs", "Binaries", "DerivedDataCache", "Intermediate", "Saved"}
UCLASS_RE = re.compile(
    r"\b(UCLASS|USTRUCT|UINTERFACE|UENUM)\s*\((?P<meta>.*?)\)\s*"
    r"(?:class|struct|enum\s+class|enum)\s+"
    r"(?:[A-Z0-9_]+_API\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)",
    re.DOTALL,
)
SUBOBJECT_RE = re.compile(
    r"CreateDefaultSubobject\s*<\s*(?P<type>[A-Za-z_][A-Za-z0-9_]*)>\s*\(\s*"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)",
)
INTERFACE_RE = re.compile(
    r"\bclass\s+[A-Z0-9_]+_API\s+(?P<iface>I[A-Za-z_][A-Za-z0-9_]*)",
)
from parse_build_cs import parse_build_cs_file

DATA_ASSET_RE = re.compile(
    r"\bclass\s+[A-Z0-9_]+_API\s+(?P<name>U[A-Za-z_][A-Za-z0-9_]*DataAsset)\b",
)
GAMEPLAY_FRAMEWORK_RE = re.compile(
    r"\bclass\s+[A-Z0-9_]+_API\s+(?P<name>A(?:GameMode(?:Base)?|GameState(?:Base)?|PlayerState|PlayerController|Character|Pawn)[A-Za-z0-9_]*)",
)
REPLICATED_UPROP_RE = re.compile(r"UPROPERTY\s*\([^)]*Replicated", re.I)
DELEGATE_RE = re.compile(r"DECLARE_(?:DYNAMIC_)?(?:MULTICAST_)?DELEGATE\w*\s*\(\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)")
GAMEPLAY_TAG_RE = re.compile(r"FGameplayTag(?:Container)?\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)")
INPUT_BINDING_RE = re.compile(r"BindAction\s*\(|SetupPlayerInputComponent")


def stable_id(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def read_text(path: Path) -> str | None:
    for encoding in ("utf-8-sig", "utf-8", "cp949", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except (UnicodeDecodeError, OSError):
            continue
    return None


def rel_path(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def should_skip(path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.parts)


def resolve_project_root(project_arg: str | None) -> Path:
    if project_arg:
        candidate = Path(project_arg).resolve()
        if candidate.is_file() and candidate.suffix.lower() == ".uproject":
            return candidate.parent
        if candidate.is_dir():
            uprojects = list(candidate.glob("*.uproject"))
            if uprojects:
                return uprojects[0].parent
            return candidate
        raise SystemExit(f"Invalid project path: {project_arg}")

    config = load_shared_config()
    active = str(config.get("activeProject") or "").strip()
    if not active:
        raise SystemExit("No activeProject. Run pick-project or pass --project.")
    active_path = Path(active).resolve()
    if active_path.suffix.lower() == ".uproject":
        return active_path.parent
    return active_path


def scan_architecture(project_root: Path) -> dict[str, Any]:
    project_name = project_root.name
    uproject = next(project_root.glob("*.uproject"), None)
    if uproject:
        project_name = uproject.stem

    classes: list[dict[str, Any]] = []
    subsystems: list[dict[str, Any]] = []
    components: list[dict[str, Any]] = []
    interfaces: list[dict[str, Any]] = []
    data_assets: list[dict[str, Any]] = []
    subobjects: list[dict[str, Any]] = []
    modules: list[dict[str, Any]] = []
    game_framework: list[dict[str, Any]] = []
    replicated_properties: list[dict[str, Any]] = []
    delegates: list[dict[str, Any]] = []
    gameplay_tags: list[dict[str, Any]] = []
    input_bindings: list[dict[str, Any]] = []

    source_root = project_root / "Source"
    if not source_root.is_dir():
        return {
            "project": project_name,
            "projectRoot": str(project_root),
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "classes": classes,
            "subsystems": subsystems,
            "components": components,
            "interfaces": interfaces,
            "dataAssets": data_assets,
            "subobjects": subobjects,
            "modules": modules,
            "gameFramework": game_framework,
            "replicatedProperties": replicated_properties,
            "delegates": delegates,
            "gameplayTags": gameplay_tags,
            "inputBindings": input_bindings,
        }

    for path in sorted(source_root.rglob("*")):
        if not path.is_file() or should_skip(path):
            continue
        if path.suffix.lower() not in SOURCE_EXTENSIONS:
            continue
        text = read_text(path) or ""
        rel = rel_path(project_root, path)
        owner_class = ""

        if path.suffix.lower() == ".Build.cs":
            module_name = path.name.removesuffix(".Build.cs")
            parsed = parse_build_cs_file(path)
            deps = parsed.get("dependencies") or {}
            mod_entry: dict[str, Any] = {
                "name": module_name,
                "path": rel,
                "dependencies": deps,
            }
            if parsed.get("conditional_dependencies"):
                mod_entry["conditional_dependencies"] = parsed["conditional_dependencies"]
            modules.append(mod_entry)
            continue

        for match in UCLASS_RE.finditer(text):
            macro = match.group(1)
            name = match.group("name")
            meta = match.group("meta") or ""
            entry = {
                "name": name,
                "macro": macro,
                "path": rel,
                "meta": meta.strip()[:200],
            }
            classes.append(entry)
            if macro == "UINTERFACE":
                interfaces.append({"name": name, "path": rel})
            lowered = name.lower()
            if "subsystem" in lowered or "Subsystem" in meta:
                subsystems.append({"name": name, "path": rel, "macro": macro})
            if "component" in lowered or macro == "UCLASS" and name.startswith("U") and "Component" in name:
                components.append({"name": name, "path": rel})

        for match in INTERFACE_RE.finditer(text):
            iface = match.group("iface")
            if iface.startswith("I") and iface not in {i["name"] for i in interfaces}:
                interfaces.append({"name": iface, "path": rel})

        for match in DATA_ASSET_RE.finditer(text):
            da_name = match.group("name")
            if da_name not in {d["name"] for d in data_assets}:
                data_assets.append({"name": da_name, "path": rel})

        class_match = re.search(
            r"\bclass\s+[A-Z0-9_]+_API\s+(?P<owner>[A-Za-z_][A-Za-z0-9_]*)",
            text,
        )
        if class_match:
            owner_class = class_match.group("owner")

        for match in SUBOBJECT_RE.finditer(text):
            subobjects.append(
                {
                    "ownerClass": owner_class,
                    "componentType": match.group("type"),
                    "memberName": match.group("name"),
                    "path": rel,
                }
            )

        for match in GAMEPLAY_FRAMEWORK_RE.finditer(text):
            gf_name = match.group("name")
            if gf_name not in {g["name"] for g in game_framework}:
                game_framework.append({"name": gf_name, "path": rel})

        for match in REPLICATED_UPROP_RE.finditer(text):
            replicated_properties.append({"path": rel, "snippet": match.group(0)[:80]})

        for match in DELEGATE_RE.finditer(text):
            delegates.append({"name": match.group("name"), "path": rel})

        for match in GAMEPLAY_TAG_RE.finditer(text):
            gameplay_tags.append({"name": match.group("name"), "path": rel})

        if INPUT_BINDING_RE.search(text):
            input_bindings.append({"path": rel, "ownerClass": owner_class or "(file-level)"})

    return {
        "project": project_name,
        "projectRoot": str(project_root),
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "classes": classes,
        "subsystems": subsystems,
        "components": components,
        "interfaces": interfaces,
        "dataAssets": data_assets,
        "subobjects": subobjects,
        "modules": modules,
        "gameFramework": game_framework,
        "replicatedProperties": replicated_properties,
        "delegates": delegates,
        "gameplayTags": gameplay_tags,
        "inputBindings": input_bindings,
        "summary": {
            "classCount": len(classes),
            "subsystemCount": len(subsystems),
            "componentCount": len(components),
            "interfaceCount": len(interfaces),
            "dataAssetCount": len(data_assets),
            "moduleCount": len(modules),
            "gameFrameworkCount": len(game_framework),
            "delegateCount": len(delegates),
        },
    }


def make_summary_text(arch: dict[str, Any], max_chars: int = 2000) -> str:
    lines = [
        f"Project Architecture Brief: {arch.get('project', '')}",
        f"Root: {arch.get('projectRoot', '')}",
        "",
        "Subsystems:",
    ]
    for item in arch.get("subsystems") or []:
        lines.append(f"  - {item['name']} ({item.get('path', '')})")
    lines.extend(["", "Components (sample):"])
    for item in (arch.get("components") or [])[:24]:
        lines.append(f"  - {item['name']} ({item.get('path', '')})")
    lines.extend(["", "DataAssets:"])
    for item in arch.get("dataAssets") or []:
        lines.append(f"  - {item['name']} ({item.get('path', '')})")
    lines.extend(["", "Interfaces:"])
    for item in arch.get("interfaces") or []:
        lines.append(f"  - {item['name']} ({item.get('path', '')})")
    lines.extend(["", "Modules:"])
    for item in arch.get("modules") or []:
        lines.append(f"  - {item['name']}")
    lines.extend(["", "Game framework (GameMode/Character/Pawn):"])
    for item in (arch.get("gameFramework") or [])[:12]:
        lines.append(f"  - {item['name']} ({item.get('path', '')})")
    lines.extend(["", "Replicated properties (sample):"])
    for item in (arch.get("replicatedProperties") or [])[:8]:
        lines.append(f"  - {item.get('snippet', '')} ({item.get('path', '')})")
    lines.extend(["", "Delegates:"])
    for item in (arch.get("delegates") or [])[:12]:
        lines.append(f"  - {item['name']} ({item.get('path', '')})")
    lines.extend(["", "Gameplay tags:"])
    for item in (arch.get("gameplayTags") or [])[:12]:
        lines.append(f"  - {item['name']} ({item.get('path', '')})")
    lines.extend(["", "Input bindings:"])
    for item in (arch.get("inputBindings") or [])[:12]:
        lines.append(f"  - {item.get('ownerClass', '')} ({item.get('path', '')})")
    text = "\n".join(lines)
    if len(text) > max_chars:
        return text[: max_chars - 3] + "..."
    return text


def make_rag_doc(arch: dict[str, Any], summary: str) -> dict[str, Any]:
    project = str(arch.get("project") or "unknown")
    project_root = str(arch.get("projectRoot") or "")
    return {
        "id": stable_id(f"project_architecture:{project_root}"),
        "source": "project_architecture",
        "path": str(Path(project_root) / "project_architecture.json"),
        "title": f"{project} architecture brief",
        "text": summary,
        "metadata": {
            "project": project,
            "project_root": project_root,
            "relative_path": "project_architecture.json",
            "extension": ".json",
            "class_count": arch.get("summary", {}).get("classCount", 0),
            "subsystem_count": arch.get("summary", {}).get("subsystemCount", 0),
            "component_count": arch.get("summary", {}).get("componentCount", 0),
        },
    }


def write_outputs(arch: dict[str, Any], out_dir: Path, jsonl_path: Path | None) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "project_architecture.json"
    json_path.write_text(json.dumps(arch, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {json_path}")

    summary = make_summary_text(arch)
    doc = make_rag_doc(arch, summary)
    jsonl_out = jsonl_path or out_dir / "raw_project_architecture.jsonl"
    jsonl_out.parent.mkdir(parents=True, exist_ok=True)
    with jsonl_out.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(doc, ensure_ascii=False) + "\n")
    print(f"Wrote {jsonl_out}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect project architecture brief (PAB).")
    parser.add_argument("--project", help="Project root or .uproject path")
    parser.add_argument("--out-dir", default="data/unreal58", help="Output directory for project_architecture.json")
    parser.add_argument("--jsonl", default="", help="Optional JSONL path for RAG index input")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = resolve_project_root(args.project)
    arch = scan_architecture(project_root)
    out_dir = Path(args.out_dir)
    jsonl = Path(args.jsonl) if args.jsonl else None
    write_outputs(arch, out_dir, jsonl)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
