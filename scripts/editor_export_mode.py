"""Choose and execute request-watcher or headless Editor export mode."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from editor_export_settings import ExportMode


def choose_export_mode(requested: ExportMode, editor_open: bool) -> ExportMode:
    if requested == "auto":
        return "request" if editor_open else "headless"
    return requested


def execute_export_mode(
    *,
    chosen_mode: ExportMode,
    active_project: Path,
    workspace: Path,
    export_dir: Path,
    job: dict[str, Any],
    timeout_sec: int,
    resolve_engine: Callable[[Path, Path], dict[str, Any]],
    run_headless: Callable[..., dict[str, Any]],
    submit_request: Callable[[dict[str, Any]], None],
    wait_for_markers: Callable[..., dict[str, Any]],
) -> tuple[dict[str, Any], Path | None, dict[str, Any]]:
    engine_root: Path | None = None
    engine_resolution: dict[str, Any] = {
        "ok": True,
        "engineRoot": "",
        "source": "",
        "errorCode": "",
        "error": "",
    }

    def resolved_headless() -> dict[str, Any]:
        nonlocal engine_root, engine_resolution
        if engine_root is None:
            engine_resolution = resolve_engine(active_project, workspace)
            if not engine_resolution.get("ok"):
                return {
                    "ok": False,
                    "errorCode": str(
                        engine_resolution.get("errorCode")
                        or "ENGINE_ASSOCIATION_UNRESOLVED"
                    ),
                    "error": str(
                        engine_resolution.get("error")
                        or "Could not resolve the project's Unreal Engine."
                    ),
                }
            root_text = str(engine_resolution.get("engineRoot") or "").strip()
            if not root_text:
                return {
                    "ok": False,
                    "errorCode": "ENGINE_ROOT_UNRESOLVED",
                    "error": "Could not resolve the project's Unreal Engine.",
                }
            engine_root = Path(root_text)
        return run_headless(
            uproject=active_project,
            engine_root=engine_root,
            job=job,
            timeout_sec=timeout_sec,
        )

    if chosen_mode == "request":
        submit_request(job)
        result = wait_for_markers(
            export_dir,
            timeout_sec=min(120, timeout_sec),
            poll_sec=2.0,
            expected_run_id=str(job["jobId"]),
        )
        if not result.get("ok"):
            fallback = resolved_headless()
            fallback.setdefault("fallback", "headless")
            result = fallback
    else:
        result = resolved_headless()
    return result, engine_root, engine_resolution


__all__ = ["choose_export_mode", "execute_export_mode"]
