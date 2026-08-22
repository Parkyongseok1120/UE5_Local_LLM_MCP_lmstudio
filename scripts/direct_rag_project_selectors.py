#!/usr/bin/env python
"""Classify exact paths and names without dropping any project selector."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def exact_project_descriptor(selector: object) -> Path | None:
    candidate = Path(str(selector or "").strip()).expanduser()
    if candidate.is_file() and candidate.suffix.casefold() == ".uproject":
        return candidate.resolve()
    if candidate.is_dir():
        descriptors = sorted(candidate.glob("*.uproject"))
        if len(descriptors) == 1:
            return descriptors[0].resolve()
    return None


def descriptor_for_indexed_root(root: str, name: str) -> Path | None:
    directory = Path(root).expanduser()
    if not directory.is_dir():
        return None
    descriptors = sorted(directory.glob("*.uproject"))
    exact = [path for path in descriptors if path.stem.casefold() == name.casefold()]
    if len(exact) == 1:
        return exact[0].resolve()
    if len(descriptors) == 1 and directory.name.casefold() == name.casefold():
        return descriptors[0].resolve()
    return None


def classify_project_selectors(
    project_selector: Any,
    workspace: Path,
    *,
    use_active: bool,
) -> dict[str, Any]:
    values = (
        [project_selector]
        if isinstance(project_selector, str)
        else project_selector
        if isinstance(project_selector, list)
        else []
    )
    selectors = list(
        dict.fromkeys(str(value or "").strip() for value in values if str(value or "").strip())
    )
    descriptors: list[Path] = []
    names: list[str] = []
    for selector in selectors:
        descriptor = exact_project_descriptor(selector)
        if descriptor is not None:
            if descriptor not in descriptors:
                descriptors.append(descriptor)
            continue
        candidate = Path(selector).expanduser()
        path_like = (
            candidate.is_absolute()
            or candidate.exists()
            or candidate.suffix.casefold() == ".uproject"
            or "/" in selector
            or "\\" in selector
        )
        if path_like:
            multiple = candidate.is_dir() and len(list(candidate.glob("*.uproject"))) > 1
            return {
                "ok": False,
                "errorCode": "PROJECT_SELECTOR_AMBIGUOUS" if multiple else "PROJECT_SELECTOR_NOT_FOUND",
                "error": f"Project selector does not identify one existing .uproject: {selector}",
                "selector": selector,
            }
        names.append(selector)
    if not selectors and use_active:
        from workspace_paths import resolve_active_project_path

        active = resolve_active_project_path(workspace)
        if active is not None and active.is_file() and active.suffix.casefold() == ".uproject":
            descriptors.append(active.resolve())
    return {"ok": True, "selectors": selectors, "descriptors": descriptors, "names": names}


__all__ = [
    "classify_project_selectors",
    "descriptor_for_indexed_root",
    "exact_project_descriptor",
]
