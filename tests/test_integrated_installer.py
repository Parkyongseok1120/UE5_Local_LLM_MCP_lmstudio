from __future__ import annotations

import json
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install.py"


def _load_installer_module():
    spec = importlib.util.spec_from_file_location("integrated_install", INSTALLER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_installer_profiles_are_manifest_driven() -> None:
    module = _load_installer_module()
    sys.modules.pop("integrated_install", None)
    manifest = json.loads((ROOT / "installer" / "manifest.json").read_text(encoding="utf-8"))
    assert module.PROFILE_DEFAULTS == {
        name: set(components)
        for name, components in manifest["profiles"].items()
        if name != "custom"
    }


def test_engine_auto_detection_accepts_native_build_sh_layout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_installer_module()
    parent = tmp_path / "engines"
    engine = parent / "UE_5.8"
    script = engine / "Engine" / "Build" / "BatchFiles" / "Linux" / "Build.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/usr/bin/env sh\n", encoding="utf-8")
    monkeypatch.setattr(module, "_common_engine_locations", lambda: [parent])
    assert module._detect_engine_root("5.8") == engine.resolve()
    sys.modules.pop("integrated_install", None)


@pytest.mark.parametrize(
    ("answers", "expected_agent_mode"),
    [
        (["n", "n", "n", "n", "1", "2", "y", "y"], True),
        (["n", "n", "n", "n", "1", "2", "n", "y"], False),
    ],
)
def test_interactive_agent_selector_confirms_or_falls_back_to_safe(
    monkeypatch: pytest.MonkeyPatch,
    answers: list[str],
    expected_agent_mode: bool,
) -> None:
    module = _load_installer_module()

    class InteractiveInput:
        @staticmethod
        def isatty() -> bool:
            return True

    responses = iter(answers)
    monkeypatch.setattr(module.sys, "stdin", InteractiveInput())
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(responses))
    args = module.build_parser().parse_args(["--profile", "standard"])
    profile, components = module._resolve_components(args)
    sys.modules.pop("integrated_install", None)

    assert profile == "standard"
    assert "unreal" in components
    assert args.enable_agent_mode is expected_agent_mode
    assert args.accept_agent_risk is expected_agent_mode


@pytest.mark.parametrize(
    ("choice", "expected_tier"),
    [("3", "standard"), ("4", "full")],
)
def test_interactive_index_selector_builds_selected_tier(
    monkeypatch: pytest.MonkeyPatch,
    choice: str,
    expected_tier: str,
) -> None:
    module = _load_installer_module()

    class InteractiveInput:
        @staticmethod
        def isatty() -> bool:
            return True

    responses = iter(["n", "n", "n", "n", choice, "1", "y"])
    monkeypatch.setattr(module.sys, "stdin", InteractiveInput())
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(responses))
    args = module.build_parser().parse_args(["--profile", "standard"])
    _, components = module._resolve_components(args)
    sys.modules.pop("integrated_install", None)

    assert "unreal" in components
    assert args.build_rag is True
    assert args.index_tier == expected_tier


def test_interactive_cline_selection_uses_default_settings_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_installer_module()

    class InteractiveInput:
        @staticmethod
        def isatty() -> bool:
            return True

    responses = iter(["n", "n", "y", "n", "1", "1", "y"])
    monkeypatch.setattr(module.sys, "stdin", InteractiveInput())
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(responses))
    args = module.build_parser().parse_args(["--profile", "standard"])
    _, components = module._resolve_components(args)
    sys.modules.pop("integrated_install", None)

    assert "cline" in components
    assert args.cline_settings == Path.home() / ".cline" / "data" / "settings" / "cline_mcp_settings.json"


@pytest.mark.parametrize(
    ("menu_choice", "target_kind"),
    [("1", "uproject"), ("2", "folder")],
)
def test_interactive_project_picker_restores_uproject_and_folder_selection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    menu_choice: str,
    target_kind: str,
) -> None:
    module = _load_installer_module()

    class InteractiveInput:
        @staticmethod
        def isatty() -> bool:
            return True

    project_dir = tmp_path / "PickedProject"
    project_dir.mkdir()
    project_file = project_dir / "PickedProject.uproject"
    project_file.write_text("{}", encoding="utf-8")
    selected = project_file if target_kind == "uproject" else project_dir
    responses = iter(["n", "n", "n", "y", menu_choice, "n", "1", "1", "y"])
    monkeypatch.setattr(module.sys, "stdin", InteractiveInput())
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(responses))
    monkeypatch.setattr(module, "_pick_indexing_target", lambda kind, initial: selected)
    args = module.build_parser().parse_args(["--profile", "standard"])
    _, components = module._resolve_components(args)
    sys.modules.pop("integrated_install", None)

    assert "unreal" in components
    assert args.workspace_root == [project_dir]
    assert args.active_project == (project_file if target_kind == "uproject" else None)


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
    assert payload["agentMode"] is False
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


def test_acknowledged_agent_mode_enables_all_unreal_authority(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        "--profile",
        "standard",
        "--enable-agent-mode",
        "--accept-agent-risk",
        "--skip-deps",
        "--workspace-root",
        str(tmp_path / "projects"),
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout)
    assert payload["agentMode"] is True
    assert payload["safeMode"] is False
    mcp = json.loads((tmp_path / "lmstudio" / "mcp.json").read_text(encoding="utf-8"))
    env = mcp["mcpServers"]["unreal-agent"]["env"]
    assert {
        env[key]
        for key in (
            "ALLOW_WRITE",
            "ALLOW_COMMANDS",
            "ALLOW_UNREAL_BUILD",
            "VALIDATE_ON_WRITE",
        )
    } == {"1"}


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


def test_portable_rule_uses_managed_default_path_when_not_supplied(tmp_path: Path) -> None:
    state_home = tmp_path / "state"
    rule = state_home / "portable-rules" / "evidence-first-code-audit.md"
    result = _run(
        tmp_path,
        "--profile",
        "custom",
        "--components",
        "portable_rule",
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout)
    assert "work evidence-first" in rule.read_text(encoding="utf-8")
    assert payload["portableRulePaths"] == [str(rule)]


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


def test_explicit_active_project_is_persisted_for_project_indexing(tmp_path: Path) -> None:
    project_dir = tmp_path / "projects" / "Demo"
    project_dir.mkdir(parents=True)
    project_file = project_dir / "Demo.uproject"
    project_file.write_text("{}", encoding="utf-8")
    result = _run(
        tmp_path,
        "--profile",
        "standard",
        "--skip-deps",
        "--active-project",
        str(project_file),
    )
    assert result.returncode == 0, result.stderr or result.stdout
    shared = json.loads(
        (tmp_path / "lmstudio" / "config" / "unreal-workspace.json").read_text(encoding="utf-8")
    )
    assert shared["activeProject"] == str(project_file)
    assert str(project_dir) in shared["projectSearchRoots"]
    assert shared["editorExportDir"] == str(project_dir / "Saved" / "LmStudioMetadataExports")


def test_explicit_engine_root_is_persisted_and_forwarded_to_mcp(tmp_path: Path) -> None:
    engine = tmp_path / "CustomEngine"
    (engine / "Engine" / "Source").mkdir(parents=True)
    result = _run(
        tmp_path,
        "--profile",
        "standard",
        "--skip-deps",
        "--engine-root",
        str(engine),
    )
    assert result.returncode == 0, result.stderr or result.stdout
    shared = json.loads(
        (tmp_path / "lmstudio" / "config" / "unreal-workspace.json").read_text(encoding="utf-8")
    )
    mcp = json.loads((tmp_path / "lmstudio" / "mcp.json").read_text(encoding="utf-8"))
    assert shared["defaultEngineRoot"] == str(engine)
    assert mcp["mcpServers"]["unreal-rag"]["env"]["UNREAL_ENGINE_ROOT"] == str(engine)
    assert mcp["mcpServers"]["unreal-agent"]["env"]["UNREAL_ENGINE_ROOT"] == str(engine)


@pytest.mark.parametrize("tier", ["standard", "full"])
def test_rag_build_uses_tier_aware_collection_pipeline(tmp_path: Path, tier: str) -> None:
    result = _run(
        tmp_path,
        "--profile",
        "standard",
        "--skip-deps",
        "--dry-run",
        "--build-rag",
        "--index-tier",
        tier,
        "--workspace-root",
        str(tmp_path / "projects"),
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "run_index_pipeline.ps1" in result.stdout
    assert f"-Tier {tier}" in result.stdout
    assert "-PythonExe" in result.stdout
    assert "rag.ps1 build" not in result.stdout


def test_tier_pipeline_removes_inputs_excluded_by_standard_and_lite() -> None:
    pipeline = (ROOT / "scripts" / "run_index_pipeline.ps1").read_text(encoding="utf-8")
    assert 'if ($resolvedTier -ne "full")' in pipeline
    assert 'Remove-TierInput -Path $sourcePath -Reason "excluded by $resolvedTier tier"' in pipeline
    for path_name in (
        "$symbolsPath",
        "$moduleGraphPath",
        "$projectSymbolsPath",
        "$projectProfilesPath",
        "$projectArchitecturePath",
    ):
        assert path_name in pipeline
