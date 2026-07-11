#!/usr/bin/env python
"""Unified .uproject/.uplugin module map shared by scans, resolver, and validation."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


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


def build_plugin_project_context(project_root: Path | str) -> PluginProjectContext:
    root = Path(project_root)
    uproject_files = list(root.glob("*.uproject"))
    project_name = uproject_files[0].stem if uproject_files else root.name
    uproject = _read_json(uproject_files[0]) if uproject_files else {}
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

    return ctx


def resolve_scan_roots(project_root: Path | str, *, include_vendor: bool = False) -> list[Path]:
    ctx = build_plugin_project_context(project_root)
    return ctx.scan_roots(include_vendor=include_vendor)


def paired_header_for_cpp(cpp_path: Path, project_root: Path | str) -> Path | None:
    """Return the paired Public/Private header for a plugin/game module cpp file."""
    root = Path(project_root)
    try:
        rel = cpp_path.relative_to(root)
    except ValueError:
        return None
    parts = rel.parts
    if len(parts) < 4 or parts[0] not in {"Source", "Plugins"}:
        return None
    stem = cpp_path.stem
    private_dir = cpp_path.parent
    if private_dir.name.lower() != "private":
        return None
    public_dir = private_dir.parent / "Public"
    candidate = public_dir / f"{stem}.h"
    return candidate if candidate.is_file() else None


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
