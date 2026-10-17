"""WORKSPACE_ROOT guard: another harness checkout as the first root is refused; a project outside is a notice."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))
import install  # noqa: E402
import rag_doctor  # noqa: E402
from workspace_root_guard import workspace_root_findings  # noqa: E402


def _harness_copy(path: Path, *, worktree: bool) -> Path:
    (path / "lmstudio-unreal-agent-mcp").mkdir(parents=True)
    (path / "install.py").write_text("# copy\n", encoding="utf-8")
    if worktree:
        (path / ".git").write_text("gitdir: /elsewhere/.git/worktrees/baseline\n", encoding="utf-8")
    return path


def test_foreign_harness_worktree_is_blocking_and_names_the_running_checkout(tmp_path):
    other = _harness_copy(tmp_path / "harness-baseline", worktree=True)
    [finding] = workspace_root_findings(other, None, ROOT)
    assert finding["code"] == "foreign_harness_checkout" and finding["severity"] == "block"
    assert "linked git worktree" in finding["message"] and str(ROOT) in finding["message"]


def test_this_checkout_and_project_folders_are_not_flagged(tmp_path):
    assert workspace_root_findings(ROOT, None, ROOT) == []
    game = tmp_path / "Game"
    game.mkdir()
    project = game / "Game.uproject"
    project.write_text("{}", encoding="utf-8")
    assert workspace_root_findings(game, project, ROOT) == []


def test_project_outside_the_workspace_is_only_a_notice(tmp_path):
    project = tmp_path / "Elsewhere" / "Game.uproject"
    project.parent.mkdir()
    project.write_text("{}", encoding="utf-8")
    [finding] = workspace_root_findings(tmp_path / "Docs", project, ROOT)
    assert finding["code"] == "active_project_outside_workspace" and finding["severity"] == "notice"


def _args(workspace: Path, **extra) -> argparse.Namespace:
    return argparse.Namespace(workspace_root=[workspace], active_project=None, yes=True, **extra)


def test_installer_refuses_a_foreign_checkout_non_interactively_unless_allowed(tmp_path):
    other = _harness_copy(tmp_path / "copy", worktree=False)
    with pytest.raises(ValueError, match="--allow-foreign-workspace-root"):
        install._check_workspace_root(_args(other))
    allowed = _args(other, allow_foreign_workspace_root=True)
    install._check_workspace_root(allowed)
    assert allowed._workspace_root_findings[0]["code"] == "foreign_harness_checkout"


def test_rag_doctor_reports_the_installed_workspace_root(tmp_path):
    other = _harness_copy(tmp_path / "baseline", worktree=True)
    mcp = tmp_path / "mcp.json"
    mcp.write_text(json.dumps({"mcpServers": {"unreal-agent": {"env": {"WORKSPACE_ROOT": str(other)}}}}), encoding="utf-8")
    checks = rag_doctor._workspace_root_checks(mcp, tmp_path / "missing-shared.json")
    assert [c["label"] for c in checks] == ["workspace_root_foreign_harness_checkout"]
    mcp.write_text(json.dumps({"mcpServers": {"unreal-agent": {"env": {"WORKSPACE_ROOT": str(ROOT)}}}}), encoding="utf-8")
    assert rag_doctor._workspace_root_checks(mcp, tmp_path / "missing-shared.json")[0]["label"] == "workspace_root"
