from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from project_controller import active_project_readiness, switch_active_project  # noqa: E402
from project_switch_invalidate import read_cache_generation  # noqa: E402


def test_project_status_without_active_project() -> None:
    payload = active_project_readiness(ROOT)
    assert "ok" in payload


def test_switch_rejects_missing_uproject(tmp_path: Path) -> None:
    payload = switch_active_project(tmp_path, project_path=str(tmp_path / "Missing.uproject"))
    assert payload["ok"] is False
    assert payload["switchResult"] == "failed"


def test_switch_valid_project_keeps_config_on_cache_error(tmp_path: Path, monkeypatch) -> None:
    project_dir = tmp_path / "Demo"
    project_dir.mkdir()
    uproject = project_dir / "Demo.uproject"
    uproject.write_text(json.dumps({"FileVersion": 3}), encoding="utf-8")
    shared = tmp_path / "shared.json"
    shared.write_text(json.dumps({"activeProject": None}), encoding="utf-8")

    import project_controller as pc
    import workspace_paths

    state = {"activeProject": None}

    def _load():
        return dict(state)

    def _save(cfg):
        state.clear()
        state.update(cfg)
        shared.write_text(json.dumps(cfg), encoding="utf-8")

    monkeypatch.setattr(pc, "load_shared_config", _load)
    monkeypatch.setattr(pc, "save_shared_config", _save)
    monkeypatch.setattr(workspace_paths, "load_shared_config", _load)
    monkeypatch.setattr(workspace_paths, "save_shared_config", _save)

    def _boom(*args, **kwargs):
        raise RuntimeError("cache failed")

    monkeypatch.setattr("project_switch_invalidate.on_project_switch_invalidate", _boom)

    payload = switch_active_project(tmp_path, project_path=str(uproject))
    assert payload["ok"] is True
    assert payload["switchResult"] == "switched_degraded"
    assert payload["cacheRefreshRequired"] is True
    saved = json.loads(shared.read_text(encoding="utf-8"))
    assert Path(str(saved["activeProject"])).name == "Demo.uproject"


def test_switch_valid_project_writes_cache_generation(tmp_path: Path, monkeypatch) -> None:
    project_dir = tmp_path / "Demo"
    project_dir.mkdir()
    uproject = project_dir / "Demo.uproject"
    uproject.write_text(json.dumps({"FileVersion": 3}), encoding="utf-8")
    shared = tmp_path / "shared.json"
    shared.write_text(json.dumps({"activeProject": None}), encoding="utf-8")

    import project_controller as pc
    import workspace_paths

    state = {"activeProject": None}

    def _load():
        return dict(state)

    def _save(cfg):
        state.clear()
        state.update(cfg)
        shared.write_text(json.dumps(cfg), encoding="utf-8")

    monkeypatch.setattr(pc, "load_shared_config", _load)
    monkeypatch.setattr(pc, "save_shared_config", _save)
    monkeypatch.setattr(workspace_paths, "load_shared_config", _load)
    monkeypatch.setattr(workspace_paths, "save_shared_config", _save)

    before = read_cache_generation(tmp_path)
    payload = switch_active_project(tmp_path, project_path=str(uproject))
    after = read_cache_generation(tmp_path)
    assert payload["ok"] is True
    assert after > before
