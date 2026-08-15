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


def test_validate_uproject_expands_portable_home_alias(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "Portable Home Project" / "Portable.uproject"
    project.parent.mkdir()
    project.write_text(json.dumps({"FileVersion": 3}), encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    import project_controller as pc

    resolved, error = pc._validate_uproject("~/Portable Home Project/Portable.uproject")

    assert error is None
    assert resolved == project.resolve()


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


def test_switch_same_exact_project_is_side_effect_free(tmp_path: Path, monkeypatch) -> None:
    project_dir = tmp_path / "Same Project"
    project_dir.mkdir()
    uproject = project_dir / "Same_Project.uproject"
    uproject.write_text(json.dumps({"FileVersion": 3}), encoding="utf-8")

    import project_controller as pc

    monkeypatch.setattr(
        pc,
        "load_shared_config",
        lambda: {"activeProject": str(uproject)},
    )

    def _unexpected(*_args, **_kwargs):
        raise AssertionError("same-project no-op must not persist or invalidate")

    monkeypatch.setattr(pc, "save_shared_config", _unexpected)
    monkeypatch.setattr(
        "project_switch_invalidate.on_project_switch_invalidate",
        _unexpected,
    )
    monkeypatch.setattr(
        "on_active_project_changed.active_project_check_status",
        _unexpected,
    )

    payload = switch_active_project(tmp_path, project_path=str(uproject))
    assert payload["ok"] is True
    assert payload["status"] == "completed"
    assert payload["switchResult"] == "already_active"
    assert payload["changed"] is False
    assert payload["activeProject"] == str(uproject.resolve())
