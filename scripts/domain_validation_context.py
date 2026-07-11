#!/usr/bin/env python
"""Cross-file domain validation context built from PluginProjectContext."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from plugin_project_context import PluginProjectContext, build_plugin_project_context


@dataclass
class DomainValidationContext:
    project_root: Path
    plugin_context: PluginProjectContext
    scan_roots: list[Path] = field(default_factory=list)
    module_names: set[str] = field(default_factory=set)
    headers_by_module: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def from_project(cls, project_root: Path | str) -> DomainValidationContext:
        root = Path(project_root)
        ctx = build_plugin_project_context(root)
        scan_roots = ctx.scan_roots()
        module_names = {module.name for module in ctx.modules if module.enabled}
        headers: dict[str, list[str]] = {}
        for scan_root in scan_roots:
            if not scan_root.is_dir():
                continue
            module_name = scan_root.name
            for path in scan_root.rglob("*.h"):
                if any(part in path.parts for part in ("Intermediate", "Binaries")):
                    continue
                rel = str(path.relative_to(root)).replace("\\", "/")
                headers.setdefault(module_name, []).append(rel)
        return cls(
            project_root=root,
            plugin_context=ctx,
            scan_roots=scan_roots,
            module_names=module_names,
            headers_by_module=headers,
        )

    def rel_path(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.project_root)).replace("\\", "/")
        except ValueError:
            return str(path).replace("\\", "/")

    def module_for_path(self, path: Path) -> str:
        rel = self.rel_path(path)
        for module in self.plugin_context.modules:
            if rel.startswith(module.source_dir.replace("\\", "/")):
                return module.name
        parts = rel.split("/")
        if len(parts) >= 2 and parts[0] == "Source":
            return parts[1]
        if len(parts) >= 4 and parts[0] == "Plugins":
            return parts[3]
        return ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "projectRoot": str(self.project_root),
            "scanRoots": [str(path) for path in self.scan_roots],
            "moduleNames": sorted(self.module_names),
            "pluginCount": len(self.plugin_context.plugins),
        }


def validate_cross_file_subsystem_cleanup(ctx: DomainValidationContext, header_text: str, cpp_text: str) -> list[str]:
    issues: list[str] = []
    if "Subsystem" in header_text and "Deinitialize" in header_text:
        if "RemoveAll" not in cpp_text and "Clear" not in cpp_text and "Reset" not in cpp_text:
            issues.append("Subsystem Deinitialize should clear timers/delegates.")
    return issues
