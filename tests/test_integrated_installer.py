from __future__ import annotations

import json
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install.py"


@pytest.fixture(autouse=True)
def _ensure_node_npm_on_path(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unreal adapter tests need node/npm; provide shims when the host has none."""
    if shutil.which("node") and shutil.which("npm"):
        return
    bindir = tmp_path_factory.mktemp("node-shims")
    if os.name == "nt":
        (bindir / "node.cmd").write_text("@echo v20.20.2\r\n", encoding="utf-8")
        (bindir / "npm.cmd").write_text("@echo 10.8.2\r\n", encoding="utf-8")
    else:
        node = bindir / "node"
        npm = bindir / "npm"
        node.write_text("#!/bin/sh\necho v20.20.2\n", encoding="utf-8")
        npm.write_text("#!/bin/sh\necho 10.8.2\n", encoding="utf-8")
        node.chmod(0o755)
        npm.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")


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
    assert module.PRODUCT_VERSION == manifest["productVersion"] == "1.3.0 RC1"
    assert module.PROFILE_DEFAULTS == {
        name: set(components)
        for name, components in manifest["profiles"].items()
        if name != "custom"
    }
    assert manifest["requires"]["linuxBaseline"] == "Ubuntu 22.04/24.04 (glibc)"
    assert manifest["portablePackage"]["runtimeArchiveIntegrity"] == "pinned-sha256"
    assert manifest["portablePackage"]["supportedHosts"] == [
        "windows",
        "ubuntu-linux",
        "macos-apple-silicon",
    ]
    assert manifest["portablePackage"]["releaseReady"] is False


@pytest.mark.parametrize(
    ("system_name", "unreal_platform"),
    [("Windows", "Win64"), ("Darwin", "Mac"), ("Linux", "Linux")],
)
def test_installer_maps_each_supported_host_to_unreal_platform(
    monkeypatch: pytest.MonkeyPatch,
    system_name: str,
    unreal_platform: str,
) -> None:
    module = _load_installer_module()
    monkeypatch.setattr(module.platform, "system", lambda: system_name)
    assert module._default_platform() == unreal_platform
    sys.modules.pop("integrated_install", None)


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
    monkeypatch.setattr(module, "_launcher_manifest_engine_locations", lambda: [])
    monkeypatch.setattr(module, "_common_engine_locations", lambda: [parent])
    assert module._detect_engine_root("5.8") == engine.resolve()
    sys.modules.pop("integrated_install", None)


def test_engine_auto_detection_uses_semantic_version_order(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_installer_module()
    parent = tmp_path / "engines"
    for name in ("UE_5.9", "UE_5.10"):
        (parent / name / "Engine" / "Source").mkdir(parents=True)
    monkeypatch.setattr(module, "_launcher_manifest_engine_locations", lambda: [])
    monkeypatch.setattr(module, "_common_engine_locations", lambda: [parent])
    assert module._detect_engine_root() == (parent / "UE_5.10").resolve()
    sys.modules.pop("integrated_install", None)


def test_windows_launcher_manifest_adds_nondefault_engine_location(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_installer_module()
    program_data = tmp_path / "ProgramData"
    engine = tmp_path / "Epic Library" / "UE_5.8"
    (engine / "Engine" / "Source").mkdir(parents=True)
    manifest = program_data / "Epic" / "UnrealEngineLauncher" / "LauncherInstalled.dat"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps({"InstallationList": [{"AppName": "UE_5.8", "InstallLocation": str(engine)}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(module.sys, "platform", "win32")
    monkeypatch.setenv("PROGRAMDATA", str(program_data))
    assert module._launcher_manifest_engine_locations() == [engine]
    sys.modules.pop("integrated_install", None)


@pytest.mark.parametrize(
    ("answers", "expected_agent_mode"),
    [
        (["n", "n", "n", "1", "1", "2", "y", "y"], True),
        (["n", "n", "n", "1", "1", "2", "n", "y"], False),
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
    assert "context_compactor" in components
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

    responses = iter(["n", "n", "n", "1", choice, "1", "y"])
    monkeypatch.setattr(module.sys, "stdin", InteractiveInput())
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(responses))
    args = module.build_parser().parse_args(["--profile", "standard"])
    _, components = module._resolve_components(args)
    sys.modules.pop("integrated_install", None)

    assert "unreal" in components
    assert "context_compactor" in components
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

    responses = iter(["n", "y", "n", "1", "1", "1", "y"])
    monkeypatch.setattr(module.sys, "stdin", InteractiveInput())
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(responses))
    args = module.build_parser().parse_args(["--profile", "standard"])
    _, components = module._resolve_components(args)
    sys.modules.pop("integrated_install", None)

    assert "cline" in components
    assert "context_compactor" in components
    assert args.cline_settings == Path.home() / ".cline" / "data" / "settings" / "cline_mcp_settings.json"


def test_applescript_quote_escapes_backslashes_and_quotes() -> None:
    module = _load_installer_module()
    sys.modules.pop("integrated_install", None)
    assert module._applescript_quote('a"b\\c') == 'a\\"b\\\\c'


def test_macos_picker_prefers_osascript_over_tkinter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_installer_module()
    project = tmp_path / "Demo.uproject"
    project.write_text("{}", encoding="utf-8")
    calls: list[str] = []

    def fake_osascript(kind: str, initial_directory: Path) -> str:
        calls.append(f"osascript:{kind}:{initial_directory}")
        return str(project)

    def fake_tkinter(kind: str, initial_directory: Path) -> str:
        calls.append("tkinter")
        raise AssertionError("tkinter should not run when osascript succeeds")

    monkeypatch.setattr(module.sys, "platform", "darwin")
    monkeypatch.setattr(module, "_pick_with_osascript", fake_osascript)
    monkeypatch.setattr(module, "_pick_with_tkinter", fake_tkinter)

    selected = module._pick_indexing_target("uproject", tmp_path)
    sys.modules.pop("integrated_install", None)

    assert selected == project.resolve()
    assert calls == [f"osascript:uproject:{tmp_path}"]


def test_macos_picker_falls_back_to_tkinter_when_osascript_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_installer_module()
    folder = tmp_path / "Projects"
    folder.mkdir()
    calls: list[str] = []

    def fake_osascript(kind: str, initial_directory: Path) -> str:
        calls.append("osascript")
        raise RuntimeError("not allowed to send Apple events")

    def fake_tkinter(kind: str, initial_directory: Path) -> str:
        calls.append(f"tkinter:{kind}")
        return str(folder)

    monkeypatch.setattr(module.sys, "platform", "darwin")
    monkeypatch.setattr(module, "_pick_with_osascript", fake_osascript)
    monkeypatch.setattr(module, "_pick_with_tkinter", fake_tkinter)

    selected = module._pick_indexing_target("folder", tmp_path)
    sys.modules.pop("integrated_install", None)

    assert selected == folder.resolve()
    assert calls == ["osascript", "tkinter:folder"]


def test_intel_macos_blocks_lmstudio_stack(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_installer_module()
    monkeypatch.setattr(module.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(module, "_host_cpu_arch", lambda: "x64")
    with pytest.raises(RuntimeError, match="Intel macOS"):
        module._assert_host_component_support({"lmstudio", "context_compactor"})
    sys.modules.pop("integrated_install", None)


def test_intel_macos_allows_cline_only_custom(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_installer_module()
    monkeypatch.setattr(module.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(module, "_host_cpu_arch", lambda: "x64")
    module._assert_host_component_support({"codex", "portable_rule", "cline"})
    sys.modules.pop("integrated_install", None)


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
    # portable_rule=n, cline=n, select projects=y, menu, add another=n,
    # engine=launcher, rag=skip, authority=safe, continue=y
    responses = iter(["n", "n", "y", menu_choice, "n", "1", "1", "1", "y"])
    monkeypatch.setattr(module.sys, "stdin", InteractiveInput())
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(responses))
    monkeypatch.setattr(module, "_pick_indexing_target", lambda kind, initial: selected)
    args = module.build_parser().parse_args(["--profile", "standard"])
    _, components = module._resolve_components(args)
    sys.modules.pop("integrated_install", None)

    assert "unreal" in components
    assert "context_compactor" in components
    assert args.workspace_root == [project_dir]
    assert args.active_project == (project_file if target_kind == "uproject" else None)


def test_interactive_engine_selector_accepts_valid_custom_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_installer_module()
    engine = tmp_path / "CustomEngine"
    (engine / "Engine" / "Source").mkdir(parents=True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "2")
    monkeypatch.setattr(module, "_pick_indexing_target", lambda kind, initial: engine)
    args = module.build_parser().parse_args(["--profile", "standard"])

    module._interactive_engine_selection(args)
    sys.modules.pop("integrated_install", None)

    assert args.engine_root == engine.resolve()
    assert args._engine_selection == "custom"


def test_interactive_engine_selector_marks_launcher_auto_detection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_installer_module()
    monkeypatch.setattr("builtins.input", lambda _prompt="": "1")
    args = module.build_parser().parse_args(["--profile", "standard"])

    module._interactive_engine_selection(args)
    sys.modules.pop("integrated_install", None)

    assert args.engine_root is None
    assert args._engine_selection == "launcher"


def test_launcher_selection_ignores_saved_custom_engine(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_installer_module()
    lmstudio = tmp_path / "lmstudio"
    stale_engine = tmp_path / "SavedCustomEngine"
    detected_engine = tmp_path / "LauncherEngine"
    for engine in (stale_engine, detected_engine):
        (engine / "Engine" / "Source").mkdir(parents=True)
    (lmstudio / "config").mkdir(parents=True)
    _plant_fake_lms(lmstudio)
    (lmstudio / "config" / "unreal-workspace.json").write_text(
        json.dumps({"defaultEngineRoot": str(stale_engine)}),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "_detect_engine_root", lambda association: detected_engine)
    args = module.build_parser().parse_args(
        [
            "--profile",
            "standard",
            "--yes",
            "--skip-deps",
            "--skip-runtime-bootstrap",
            "--codex-home",
            str(tmp_path / "codex"),
            "--lmstudio-home",
            str(lmstudio),
            "--state-home",
            str(tmp_path / "state"),
            "--workspace-root",
            str(tmp_path / "projects"),
        ]
    )
    args._engine_selection = "launcher"

    module.install(args)
    sys.modules.pop("integrated_install", None)

    shared = json.loads(
        (lmstudio / "config" / "unreal-workspace.json").read_text(encoding="utf-8")
    )
    assert shared["defaultEngineRoot"] == str(detected_engine)


def test_interactive_engine_selector_rejects_invalid_custom_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_installer_module()
    monkeypatch.setattr("builtins.input", lambda _prompt="": "2")
    monkeypatch.setattr(module, "_pick_indexing_target", lambda kind, initial: tmp_path)
    args = module.build_parser().parse_args(["--profile", "standard"])

    with pytest.raises(ValueError, match="usable Unreal Engine layout"):
        module._interactive_engine_selection(args)
    sys.modules.pop("integrated_install", None)


def _plant_fake_lms(lmstudio_home: Path) -> None:
    """Provide a no-op LMS CLI that marks the context-compactor plugin as installed."""
    bindir = lmstudio_home / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    plugin_dir = (
        lmstudio_home
        / "extensions"
        / "plugins"
        / "codex"
        / "unreal-context-compactor"
    )
    manifest = plugin_dir / "manifest.json"
    if os.name == "nt":
        script = bindir / "lms.cmd"
        script.write_text(
            "\r\n".join(
                [
                    "@echo off",
                    'if /I "%~1"=="--version" (echo lms 0.0.0-test & exit /b 0)',
                    f'mkdir "{plugin_dir}" >nul 2>nul',
                    (
                        "echo {\"type\":\"plugin\",\"runner\":\"node\",\"owner\":\"codex\","
                        "\"name\":\"unreal-context-compactor\",\"revision\":8} > "
                        f'"{manifest}"'
                    ),
                    "exit /b 0",
                    "",
                ]
            ),
            encoding="utf-8",
        )
    else:
        script = bindir / "lms"
        script.write_text(
            "\n".join(
                [
                    "#!/bin/sh",
                    'if [ "$1" = "--version" ]; then echo "lms 0.0.0-test"; exit 0; fi',
                    f'mkdir -p "{plugin_dir}"',
                    (
                        "printf '%s\\n' "
                        "'{\"type\":\"plugin\",\"runner\":\"node\",\"owner\":\"codex\","
                        "\"name\":\"unreal-context-compactor\",\"revision\":8}' "
                        f'> "{manifest}"'
                    ),
                    "exit 0",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        script.chmod(0o755)


def _run(tmp_path: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    lmstudio_home = tmp_path / "lmstudio"
    extras = list(extra)
    dry_run = "--dry-run" in extras
    if not dry_run:
        lmstudio_home.mkdir(parents=True, exist_ok=True)
        _plant_fake_lms(lmstudio_home)
    if "--skip-deps" not in extras and not dry_run:
        extras.append("--skip-deps")
    return subprocess.run(
        [
            sys.executable,
            str(INSTALLER),
            "--yes",
            "--skip-runtime-bootstrap",
            "--codex-home",
            str(tmp_path / "codex"),
            "--lmstudio-home",
            str(lmstudio_home),
            "--state-home",
            str(tmp_path / "state"),
            *extras,
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


def test_installer_rejects_filesystem_root_as_managed_target(tmp_path: Path) -> None:
    module = _load_installer_module()
    args = module.build_parser().parse_args(["--profile", "safe", "--yes", "--dry-run"])
    args.codex_home = Path(Path.cwd().anchor)
    args.lmstudio_home = tmp_path / "lmstudio"
    args.state_home = tmp_path / "state"
    with pytest.raises(ValueError, match="must not be a filesystem root"):
        module.install(args)
    sys.modules.pop("integrated_install", None)


def test_existing_install_lock_fails_before_managed_targets_are_written(tmp_path: Path) -> None:
    lock = tmp_path / "state" / "install.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text(json.dumps({"pid": os.getpid(), "createdAt": 1.0}), encoding="utf-8")
    result = _run(tmp_path, "--profile", "safe")
    assert result.returncode == 1
    assert "another installer is active" in result.stdout
    assert not (tmp_path / "codex").exists()
    # Test harness may plant a fake lms under lmstudio/bin before the lock fails.
    assert not (tmp_path / "lmstudio" / "mcp.json").exists()
    assert not (tmp_path / "lmstudio" / "settings.json").exists()
    assert not (tmp_path / "lmstudio" / "config-presets").exists()


def test_fresh_partial_install_lock_is_not_stolen(tmp_path: Path) -> None:
    lock = tmp_path / "state" / "install.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("", encoding="utf-8")
    result = _run(tmp_path, "--profile", "safe")
    assert result.returncode == 1
    assert "another installer is active" in result.stdout
    assert lock.exists()


def test_bootstrap_lock_can_be_reacquired_only_with_same_reexec_token(tmp_path: Path) -> None:
    module = _load_installer_module()
    first = module.InstallLock(
        tmp_path,
        lock_name="runtime-bootstrap.lock",
        owner_token="same-install",
    )
    first.acquire()
    resumed = module.InstallLock(
        tmp_path,
        lock_name="runtime-bootstrap.lock",
        owner_token="same-install",
    )
    resumed.acquire()
    assert resumed.acquired is True
    resumed.release()
    assert not resumed.path.exists()
    first.acquired = False
    sys.modules.pop("integrated_install", None)


def test_runtime_bootstrap_only_requests_components_that_need_runtimes() -> None:
    module = _load_installer_module()
    assert module._runtime_requirements({"codex", "lmstudio"}, build_rag=False) == (False, False)
    assert module._runtime_requirements({"unreal"}, build_rag=False) == (True, False)
    assert module._runtime_requirements({"context_compactor"}, build_rag=False) == (True, False)
    assert module._runtime_requirements({"codex", "lmstudio", "context_compactor"}, build_rag=False) == (
        True,
        False,
    )
    assert module._runtime_requirements({"unreal"}, build_rag=True) == (True, True)
    sys.modules.pop("integrated_install", None)


def test_context_compactor_is_forced_for_lmstudio_profiles() -> None:
    module = _load_installer_module()
    for profile in ("safe", "standard", "full"):
        args = module.build_parser().parse_args(["--profile", profile, "--yes"])
        resolved_profile, components = module._resolve_components(args)
        assert resolved_profile == profile
        assert "context_compactor" in components
    sys.modules.pop("integrated_install", None)


def test_skip_context_compactor_requires_allow_flag() -> None:
    module = _load_installer_module()
    args = module.build_parser().parse_args(
        ["--profile", "standard", "--yes", "--skip-context-compactor"]
    )
    with pytest.raises(ValueError, match="Context compactor is required"):
        module._resolve_components(args)
    args = module.build_parser().parse_args(
        [
            "--profile",
            "standard",
            "--yes",
            "--skip-context-compactor",
            "--allow-skip-context-compactor",
        ]
    )
    _, components = module._resolve_components(args)
    assert "context_compactor" not in components
    sys.modules.pop("integrated_install", None)


def test_resolve_lms_cli_prefers_env_and_platform_binaries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_installer_module()
    home = tmp_path / ".lmstudio"
    binary = home / "bin" / ("lms.exe" if os.name == "nt" else "lms")
    binary.parent.mkdir(parents=True)
    binary.write_text("", encoding="utf-8")
    monkeypatch.delenv("LMSTUDIO_CLI", raising=False)
    monkeypatch.setattr(module.shutil, "which", lambda _name: str(tmp_path / "path-lms"))
    (tmp_path / "path-lms").write_text("", encoding="utf-8")
    assert module._resolve_lms_cli(home) == str(binary.resolve())
    override = tmp_path / "custom-lms"
    override.write_text("", encoding="utf-8")
    monkeypatch.setenv("LMSTUDIO_CLI", str(override))
    assert module._resolve_lms_cli(home) == str(override)
    sys.modules.pop("integrated_install", None)


def test_activate_context_compactor_pins_plugin_in_settings(tmp_path: Path) -> None:
    module = _load_installer_module()
    home = tmp_path / ".lmstudio"
    home.mkdir()
    result = module._activate_context_compactor_in_settings(home, dry_run=False)
    settings = json.loads((home / "settings.json").read_text(encoding="utf-8"))
    assert result["pinned"] is True
    assert module.CONTEXT_COMPACTOR_PLUGIN_ID in settings["chat"]["pinnedPlugins"]
    assert settings["developer"]["allowDevelopmentPlugins"] is True
    sys.modules.pop("integrated_install", None)


def test_ensure_context_compactor_materializes_from_source_when_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_installer_module()
    home = tmp_path / "managed-lmstudio"
    empty_default = tmp_path / "empty-default-lmstudio"
    empty_default.mkdir()
    monkeypatch.setattr(module, "_default_lmstudio_home", lambda: empty_default)
    plugin_src = ROOT / "lmstudio-context-compactor-plugin"
    detail = module._ensure_context_compactor_on_disk(
        plugin_src=plugin_src,
        lmstudio_home=home,
    )
    manifest = home / "extensions" / "plugins" / "codex" / "unreal-context-compactor" / "manifest.json"
    assert detail["copied"] is True
    assert detail["source"] == "repository-source"
    assert manifest.is_file()
    sys.modules.pop("integrated_install", None)


def test_directory_replacement_restores_original_when_old_cleanup_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_installer_module()
    source = tmp_path / "source"
    target = tmp_path / "managed" / "target"
    state = tmp_path / "state"
    source.mkdir()
    target.mkdir(parents=True)
    (source / "value.txt").write_text("new", encoding="utf-8")
    (target / "value.txt").write_text("old", encoding="utf-8")
    transaction = module.Transaction(state, [tmp_path])
    original_rmtree = module.shutil.rmtree
    failed = False

    def fail_old_cleanup(path, *args, **kwargs):
        nonlocal failed
        candidate = Path(path)
        if not failed and candidate.name.startswith(".target-old-"):
            failed = True
            raise OSError("simulated cleanup failure")
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(module.shutil, "rmtree", fail_old_cleanup)
    with pytest.raises(OSError, match="simulated cleanup failure"):
        transaction.replace_directory(source, target)
    assert (target / "value.txt").read_text(encoding="utf-8") == "old"
    assert transaction.actions == []
    sys.modules.pop("integrated_install", None)


def test_stale_install_lock_is_cleared_automatically(tmp_path: Path) -> None:
    lock = tmp_path / "state" / "install.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text(json.dumps({"pid": 2_147_483_647, "createdAt": 1.0}), encoding="utf-8")
    result = _run(tmp_path, "--profile", "safe")
    assert result.returncode == 0, result.stderr or result.stdout
    assert json.loads(result.stdout)["ok"] is True
    assert not lock.exists()


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


def test_rag_build_rejects_profiles_without_unreal_component(tmp_path: Path) -> None:
    result = _run(tmp_path, "--profile", "safe", "--build-rag")
    assert result.returncode == 1
    assert "--build-rag requires the unreal component" in result.stdout


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
    assert Path(env["PYTHON_EXE"]).resolve() == Path(sys.executable).resolve()
    assert {
        env[key]
        for key in (
            "ALLOW_WRITE",
            "ALLOW_COMMANDS",
            "ALLOW_UNREAL_BUILD",
            "VALIDATE_ON_WRITE",
        )
    } == {"1"}
    runtime_manifest_path = tmp_path / "lmstudio" / "config" / "control-runtime.json"
    runtime_manifest = json.loads(runtime_manifest_path.read_text(encoding="utf-8"))
    assert payload["controlRuntimeManifest"] == str(runtime_manifest_path)
    assert set(runtime_manifest["components"]) == {"agent", "rag", "compactor"}
    assert env["CONTROL_RUNTIME_MANIFEST"] == str(runtime_manifest_path)
    assert env["CONTROL_RUNTIME_REQUIRED"] == "1"
    installed_runtime_manifest = (
        tmp_path
        / "lmstudio"
        / "extensions"
        / "plugins"
        / "codex"
        / "unreal-context-compactor"
        / "control-runtime.json"
    )
    assert installed_runtime_manifest.is_file()


def test_full_agent_install_keeps_context_proxy_advisory(tmp_path: Path) -> None:
    module = _load_installer_module()
    args = module.build_parser().parse_args(["--profile", "full"])
    args.enable_agent_mode = True
    args.lmstudio_home = tmp_path / "lmstudio"
    args.workspace_root = [tmp_path / "projects"]
    entries = module._unreal_entries(
        args,
        Path(sys.executable),
        tmp_path / "node",
        tmp_path / "shared.json",
        tmp_path / "agent.json",
        context_compactor_advisory=True,
    )
    rag_env = entries["unreal-rag"]["env"]
    assert rag_env["MCP_FRONTEND"] == "lmstudio"
    assert rag_env["MCP_CONTEXT_COMPACTOR_ADVISORY"] == "1"
    assert rag_env["MCP_REQUIRE_CONTEXT_COMPACTOR_ACTIVE"] == "0"
    assert rag_env["MCP_CONTEXT_COMPACTOR_REQUIRED_FRONTENDS"] == "lmstudio"
    assert rag_env["MCP_CONTEXT_COMPACTOR_MAX_AGE_SECONDS"] == "300"
    manifest_path = tmp_path / "lmstudio" / "config" / "control-runtime.json"
    for name in ("unreal-rag", "unreal-agent"):
        runtime_env = entries[name]["env"]
        assert runtime_env["CONTROL_RUNTIME_MANIFEST"] == str(manifest_path)
        assert runtime_env["CONTROL_RUNTIME_REQUIRED"] == "1"
    assert rag_env["CONTROL_RUNTIME_COMPONENT"] == "rag"
    assert entries["unreal-agent"]["env"]["CONTROL_RUNTIME_COMPONENT"] == "agent"
    sys.modules.pop("integrated_install", None)


def test_cline_entries_do_not_inherit_lmstudio_context_policy(tmp_path: Path) -> None:
    module = _load_installer_module()
    args = module.build_parser().parse_args(["--profile", "full"])
    args.enable_agent_mode = True
    args.lmstudio_home = tmp_path / "lmstudio"
    args.workspace_root = [tmp_path / "projects"]
    entries = module._unreal_entries(
        args,
        Path(sys.executable),
        tmp_path / "node",
        tmp_path / "shared.json",
        tmp_path / "agent.json",
        context_compactor_advisory=True,
    )

    cline_rag = module._mcp_entry_for_frontend(entries["unreal-rag"], "cline")
    cline_agent = module._mcp_entry_for_frontend(entries["unreal-agent"], "cline")

    assert entries["unreal-rag"]["env"]["MCP_FRONTEND"] == "lmstudio"
    assert entries["unreal-rag"]["env"]["MCP_CONTEXT_COMPACTOR_ADVISORY"] == "1"
    assert cline_rag["env"]["MCP_FRONTEND"] == "cline"
    assert cline_agent["env"]["MCP_FRONTEND"] == "cline"
    assert module.LMSTUDIO_CONTEXT_POLICY_ENV.isdisjoint(cline_rag["env"])
    assert module.LMSTUDIO_CONTEXT_POLICY_ENV.isdisjoint(cline_agent["env"])
    sys.modules.pop("integrated_install", None)


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


def test_custom_unreal_cline_install_uses_cline_frontend_identity(tmp_path: Path) -> None:
    cline = tmp_path / "cline" / "mcp.json"
    result = _run(
        tmp_path,
        "--profile",
        "custom",
        "--components",
        "codex,lmstudio,unreal,cline",
        "--cline-settings",
        str(cline),
        "--workspace-root",
        str(tmp_path / "projects"),
        "--enable-agent-mode",
        "--accept-agent-risk",
        "--skip-deps",
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(cline.read_text(encoding="utf-8"))
    for name in ("unreal-rag", "unreal-agent"):
        env = payload["mcpServers"][name]["env"]
        assert env["MCP_FRONTEND"] == "cline"
        assert not any(key.startswith("MCP_CONTEXT_COMPACTOR_") for key in env)
    assert "MCP_REQUIRE_CONTEXT_COMPACTOR_ACTIVE" not in payload["mcpServers"]["unreal-rag"]["env"]


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


def test_rollback_preflight_preserves_current_file_when_backup_is_missing(tmp_path: Path) -> None:
    module = _load_installer_module()
    state = tmp_path / "state"
    target = tmp_path / "managed" / "config.json"
    target.parent.mkdir()
    target.write_text("current", encoding="utf-8")
    state.mkdir()
    (state / "install-journal.json").write_text(
        json.dumps(
            {
                "allowedRoots": [str(tmp_path / "managed")],
                "backupRoot": str(state / "backups" / "missing-generation"),
                "actions": [
                    {
                        "kind": "file",
                        "target": str(target),
                        "existed": True,
                        "backup": str(state / "backups" / "missing-generation" / "000-config.json"),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="backup is missing"):
        module.rollback_last_install(state)
    assert target.read_text(encoding="utf-8") == "current"
    sys.modules.pop("integrated_install", None)


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


def test_invalid_unreal_engine_environment_fails_instead_of_silently_falling_back(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("UNREAL_ENGINE_ROOT", str(tmp_path / "missing-engine"))
    result = _run(
        tmp_path,
        "--profile",
        "standard",
        "--skip-deps",
    )
    assert result.returncode == 1
    assert "UNREAL_ENGINE_ROOT does not contain a usable Unreal Engine layout" in result.stdout


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
    combined = f"{result.stdout}\n{result.stderr}"
    assert "run_index_pipeline.ps1" in combined
    assert f"-Tier {tier}" in combined
    assert "-PythonExe" in combined
    assert "rag.ps1 build" not in combined
    if sys.platform == "win32":
        assert "-NoProfile -ExecutionPolicy Bypass -File" in combined
    else:
        assert "-ExecutionPolicy" not in combined


@pytest.mark.parametrize(
    ("system_name", "expected"),
    [
        (
            "Windows",
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                "pipeline.ps1",
                "-Tier",
                "full",
            ],
        ),
        (
            "Linux",
            [
                "pwsh",
                "-NoProfile",
                "-File",
                "pipeline.ps1",
                "-Tier",
                "full",
            ],
        ),
        (
            "Darwin",
            [
                "pwsh",
                "-NoProfile",
                "-File",
                "pipeline.ps1",
                "-Tier",
                "full",
            ],
        ),
    ],
)
def test_powershell_file_command_is_host_aware(
    monkeypatch: pytest.MonkeyPatch,
    system_name: str,
    expected: list[str],
) -> None:
    module = _load_installer_module()
    monkeypatch.setattr(module.platform, "system", lambda: system_name)
    executable = "powershell" if system_name == "Windows" else "pwsh"

    command = module._powershell_file_command(
        executable,
        Path("pipeline.ps1"),
        ["-Tier", "full"],
    )
    sys.modules.pop("integrated_install", None)

    assert command == expected


@pytest.mark.skipif(sys.platform != "win32", reason="Windows execution policies only")
def test_powershell_file_command_bypasses_restricted_process_policy(tmp_path: Path) -> None:
    module = _load_installer_module()
    powershell = shutil.which("powershell")
    assert powershell is not None
    script = tmp_path / "policy-smoke.ps1"
    script.write_text("Write-Output 'policy-smoke'\n", encoding="utf-8")
    environment = os.environ.copy()
    environment["PSExecutionPolicyPreference"] = "Restricted"

    blocked = subprocess.run(
        [powershell, "-NoProfile", "-File", str(script)],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    command = module._powershell_file_command(powershell, script, [])
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    sys.modules.pop("integrated_install", None)

    assert blocked.returncode != 0
    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert completed.stdout.strip() == "policy-smoke"


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


def test_tier_pipeline_collects_portable_guideline_inputs_before_build() -> None:
    pipeline = (ROOT / "scripts" / "run_index_pipeline.ps1").read_text(encoding="utf-8")
    assert 'collect_project_guidelines.py' in pipeline
    assert 'collect_game_design_docs.py' in pipeline
    assert '$guidelinesRoot = Join-Path $workspace "RAG_Project_Guidelines"' in pipeline
    assert '$gameDesignRoot = Join-Path $workspace "Game_Design_Docs"' in pipeline
    assert pipeline.index('Write-Host "[1/9] collect-guidelines"') < pipeline.index(
        'Write-Host "[9/9] build index"'
    )
