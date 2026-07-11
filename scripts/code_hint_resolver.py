#!/usr/bin/env python
"""Resolve C++ domain hints from activeProject Source/**/Domain folders."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from project_context import resolve_active_project_context

DOMAIN_ALIASES: dict[str, str] = {
    "cinematic": "Cinematic",
    "cinema": "Cinematic",
    "시네마틱": "Cinematic",
    "combat": "Combat",
    "전투": "Combat",
    "ai": "AI",
    "inventory": "Inventory",
    "quest": "Quest",
    "ui": "UI",
    "audio": "Audio",
    "network": "Network",
    "networking": "Network",
    "gameplay": "Gameplay",
    "character": "Character",
    "animation": "Animation",
    "subsystem": "Subsystem",
}

CPP_MARKERS = (
    "c++",
    "cpp",
    "source/",
    "subsystem",
    "class",
    "header",
    "combat",
    "cinematic",
    "시네마틱",
    "전투",
)


def _normalize_domain_key(text: str) -> str:
    return re.sub(r"[^a-z0-9가-힣]+", " ", str(text or "").lower()).strip()


def resolve_domain_folder_name(hint: str) -> str | None:
    text = _normalize_domain_key(hint)
    if not text:
        return None
    for alias, folder in DOMAIN_ALIASES.items():
        if alias in text:
            return folder
    for token in text.split():
        if token[:1].isupper() and token.isalpha():
            return token[:1].upper() + token[1:]
        if token.isalpha() and len(token) > 2:
            return token[:1].upper() + token[1:]
    return None


def find_domain_dirs(project_dir: Path, domain_folder: str) -> list[dict[str, Any]]:
    source_root = project_dir / "Source"
    if not source_root.is_dir():
        return []
    matches: list[dict[str, Any]] = []
    target_name = str(domain_folder or "").strip()
    if not target_name:
        return matches
    for root, dirnames, _filenames in os.walk(source_root):
        for dirname in dirnames:
            if dirname.lower() != target_name.lower():
                continue
            abs_path = Path(root) / dirname
            rel_from_project = abs_path.relative_to(project_dir)
            rel_from_source = abs_path.relative_to(source_root)
            matches.append(
                {
                    "absolutePath": str(abs_path.resolve()),
                    "projectRelPath": str(rel_from_project).replace("\\", "/"),
                    "sourceRelPath": str(rel_from_source).replace("\\", "/"),
                    "moduleName": rel_from_source.parts[0] if rel_from_source.parts else "",
                    "domainFolder": dirname,
                }
            )
    return matches


def _workspace_rel(workspace_root: Path, abs_path: Path) -> str:
    try:
        rel = os.path.relpath(str(abs_path.resolve()), str(workspace_root.resolve()))
    except ValueError:
        return ""
    if rel.startswith(".."):
        return ""
    return rel.replace("\\", "/")


def resolve_code_domain_hint(
    hint: str,
    project_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ctx = project_context or resolve_active_project_context()
    if not ctx.get("ok"):
        return {
            "ok": False,
            "error": ctx.get("error") or "activeProject is not set",
            "suggestedToolCalls": ctx.get("suggestedToolCalls") or [],
        }

    domain_folder = resolve_domain_folder_name(hint)
    if not domain_folder:
        return {
            "ok": False,
            "error": f"Could not infer C++ domain folder from hint: {hint!r}",
            "projectName": ctx["projectName"],
        }

    project_dir = Path(str(ctx["projectDir"]))
    workspace_root = Path(str(ctx["workspaceRoot"]))
    domain_dirs = find_domain_dirs(project_dir, domain_folder)
    domain_rel_paths = [
        _workspace_rel(workspace_root, Path(item["absolutePath"]))
        for item in domain_dirs
        if _workspace_rel(workspace_root, Path(item["absolutePath"]))
    ]

    search_query = domain_folder
    if domain_folder == "Cinematic":
        search_query = "CinematicDirector"
    elif domain_folder == "Combat":
        search_query = "Combat"

    payload: dict[str, Any] = {
        "ok": bool(domain_dirs),
        "domain": domain_folder.lower(),
        "domainFolder": domain_folder,
        "projectName": ctx["projectName"],
        "sourceBrowsePath": ctx.get("sourceBrowsePath") or "",
        "browseAvailable": bool(ctx.get("browseAvailable")),
        "domainSourcePaths": domain_dirs,
        "domainRelPaths": [path for path in domain_rel_paths if path],
        "searchFilesQuery": search_query,
        "ragQuery": f"{ctx['projectName']} {domain_folder.lower()} {search_query.lower()} subsystem",
    }

    suggested: list[dict[str, Any]] = [
        {"tool": "unreal_get_active_project", "args": {}},
    ]
    if ctx.get("browseAvailable") and ctx.get("sourceBrowsePath"):
        for rel_path in payload["domainRelPaths"][:3] or [ctx["sourceBrowsePath"]]:
            suggested.append(
                {
                    "tool": "search_files",
                    "args": {
                        "query": search_query,
                        "path": rel_path,
                    },
                }
            )
            break
        if not payload["domainRelPaths"]:
            suggested.append(
                {
                    "tool": "search_files",
                    "args": {
                        "query": search_query,
                        "path": ctx["sourceBrowsePath"],
                    },
                }
            )
    suggested.append({"tool": "read_file", "args": {"path": "<from search_files matches>"}})
    suggested.append(
        {
            "tool": "unreal_rag_search",
            "args": {
                "query": payload["ragQuery"],
                "mode": "review",
                "hybrid": False,
                "top_k": 6,
            },
        }
    )
    payload["suggestedToolCalls"] = suggested
    if not domain_dirs:
        payload["error"] = f"No Source/**/{domain_folder} directory found under {project_dir}"
    return payload


def looks_like_cpp_domain_request(text: str) -> bool:
    raw = str(text or "").lower()
    return any(marker in raw for marker in CPP_MARKERS)
