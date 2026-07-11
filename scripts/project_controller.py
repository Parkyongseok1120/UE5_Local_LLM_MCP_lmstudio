#!/usr/bin/env python
"""Unified active-project switch: invalidate caches, report readiness, optional prepare."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from workspace_paths import find_workspace_root, load_shared_config, save_shared_config


def _validate_uproject(project_path: str) -> tuple[Path | None, str | None]:
    resolved = Path(project_path).resolve()
    if not resolved.is_file():
        return None, f"projectPath not found: {resolved}"
    if resolved.suffix.lower() != ".uproject":
        return None, "projectPath must be an existing .uproject file."
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"Invalid .uproject JSON: {exc}"
    if not isinstance(payload, dict):
        return None, "Invalid .uproject: root must be a JSON object."
    return resolved, None


def switch_active_project(
    workspace: Path,
    *,
    project_path: str | None = None,
    clear: bool = False,
    prepare: bool = False,
    force_prepare: bool = False,
) -> dict[str, Any]:
    config = load_shared_config()
    previous = str(config.get("activeProject") or "").strip()

    if clear:
        config["activeProject"] = None
        save_shared_config(config)
        invalidate_payload: dict[str, Any] | None = None
        try:
            from project_switch_invalidate import on_project_switch_invalidate

            invalidate_payload = on_project_switch_invalidate(previous or None, None, workspace=workspace)
        except Exception as exc:
            invalidate_payload = {"ok": False, "error": str(exc)}
        return {
            "ok": True,
            "switchResult": "cleared",
            "activeProject": None,
            "message": "Active project cleared.",
            "cacheInvalidation": invalidate_payload,
            "readiness": {"ready": False, "reason": "no_active_project"},
        }

    if not project_path:
        return {"ok": False, "switchResult": "failed", "error": "Provide projectPath or clear=true."}

    resolved, error = _validate_uproject(project_path)
    if error or resolved is None:
        return {"ok": False, "switchResult": "failed", "error": error}

    readiness: dict[str, Any]
    try:
        from on_active_project_changed import active_project_check_status

        readiness = active_project_check_status(resolved, workspace)
    except Exception as exc:
        return {
            "ok": False,
            "switchResult": "failed",
            "error": f"Project validation failed: {exc}",
            "activeProject": previous or None,
        }

    config["activeProject"] = str(resolved)
    try:
        save_shared_config(config)
    except Exception as exc:
        return {
            "ok": False,
            "switchResult": "failed",
            "error": f"Failed to save shared config: {exc}",
            "activeProject": previous or None,
        }

    invalidate_payload: dict[str, Any] | None = None
    try:
        from project_switch_invalidate import on_project_switch_invalidate

        invalidate_payload = on_project_switch_invalidate(previous or None, resolved, workspace=workspace)
    except Exception as exc:
        config["activeProject"] = previous or None
        try:
            save_shared_config(config)
        except Exception:
            pass
        return {
            "ok": False,
            "switchResult": "failed",
            "error": f"Cache invalidation failed; rolled back active project: {exc}",
            "activeProject": previous or None,
        }

    prepare_payload: dict[str, Any] | None = None
    if prepare or force_prepare:
        try:
            from on_active_project_changed import ensure_active_project_ready

            prepare_payload = ensure_active_project_ready(
                resolved,
                previous_project=previous or None,
                force=force_prepare,
            )
            readiness = prepare_payload.get("check") or readiness
        except Exception as exc:
            prepare_payload = {"ok": False, "error": str(exc)}

    switch_result = "switched" if readiness.get("ready") else "switched_degraded"
    return {
        "ok": True,
        "switchResult": switch_result,
        "activeProject": str(resolved),
        "message": f"Active project set to {resolved.name}",
        "cacheInvalidation": invalidate_payload,
        "readiness": readiness,
        "autoSetup": prepare_payload,
        "prepareRequested": prepare or force_prepare,
    }


def active_project_readiness(workspace: Path | None = None) -> dict[str, Any]:
    from on_active_project_changed import project_prepare_status

    ws = workspace or find_workspace_root()
    return project_prepare_status(ws)


def main() -> int:
    parser = argparse.ArgumentParser(description="Unified active project switch controller.")
    parser.add_argument("--switch", dest="project_path", default="", help="Path to .uproject")
    parser.add_argument("--clear", action="store_true")
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--force-prepare", action="store_true")
    parser.add_argument("--status", action="store_true", help="Report active project readiness only.")
    args = parser.parse_args()

    workspace = find_workspace_root()
    if args.status:
        payload = active_project_readiness(workspace)
    else:
        payload = switch_active_project(
            workspace,
            project_path=args.project_path or None,
            clear=args.clear,
            prepare=args.prepare,
            force_prepare=args.force_prepare,
        )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
