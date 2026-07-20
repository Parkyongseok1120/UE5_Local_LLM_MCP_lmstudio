from __future__ import annotations

import json
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install.py"


def test_installer_profiles_are_manifest_driven() -> None:
    spec = importlib.util.spec_from_file_location("integrated_install", INSTALLER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    manifest = json.loads((ROOT / "installer" / "manifest.json").read_text(encoding="utf-8"))
    assert module.PROFILE_DEFAULTS == {
        name: set(components)
        for name, components in manifest["profiles"].items()
        if name != "custom"
    }


def _run(tmp_path: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(INSTALLER),
            "--yes",
            "--codex-home",
            str(tmp_path / "codex"),
            "--lmstudio-home",
            str(tmp_path / "lmstudio"),
            "--state-home",
            str(tmp_path / "state"),
            *extra,
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_safe_profile_installs_codex_lmstudio_and_preserves_other_mcp(tmp_path: Path) -> None:
    lmstudio = tmp_path / "lmstudio"
    lmstudio.mkdir()
    (lmstudio / "mcp.json").write_text(
        json.dumps({"mcpServers": {"keep-me": {"command": "example"}}}),
        encoding="utf-8",
    )
    result = _run(tmp_path, "--profile", "safe")
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["safeMode"] is True
    assert (tmp_path / "codex" / "skills" / "evidence-first-code-audit" / "SKILL.md").is_file()
    assert (lmstudio / "config-presets" / "evidence-first-code-audit.preset.json").is_file()
    mcp = json.loads((lmstudio / "mcp.json").read_text(encoding="utf-8"))
    assert "keep-me" in mcp["mcpServers"]
    evidence = mcp["mcpServers"]["evidence-first"]
    assert evidence["env"]["EVIDENCE_FIRST_SAFE_MODE"] == "1"
    assert payload["mcpSmoke"]["ok"] is True


def test_safe_profile_normalizes_known_existing_unsafe_state(tmp_path: Path) -> None:
    lmstudio = tmp_path / "lmstudio"
    lmstudio.mkdir()
    (lmstudio / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "unreal-agent": {
                        "command": "node",
                        "env": {
                            "ALLOW_WRITE": "1",
                            "ALLOW_COMMANDS": "true",
                            "ALLOW_UNREAL_BUILD": "yes",
                            "VALIDATE_ON_WRITE": "1",
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (lmstudio / "settings.json").write_text(
        json.dumps(
            {
                "chat": {
                    "skipToolConfirmationPatterns": [
                        "keep-me",
                        "mcp/unreal-agent:*",
                        "mcp/unreal-rag:*",
                        "lmstudio/js-code-sandbox:*",
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    result = _run(tmp_path, "--profile", "safe")
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout)
    assert payload["knownIntegrationsSafe"] is True
    assert payload["safetyNormalizations"]
    mcp = json.loads((lmstudio / "mcp.json").read_text(encoding="utf-8"))
    env = mcp["mcpServers"]["unreal-agent"]["env"]
    assert {env[key] for key in ("ALLOW_WRITE", "ALLOW_COMMANDS", "ALLOW_UNREAL_BUILD")} == {"0"}
    settings = json.loads((lmstudio / "settings.json").read_text(encoding="utf-8"))
    assert settings["chat"]["skipToolConfirmationPatterns"] == ["keep-me"]


def test_dry_run_is_zero_mutation(tmp_path: Path) -> None:
    result = _run(tmp_path, "--profile", "safe", "--dry-run")
    assert result.returncode == 0, result.stderr or result.stdout
    assert not (tmp_path / "codex").exists()
    assert not (tmp_path / "lmstudio").exists()
    assert not (tmp_path / "state").exists()


def test_existing_install_lock_fails_before_managed_targets_are_written(tmp_path: Path) -> None:
    lock = tmp_path / "state" / "install.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("{}", encoding="utf-8")
    result = _run(tmp_path, "--profile", "safe")
    assert result.returncode == 1
    assert "another installer is active" in result.stdout
    assert not (tmp_path / "codex").exists()
    assert not (tmp_path / "lmstudio").exists()


def test_safe_profile_rejects_agent_mode(tmp_path: Path) -> None:
    result = _run(tmp_path, "--profile", "safe", "--enable-agent-mode")
    assert result.returncode == 1
    assert "SAFE profile cannot enable agent mode" in result.stdout


def test_noninteractive_agent_mode_requires_explicit_risk_acceptance(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        "--profile",
        "custom",
        "--components",
        "codex,lmstudio,unreal",
        "--enable-agent-mode",
        "--skip-deps",
    )
    assert result.returncode == 1
    assert "--accept-agent-risk" in result.stdout


def test_custom_rule_and_cline_install(tmp_path: Path) -> None:
    rule = tmp_path / "agent" / "rule.md"
    cline = tmp_path / "cline" / "mcp.json"
    result = _run(
        tmp_path,
        "--profile",
        "custom",
        "--components",
        "codex,lmstudio,portable_rule,cline",
        "--rule-path",
        str(rule),
        "--cline-settings",
        str(cline),
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "work evidence-first" in rule.read_text(encoding="utf-8")
    cline_payload = json.loads(cline.read_text(encoding="utf-8"))
    assert "evidence-first" in cline_payload["mcpServers"]


def test_last_install_can_be_rolled_back(tmp_path: Path) -> None:
    original = tmp_path / "lmstudio" / "mcp.json"
    original.parent.mkdir()
    original.write_text(json.dumps({"mcpServers": {"original": {}}}), encoding="utf-8")
    install = _run(tmp_path, "--profile", "safe")
    assert install.returncode == 0, install.stderr or install.stdout
    rollback = _run(tmp_path, "--rollback")
    assert rollback.returncode == 0, rollback.stderr or rollback.stdout
    restored = json.loads(original.read_text(encoding="utf-8"))
    assert restored == {"mcpServers": {"original": {}}}
    assert not (tmp_path / "codex" / "skills" / "evidence-first-code-audit").exists()


def test_unreal_safe_component_registers_read_only_agent(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        "--profile",
        "custom",
        "--components",
        "codex,lmstudio,unreal",
        "--skip-deps",
        "--workspace-root",
        str(tmp_path / "projects"),
    )
    assert result.returncode == 0, result.stderr or result.stdout
    mcp = json.loads((tmp_path / "lmstudio" / "mcp.json").read_text(encoding="utf-8"))
    agent = mcp["mcpServers"]["unreal-agent"]
    assert agent["env"]["ALLOW_WRITE"] == "0"
    assert agent["env"]["ALLOW_COMMANDS"] == "0"
    assert agent["env"]["ALLOW_UNREAL_BUILD"] == "0"


def test_standard_adds_read_only_unreal_and_index_tier_is_orthogonal(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        "--profile",
        "standard",
        "--skip-deps",
        "--index-tier",
        "lite",
        "--workspace-root",
        str(tmp_path / "projects"),
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout)
    assert payload["safeMode"] is True
    assert payload["indexTier"] == "lite"
    shared = json.loads(
        (tmp_path / "lmstudio" / "config" / "unreal-workspace.json").read_text(encoding="utf-8")
    )
    assert shared["indexingTier"] == "lite"


@pytest.mark.skipif(shutil.which("powershell") is None, reason="Windows PowerShell not installed")
def test_legacy_agent_mode_toggles_resolve_lmstudio_home(tmp_path: Path) -> None:
    lmstudio = tmp_path / "lmstudio"
    lmstudio.mkdir()
    mcp_path = lmstudio / "mcp.json"
    mcp_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "unreal-agent": {
                        "command": "node",
                        "args": [],
                        "env": {
                            "ALLOW_WRITE": "0",
                            "ALLOW_COMMANDS": "0",
                            "ALLOW_UNREAL_BUILD": "0",
                            "VALIDATE_ON_WRITE": "0",
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    enable = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "installer" / "Enable-AgentMode.ps1"),
            "-LmStudioHome",
            str(lmstudio),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert enable.returncode == 0, enable.stderr or enable.stdout
    assert json.loads(mcp_path.read_text(encoding="utf-8-sig"))["mcpServers"]["unreal-agent"]["env"]["ALLOW_WRITE"] == "1"
    disable = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "installer" / "Disable-AgentMode.ps1"),
            "-LmStudioHome",
            str(lmstudio),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert disable.returncode == 0, disable.stderr or disable.stdout
    env = json.loads(mcp_path.read_text(encoding="utf-8-sig"))["mcpServers"]["unreal-agent"]["env"]
    assert {env[key] for key in ("ALLOW_WRITE", "ALLOW_COMMANDS", "ALLOW_UNREAL_BUILD")} == {"0"}
