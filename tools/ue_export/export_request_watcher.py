# Polls export_dir/lmstudio_export_request.json while Unreal Editor is open.
# Register once per Editor session (also called from register_export_menu.py).

import json
import os
import time
import traceback
from pathlib import Path


REQUEST_NAME = "lmstudio_export_request.json"
DONE_NAME = "lmstudio_export_done.json"
ERROR_NAME = "lmstudio_export_error.json"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _process_request(export_dir: Path, tools_dir: Path) -> None:
    request_path = export_dir / REQUEST_NAME
    if not request_path.is_file():
        return

    try:
        job = json.loads(request_path.read_text(encoding="utf-8"))
    except Exception as exc:
        _write_json(
            export_dir / ERROR_NAME,
            {"ok": False, "mode": "request_watcher", "error": f"Invalid request JSON: {exc}"},
        )
        request_path.unlink(missing_ok=True)
        return

    content_path = str(job.get("contentPath") or "/Game")
    maps_path = str(job.get("mapsPath") or content_path)
    scope = str(job.get("scope") or "all").lower()

    run_all_path = tools_dir / "run_all_exports.py"
    namespace: dict = {}
    with open(run_all_path, encoding="utf-8") as handle:
        exec(handle.read(), namespace)

    if scope in {"material", "materials"}:
        manifest = namespace["export_materials_only"](str(export_dir), content_path=content_path, tools_dir=str(tools_path))
    elif scope in {"blueprint", "blueprints", "bp"}:
        manifest = namespace["export_blueprints_only"](str(export_dir), content_path=content_path, tools_dir=str(tools_path))
    else:
        manifest = namespace["run_all_metadata_exports"](
            str(export_dir),
            content_path=content_path,
            maps_path=maps_path,
            tools_dir=str(tools_path),
        )

    request_path.unlink(missing_ok=True)
    _write_json(
        export_dir / DONE_NAME,
        {
            "ok": True,
            "mode": "request_watcher",
            "exportDir": str(export_dir),
            "contentPath": content_path,
            "scope": scope,
            "manifest": manifest,
        },
    )


def start_export_request_watcher(export_dir: str, tools_dir: str = "", poll_seconds: float = 2.0) -> None:
    import unreal

    export_path = Path(export_dir)
    tools_path = Path(tools_dir) if tools_dir else Path(__file__).resolve().parent
    handle = {"active": True}

    def _tick(_delta_seconds: float) -> None:
        if not handle["active"]:
            return
        try:
            _process_request(export_path, tools_path)
        except Exception as exc:
            _write_json(
                export_path / ERROR_NAME,
                {
                    "ok": False,
                    "mode": "request_watcher",
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                },
            )
            (export_path / REQUEST_NAME).unlink(missing_ok=True)

    unreal.register_slate_post_tick_callback(_tick)
    unreal.log(
        f"LM Studio export request watcher started. export_dir={export_path} poll={poll_seconds}s"
    )
