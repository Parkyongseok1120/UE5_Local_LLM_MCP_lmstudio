#!/usr/bin/env python
"""Resolve natural-language or filesystem hints to /Game asset search tokens."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from project_context import resolve_active_project_context

CONTENT_SEGMENT_RE = re.compile(r"[\\/](?:Content)[\\/](.+)$", re.IGNORECASE)
GAME_PATH_RE = re.compile(r"^/Game/[A-Za-z0-9_/]+$", re.IGNORECASE)


def content_abs_path_to_game_path(abs_path: str, project_dir: Path) -> str:
    text = str(abs_path or "").strip().replace("/", "\\")
    if not text:
        return ""
    match = CONTENT_SEGMENT_RE.search(text)
    if match:
        rel = match.group(1).replace("\\", "/").strip("/")
        return f"/Game/{rel}" if rel else "/Game"
    try:
        resolved = Path(text).resolve()
        content_root = (project_dir / "Content").resolve()
        rel = resolved.relative_to(content_root)
        rel_text = str(rel).replace("\\", "/").strip("/")
        return f"/Game/{rel_text}" if rel_text else "/Game"
    except (OSError, ValueError):
        return ""


def normalize_folder_token(hint: str) -> str:
    text = str(hint or "").strip().replace("\\", "/").strip("/")
    suffixes = (" folder", " folders", " 폴더", " materials", " material", " 머티리얼", " analysis")
    changed = True
    while changed:
        changed = False
        lowered = text.lower()
        for suffix in suffixes:
            if lowered.endswith(suffix):
                text = text[: -len(suffix)].strip()
                changed = True
                break
    if not text:
        return ""
    if text.lower().startswith("/game/"):
        return text
    if GAME_PATH_RE.match(text):
        return text
    if "/Game/" in text or text.startswith("Game/"):
        if text.startswith("Game/"):
            return "/" + text
        idx = text.lower().find("/game/")
        return text[idx:] if idx >= 0 else text
    segment = text.rsplit("/", 1)[-1]
    return segment


def resolve_asset_folder_hint(
    hint: str,
    project_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ctx = project_context or resolve_active_project_context()
    raw = str(hint or "").strip()
    if not raw:
        return {"ok": False, "error": "folder hint is empty", "searchToken": ""}

    if not ctx.get("ok"):
        return {
            "ok": False,
            "error": ctx.get("error") or "activeProject is not set",
            "suggestedToolCalls": ctx.get("suggestedToolCalls") or [],
            "searchToken": normalize_folder_token(raw),
        }

    project_dir = Path(str(ctx["projectDir"]))
    game_path = ""
    if "\\" in raw or ":/" in raw.replace("\\", "/").lower() or raw.lower().startswith("c:"):
        game_path = content_abs_path_to_game_path(raw, project_dir)

    search_token = game_path or normalize_folder_token(raw)
    folder_segment = search_token.rsplit("/", 1)[-1] if search_token.startswith("/Game/") else search_token

    return {
        "ok": True,
        "hint": raw,
        "projectName": ctx["projectName"],
        "searchToken": search_token,
        "folderSegment": folder_segment,
        "gamePathPrefix": search_token if search_token.startswith("/Game/") else "",
        "contentBrowsePath": ctx.get("contentBrowsePath") or "",
    }
