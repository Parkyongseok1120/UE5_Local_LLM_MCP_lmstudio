from __future__ import annotations

import json
import importlib.util
import os
import shutil
import sqlite3
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
    node_package = json.loads(
        (ROOT / "lmstudio-unreal-agent-mcp" / "package.json").read_text(encoding="utf-8")
    )
    node_lock = json.loads(
        (ROOT / "lmstudio-unreal-agent-mcp" / "package-lock.json").read_text(encoding="utf-8")
    )
    compactor_package = json.loads(
        (ROOT / "lmstudio-context-compactor-plugin" / "package.json").read_text(encoding="utf-8")
    )
    compactor_lock = json.loads(
        (ROOT / "lmstudio-context-compactor-plugin" / "package-lock.json").read_text(encoding="utf-8")
    )
    compactor_manifest = json.loads(
        (ROOT / "lmstudio-context-compactor-plugin" / "manifest.json").read_text(encoding="utf-8")
    )
    assert module.PRODUCT_VERSION == manifest["productVersion"] == "1.3.3"
    assert manifest["version"] == "2.1.16"
    assert manifest["safety"]["contextCompactorInstalledWithLmStudio"] is True
    assert manifest["safety"]["contextCompactorChatActivationManagedByInstaller"] is False
    assert manifest["safety"]["contextCompactionEnabledByDefault"] is False
    assert "contextCompactorEnabledByDefault" not in manifest["safety"]
    assert "contextCompactorRequiredWithLmStudio" not in manifest["safety"]
    assert node_package["version"] == node_lock["version"] == "0.3.21"
    assert node_lock["packages"][""]["version"] == "0.3.21"
    assert compactor_package["version"] == compactor_lock["version"] == "0.4.51"
    assert compactor_lock["packages"][""]["version"] == "0.4.51"
    assert compactor_manifest["revision"] == 98
    assert module.PROFILE_DEFAULTS == {
        name: set(components)
        for name, components in manifest["profiles"].items()
        if name != "custom"
    }
    assert manifest["requires"]["linuxBaseline"] == "Ubuntu 22.04/24.04 (glibc)"
    assert manifest["launcherBootstrap"] == {
        "hostPythonRequired": False,
        "managedPythonDefinition": "installer/runtime-manifest.json#runtimes.python",
        "seedRuntime": "uv",
        "systemWideInstall": False,
        "skipFlag": "--skip-runtime-bootstrap",
    }
    assert manifest["portablePackage"]["runtimeArchiveIntegrity"] == "pinned-sha256"
    assert manifest["portablePackage"]["supportedHosts"] == [
        "windows",
        "ubuntu-linux",
        "macos-apple-silicon",
    ]
    assert manifest["portablePackage"]["releaseReady"] is True


def test_installer_upgrade_cleanup_preserves_unrelated_custom_mcps() -> None:
    module = _load_installer_module()
    config = {
        "mcpServers": {
            "unreal-rag-strict": {
                "command": "python",
                "args": ["copy.py"],
                "env": {"MCP_EXECUTION_MODE": "strict"},
            },
            "renamed-old-rag": {
                "command": "python.exe",
                "args": ["C:/old/scripts/unreal_rag_mcp.py"],
            },
            "renamed-control": {
                "command": "python3",
                "args": ["renamed.py"],
                "env": {
                    "CONTROL_RUNTIME_COMPONENT": "rag",
                    "CONTROL_RUNTIME_REQUIRED": "1",
                },
            },
            "keep-custom": {
                "command": "python",
                "args": ["custom.py"],
                "env": {"MCP_EXECUTION_MODE": "strict"},
            },
        }
    }

    removed = module._remove_legacy_python_control_entries(config)

    assert removed == ["unreal-rag-strict", "renamed-old-rag", "renamed-control"]
    assert set(config["mcpServers"]) == {"keep-custom"}
    sys.modules.pop("integrated_install", None)


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


def test_engine_auto_detection_never_substitutes_latest_for_custom_association(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_installer_module()
    parent = tmp_path / "engines"
    for name in ("UE_4.27", "UE_5.10"):
        (parent / name / "Engine" / "Source").mkdir(parents=True)
    monkeypatch.setattr(module, "_launcher_manifest_engine_locations", lambda: [])
    monkeypatch.setattr(module, "_common_engine_locations", lambda: [parent])

    assert module._detect_engine_root("UE_4.27") == (parent / "UE_4.27").resolve()
    assert module._detect_engine_root("source-build-guid") is None
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
    """Provide an LMS CLI that installs the current compiled compactor identity."""
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
    bundle = plugin_dir / ".lmstudio" / "production.js"
    current_manifest = json.loads(
        (ROOT / "lmstudio-context-compactor-plugin" / "manifest.json").read_text(encoding="utf-8")
    )
    manifest_json = json.dumps(current_manifest, separators=(",", ":"))
    if os.name == "nt":
        script = bindir / "lms.cmd"
        script.write_text(
            "\r\n".join(
                [
                    "@echo off",
                    'if /I "%~1"=="--version" (echo lms 0.0.0-test & exit /b 0)',
                    f'mkdir "{bundle.parent}" >nul 2>nul',
                    f'echo {manifest_json} > "{manifest}"',
                    f'echo module.exports = {{}}; > "{bundle}"',
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
                    f'mkdir -p "{bundle.parent}"',
                    f"printf '%s\\n' '{manifest_json}' > \"{manifest}\"",
                    f"printf '%s\\n' 'module.exports = {{}};' > \"{bundle}\"",
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
                        "mcp/unreal-rag:unreal_architecture_reasoning",
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
    assert "VALIDATE_ON_WRITE" not in env
    settings = json.loads((lmstudio / "settings.json").read_text(encoding="utf-8"))
    assert settings["chat"]["skipToolConfirmationPatterns"] == ["keep-me"]


def test_standard_upgrade_removes_stale_python_controller_entries_and_reports_them(
    tmp_path: Path,
) -> None:
    lmstudio = tmp_path / "lmstudio"
    lmstudio.mkdir()
    (lmstudio / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "unreal-rag-strict": {
                        "command": "python",
                        "args": ["renamed.py"],
                        "env": {"MCP_EXECUTION_MODE": "strict"},
                    },
                    "copied-old-rag": {
                        "command": "python",
                        "args": ["C:/old/scripts/unreal_rag_mcp.py"],
                    },
                    "keep-custom": {"command": "custom", "args": ["server"]},
                }
            }
        ),
        encoding="utf-8",
    )

    result = _run(
        tmp_path,
        "--profile",
        "standard",
        "--skip-deps",
        "--workspace-root",
        str(tmp_path / "projects"),
    )

    assert result.returncode == 0, result.stderr or result.stdout
    report = json.loads(result.stdout)
    assert "mcpServers.unreal-rag-strict:removed_legacy_python_control" in report[
        "safetyNormalizations"
    ]
    assert "mcpServers.copied-old-rag:removed_legacy_python_control" in report[
        "safetyNormalizations"
    ]
    config = json.loads((lmstudio / "mcp.json").read_text(encoding="utf-8"))
    assert "unreal-rag-strict" not in config["mcpServers"]
    assert "copied-old-rag" not in config["mcpServers"]
    assert "keep-custom" in config["mcpServers"]
    assert config["mcpServers"]["unreal-rag"]["args"][0].endswith("unreal_rag_direct.py")


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
    assert module._runtime_requirements({"unreal"}, build_rag=True) == (True, False)
    sys.modules.pop("integrated_install", None)


def test_context_compactor_installation_is_included_for_lmstudio_profiles() -> None:
    module = _load_installer_module()
    for profile in ("safe", "standard", "full"):
        args = module.build_parser().parse_args(["--profile", profile, "--yes"])
        resolved_profile, components = module._resolve_components(args)
        assert resolved_profile == profile
        assert "context_compactor" in components
    sys.modules.pop("integrated_install", None)


def test_custom_context_compactor_only_selection_remains_a_plugin_only_repair() -> None:
    module = _load_installer_module()
    args = module.build_parser().parse_args(
        ["--profile", "custom", "--components", "context_compactor", "--yes"]
    )
    resolved_profile, components = module._resolve_components(args)
    assert resolved_profile == "custom"
    assert components == {"context_compactor"}
    sys.modules.pop("integrated_install", None)


def test_skip_context_compactor_installation_requires_allow_flag() -> None:
    module = _load_installer_module()
    args = module.build_parser().parse_args(
        ["--profile", "standard", "--yes", "--skip-context-compactor"]
    )
    with pytest.raises(ValueError, match="Context compactor installation is required"):
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


def test_context_compactor_pins_shortcut_without_activation_claim(tmp_path: Path) -> None:
    module = _load_installer_module()
    home = tmp_path / ".lmstudio"
    home.mkdir()
    result = module._configure_context_compactor_availability(home, dry_run=False)
    settings = json.loads((home / "settings.json").read_text(encoding="utf-8"))
    assert result["pinned"] is True
    assert "activation" not in result
    assert module.CONTEXT_COMPACTOR_PLUGIN_ID in settings["chat"]["pinnedPlugins"]
    assert settings["developer"]["allowDevelopmentPlugins"] is True
    sys.modules.pop("integrated_install", None)


def _make_context_compactor_source(tmp_path: Path) -> Path:
    source = tmp_path / "context-compactor-source"
    source.mkdir()
    for name in ("manifest.json", "package.json"):
        shutil.copy2(ROOT / "lmstudio-context-compactor-plugin" / name, source / name)
    return source


def _plant_current_context_compactor_install(module, home: Path, plugin_src: Path) -> Path:
    target_dir = module._context_compactor_install_path(home).parent
    production = target_dir / ".lmstudio" / "production.js"
    production.parent.mkdir(parents=True)
    shutil.copy2(plugin_src / "manifest.json", target_dir / "manifest.json")
    production.write_text("module.exports = { current: true };\n", encoding="utf-8")
    return target_dir


def test_ensure_context_compactor_syncs_current_default_install_when_target_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_installer_module()
    home = tmp_path / "managed-lmstudio"
    default_home = tmp_path / "default-lmstudio"
    plugin_src = _make_context_compactor_source(tmp_path)
    _plant_current_context_compactor_install(module, default_home, plugin_src)
    monkeypatch.setattr(module, "_default_lmstudio_home", lambda: default_home)
    detail = module._ensure_context_compactor_on_disk(
        plugin_src=plugin_src,
        lmstudio_home=home,
    )
    manifest = home / "extensions" / "plugins" / "codex" / "unreal-context-compactor" / "manifest.json"
    assert detail["copied"] is True
    assert detail["source"] == "default-lmstudio-home"
    assert detail["ready"] is True
    assert manifest.is_file()
    assert (manifest.parent / ".lmstudio" / "production.js").is_file()
    sys.modules.pop("integrated_install", None)


def test_ensure_context_compactor_replaces_stale_revision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_installer_module()
    plugin_src = _make_context_compactor_source(tmp_path)
    source_manifest = json.loads((plugin_src / "manifest.json").read_text(encoding="utf-8"))
    home = tmp_path / "managed-lmstudio"
    default_home = tmp_path / "default-lmstudio"
    _plant_current_context_compactor_install(module, default_home, plugin_src)
    target_dir = module._context_compactor_install_path(home).parent
    target_bundle = target_dir / ".lmstudio" / "production.js"
    target_bundle.parent.mkdir(parents=True)
    stale_manifest = {**source_manifest, "revision": source_manifest["revision"] - 1}
    (target_dir / "manifest.json").write_text(json.dumps(stale_manifest), encoding="utf-8")
    target_bundle.write_text("module.exports = { stale: true };\n", encoding="utf-8")
    monkeypatch.setattr(module, "_default_lmstudio_home", lambda: default_home)

    detail = module._ensure_context_compactor_on_disk(
        plugin_src=plugin_src,
        lmstudio_home=home,
    )

    installed_manifest = json.loads((target_dir / "manifest.json").read_text(encoding="utf-8"))
    assert detail["previousStatus"] == "manifest-mismatch"
    assert detail["source"] == "default-lmstudio-home"
    assert detail["copied"] is True
    assert detail["ready"] is True
    assert installed_manifest["revision"] == source_manifest["revision"]
    assert target_bundle.read_text(encoding="utf-8") == "module.exports = { current: true };\n"
    sys.modules.pop("integrated_install", None)


def test_ensure_context_compactor_replaces_current_manifest_without_bundle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_installer_module()
    plugin_src = _make_context_compactor_source(tmp_path)
    home = tmp_path / "managed-lmstudio"
    default_home = tmp_path / "default-lmstudio"
    _plant_current_context_compactor_install(module, default_home, plugin_src)
    target_dir = module._context_compactor_install_path(home).parent
    target_dir.mkdir(parents=True)
    shutil.copy2(plugin_src / "manifest.json", target_dir / "manifest.json")
    (target_dir / "stale-only.txt").write_text("incomplete\n", encoding="utf-8")
    monkeypatch.setattr(module, "_default_lmstudio_home", lambda: default_home)

    detail = module._ensure_context_compactor_on_disk(
        plugin_src=plugin_src,
        lmstudio_home=home,
    )

    assert detail["previousStatus"] == "missing-production-bundle"
    assert detail["source"] == "default-lmstudio-home"
    assert detail["copied"] is True
    assert detail["ready"] is True
    assert (target_dir / ".lmstudio" / "production.js").is_file()
    assert not (target_dir / "stale-only.txt").exists()
    sys.modules.pop("integrated_install", None)


def test_ensure_context_compactor_rejects_source_tree_without_production_bundle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_installer_module()
    plugin_src = _make_context_compactor_source(tmp_path)
    home = tmp_path / "managed-lmstudio"
    empty_default = tmp_path / "empty-default-lmstudio"
    empty_default.mkdir()
    monkeypatch.setattr(module, "_default_lmstudio_home", lambda: empty_default)

    detail = module._ensure_context_compactor_on_disk(
        plugin_src=plugin_src,
        lmstudio_home=home,
    )

    assert detail["ready"] is False
    assert detail["source"] == "missing-current-production-install"
    assert detail["candidateStatuses"] == {"default-lmstudio-home": "missing-manifest"}
    assert not module._context_compactor_install_path(home).exists()
    sys.modules.pop("integrated_install", None)


def test_context_compactor_backup_cleanup_failure_keeps_new_install(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from installer import lmstudio_plugin_install

    module = _load_installer_module()
    plugin_src = _make_context_compactor_source(tmp_path)
    source_manifest = json.loads((plugin_src / "manifest.json").read_text(encoding="utf-8"))
    default_home = tmp_path / "default-lmstudio"
    default_dir = _plant_current_context_compactor_install(module, default_home, plugin_src)
    home = tmp_path / "managed-lmstudio"
    target_dir = module._context_compactor_install_path(home).parent
    target_bundle = target_dir / ".lmstudio" / "production.js"
    target_bundle.parent.mkdir(parents=True)
    (target_dir / "manifest.json").write_text(
        json.dumps({**source_manifest, "revision": source_manifest["revision"] - 1}),
        encoding="utf-8",
    )
    target_bundle.write_text("module.exports = { stale: true };\n", encoding="utf-8")
    (target_dir / "keep.txt").write_text("old backup evidence\n", encoding="utf-8")

    original_rmtree = lmstudio_plugin_install.shutil.rmtree

    def partially_fail_backup_cleanup(path, *args, **kwargs):
        candidate = Path(path)
        if candidate.name.startswith(".unreal-context-compactor-old-"):
            keep = candidate / "keep.txt"
            if keep.exists():
                keep.unlink()
            raise OSError("simulated partial backup cleanup failure")
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(
        lmstudio_plugin_install.shutil,
        "rmtree",
        partially_fail_backup_cleanup,
    )
    detail = lmstudio_plugin_install.ensure_current_plugin_install(
        source_dir=plugin_src,
        target_dir=target_dir,
        installed_candidates=[("default-lmstudio-home", default_dir)],
    )

    assert detail["ready"] is True
    assert detail["backupCleanup"]["pending"] is True
    assert "partial backup cleanup failure" in detail["backupCleanup"]["error"]
    assert json.loads((target_dir / "manifest.json").read_text(encoding="utf-8"))["revision"] == source_manifest["revision"]
    assert target_bundle.read_text(encoding="utf-8") == "module.exports = { current: true };\n"
    assert Path(detail["backupCleanup"]["path"]).exists()
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


def test_unreal_agent_dependency_probe_rejects_missing_sdk(tmp_path: Path) -> None:
    module = _load_installer_module()
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is unavailable")
    empty_agent = tmp_path / "lmstudio-unreal-agent-mcp"
    (empty_agent / "src").mkdir(parents=True)
    (empty_agent / "src" / "server.js").write_text("// fixture\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="UNREAL_AGENT_DEPENDENCY_MISSING") as raised:
        module._verify_unreal_agent_dependency(
            Path(node).resolve(),
            empty_agent,
            dependency_source="preinstalled_skip_deps",
        )

    assert "without --skip-deps" in str(raised.value)
    sys.modules.pop("integrated_install", None)


def test_rag_build_rejects_profiles_without_unreal_component(tmp_path: Path) -> None:
    result = _run(tmp_path, "--profile", "safe", "--build-rag")
    assert result.returncode == 1
    assert "--build-rag requires the unreal component" in result.stdout


def test_acknowledged_agent_mode_enables_all_unreal_authority(tmp_path: Path) -> None:
    lmstudio = tmp_path / "lmstudio"
    lmstudio.mkdir()
    (lmstudio / "settings.json").write_text(
        json.dumps(
            {
                "chat": {
                    "skipToolConfirmationPatterns": [
                        "keep-me",
                        "mcp/unreal-agent:*",
                        "mcp/unreal-rag:*",
                        "mcp/unreal-rag:unreal_architecture_reasoning",
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
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
    assert payload["unrealAgentDependency"]["ok"] is True
    assert payload["unrealAgentDependency"]["entrypoint"] == (
        "@modelcontextprotocol/sdk/server/index.js"
    )
    assert payload["unrealAgentDependency"]["source"] == "preinstalled_skip_deps"
    mcp = json.loads((tmp_path / "lmstudio" / "mcp.json").read_text(encoding="utf-8"))
    env = mcp["mcpServers"]["unreal-agent"]["env"]
    assert mcp["mcpServers"]["unreal-agent"]["args"] == [
        str(ROOT / "lmstudio-unreal-agent-mcp" / "src" / "direct-server.js")
    ]
    assert Path(env["PYTHON_EXE"]).resolve() == Path(sys.executable).resolve()
    assert {
        env[key]
        for key in (
            "ALLOW_WRITE",
            "ALLOW_COMMANDS",
            "ALLOW_UNREAL_BUILD",
        )
    } == {"1"}
    runtime_manifest_path = tmp_path / "lmstudio" / "config" / "control-runtime.json"
    assert not runtime_manifest_path.exists()
    assert "controlRuntimeManifest" not in payload
    assert "MCP_EXECUTION_MODE" not in env
    assert "CONTROL_RUNTIME_MANIFEST" not in env
    rag_env = mcp["mcpServers"]["unreal-rag"]["env"]
    assert "CONTROL_RUNTIME_MANIFEST" not in rag_env
    assert "CONTROL_RUNTIME_REQUIRED" not in rag_env
    assert "MCP_REQUIRE_PLAN_AUTH" not in env
    assert "VALIDATE_ON_WRITE" not in env
    settings = json.loads((lmstudio / "settings.json").read_text(encoding="utf-8"))
    assert settings["chat"]["skipToolConfirmationPatterns"] == ["keep-me"]
    assert any(
        "mcp/unreal-agent:*" in item
        for item in payload["safetyNormalizations"]
    )
    installed_runtime_manifest = (
        tmp_path
        / "lmstudio"
        / "extensions"
        / "plugins"
        / "codex"
        / "unreal-context-compactor"
        / "control-runtime.json"
    )
    assert not installed_runtime_manifest.exists()


def test_full_agent_install_keeps_compactor_independent_from_mcp_authority(tmp_path: Path) -> None:
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
    assert not any(key.startswith("MCP_CONTEXT_COMPACTOR_") for key in rag_env)
    assert "MCP_REQUIRE_CONTEXT_COMPACTOR_ACTIVE" not in rag_env
    runtime_env = entries["unreal-rag"]["env"]
    assert "CONTROL_RUNTIME_MANIFEST" not in runtime_env
    assert "CONTROL_RUNTIME_REQUIRED" not in runtime_env
    assert "CONTROL_RUNTIME_COMPONENT" not in rag_env
    assert "MCP_EXECUTION_MODE" not in entries["unreal-agent"]["env"]
    assert "CONTROL_RUNTIME_COMPONENT" not in entries["unreal-agent"]["env"]
    assert entries["unreal-rag"]["args"] == [str(ROOT / "scripts" / "unreal_rag_direct.py")]
    assert entries["unreal-agent"]["args"] == [str(ROOT / "lmstudio-unreal-agent-mcp" / "src" / "direct-server.js")]
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
    assert not any(key.startswith("MCP_CONTEXT_COMPACTOR_") for key in entries["unreal-rag"]["env"])
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
    assert mcp["mcpServers"]["unreal-rag"]["env"]["UNREAL_ENGINE_ROOT_ASSOCIATION"] == ""
    assert mcp["mcpServers"]["unreal-agent"]["env"]["UNREAL_ENGINE_ROOT_ASSOCIATION"] == ""


def test_custom_active_project_engine_is_mapped_and_runtime_commit_is_forwarded(
    tmp_path: Path,
) -> None:
    engine = tmp_path / "CustomEngine"
    (engine / "Engine" / "Source").mkdir(parents=True)
    project_dir = tmp_path / "projects" / "SourceBuildGame"
    project_dir.mkdir(parents=True)
    association = "{SOURCE-BUILD-IDENTITY}"
    project = project_dir / "SourceBuildGame.uproject"
    project.write_text(json.dumps({"FileVersion": 3, "EngineAssociation": association}), encoding="utf-8")

    result = _run(
        tmp_path,
        "--profile",
        "standard",
        "--skip-deps",
        "--active-project",
        str(project),
        "--engine-root",
        str(engine),
    )

    assert result.returncode == 0, result.stderr or result.stdout
    shared = json.loads(
        (tmp_path / "lmstudio" / "config" / "unreal-workspace.json").read_text(encoding="utf-8")
    )
    mcp = json.loads((tmp_path / "lmstudio" / "mcp.json").read_text(encoding="utf-8"))
    assert shared["engineRootsByAssociation"][association] == str(engine)
    assert (
        mcp["mcpServers"]["unreal-rag"]["env"]["UNREAL_ENGINE_ROOT_ASSOCIATION"]
        == association
    )
    assert (
        mcp["mcpServers"]["unreal-agent"]["env"]["UNREAL_ENGINE_ROOT_ASSOCIATION"]
        == association
    )
    assert "CONTROL_RUNTIME_GIT_COMMIT" not in mcp["mcpServers"]["unreal-rag"]["env"]
    assert "CONTROL_RUNTIME_GIT_COMMIT" not in mcp["mcpServers"]["unreal-agent"]["env"]
    assert "CONTROL_RUNTIME_EXPECTED_GIT_COMMIT" not in mcp["mcpServers"]["unreal-rag"]["env"]
    assert "CONTROL_RUNTIME_EXPECTED_GIT_COMMIT" not in mcp["mcpServers"]["unreal-agent"]["env"]


def test_custom_active_project_engine_fails_closed_without_an_exact_binding(tmp_path: Path) -> None:
    project_dir = tmp_path / "projects" / "SourceBuildGame"
    project_dir.mkdir(parents=True)
    project = project_dir / "SourceBuildGame.uproject"
    project.write_text(
        json.dumps({"FileVersion": 3, "EngineAssociation": "source-build-guid"}),
        encoding="utf-8",
    )

    result = _run(
        tmp_path,
        "--profile",
        "standard",
        "--skip-deps",
        "--active-project",
        str(project),
    )

    assert result.returncode == 1
    assert "ENGINE_ASSOCIATION_UNRESOLVED" in result.stdout


def test_selected_engine_writes_dynamic_index_config_for_unpinned_rag_mcp(tmp_path: Path) -> None:
    engine = tmp_path / "UE_5.10"
    (engine / "Engine" / "Source").mkdir(parents=True)
    build_version = engine / "Engine" / "Build" / "Build.version"
    build_version.parent.mkdir(parents=True, exist_ok=True)
    build_version.write_text(
        json.dumps({"MajorVersion": 5, "MinorVersion": 10}),
        encoding="utf-8",
    )

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
    assert shared["engineVersion"] == "5.10"
    assert shared["indexNamespace"] == "unreal510"
    managed_index = (
        tmp_path / "state" / "indexes" / "unreal510" / "rag.sqlite"
    ).resolve()
    assert Path(shared["indexPath"]).resolve() == managed_index
    assert mcp["mcpServers"]["unreal-rag"]["args"] == [
        str(ROOT / "scripts" / "unreal_rag_direct.py"),
        "--index",
        str(managed_index),
    ]
    assert mcp["mcpServers"]["unreal-rag"]["env"]["UNREAL_RAG_INDEX_PATH"] == str(
        managed_index
    )
    assert mcp["mcpServers"]["unreal-agent"]["env"]["UNREAL_RAG_INDEX_PATH"] == str(
        managed_index
    )
    assert json.loads(result.stdout)["ragReadiness"]["status"] == "missing"


def test_installer_keeps_nonstandard_shared_index_path(tmp_path: Path) -> None:
    module = _load_installer_module()
    shared = {"indexNamespace": "custom", "indexPath": "indexes/project-rag.sqlite"}
    engine = tmp_path / "UE_5.9"
    (engine / "Engine" / "Source").mkdir(parents=True)

    module._sync_installer_index_settings(shared, engine)
    sys.modules.pop("integrated_install", None)

    assert shared == {"indexNamespace": "custom", "indexPath": "indexes/project-rag.sqlite"}


def test_installer_managed_index_is_stable_across_versioned_package_roots(
    tmp_path: Path,
) -> None:
    module = _load_installer_module()
    shared = {"indexNamespace": "unreal58", "indexPath": "data/unreal58/rag.sqlite"}
    engine = tmp_path / "UE_5.8"
    (engine / "Engine" / "Source").mkdir(parents=True)

    module._sync_installer_index_settings(
        shared,
        engine,
        state_home=tmp_path / "state",
    )
    sys.modules.pop("integrated_install", None)

    assert Path(shared["indexPath"]).resolve() == (
        tmp_path / "state" / "indexes" / "unreal58" / "rag.sqlite"
    ).resolve()
    assert "Evidence-First-Integrated" not in shared["indexPath"]


def test_managed_index_migration_requires_query_level_readiness(tmp_path: Path) -> None:
    module = _load_installer_module()
    source = tmp_path / "packages" / "old" / "data" / "unreal58" / "rag.sqlite"
    source.parent.mkdir(parents=True)
    with sqlite3.connect(source) as connection:
        connection.execute("create table chunks(chunk_id text primary key, text text)")
        connection.execute("insert into chunks values ('one', 'cinematic')")
        connection.execute("create virtual table chunks_fts using fts5(text)")
        connection.execute("insert into chunks_fts values ('cinematic')")
    (source.parent / "build_manifest.json").write_text(
        json.dumps({"engineVersion": "5.8"}),
        encoding="utf-8",
    )
    target = tmp_path / "state" / "indexes" / "unreal58" / "rag.sqlite"

    migrated = module._migrate_managed_rag_index(
        target,
        [source],
        dry_run=False,
    )
    sys.modules.pop("integrated_install", None)

    assert migrated["ready"] is True
    assert migrated["action"] == "migrated"
    assert migrated["querySmoke"] == {"chunks": True, "chunksFts": True}
    assert migrated["hardLinkedFiles"] + migrated["copiedFiles"] == 2
    assert target.is_file()


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


@pytest.mark.parametrize("tier", ["lite", "standard", "full"])
def test_rag_build_uses_transactional_direct_python_plan(tmp_path: Path, tier: str) -> None:
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
    payload = json.loads(result.stdout[result.stdout.find("{") :])
    assert payload["ragBuild"]["tier"] == tier
    assert payload["ragBuild"]["transactional"] is True
    assert "collect_unreal_projects.py" in combined
    assert "direct_rag_build_generation.py" in combined
    assert "run_index_pipeline.ps1" not in combined
    assert "warm_symbol_cache.py" not in combined
    assert "rag_search.py" not in combined
    assert "pwsh" not in combined.lower()
    if tier == "lite":
        assert "collect_unreal_symbols.py" not in combined
        assert "collect_unreal_source.py" not in combined
    else:
        assert "collect_unreal_symbols.py" in combined
        assert ("collect_unreal_source.py" in combined) is (tier == "full")


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


@pytest.mark.parametrize(
    ("tier", "has_symbols", "has_source"),
    [("lite", False, False), ("standard", True, False), ("full", True, True)],
)
def test_direct_rag_plan_owns_tier_pruning_and_collector_order(
    tmp_path: Path,
    tier: str,
    has_symbols: bool,
    has_source: bool,
) -> None:
    from installer.direct_rag_build import create_direct_rag_build_plan

    guidelines = tmp_path / "guidelines"
    game_design = tmp_path / "game-design"
    guidelines.mkdir()
    game_design.mkdir()
    plan = create_direct_rag_build_plan(
        python_executable=Path(sys.executable),
        index_dir=tmp_path / "index" / "rag.sqlite",
        tier=tier,
        project_roots=[tmp_path / "projects"],
        active_project=None,
        engine_root=tmp_path / "UE_5.8",
        guidelines_root=guidelines,
        game_design_root=game_design,
        dry_run=True,
    )

    names = [step.name for step in plan.steps]
    assert names[:3] == ["collect-guidelines", "collect-game-design", "collect-projects"]
    assert names[-1] == "build-index"
    assert ("collect-engine-public-symbols" in names) is has_symbols
    assert ("collect-engine-source" in names) is has_source
    assert ("raw_source.jsonl" in plan.prune_files) is (tier != "full")
    if tier == "lite":
        assert "raw_symbols.jsonl" in plan.prune_files
    assert "run_index_pipeline.ps1" not in " ".join(
        argument for step in plan.steps for argument in step.command
    )


def _write_tiny_unreal_fixture(tmp_path: Path) -> tuple[Path, Path]:
    engine = tmp_path / "UE_5.8"
    engine_header = engine / "Engine" / "Source" / "Runtime" / "Core" / "Public" / "TinyEngineType.h"
    engine_header.parent.mkdir(parents=True)
    engine_header.write_text(
        "#pragma once\nUSTRUCT()\nstruct FTinyEngineType { GENERATED_BODY() };\n",
        encoding="utf-8",
    )
    (engine_header.parents[1] / "Core.Build.cs").write_text(
        "using UnrealBuildTool; public class Core : ModuleRules { public Core(ReadOnlyTargetRules T) : base(T) {} }\n",
        encoding="utf-8",
    )

    project = tmp_path / "TinyProject"
    descriptor = project / "TinyProject.uproject"
    source = project / "Source" / "TinyProject"
    (source / "Public").mkdir(parents=True)
    descriptor.write_text(
        json.dumps({"FileVersion": 3, "Modules": [{"Name": "TinyProject", "Type": "Runtime"}]}),
        encoding="utf-8",
    )
    (source / "TinyProject.Build.cs").write_text(
        "using UnrealBuildTool; public class TinyProject : ModuleRules { public TinyProject(ReadOnlyTargetRules T) : base(T) {} }\n",
        encoding="utf-8",
    )
    (source / "Public" / "TinyActor.h").write_text(
        "#pragma once\nUCLASS()\nclass UTinyActor { GENERATED_BODY() };\n",
        encoding="utf-8",
    )
    return engine, descriptor


def test_direct_rag_plan_builds_and_commits_a_real_current_index(tmp_path: Path) -> None:
    from installer.direct_rag_build import create_direct_rag_build_plan

    engine, descriptor = _write_tiny_unreal_fixture(tmp_path)
    index_dir = tmp_path / "index"
    index_dir.mkdir()
    (index_dir / "raw_source.jsonl").write_text('{"id":"stale"}\n', encoding="utf-8")
    plan = create_direct_rag_build_plan(
        python_executable=Path(sys.executable),
        index_dir=index_dir,
        tier="standard",
        project_roots=[descriptor.parent],
        active_project=descriptor,
        engine_root=engine,
        guidelines_root=tmp_path / "no-guidelines",
        game_design_root=tmp_path / "no-game-design",
    )
    try:
        for step in plan.steps:
            completed = subprocess.run(
                step.command,
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
            assert completed.returncode == 0, f"{step.name}: {completed.stdout}\n{completed.stderr}"
        plan.commit()
    finally:
        plan.discard()

    assert not (index_dir / "raw_source.jsonl").exists()
    for name in (
        "raw_projects.jsonl",
        "raw_symbols.jsonl",
        "raw_project_symbols.jsonl",
        "raw_project_profiles.jsonl",
        "raw_project_architecture.jsonl",
        "rag.sqlite",
        "build_manifest.json",
    ):
        assert (index_dir / name).is_file(), name
    with sqlite3.connect(index_dir / "rag.sqlite") as connection:
        assert connection.execute("select count(*) from chunks").fetchone()[0] > 0
    manifest = json.loads((index_dir / "build_manifest.json").read_text(encoding="utf-8"))
    assert str(plan.stage_dir) not in json.dumps(manifest)


def test_direct_rag_plan_preserves_custom_engine_provenance_in_fresh_index(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import installer.direct_rag_build as direct_build

    engine, descriptor = _write_tiny_unreal_fixture(tmp_path)
    descriptor.write_text(
        json.dumps({"FileVersion": 3, "EngineAssociation": "StudioFork"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        direct_build,
        "project_engine_version",
        lambda project, _workspace: {
            "ok": True,
            "project": str(project),
            "engineVersion": "5.8",
            "engineAssociation": "StudioFork",
            "engineRoot": str(engine),
        },
    )
    index_dir = tmp_path / "custom-index"
    plan = direct_build.create_direct_rag_build_plan(
        python_executable=Path(sys.executable),
        index_dir=index_dir,
        tier="standard",
        project_roots=[descriptor],
        active_project=descriptor,
        engine_root=engine,
        guidelines_root=tmp_path / "no-guidelines",
        game_design_root=tmp_path / "no-game-design",
    )
    try:
        for step in plan.steps:
            completed = subprocess.run(
                step.command,
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
            assert completed.returncode == 0, f"{step.name}: {completed.stdout}\n{completed.stderr}"
        plan.commit()
    finally:
        plan.discard()

    manifest = json.loads((index_dir / "build_manifest.json").read_text(encoding="utf-8"))
    assert manifest["engineVersion"] == "5.8"
    assert manifest["engineAssociation"] == "StudioFork"
    assert plan.engine_association == "StudioFork"


def test_direct_rag_plan_excludes_projects_owned_by_another_engine(
    tmp_path: Path,
) -> None:
    from installer.direct_rag_build import create_direct_rag_build_plan

    engine, selected = _write_tiny_unreal_fixture(tmp_path)
    selected.write_text(json.dumps({"EngineAssociation": "5.8"}), encoding="utf-8")
    other = selected.parent / "Other.uproject"
    other.write_text(json.dumps({"EngineAssociation": "5.7"}), encoding="utf-8")

    plan = create_direct_rag_build_plan(
        python_executable=Path(sys.executable),
        index_dir=tmp_path / "index",
        tier="standard",
        project_roots=[selected.parent],
        active_project=selected,
        engine_root=engine,
        guidelines_root=tmp_path / "no-guidelines",
        game_design_root=tmp_path / "no-game-design",
        dry_run=True,
    )

    assert plan.included_projects == (selected.resolve(),)
    assert plan.excluded_projects == (other.resolve(),)
    collect = next(step for step in plan.steps if step.name == "collect-project-set")
    assert str(selected.resolve()) in collect.command
    assert str(other.resolve()) not in collect.command


def test_standard_plan_collects_every_included_exact_project(tmp_path: Path) -> None:
    from installer.direct_rag_build import create_direct_rag_build_plan

    engine, project_a = _write_tiny_unreal_fixture(tmp_path)
    project_a.write_text(json.dumps({"EngineAssociation": "5.8"}), encoding="utf-8")
    project_b = project_a.parent / "SecondProject.uproject"
    project_b.write_text(json.dumps({"EngineAssociation": "5.8"}), encoding="utf-8")

    plan = create_direct_rag_build_plan(
        python_executable=Path(sys.executable),
        index_dir=tmp_path / "index",
        tier="standard",
        project_roots=[project_a.parent],
        active_project=project_a,
        engine_root=engine,
        guidelines_root=tmp_path / "no-guidelines",
        game_design_root=tmp_path / "no-game-design",
        dry_run=True,
    )

    assert set(plan.included_projects) == {project_a.resolve(), project_b.resolve()}
    names = [step.name for step in plan.steps]
    assert "collect-project-set" in names
    assert not any(name.startswith("collect-active-project-") for name in names)
    collect = next(step for step in plan.steps if step.name == "collect-project-set")
    assert collect.command.count("--project") == 2
    assert str(project_a.resolve()) in collect.command
    assert str(project_b.resolve()) in collect.command


def test_project_set_collector_preserves_same_folder_descriptor_identity(
    tmp_path: Path,
) -> None:
    shared = tmp_path / "Shared"
    source = shared / "Source" / "Shared" / "Public"
    source.mkdir(parents=True)
    project_a = shared / "GameA.uproject"
    project_b = shared / "GameB.uproject"
    for descriptor, module in ((project_a, "GameA"), (project_b, "GameB")):
        descriptor.write_text(
            json.dumps(
                {
                    "EngineAssociation": "5.8",
                    "Modules": [{"Name": module, "Type": "Runtime"}],
                }
            ),
            encoding="utf-8",
        )
    (source.parent / "Shared.Build.cs").write_text(
        "using UnrealBuildTool; public class Shared : ModuleRules "
        "{ public Shared(ReadOnlyTargetRules T) : base(T) {} }\n",
        encoding="utf-8",
    )
    (source / "SharedActor.h").write_text(
        "#pragma once\nUCLASS()\nclass USharedActor { GENERATED_BODY() };\n",
        encoding="utf-8",
    )
    stage = tmp_path / "stage"

    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "direct_rag_project_set.py"),
            "--workspace", str(ROOT),
            "--out-dir", str(stage),
            "--project", str(project_a),
            "--project", str(project_b),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    for name in (
        "raw_projects.jsonl",
        "raw_project_profiles.jsonl",
        "raw_project_architecture.jsonl",
        "raw_project_symbols.jsonl",
    ):
        rows = [
            json.loads(line)
            for line in (stage / name).read_text(encoding="utf-8").splitlines()
        ]
        by_project = {
            project: {row["id"] for row in rows if row["metadata"]["project"] == project}
            for project in ("GameA", "GameB")
        }
        assert all(by_project.values()), name
        assert by_project["GameA"].isdisjoint(by_project["GameB"]), name
        assert {row["metadata"]["project_root"] for row in rows} == {str(shared.resolve())}
    assert len(list((stage / "project_architecture").glob("*/project_architecture.json"))) == 2
    assert not list(stage.glob(".project-collection-*"))


def test_standard_plan_builds_both_same_binding_projects_end_to_end(
    tmp_path: Path,
) -> None:
    from installer.direct_rag_build import create_direct_rag_build_plan

    engine, project_a = _write_tiny_unreal_fixture(tmp_path)
    project_a.write_text(json.dumps({"EngineAssociation": "5.8"}), encoding="utf-8")
    project_b = project_a.parent / "SecondProject.uproject"
    project_b.write_text(json.dumps({"EngineAssociation": "5.8"}), encoding="utf-8")
    index_dir = tmp_path / "index"
    plan = create_direct_rag_build_plan(
        python_executable=Path(sys.executable),
        index_dir=index_dir,
        tier="standard",
        project_roots=[project_a.parent],
        active_project=project_a,
        engine_root=engine,
        guidelines_root=tmp_path / "no-guidelines",
        game_design_root=tmp_path / "no-game-design",
    )
    try:
        for step in plan.steps:
            completed = subprocess.run(
                step.command,
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
            assert completed.returncode == 0, (
                f"{step.name}: {completed.stdout}\n{completed.stderr}"
            )
        plan.commit()
    finally:
        plan.discard()

    for name in (
        "raw_projects.jsonl",
        "raw_project_profiles.jsonl",
        "raw_project_architecture.jsonl",
        "raw_project_symbols.jsonl",
    ):
        rows = [
            json.loads(line)
            for line in (index_dir / name).read_text(encoding="utf-8").splitlines()
        ]
        assert {row["metadata"]["project"] for row in rows} == {
            "TinyProject",
            "SecondProject",
        }, name
    with sqlite3.connect(index_dir / "rag.sqlite") as connection:
        projects = {
            row[0]
            for row in connection.execute(
                "select distinct project from chunks where project <> ''"
            )
        }
    assert {"TinyProject", "SecondProject"} <= projects


def test_failed_direct_rag_stage_leaves_prior_inputs_and_index_byte_stable(tmp_path: Path) -> None:
    from installer.direct_rag_build import create_direct_rag_build_plan

    _engine, descriptor = _write_tiny_unreal_fixture(tmp_path)
    index_dir = tmp_path / "index"
    index_dir.mkdir()
    old_raw = b'{"id":"prior"}\n'
    old_index = b"prior-index-bytes"
    (index_dir / "raw_projects.jsonl").write_bytes(old_raw)
    (index_dir / "rag.sqlite").write_bytes(old_index)
    plan = create_direct_rag_build_plan(
        python_executable=Path(sys.executable),
        index_dir=index_dir,
        tier="lite",
        project_roots=[descriptor.parent],
        active_project=descriptor,
        engine_root=None,
        guidelines_root=tmp_path / "no-guidelines",
        game_design_root=tmp_path / "no-game-design",
    )
    try:
        first = subprocess.run(plan.steps[0].command, cwd=str(ROOT), check=False, timeout=60)
        assert first.returncode == 0
        failed = subprocess.run([sys.executable, "-c", "raise SystemExit(9)"], check=False)
        assert failed.returncode == 9
    finally:
        plan.discard()

    assert (index_dir / "raw_projects.jsonl").read_bytes() == old_raw
    assert (index_dir / "rag.sqlite").read_bytes() == old_index
    assert not plan.stage_dir.exists()
