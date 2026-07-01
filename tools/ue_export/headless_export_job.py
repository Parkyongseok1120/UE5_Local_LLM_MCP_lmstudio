# Executed inside Unreal Editor via -ExecutePythonScript.
# Reads job JSON from LMSTUDIO_EXPORT_JOB env var, runs metadata export, writes done marker, quits.

import json
import os
import traceback
from pathlib import Path


def _load_job() -> dict:
    job_path = os.environ.get("LMSTUDIO_EXPORT_JOB", "").strip()
    if not job_path:
        raise RuntimeError("LMSTUDIO_EXPORT_JOB is not set")
    return json.loads(Path(job_path).read_text(encoding="utf-8"))


def _write_json(path: str, payload: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_headless_export_job() -> None:
    import unreal

    job = _load_job()
    export_dir = str(job["exportDir"])
    content_path = str(job.get("contentPath") or "/Game")
    maps_path = str(job.get("mapsPath") or content_path)
    scope = str(job.get("scope") or "all").lower()
    tools_dir = str(job["toolsDir"])
    done_path = str(job["donePath"])
    error_path = str(job.get("errorPath") or "")

    run_all_path = os.path.join(tools_dir, "run_all_exports.py")
    namespace: dict = {}
    with open(run_all_path, encoding="utf-8") as handle:
        source = handle.read()
    exec(f"_TOOLS_DIR = {tools_dir!r}\n" + source, namespace)

    if scope in {"material", "materials"}:
        manifest = namespace["export_materials_only"](export_dir, content_path=content_path, tools_dir=tools_dir)
    elif scope in {"blueprint", "blueprints", "bp"}:
        manifest = namespace["export_blueprints_only"](export_dir, content_path=content_path, tools_dir=tools_dir)
    else:
        manifest = namespace["run_all_metadata_exports"](
            export_dir,
            content_path=content_path,
            maps_path=maps_path,
            tools_dir=tools_dir,
        )

    payload = {
        "ok": True,
        "mode": "headless",
        "exportDir": export_dir,
        "contentPath": content_path,
        "scope": scope,
        "manifest": manifest,
    }
    _write_json(done_path, payload)
    unreal.log(f"LM Studio headless metadata export complete: {export_dir}")
    unreal.SystemLibrary.quit_editor()


if __name__ == "__main__":
    job = _load_job()
    error_path = str(job.get("errorPath") or "")
    try:
        run_headless_export_job()
    except Exception as exc:
        if error_path:
            _write_json(
                error_path,
                {
                    "ok": False,
                    "mode": "headless",
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                },
            )
        raise
