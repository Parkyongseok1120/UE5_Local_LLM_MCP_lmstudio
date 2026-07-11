#!/usr/bin/env python
"""Unified .uproject/.uplugin module map shared by scans, resolver, and validation."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

SOURCE_FILE_SUFFIXES = {".h", ".hpp", ".hh", ".cpp", ".c", ".cc", ".cxx", ".cs"}
FLAT_FIXTURE_SKIP_DIRS = {
    ".git",
    ".vs",
    "Binaries",
    "Intermediate",
    "Saved",
    "DerivedDataCache",
    "ThirdParty",
    "node_modules",
    "tests",
    "scripts",
    "data",
    "docs",
    "Reports",
    "lmstudio-unreal-agent-mcp",
}


def is_source_like_file(path: Path) -> bool:
    suffix = path.suffix.lower()
    return suffix in SOURCE_FILE_SUFFIXES or path.name.endswith(".Build.cs")


def flat_fixture_has_direct_sources(root: Path) -> bool:
    """True for holdout-style trees without Source/Plugins layout."""
    if not root.is_dir():
        return False
    if (root / "Source").is_dir() or (root / "Plugins").is_dir():
        return False
    for path in root.iterdir():
        if is_source_like_file(path):
            return True
        if path.is_dir() and path.name not in FLAT_FIXTURE_SKIP_DIRS:
            for sub in path.iterdir():
                if is_source_like_file(sub):
                    return True
    return False


def fallback_scan_roots(project_root: Path | str) -> list[Path]:
    """Never return an ambiguous repo root for deep recursive scans."""
    root = Path(project_root)
    source = root / "Source"
    if source.is_dir():
        return [source]
    if flat_fixture_has_direct_sources(root):
        return [root]
    return []


def uses_deep_scan(scan_root: Path) -> bool:
    """Deep rglob only for standard Unreal Source/Plugins module trees."""
    parts = [part.lower() for part in scan_root.parts]
    if scan_root.name == "Source":
        return True
    if scan_root.parent.name == "Source":
        return True
    if "plugins" in parts and "source" in parts:
        return True
    if scan_root.name.lower() in {"public", "private", "classes"} and "source" in parts:
        return True
    return False


def iter_scan_root_files(
    scan_root: Path,
    *,
    skip_dirs: Iterable[str] | None = None,
) -> list[Path]:
    """Iterate source files under a scan root without scanning whole workspaces."""
    skip = set(skip_dirs or FLAT_FIXTURE_SKIP_DIRS)
    files: list[Path] = []
    if not scan_root.is_dir():
        return files
    if uses_deep_scan(scan_root):
        for path in scan_root.rglob("*"):
            if not path.is_file() or not is_source_like_file(path):
                continue
            if any(part in skip for part in path.parts):
                continue
            files.append(path)
        return files
    for path in scan_root.iterdir():
        if path.is_file() and is_source_like_file(path):
            files.append(path)
            continue
        if path.is_dir() and path.name not in skip:
            for sub in path.iterdir():
                if sub.is_file() and is_source_like_file(sub):
                    files.append(sub)
    return files



@dataclass
class ModuleDescriptor:
    name: str
    root: str
    source_dir: str
    module_type: str = "Runtime"
    loading_phase: str = "Default"
    plugin_name: str = ""
    installed: bool = False
    content_plugin: bool = False
    enabled: bool = True
    visibility: str = "Public"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PluginProjectContext:
    project_root: Path
    project_name: str = ""
    modules: list[ModuleDescriptor] = field(default_factory=list)
    plugins: list[dict[str, Any]] = field(default_factory=list)

    def module_by_name(self, name: str) -> ModuleDescriptor | None:
        for module in self.modules:
            if module.name == name:
                return module
        return None

    def scan_roots(self, *, include_vendor: bool = False) -> list[Path]:
        roots: list[Path] = []
        seen: set[str] = set()
        for module in self.modules:
            if not module.enabled:
                continue
            if module.installed and not include_vendor:
                continue
            source = self.project_root / module.source_dir
            key = str(source)
            if source.is_dir() and key not in seen:
                seen.add(key)
                roots.append(source)
        if not roots:
            fallback = self.project_root / "Source"
            if fallback.is_dir():
                roots.append(fallback)
        return roots

    def to_dict(self) -> dict[str, Any]:
        return {
            "projectRoot": str(self.project_root),
            "projectName": self.project_name,
            "modules": [module.to_dict() for module in self.modules],
            "plugins": self.plugins,
        }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _discover_module_dirs(source_dir: Path) -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    if not source_dir.is_dir():
        return found
    for child in sorted(source_dir.iterdir()):
        if not child.is_dir():
            continue
        if (child / "Public").is_dir() or (child / "Private").is_dir():
            found.append((child.name, child))
            continue
        for nested in sorted(child.iterdir()):
            if nested.is_dir() and ((nested / "Public").is_dir() or (nested / "Private").is_dir()):
                found.append((nested.name, nested))
    return found


def _parse_uplugin(path: Path) -> dict[str, Any]:
    data = _read_json(path)
    modules = []
    for item in data.get("Modules") or []:
        if isinstance(item, dict) and item.get("Name"):
            modules.append(
                {
                    "name": str(item.get("Name")),
                    "type": str(item.get("Type") or "Runtime"),
                    "loadingPhase": str(item.get("LoadingPhase") or "Default"),
                }
            )
    return {
        "friendlyName": str(data.get("FriendlyName") or path.parent.name),
        "installed": bool(data.get("Installed", False)),
        "canContainContent": bool(data.get("CanContainContent", False)),
        "enabled": bool(data.get("Enabled", True)),
        "modules": modules,
    }


def build_plugin_project_context(
    project_root: Path | str,
    *,
    include_disabled_plugins: bool = False,
) -> PluginProjectContext:
    root = Path(project_root)
    uproject_files = list(root.glob("*.uproject"))
    project_name = uproject_files[0].stem if uproject_files else root.name
    uproject = _read_json(uproject_files[0]) if uproject_files else {}
    disabled_plugins = {
        str(item.get("Name") or "")
        for item in (uproject.get("Plugins") or [])
        if isinstance(item, dict) and item.get("Enabled") is False and item.get("Name")
    }
    enabled_modules = {
        str(item.get("Name") or ""): str(item.get("Type") or "Runtime")
        for item in (uproject.get("Modules") or [])
        if isinstance(item, dict) and item.get("Name")
    }

    ctx = PluginProjectContext(project_root=root, project_name=project_name)
    game_source = root / "Source"
    for module_name, module_type in enabled_modules.items():
        module_dirs = _discover_module_dirs(game_source / module_name)
        if module_dirs:
            _, module_path = module_dirs[0]
            rel = str(module_path.relative_to(root)).replace("\\", "/")
        else:
            rel = f"Source/{module_name}"
        ctx.modules.append(
            ModuleDescriptor(
                name=module_name,
                root=rel,
                source_dir=rel,
                module_type=module_type,
                enabled=True,
            )
        )

    plugins_root = root / "Plugins"
    if plugins_root.is_dir():
        for uplugin in sorted(plugins_root.rglob("*.uplugin")):
            if any(part in uplugin.parts for part in ("Intermediate", "Binaries", "Saved")):
                continue
            meta = _parse_uplugin(uplugin)
            plugin_name = uplugin.parent.name
            disabled_in_uproject = plugin_name in disabled_plugins
            if disabled_in_uproject and not include_disabled_plugins:
                ctx.plugins.append(
                    {
                        "name": plugin_name,
                        "path": str(uplugin.parent.relative_to(root)).replace("\\", "/"),
                        "descriptor": str(uplugin.relative_to(root)).replace("\\", "/"),
                        **meta,
                        "enabled": False,
                        "disabledInUproject": True,
                    }
                )
                continue
            plugin_rel = str(uplugin.parent.relative_to(root)).replace("\\", "/")
            ctx.plugins.append(
                {
                    "name": plugin_name,
                    "path": plugin_rel,
                    "descriptor": str(uplugin.relative_to(root)).replace("\\", "/"),
                    **meta,
                }
            )
            if not meta.get("enabled", True):
                continue
            source_dir = uplugin.parent / "Source"
            for module_name, module_path in _discover_module_dirs(source_dir):
                rel = str(module_path.relative_to(root)).replace("\\", "/")
                ctx.modules.append(
                    ModuleDescriptor(
                        name=module_name,
                        root=rel,
                        source_dir=rel,
                        module_type=next(
                            (m.get("type", "Runtime") for m in meta.get("modules") or [] if m.get("name") == module_name),
                            "Runtime",
                        ),
                        plugin_name=plugin_name,
                        installed=bool(meta.get("installed")),
                        content_plugin=bool(meta.get("canContainContent")),
                        enabled=True,
                    )
                )
        for plugin_dir in sorted(plugins_root.iterdir()):
            if not plugin_dir.is_dir() or list(plugin_dir.glob("*.uplugin")):
                continue
            source_dir = plugin_dir / "Source"
            for module_name, module_path in _discover_module_dirs(source_dir):
                rel = str(module_path.relative_to(root)).replace("\\", "/")
                if any(module.source_dir == rel for module in ctx.modules):
                    continue
                ctx.modules.append(
                    ModuleDescriptor(
                        name=module_name,
                        root=rel,
                        source_dir=rel,
                        module_type="Runtime",
                        plugin_name=plugin_dir.name,
                        installed=False,
                        enabled=True,
                    )
                )

    return ctx


def resolve_scan_roots(
    project_root: Path | str,
    *,
    include_vendor: bool = False,
    include_disabled_plugins: bool = False,
) -> list[Path]:
    ctx = build_plugin_project_context(
        project_root,
        include_disabled_plugins=include_disabled_plugins,
    )
    roots = ctx.scan_roots(include_vendor=include_vendor)
    if roots:
        return roots
    return fallback_scan_roots(project_root)


def paired_header_for_cpp(cpp_path: Path, project_root: Path | str) -> Path | None:
    """Return a module-relative Public header for nested game/plugin Private cpp paths."""
    root = Path(project_root).resolve()
    cpp = cpp_path.resolve()
    ctx = build_plugin_project_context(root)
    for module in ctx.modules:
        module_root = (root / module.source_dir).resolve()
        try:
            rel = cpp.relative_to(module_root)
        except ValueError:
            continue
        parts = list(rel.parts)
        if not parts or parts[0].lower() != "private":
            continue
        parts[0] = "Public"
        parts[-1] = f"{cpp.stem}.h"
        candidate = module_root.joinpath(*parts)
        if candidate.is_file():
            return candidate
        public_root = module_root / "Public"
        matches = sorted(public_root.rglob(f"{cpp.stem}.h")) if public_root.is_dir() else []
        return matches[0] if len(matches) == 1 else None
    return None


def validate_uplugin_descriptor(path: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    rel = str(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        return [{"severity": "error", "code": "UPLUGIN_JSON_INVALID", "path": rel, "message": str(exc)}]
    except OSError as exc:
        return [{"severity": "error", "code": "UPLUGIN_READ_FAILED", "path": rel, "message": str(exc)}]

    modules = data.get("Modules") or []
    if not isinstance(modules, list) or not modules:
        findings.append(
            {"severity": "error", "code": "UPLUGIN_MODULES_MISSING", "path": rel, "message": "Modules array is required."}
        )
        return findings

    names: set[str] = set()
    for item in modules:
        if not isinstance(item, dict):
            findings.append({"severity": "error", "code": "UPLUGIN_MODULE_INVALID", "path": rel, "message": "Module entry must be object."})
            continue
        name = str(item.get("Name") or "").strip()
        if not name:
            findings.append({"severity": "error", "code": "UPLUGIN_MODULE_NAME_MISSING", "path": rel, "message": "Module Name is required."})
            continue
        if name in names:
            findings.append({"severity": "error", "code": "UPLUGIN_MODULE_DUPLICATE", "path": rel, "message": f"Duplicate module name {name}."})
        names.add(name)
        if str(item.get("Type") or "").strip() not in {"Runtime", "Editor", "Developer", "UncookedOnly", "CookedOnly"}:
            findings.append(
                {
                    "severity": "warning",
                    "code": "UPLUGIN_MODULE_TYPE_UNKNOWN",
                    "path": rel,
                    "message": f"Module {name} has unusual Type={item.get('Type')}.",
                }
            )
    return findings


def is_blocked_plugin_write(path: Path, project_root: Path | str) -> str | None:
    root = Path(project_root)
    try:
        rel = path.relative_to(root)
    except ValueError:
        return "path outside project root"
    parts = rel.parts
    if parts[0] != "Plugins":
        return None
    ctx = build_plugin_project_context(root)
    plugin_name = parts[1] if len(parts) > 1 else ""
    for plugin in ctx.plugins:
        if plugin.get("name") == plugin_name and plugin.get("installed"):
            return f"vendor plugin writes blocked: {plugin_name}"
    return None
