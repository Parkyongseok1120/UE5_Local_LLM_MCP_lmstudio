"""Resolve one engine-bound set of exact projects for an installer build."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from installer.direct_rag_build_model import TIERS
from workspace_paths import canonical_absolute_path_identity


ProjectBinding = Callable[[Path, Path], dict[str, Any]]
EngineVersion = Callable[[Path | None], str]
BindingMatch = Callable[[str, str, str, str], bool]
ProjectDiscovery = Callable[[Path], list[Path]]


@dataclass(frozen=True)
class BuildScope:
    tier: str
    root: Path
    python: Path
    target: Path
    active_project: Path | None
    engine_source: Path | None
    engine_version: str
    engine_association: str
    included_projects: tuple[Path, ...]
    excluded_projects: tuple[Path, ...]
    unresolved_dry_roots: tuple[Path, ...]


def _unique_paths(values: list[Path]) -> list[Path]:
    unique: dict[str, Path] = {}
    for value in values:
        resolved = value.expanduser().resolve()
        unique.setdefault(canonical_absolute_path_identity(resolved), resolved)
    return list(unique.values())


def _project_files(
    values: list[Path],
    discover_projects: ProjectDiscovery,
) -> list[Path]:
    discovered: list[Path] = []
    identities: set[str] = set()
    for value in _unique_paths(values):
        for descriptor in discover_projects(value):
            resolved = descriptor.resolve()
            identity = canonical_absolute_path_identity(resolved)
            if identity not in identities:
                discovered.append(resolved)
                identities.add(identity)
    return discovered


def _index_target(index_dir: Path) -> Path:
    configured = index_dir.expanduser().resolve()
    if configured.suffix and configured.name.casefold() != "rag.sqlite":
        raise ValueError(
            "Direct RAG writes require an index named rag.sqlite; "
            f"refusing to build a different target: {configured}"
        )
    return configured.parent if configured.name.casefold() == "rag.sqlite" else configured


def resolve_build_scope(
    *,
    python_executable: Path,
    index_dir: Path,
    tier: str,
    project_roots: list[Path],
    active_project: Path | None,
    engine_root: Path | None,
    root: Path,
    dry_run: bool,
    project_binding: ProjectBinding,
    engine_version: EngineVersion,
    bindings_match: BindingMatch,
    discover_projects: ProjectDiscovery,
) -> BuildScope:
    resolved_tier = str(tier or "standard").strip().lower()
    if resolved_tier not in TIERS:
        raise ValueError(f"unsupported Direct RAG index tier: {resolved_tier}")
    workspace = root.expanduser().resolve()
    active = active_project.expanduser().resolve() if active_project else None
    if active is not None and (not active.is_file() or active.suffix.casefold() != ".uproject"):
        raise ValueError(f"active project must be an existing .uproject file: {active}")

    target_binding = (
        project_binding(active, workspace)
        if active is not None
        else {
            "ok": True,
            "engineVersion": engine_version(engine_root) if engine_root else "",
            "engineAssociation": "",
        }
    )
    if target_binding.get("ok") is not True:
        raise ValueError(str(target_binding.get("error") or "active project engine is unresolved"))
    target_version = str(target_binding.get("engineVersion") or "")
    target_association = str(target_binding.get("engineAssociation") or "")

    engine_source: Path | None = None
    if resolved_tier in {"standard", "full"}:
        if engine_root is None:
            if not dry_run:
                raise ValueError(f"{resolved_tier} Direct RAG build requires an Unreal Engine root")
            engine_source = workspace / "<unresolved-engine-root>" / "Engine" / "Source"
        else:
            engine_source = engine_root.expanduser().resolve() / "Engine" / "Source"
        if not dry_run and not engine_source.is_dir():
            raise ValueError(f"Unreal Engine source directory is missing: {engine_source}")
        actual_version = engine_version(engine_root) if engine_root else ""
        if not dry_run and target_version and actual_version != target_version:
            raise ValueError(
                f"active project uses Unreal {target_version}, "
                f"but engine root is {actual_version or 'unknown'}"
            )
        bound_root = str(target_binding.get("engineRoot") or "").strip()
        if bound_root and engine_root and Path(bound_root).resolve() != engine_root.resolve():
            raise ValueError("custom EngineAssociation resolved to a different engine root")

    candidates = _project_files([*project_roots, *([active] if active else [])], discover_projects)
    included: list[Path] = []
    excluded: list[Path] = []
    for descriptor in candidates:
        binding = project_binding(descriptor, workspace)
        if binding.get("ok") is True and bindings_match(
            str(binding.get("engineVersion") or ""),
            str(binding.get("engineAssociation") or ""),
            target_version,
            target_association,
        ):
            included.append(descriptor)
        else:
            excluded.append(descriptor)
    if active is not None and active not in included:
        raise ValueError("active project was excluded from its own engine-bound RAG generation")
    unresolved = _unique_paths(project_roots) if dry_run and not candidates else []
    if not included and not unresolved:
        raise ValueError("Direct RAG build found no project compatible with the target engine binding")

    return BuildScope(
        tier=resolved_tier,
        root=workspace,
        python=python_executable.expanduser().resolve(),
        target=_index_target(index_dir),
        active_project=active,
        engine_source=engine_source,
        engine_version=target_version,
        engine_association=target_association,
        included_projects=tuple(included),
        excluded_projects=tuple(excluded),
        unresolved_dry_roots=tuple(unresolved),
    )


__all__ = ["BuildScope", "resolve_build_scope"]
