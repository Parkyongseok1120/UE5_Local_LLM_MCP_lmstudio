#!/usr/bin/env python
"""Collect Unreal project profile summaries for RAG."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


BUILD_DEP_RE = re.compile(
    r"(?P<kind>PublicDependencyModuleNames|PrivateDependencyModuleNames|PublicIncludePathModuleNames|PrivateIncludePathModuleNames)"
    r"\.AddRange\s*\(\s*new\s+string\[\]\s*\{(?P<body>.*?)\}\s*\)",
    re.DOTALL,
)
QUOTED_RE = re.compile(r'"([^"]+)"')
SKIP_DIRS = {".git", ".vs", "Binaries", "DerivedDataCache", "Intermediate", "Saved"}


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


def read_json(path: Path) -> dict[str, Any]:
    text = read_text(path)
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


def has_skip_part(path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.parts)


def find_projects(root: Path) -> list[Path]:
    if root.is_file() and root.suffix.lower() == ".uproject":
        return [root]
    direct = sorted(root.glob("*.uproject"))
    if direct:
        return direct
    return sorted(path for path in root.rglob("*.uproject") if not has_skip_part(path))


def parse_build_deps(path: Path) -> dict[str, list[str]]:
    text = read_text(path) or ""
    deps: dict[str, list[str]] = {}
    for match in BUILD_DEP_RE.finditer(text):
        deps[match.group("kind")] = QUOTED_RE.findall(match.group("body"))
    return deps


def relative(project_root: Path, path: Path) -> str:
    try:
        return path.relative_to(project_root).as_posix()
    except ValueError:
        return path.as_posix()


def make_doc(
    *,
    project_root: Path,
    project_name: str,
    path: Path,
    title: str,
    text: str,
    relative_path: str,
) -> dict[str, Any]:
    return {
        "id": stable_id(f"project_profile:{project_root}:{title}:{relative_path}"),
        "source": "project_profile",
        "path": str(path),
        "title": title,
        "text": text,
        "metadata": {
            "project": project_name,
            "project_root": str(project_root),
            "relative_path": relative_path,
            "extension": path.suffix.lower() or ".profile",
        },
    }


def project_summary_doc(project_file: Path) -> dict[str, Any]:
    project_root = project_file.parent.resolve()
    project_name = project_file.stem
    data = read_json(project_file)

    modules = data.get("Modules") or []
    plugins = data.get("Plugins") or []
    targets = sorted(path for path in project_root.rglob("*.Target.cs") if not has_skip_part(path))
    build_files = sorted(path for path in project_root.rglob("*.Build.cs") if not has_skip_part(path))
    config_files = sorted(path for path in (project_root / "Config").rglob("*.ini")) if (project_root / "Config").exists() else []
    uplugins = sorted(path for path in project_root.rglob("*.uplugin") if not has_skip_part(path))

    lines = [
        f"Unreal project profile: {project_name}",
        f"Project file: {project_file}",
        f"Project root: {project_root}",
        f"EngineAssociation: {data.get('EngineAssociation', '(not specified)')}",
        "",
        "Modules declared in .uproject:",
    ]
    if modules:
        for module in modules:
            lines.append(
                "- "
                + f"Name: {module.get('Name', '')}; Type: {module.get('Type', '')}; "
                + f"LoadingPhase: {module.get('LoadingPhase', '')}"
            )
    else:
        lines.append("- none declared")

    enabled_plugins = [plugin.get("Name", "") for plugin in plugins if plugin.get("Enabled")]
    disabled_plugins = [plugin.get("Name", "") for plugin in plugins if plugin.get("Enabled") is False]
    lines.extend(
        [
            "",
            f"Enabled plugins: {', '.join(enabled_plugins) if enabled_plugins else '(none listed)'}",
            f"Disabled plugins: {', '.join(disabled_plugins) if disabled_plugins else '(none listed)'}",
            "",
            "Build.cs files:",
            *[f"- {relative(project_root, path)}" for path in build_files[:80]],
            "",
            "Target.cs files:",
            *[f"- {relative(project_root, path)}" for path in targets[:40]],
            "",
            "Config files:",
            *[f"- {relative(project_root, path)}" for path in config_files[:80]],
            "",
            "Plugin descriptor files:",
            *[f"- {relative(project_root, path)}" for path in uplugins[:80]],
        ]
    )
    return make_doc(
        project_root=project_root,
        project_name=project_name,
        path=project_file,
        title=f"{project_name} project profile",
        text="\n".join(lines),
        relative_path=project_file.name,
    )


def module_profile_docs(project_file: Path) -> list[dict[str, Any]]:
    project_root = project_file.parent.resolve()
    project_name = project_file.stem
    docs: list[dict[str, Any]] = []
    for path in sorted(project_root.rglob("*.Build.cs")):
        if has_skip_part(path):
            continue
        module_name = path.name.removesuffix(".Build.cs")
        deps = parse_build_deps(path)
        lines = [
            f"Unreal project module profile: {module_name}",
            f"Project: {project_name}",
            f"Build.cs: {relative(project_root, path)}",
            "Dependency visibility:",
        ]
        for key in (
            "PublicDependencyModuleNames",
            "PrivateDependencyModuleNames",
            "PublicIncludePathModuleNames",
            "PrivateIncludePathModuleNames",
        ):
            values = deps.get(key) or []
            lines.append(f"- {key}: {', '.join(values) if values else '(empty)'}")
        lines.extend(
            [
                "",
                "Rule:",
                "- Public header exposes another module type: use PublicDependencyModuleNames.",
                "- Private .cpp-only usage: use PrivateDependencyModuleNames.",
                "- Editor-only dependencies belong in Editor modules.",
            ]
        )
        docs.append(
            make_doc(
                project_root=project_root,
                project_name=project_name,
                path=path,
                title=f"{project_name}/{module_name} module profile",
                text="\n".join(lines),
                relative_path=relative(project_root, path),
            )
        )
    return docs


def collect(args: argparse.Namespace) -> None:
    roots = [Path(value).expanduser().resolve() for value in (args.root or ["data"])]
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    docs: list[dict[str, Any]] = []
    for root in roots:
        if not root.exists():
            print(f"[skip] missing root: {root}")
            continue
        projects = find_projects(root)
        if not projects:
            print(f"[skip] no .uproject files found under: {root}")
            continue
        for project_file in projects:
            docs.append(project_summary_doc(project_file))
            docs.extend(module_profile_docs(project_file))

    with out_path.open("w", encoding="utf-8") as handle:
        for doc in docs:
            handle.write(json.dumps(doc, ensure_ascii=False) + "\n")

    print(f"done: wrote {len(docs)} project profile records to {out_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect Unreal project profile summaries as JSONL.")
    parser.add_argument("--root", action="append", default=None)
    parser.add_argument("--out", default="data/unreal58/raw_project_profiles.jsonl")
    return parser.parse_args()


if __name__ == "__main__":
    collect(parse_args())
