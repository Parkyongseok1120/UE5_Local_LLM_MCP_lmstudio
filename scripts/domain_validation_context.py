#!/usr/bin/env python
"""Shared, read-once cross-file context for Unreal domain validators."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cpp_parse_utils import mask_comments_and_strings
from plugin_project_context import PluginProjectContext, build_plugin_project_context, resolve_scan_roots

SOURCE_SUFFIXES = {".h", ".hpp", ".cpp", ".c", ".cc"}
HEADER_SUFFIXES = {".h", ".hpp"}
CPP_SUFFIXES = {".cpp", ".c", ".cc"}
CLASS_RE = re.compile(
    r"\bclass\s+(?:[A-Z0-9_]+_API\s+)?(?P<name>[A-Za-z_]\w*)"
    r"(?:\s*:\s*(?:public|protected|private)\s+(?P<base>[A-Za-z_]\w*))?[^;{]*\{"
)
CPP_OWNER_RE = re.compile(r"\b(?P<class>[A-Za-z_]\w*)::(?P<func>~?[A-Za-z_]\w*)\s*\(")


def _read_text(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp949", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
        except OSError:
            return ""
    return ""


@dataclass
class DomainValidationContext:
    project_root: Path
    plugin_context: PluginProjectContext
    scan_roots: list[Path] = field(default_factory=list)
    paths: list[Path] = field(default_factory=list)
    texts: dict[Path, str] = field(default_factory=dict)
    module_names: set[str] = field(default_factory=set)
    headers_by_module: dict[str, list[str]] = field(default_factory=dict)
    headers_by_class: dict[str, Path] = field(default_factory=dict)
    class_bases: dict[str, str] = field(default_factory=dict)
    cpp_paths_by_class: dict[str, list[Path]] = field(default_factory=dict)

    @classmethod
    def from_project(
        cls,
        project_root: Path | str,
        *,
        paths: list[Path] | None = None,
        texts: dict[Path, str] | None = None,
    ) -> DomainValidationContext:
        root = Path(project_root).resolve()
        plugin_context = build_plugin_project_context(root)
        scan_roots = [path.resolve() for path in resolve_scan_roots(root)]
        if paths is None:
            from plugin_project_context import iter_scan_root_files

            discovered: list[Path] = []
            for scan_root in scan_roots:
                if not scan_root.is_dir():
                    continue
                discovered.extend(
                    path.resolve()
                    for path in iter_scan_root_files(
                        scan_root,
                        skip_dirs={"Intermediate", "Binaries", "Saved", "ThirdParty"},
                    )
                )
            selected = sorted(set(discovered))
        else:
            selected = sorted({Path(path).resolve() for path in paths if Path(path).is_file()})

        supplied = {Path(path).resolve(): value for path, value in (texts or {}).items()}
        loaded = {path: supplied.get(path, _read_text(path)) for path in selected}
        context = cls(
            project_root=root,
            plugin_context=plugin_context,
            scan_roots=scan_roots,
            paths=selected,
            texts=loaded,
            module_names={module.name for module in plugin_context.modules if module.enabled},
        )
        for path in selected:
            text = loaded.get(path, "")
            masked = mask_comments_and_strings(text)
            module_name = context.module_for_path(path)
            if path.suffix.lower() in HEADER_SUFFIXES:
                rel = context.rel_path(path)
                context.headers_by_module.setdefault(module_name, []).append(rel)
                for match in CLASS_RE.finditer(masked):
                    name = match.group("name")
                    context.headers_by_class.setdefault(name, path)
                    if match.group("base"):
                        context.class_bases.setdefault(name, match.group("base"))
            elif path.suffix.lower() in CPP_SUFFIXES:
                for match in CPP_OWNER_RE.finditer(masked):
                    owner = match.group("class")
                    context.cpp_paths_by_class.setdefault(owner, [])
                    if path not in context.cpp_paths_by_class[owner]:
                        context.cpp_paths_by_class[owner].append(path)
        return context

    def rel_path(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.project_root)).replace("\\", "/")
        except ValueError:
            return str(path).replace("\\", "/")

    def module_for_path(self, path: Path) -> str:
        rel = self.rel_path(path)
        best: tuple[int, str] = (0, "")
        for module in self.plugin_context.modules:
            prefix = module.source_dir.replace("\\", "/").rstrip("/") + "/"
            if rel.startswith(prefix) and len(prefix) > best[0]:
                best = (len(prefix), module.name)
        return best[1]

    def text_for(self, path: Path | None) -> str:
        if path is None:
            return ""
        resolved = path.resolve()
        if resolved not in self.texts and resolved.is_file():
            self.texts[resolved] = _read_text(resolved)
        return self.texts.get(resolved, "")

    def header_for_class(self, class_name: str) -> Path | None:
        return self.headers_by_class.get(class_name)

    def paired_header(self, cpp_path: Path, class_name: str = "") -> Path | None:
        if class_name and class_name in self.headers_by_class:
            return self.headers_by_class[class_name]
        module = next(
            (item for item in self.plugin_context.modules if item.name == self.module_for_path(cpp_path)),
            None,
        )
        if module is None:
            return None
        module_root = (self.project_root / module.source_dir).resolve()
        try:
            rel = cpp_path.resolve().relative_to(module_root)
        except ValueError:
            return None
        parts = list(rel.parts)
        if parts and parts[0].lower() == "private":
            parts[0] = "Public"
        parts[-1] = f"{cpp_path.stem}.h"
        candidate = module_root.joinpath(*parts)
        if candidate.is_file():
            return candidate.resolve()
        public_root = module_root / "Public"
        matches = sorted(public_root.rglob(f"{cpp_path.stem}.h")) if public_root.is_dir() else []
        return matches[0].resolve() if len(matches) == 1 else None

    def class_text(self, class_name: str) -> str:
        parts: list[str] = []
        header = self.header_for_class(class_name)
        if header:
            parts.append(self.text_for(header))
        for cpp in self.cpp_paths_by_class.get(class_name, []):
            parts.append(self.text_for(cpp))
        return "\n".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "projectRoot": str(self.project_root),
            "scanRoots": [str(path) for path in self.scan_roots],
            "pathCount": len(self.paths),
            "moduleNames": sorted(self.module_names),
            "pluginCount": len(self.plugin_context.plugins),
            "classCount": len(self.headers_by_class),
        }


def validate_cross_file_subsystem_cleanup(
    ctx: DomainValidationContext,
    header_text: str,
    cpp_text: str,
) -> list[str]:
    """Compatibility helper; report cleanup only when setup evidence exists."""
    issues: list[str] = []
    setup = any(token in cpp_text for token in ("SetTimer(", "AddDynamic(", "AddUObject(", "AddRaw("))
    teardown = any(
        token in cpp_text
        for token in ("ClearTimer(", "ClearAllTimersForObject(", "RemoveDynamic(", "RemoveAll(", "Reset(")
    )
    if setup and "Deinitialize" in header_text and not teardown:
        issues.append("Subsystem Deinitialize should clear timers/delegates registered by this class.")
    return issues
