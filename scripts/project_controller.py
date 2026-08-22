#!/usr/bin/env python
"""Direct active-project selection and factual binding status.

This module owns one piece of mutable state: ``activeProject`` in the shared
workspace configuration.  It deliberately does not plan work, prepare a
project, launch Unreal Editor, rebuild an index, or choose a follow-up tool.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from direct_rag_project_cache import invalidate_direct_project_switch
from workspace_paths import (
    canonical_absolute_path_identity,
    find_workspace_root,
    load_shared_config,
    save_shared_config,
)


def _validate_uproject(project_path: str) -> tuple[Path | None, str | None]:
    try:
        resolved = Path(project_path).expanduser().resolve()
    except OSError as exc:
        return None, f"Could not resolve projectPath: {exc}"
    if not resolved.is_file():
        return None, f"projectPath not found: {resolved}"
    if resolved.suffix.casefold() != ".uproject":
        return None, "projectPath must be an existing .uproject file."
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"Invalid .uproject JSON: {exc}"
    if not isinstance(payload, dict):
        return None, "Invalid .uproject: root must be a JSON object."
    return resolved, None


def _binding_status(value: str | None) -> dict[str, Any]:
    if not value:
        return {
            "ok": True,
            "ready": False,
            "reason": "no_active_project",
            "activeProject": None,
            "bindingStatus": "unbound",
        }
    resolved, error = _validate_uproject(value)
    if error or resolved is None:
        return {
            "ok": True,
            "ready": False,
            "reason": "active_project_invalid",
            "activeProject": value,
            "bindingStatus": "stale",
            "observation": error,
        }
    return {
        "ok": True,
        "ready": True,
        "reason": "active_project_valid",
        "activeProject": str(resolved),
        "projectName": resolved.stem,
        "projectRoot": str(resolved.parent),
        "bindingStatus": "bound",
    }


def _invalidate(previous: str | None, current: str | None) -> tuple[dict[str, Any], bool]:
    try:
        payload = invalidate_direct_project_switch(previous, current)
    except Exception as exc:  # Config is already durable; report cache degradation.
        return {"ok": False, "error": str(exc)}, True
    return payload, payload.get("ok") is not True


def switch_active_project(
    workspace: Path,
    *,
    project_path: str | None = None,
    clear: bool = False,
) -> dict[str, Any]:
    """Atomically select or clear one exact project, then invalidate Direct caches."""

    del workspace  # Retained as an explicit caller boundary; no repository state is mutated.
    config = load_shared_config()
    previous = str(config.get("activeProject") or "").strip()

    if clear:
        if not previous:
            return {
                "ok": True,
                "status": "completed",
                "switchResult": "already_clear",
                "changed": False,
                "activeProject": None,
                "cacheInvalidation": None,
                "cacheRefreshRequired": False,
                "readiness": _binding_status(None),
            }
        config["activeProject"] = None
        try:
            save_shared_config(config)
        except Exception as exc:
            return {
                "ok": False,
                "switchResult": "failed",
                "error": f"Failed to save shared config: {exc}",
                "activeProject": previous,
            }
        invalidation, degraded = _invalidate(previous, None)
        return {
            "ok": True,
            "status": "completed",
            "switchResult": "cleared_degraded" if degraded else "cleared",
            "changed": True,
            "activeProject": None,
            "cacheInvalidation": invalidation,
            "cacheRefreshRequired": degraded,
            "readiness": _binding_status(None),
        }

    if not project_path:
        return {
            "ok": False,
            "switchResult": "failed",
            "error": "Provide projectPath or clear=true.",
        }
    resolved, error = _validate_uproject(project_path)
    if error or resolved is None:
        return {"ok": False, "switchResult": "failed", "error": error}

    if previous and canonical_absolute_path_identity(previous) == canonical_absolute_path_identity(resolved):
        return {
            "ok": True,
            "status": "completed",
            "switchResult": "already_active",
            "changed": False,
            "activeProject": str(resolved),
            "cacheInvalidation": None,
            "cacheRefreshRequired": False,
            "readiness": _binding_status(str(resolved)),
        }

    config["activeProject"] = str(resolved)
    configured_export = str(config.get("editorExportDir") or "").replace("\\", "/").rstrip("/").casefold()
    if not configured_export or configured_export.endswith("/saved/lmstudiometadataexports"):
        config["editorExportDir"] = str(resolved.parent / "Saved" / "LmStudioMetadataExports")
    try:
        save_shared_config(config)
    except Exception as exc:
        return {
            "ok": False,
            "switchResult": "failed",
            "error": f"Failed to save shared config: {exc}",
            "activeProject": previous or None,
        }

    invalidation, degraded = _invalidate(previous or None, str(resolved))
    return {
        "ok": True,
        "status": "completed",
        "switchResult": "switched_degraded" if degraded else "switched",
        "changed": True,
        "activeProject": str(resolved),
        "cacheInvalidation": invalidation,
        "cacheRefreshRequired": degraded,
        "readiness": _binding_status(str(resolved)),
    }


def active_project_readiness(workspace: Path | None = None) -> dict[str, Any]:
    del workspace
    configured = str(load_shared_config().get("activeProject") or "").strip()
    return _binding_status(configured or None)


def main() -> int:
    parser = argparse.ArgumentParser(description="Select or inspect the shared Direct active project.")
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--switch", dest="project_path", help="Absolute path to one .uproject")
    actions.add_argument("--clear", action="store_true")
    actions.add_argument("--status", action="store_true")
    args = parser.parse_args()

    workspace = find_workspace_root()
    payload = (
        active_project_readiness(workspace)
        if args.status
        else switch_active_project(
            workspace,
            project_path=args.project_path,
            clear=args.clear,
        )
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
