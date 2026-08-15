"""Integration coverage for the Python-to-Node exact project resolver bridge."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from project_name_resolver import (  # noqa: E402
    clear_project_name_resolution_cache,
    resolve_project_name,
)
import project_name_resolver as resolver_module  # noqa: E402


def _project(parent: Path, directory: str, name: str | None = None) -> Path:
    project_name = name or directory
    root = parent / directory
    root.mkdir(parents=True)
    project_file = root / f"{project_name}.uproject"
    project_file.write_text(
        json.dumps({"FileVersion": 3, "EngineAssociation": "5.8"}),
        encoding="utf-8",
    )
    return project_file


def test_python_bridge_uses_node_ssot_with_unicode_paths_and_cache(
    monkeypatch, tmp_path: Path
):
    search_root = tmp_path / "프로젝트 roots with spaces"
    expected = _project(search_root, "Owner One", "Portable_Game")
    shared = tmp_path / "shared config.json"
    shared.write_text(
        json.dumps({"projectSearchRoots": [str(search_root)]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("SHARED_UNREAL_CONFIG", str(shared))
    monkeypatch.delenv("AGENT_MCP_CONFIG", raising=False)
    clear_project_name_resolution_cache()

    first = resolve_project_name(ROOT, "  ＰＯＲＴＡＢＬＥ-game.UPROJECT  ")
    second = resolve_project_name(ROOT, "  ＰＯＲＴＡＢＬＥ-game.UPROJECT  ")

    assert first["ok"] is True
    assert Path(first["selected"]["projectPath"]) == expected.resolve()
    assert first["cacheHit"] is False
    assert second["selected"] == first["selected"]
    assert second["cacheHit"] is True


def test_python_bridge_keeps_partial_matches_fail_closed(monkeypatch, tmp_path: Path):
    search_root = tmp_path / "projects"
    _project(search_root, "AlphaGame")
    _project(search_root, "AlphaTools")
    shared = tmp_path / "shared.json"
    shared.write_text(
        json.dumps({"projectSearchRoots": [str(search_root)]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("SHARED_UNREAL_CONFIG", str(shared))
    clear_project_name_resolution_cache()

    result = resolve_project_name(ROOT, "Alpha")

    assert result["ok"] is False
    assert result["status"] == "await_user"
    assert result["errorCode"] == "PROJECT_NAME_NOT_FOUND"
    assert {row["projectName"] for row in result["suggestions"]} == {
        "AlphaGame",
        "AlphaTools",
    }


def test_python_bridge_fails_closed_when_node_binary_is_unavailable(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setenv("NODE_BINARY", str(tmp_path / "missing-node"))
    clear_project_name_resolution_cache()

    result = resolve_project_name(ROOT, "AnyProject")

    assert result["ok"] is False
    assert result["status"] == "await_user"
    assert result["errorCode"] == "PROJECT_NAME_RESOLUTION_FAILED"


def test_bridge_cache_is_scoped_to_shared_config_environment_node_and_cwd(
    monkeypatch, tmp_path: Path
):
    shared_a = tmp_path / "shared-a.json"
    shared_b = tmp_path / "shared-b.json"
    for path in (shared_a, shared_b):
        path.write_text('{"projectSearchRoots": []}', encoding="utf-8")
    # Reproduce the former collision: distinct config files can legitimately
    # have the same timestamp and target string.
    shared_timestamp = 1_700_000_000_000_000_000
    os.utime(shared_a, ns=(shared_timestamp, shared_timestamp))
    os.utime(shared_b, ns=(shared_timestamp, shared_timestamp))

    calls: list[dict[str, str]] = []

    def fake_run(argv, **kwargs):
        shared_name = Path(os.environ["SHARED_UNREAL_CONFIG"]).stem
        calls.append(
            {
                "node": str(argv[0]),
                "shared": shared_name,
                "roots": os.environ.get("PROJECT_SEARCH_ROOTS", ""),
                "depth": os.environ.get("PROJECT_SEARCH_MAX_DEPTH", ""),
                "cwd": os.getcwd(),
            }
        )
        payload = {
            "ok": True,
            "selected": {
                "projectName": shared_name,
                "projectPath": str(tmp_path / f"{shared_name}.uproject"),
            },
            "suggestions": [],
        }
        return SimpleNamespace(
            stdout=json.dumps(payload), stderr="", returncode=0
        )

    monkeypatch.setattr(resolver_module.subprocess, "run", fake_run)
    monkeypatch.setenv("NODE_BINARY", "node-one")
    monkeypatch.delenv("PROJECT_SEARCH_ROOTS", raising=False)
    monkeypatch.delenv("PROJECT_SEARCH_MAX_DEPTH", raising=False)
    clear_project_name_resolution_cache()

    monkeypatch.setenv("SHARED_UNREAL_CONFIG", str(shared_a))
    first = resolve_project_name(ROOT, "SharedName")
    monkeypatch.setenv("SHARED_UNREAL_CONFIG", str(shared_b))
    second = resolve_project_name(ROOT, "SharedName")
    cached = resolve_project_name(ROOT, "SharedName")

    assert first["selected"]["projectName"] == "shared-a"
    assert second["selected"]["projectName"] == "shared-b"
    assert second["cacheHit"] is False
    assert cached["cacheHit"] is True
    assert len(calls) == 2

    monkeypatch.setenv("PROJECT_SEARCH_ROOTS", "relative-projects")
    roots_changed = resolve_project_name(ROOT, "SharedName")
    monkeypatch.setenv("PROJECT_SEARCH_MAX_DEPTH", "7")
    depth_changed = resolve_project_name(ROOT, "SharedName")
    monkeypatch.setenv("NODE_BINARY", "node-two")
    node_changed = resolve_project_name(ROOT, "SharedName")
    unrelated_cwd = tmp_path / "unrelated-cwd"
    unrelated_cwd.mkdir()
    monkeypatch.chdir(unrelated_cwd)
    cwd_changed = resolve_project_name(ROOT, "SharedName")

    assert all(
        result["cacheHit"] is False
        for result in (roots_changed, depth_changed, node_changed, cwd_changed)
    )
    assert len(calls) == 6
