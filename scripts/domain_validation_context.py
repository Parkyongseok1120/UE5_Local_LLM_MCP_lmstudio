#!/usr/bin/env python
"""Shared, read-once cross-file context for Unreal domain validators."""

from __future__ import annotations

import hashlib
import re
import time
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


def qualified_class_key(module: str, class_name: str) -> str:
    module = (module or "").strip()
    return f"{module}::{class_name}" if module else class_name


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
                    qkey = qualified_class_key(module_name, name)
                    context.headers_by_class[qkey] = path
                    if match.group("base"):
                        context.class_bases[qkey] = match.group("base")
            elif path.suffix.lower() in CPP_SUFFIXES:
                module_name = context.module_for_path(path)
                for match in CPP_OWNER_RE.finditer(masked):
                    owner = match.group("class")
                    qkey = qualified_class_key(module_name, owner)
                    context.cpp_paths_by_class.setdefault(qkey, [])
                    if path not in context.cpp_paths_by_class[qkey]:
                        context.cpp_paths_by_class[qkey].append(path)
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

    def header_for_class(self, class_name: str, module: str = "") -> Path | None:
        if module:
            hit = self.headers_by_class.get(qualified_class_key(module, class_name))
            if hit:
                return hit
        direct = self.headers_by_class.get(class_name)
        if direct:
            return direct
        suffix = f"::{class_name}"
        matches = [path for key, path in self.headers_by_class.items() if key.endswith(suffix)]
        return matches[0] if len(matches) == 1 else None

    def paired_header(self, cpp_path: Path, class_name: str = "") -> Path | None:
        module = self.module_for_path(cpp_path)
        if class_name:
            header = self.header_for_class(class_name, module)
            if header:
                return header
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

    def class_text(self, class_name: str, module: str = "") -> str:
        parts: list[str] = []
        header = self.header_for_class(class_name, module)
        if header:
            parts.append(self.text_for(header))
        qkey = qualified_class_key(module, class_name) if module else class_name
        seen: set[Path] = set()
        for key in (qkey, class_name):
            for cpp in self.cpp_paths_by_class.get(key, []):
                if cpp not in seen:
                    seen.add(cpp)
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


_CONTEXT_CACHE: dict[str, tuple[float, DomainValidationContext]] = {}
_CONTEXT_METRICS: dict[str, Any] = {"hits": 0, "misses": 0, "buildMs": []}


def _path_fingerprint(paths: list[Path]) -> str:
    parts: list[str] = []
    for path in sorted(paths):
        try:
            stat = path.stat()
            parts.append(f"{path}:{stat.st_mtime_ns}:{stat.st_size}")
        except OSError:
            parts.append(f"{path}:missing")
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _plugin_descriptor_fingerprint(project_root: Path) -> str:
    try:
        ctx = build_plugin_project_context(project_root)
        payload = sorted(f"{p.name}:{m.name}:{m.enabled}" for p in ctx.plugins for m in p.modules)
        return hashlib.sha256("|".join(payload).encode("utf-8")).hexdigest()[:16]
    except Exception:
        return "unknown"


def clear_domain_validation_cache(project_root: Path | str | None = None) -> None:
    global _CONTEXT_CACHE
    if project_root is None:
        _CONTEXT_CACHE.clear()
        return
    root = str(Path(project_root).resolve())
    _CONTEXT_CACHE = {key: value for key, value in _CONTEXT_CACHE.items() if not key.startswith(root + "|")}


def invalidate_domain_validation_cache_for_paths(
    project_root: Path | str,
    changed_paths: list[Path | str] | None = None,
) -> None:
    """Drop cached validation contexts after writes; project-wide when paths unknown."""
    clear_domain_validation_cache(project_root)
    if changed_paths:
        _CONTEXT_METRICS["invalidations"] = int(_CONTEXT_METRICS.get("invalidations") or 0) + len(changed_paths)


def get_context_cache_metrics() -> dict[str, Any]:
    return dict(_CONTEXT_METRICS)


def get_cached_domain_context(
    project_root: Path | str,
    *,
    paths: list[Path] | None = None,
    texts: dict[Path, str] | None = None,
    validation_mode: str = "full",
) -> DomainValidationContext:
    root = Path(project_root).resolve()
    selected = sorted({Path(path).resolve() for path in (paths or []) if Path(path).is_file()})
    cache_key = "|".join(
        [
            str(root),
            _plugin_descriptor_fingerprint(root),
            validation_mode,
            _path_fingerprint(selected),
        ]
    )
    now = time.time()
    cached = _CONTEXT_CACHE.get(cache_key)
    if cached and now - cached[0] < 300:
        _CONTEXT_METRICS["hits"] = int(_CONTEXT_METRICS.get("hits") or 0) + 1
        return cached[1]
    started = time.perf_counter()
    context = DomainValidationContext.from_project(root, paths=paths, texts=texts)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    _CONTEXT_METRICS["misses"] = int(_CONTEXT_METRICS.get("misses") or 0) + 1
    builds = list(_CONTEXT_METRICS.get("buildMs") or [])
    builds.append(elapsed_ms)
    _CONTEXT_METRICS["buildMs"] = builds[-32:]
    _CONTEXT_CACHE[cache_key] = (now, context)
    return context


def expand_domain_validation_scope(
    project_root: Path | str,
    requested_paths: list[Path | str],
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    requested = sorted({Path(item).resolve() for item in requested_paths if Path(item).is_file()})
    expanded: set[Path] = set(requested)
    reasons: dict[str, str] = {}
    unresolved: list[str] = []
    ctx = DomainValidationContext.from_project(root)

    for path in requested:
        rel = ctx.rel_path(path)
        suffix = path.suffix.lower()
        if suffix in CPP_SUFFIXES:
            module = ctx.module_for_path(path)
            for match in CPP_OWNER_RE.finditer(mask_comments_and_strings(ctx.text_for(path))):
                class_name = match.group("class")
                header = ctx.paired_header(path, class_name)
                if header and header not in expanded:
                    expanded.add(header)
                    reasons[ctx.rel_path(header)] = f"paired header for {module}::{class_name}"
            if header := ctx.paired_header(path):
                if header not in expanded:
                    expanded.add(header)
                    reasons[ctx.rel_path(header)] = f"paired header for {rel}"
        elif suffix in HEADER_SUFFIXES:
            module = ctx.module_for_path(path)
            header_text = ctx.text_for(path)
            masked = mask_comments_and_strings(header_text)
            class_names = [match.group("name") for match in CLASS_RE.finditer(masked)]
            if not class_names:
                class_names = [path.stem]
            for class_name in class_names:
                qkey = qualified_class_key(module, class_name)
                cpp_hits = ctx.cpp_paths_by_class.get(qkey, [])
                if not cpp_hits:
                    cpp_hits = ctx.cpp_paths_by_class.get(class_name, [])
                if not cpp_hits:
                    unresolved.append(qkey)
                for cpp in cpp_hits:
                    if cpp not in expanded:
                        expanded.add(cpp)
                        reasons[ctx.rel_path(cpp)] = f"implementation for {qkey}"
            build_cs = list(root.rglob("*.Build.cs"))
            for build_path in build_cs:
                if module and module.lower() in build_path.name.lower():
                    if build_path not in expanded:
                        expanded.add(build_path.resolve())
                        reasons[ctx.rel_path(build_path)] = "module Build.cs for visibility"

    return {
        "requestedScope": [ctx.rel_path(path) for path in requested],
        "expandedScope": [ctx.rel_path(path) for path in sorted(expanded)],
        "reasons": reasons,
        "unresolved": unresolved,
        "paths": sorted(expanded),
    }
