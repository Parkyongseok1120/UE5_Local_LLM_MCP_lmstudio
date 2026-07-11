from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from project_controller import switch_active_project  # noqa: E402
from project_switch_invalidate import read_cache_generation  # noqa: E402


def test_project_status_without_active_project() -> None:
    from project_controller import active_project_readiness

    payload = active_project_readiness(ROOT)
    assert "ok" in payload


def test_switch_rejects_missing_uproject(tmp_path: Path) -> None:
    payload = switch_active_project(tmp_path, project_path=str(tmp_path / "Missing.uproject"))
    assert payload["ok"] is False
    assert payload["switchResult"] == "failed"


def test_switch_rejects_invalid_uproject_json(tmp_path: Path) -> None:
    bad = tmp_path / "Bad.uproject"
    bad.write_text("not-json", encoding="utf-8")
    payload = switch_active_project(tmp_path, project_path=str(bad))
    assert payload["ok"] is False
    assert payload["switchResult"] == "failed"


def test_switch_valid_project_writes_cache_generation(tmp_path: Path, monkeypatch) -> None:
    project_dir = tmp_path / "Demo"
    project_dir.mkdir()
    uproject = project_dir / "Demo.uproject"
    uproject.write_text(json.dumps({"FileVersion": 3}), encoding="utf-8")
    shared = tmp_path / "shared.json"
    shared.write_text(json.dumps({"activeProject": None}), encoding="utf-8")

    import workspace_paths

    monkeypatch.setattr(workspace_paths, "load_shared_config", lambda: json.loads(shared.read_text(encoding="utf-8")))
    monkeypatch.setattr(
        workspace_paths,
        "save_shared_config",
        lambda cfg: shared.write_text(json.dumps(cfg), encoding="utf-8"),
    )

    before = read_cache_generation(tmp_path)
    payload = switch_active_project(tmp_path, project_path=str(uproject))
    after = read_cache_generation(tmp_path)
    assert payload["ok"] is True
    assert payload["switchResult"] in {"switched", "switched_degraded"}
    assert after > before
