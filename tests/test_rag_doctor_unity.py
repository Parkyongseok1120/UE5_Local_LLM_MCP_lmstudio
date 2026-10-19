"""rag_doctor reports a configured Unity server through the installer's own check."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import rag_doctor  # noqa: E402


def _config(tmp_path: Path, servers: dict) -> Path:
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
    return path


def _runner(report: dict | None, code: int = 0, stderr: str = ""):
    def run(command):
        assert command[1].endswith("check_unity_install.js")
        return subprocess.CompletedProcess(command, code, json.dumps(report) if report is not None else "", stderr)
    return run


def test_unity_not_configured_reports_nothing(tmp_path):
    assert rag_doctor._unity_checks(_config(tmp_path, {"unreal-agent": {}}), Path("node"), _runner({})) == []


def test_closed_editor_is_a_warning_with_its_next_step(tmp_path):
    report = {"tools": 30, "connection": "disconnected", "reasonCode": "editor_not_running",
              "nextStep": "Open this project in the Unity Editor", "worker": "configured",
              "serverPermissions": {"write": False, "commands": False}}
    checks = {c["label"]: c for c in rag_doctor._unity_checks(_config(tmp_path, {"unity-tools": {}}), Path("node"), _runner(report))}
    assert checks["unity_mcp"]["status"] == "PASS"
    assert checks["unity_editor_connection"]["status"] == "WARN"
    assert "editor_not_running" in checks["unity_editor_connection"]["detail"]
    assert "Open this project" in checks["unity_editor_connection"]["detail"]
    assert checks["unity_symbol_worker"]["status"] == "PASS"
    assert "write=off, commands=off" in checks["unity_server_permissions"]["detail"]


def test_missing_worker_fails_and_a_broken_check_warns(tmp_path):
    report = {"tools": 30, "connection": "connected", "editorVersion": "6000.3.14f1", "bridge": {"version": "1.4.0"},
              "worker": "missing", "workerRemedy": "Reinstall the Unity component", "serverPermissions": {"write": True}}
    checks = {c["label"]: c for c in rag_doctor._unity_checks(_config(tmp_path, {"unity-tools": {}}), Path("node"), _runner(report))}
    assert checks["unity_editor_connection"]["status"] == "PASS"
    assert checks["unity_symbol_worker"]["status"] == "FAIL"
    broken = rag_doctor._unity_checks(_config(tmp_path, {"unity-tools": {}}), Path("node"), _runner(None, 1, "Missing required tool"))
    assert [c["status"] for c in broken] == ["WARN"] and "Missing required tool" in broken[0]["detail"]
