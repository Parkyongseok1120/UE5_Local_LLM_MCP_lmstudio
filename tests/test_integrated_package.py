from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "build_integrated_package.py"


def _legacy_stdout_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "cp1252"
    return env


def _plant_fake_lms(lmstudio_home: Path) -> None:
    """Hermetic LMS stub so package smoke installs do not require a real LM Studio app."""
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
        script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _build(tmp_path: Path, name: str, *, legacy_stdout: bool = False) -> tuple[Path, Path]:
    output = tmp_path / name
    archive = tmp_path / f"{name}.zip"
    result = subprocess.run(
        [sys.executable, str(BUILDER), "--output", str(output), "--zip", str(archive)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=180,
        env=_legacy_stdout_env() if legacy_stdout else None,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return output, archive


def _load_builder_module():
    spec = importlib.util.spec_from_file_location("build_integrated_package", BUILDER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_include_index_uses_configured_index_path(tmp_path: Path) -> None:
    source = tmp_path / "portable-source"
    index = source / "data" / "unreal510" / "rag.sqlite"
    index.parent.mkdir(parents=True)
    index.write_bytes(b"fixture")
    config_dir = source / "config"
    config_dir.mkdir()
    (config_dir / "workspace.json").write_text(
        json.dumps({"indexNamespace": "unreal510", "indexPath": "data/unreal510/rag.sqlite"}),
        encoding="utf-8",
    )
    builder = _load_builder_module()
    relative = builder._included_index_relative(source)

    assert relative == Path("data/unreal510/rag.sqlite")
    assert builder._include(relative, include_index=True, index_relative=relative) is True
    assert builder._include(
        Path("data/unreal58/rag.sqlite"),
        include_index=True,
        index_relative=relative,
    ) is False


def test_include_index_uses_the_supplied_source_not_another_mcp_workspace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "portable-source"
    other = tmp_path / "running-mcp-workspace"
    for root, namespace in ((source, "unreal510"), (other, "unreal58")):
        config = root / "config"
        config.mkdir(parents=True)
        (config / "workspace.json").write_text(
            json.dumps({"indexPath": f"data/{namespace}/rag.sqlite"}),
            encoding="utf-8",
        )
    monkeypatch.setenv("UNREAL58_ROOT", str(other))

    builder = _load_builder_module()

    assert builder._included_index_relative(source) == Path("data/unreal510/rag.sqlite")


def test_package_has_all_platform_launchers_and_no_local_state(tmp_path: Path) -> None:
    output, archive = _build(tmp_path, "portable 한글 one", legacy_stdout=True)
    for relative in (
        "INSTALL.bat",
        "install.sh",
        "install.py",
        "skills/evidence-first-code-audit/SKILL.md",
        "skills/evidence-first-code-audit/assets/lmstudio-evidence-first.preset.json",
        "skills/evidence-first-code-audit/scripts/evidence_first_mcp.py",
        "config/evidence_first_benchmark_cases.json",
        "docs/Release_Notes_1_3_0_RC3.md",
        "docs/Release_Notes_1_3_0_Beta4.md",
        "scripts/architecture_reasoning.py",
        "scripts/build_symbol_graph.py",
        "scripts/change_impact_contract.py",
        "scripts/code_generation_contract.py",
        "scripts/symbol_graph.py",
        "scripts/architecture_portfolio.py",
        "scripts/asset_migration_contract.py",
        "scripts/task_continuity.py",
        "scripts/project_name_resolver.py",
        "scripts/semantic_ambiguity.py",
        "scripts/target_resolver.py",
        "scripts/task_autonomy_supervisor.py",
        "scripts/feature_intent_contract.py",
        "scripts/runtime_oracle.py",
        "scripts/runtime_experiment_runner.py",
        "scripts/automation_report_parser.py",
        "scripts/unreal_insights_analyzer.py",
        "scripts/patch_candidate_comparison.py",
        "scripts/patch_candidate_sandbox.py",
        "scripts/semantic_refactor_guard.py",
        "scripts/phase_tool_router.py",
        "scripts/approve_feature_intent.py",
        "scripts/mutation_semantic_guard.py",
        "scripts/unreal_api_denylist.py",
        "scripts/unreal_source_extensions.py",
        "scripts/installer_support/Install-PathHelpers.ps1",
        "scripts/manage_runtime_manifest.py",
        "installer/runtime-manifest.json",
        "lmstudio-unreal-agent-mcp/src/server.js",
        "lmstudio-unreal-agent-mcp/src/command-policy.js",
        "lmstudio-unreal-agent-mcp/src/resolve-project-name-cli.js",
        "lmstudio-unreal-agent-mcp/src/filesystem-path-identity.js",
        "lmstudio-unreal-agent-mcp/src/recovery-log-contract.js",
        "lmstudio-unreal-agent-mcp/src/route-watcher.js",
        "lmstudio-unreal-agent-mcp/src/mutation-semantic-guard.js",
        "lmstudio-context-compactor-plugin/package-lock.json",
        "package-manifest.json",
    ):
        assert (output / relative).is_file(), relative
    package_manifest = json.loads(
        (output / "package-manifest.json").read_text(encoding="utf-8")
    )
    expected_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert package_manifest["sourceGitCommit"] == expected_commit
    identity_env = os.environ.copy()
    identity_env.pop("CONTROL_RUNTIME_GIT_COMMIT", None)
    identity_env["PYTHONDONTWRITEBYTECODE"] = "1"
    packaged_identity = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json,sys; sys.path.insert(0,'scripts'); "
                "from control_runtime_identity import build_runtime_manifest; "
                "print(json.dumps(build_runtime_manifest('.')))"
            ),
        ],
        cwd=output,
        capture_output=True,
        text=True,
        check=True,
        env=identity_env,
    )
    components = json.loads(packaged_identity.stdout)["components"]
    assert {value["gitCommit"] for value in components.values()} == {expected_commit}
    public_launchers = {
        path.relative_to(output).as_posix()
        for path in output.rglob("*")
        if path.is_file()
        and (
            path.suffix.lower() in {".bat", ".cmd", ".command"}
            or path.name == "install.sh"
        )
    }
    assert public_launchers == {"INSTALL.bat", "install.sh"}
    assert {path.name for path in (output / "installer").iterdir()} == {
        "README.md",
        "__init__.py",
        "bootstrap_runtimes.py",
        "manifest.json",
        "runtime-manifest.json",
    }
    packaged_installer_manifest = json.loads((output / "installer" / "manifest.json").read_text(encoding="utf-8"))
    assert packaged_installer_manifest["productVersion"] == "1.3.0 RC3"
    assert packaged_installer_manifest["version"] == "2.1.5"
    assert (output / "INSTALL.bat").read_bytes() == (ROOT / "INSTALL.bat").read_bytes()
    source_launcher = (ROOT / "install.sh").read_bytes()
    expected_launcher = source_launcher.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    assert (output / "install.sh").read_bytes() == expected_launcher
    assert b"\r" not in (output / "install.sh").read_bytes()
    windows_launcher = (output / "INSTALL.bat").read_text(encoding="utf-8")
    posix_launcher = (output / "install.sh").read_text(encoding="utf-8")
    assert "pause >nul" in windows_launcher
    assert "Python 3.10 or newer" in windows_launcher
    assert "Python 3.10+" in posix_launcher
    assert "python3.10" in posix_launcher
    assert "sudo apt-get install -y python3 ca-certificates" in posix_launcher
    if os.name != "nt":
        launcher_help = subprocess.run(
            [str(output / "install.sh"), "--help"],
            cwd=output,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert launcher_help.returncode == 0, launcher_help.stderr or launcher_help.stdout
    portable_help = (output / "PORTABLE-INSTALL.md").read_text(encoding="utf-8")
    assert "Ubuntu 22.04/24.04 with glibc" in portable_help
    assert "pinned by SHA-256" in portable_help
    forbidden = {".git", ".venv", "node_modules", "tests", "Reports", ".agent"}
    assert not any(forbidden.intersection(path.relative_to(output).parts) for path in output.rglob("*"))
    assert not any(path.suffix in {".sqlite", ".db"} for path in output.rglob("*"))
    assert str(Path.home()) not in "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in output.rglob("*")
        if path.is_file() and path.stat().st_size < 4 * 1024 * 1024
    )
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
    assert any(name.endswith("/install.sh") for name in names)
    assert not any(name.endswith("/INSTALL.command") for name in names)
    assert not any("node_modules" in name or "/.git/" in name for name in names)
    lmstudio_home = tmp_path / "isolated lmstudio"
    lmstudio_home.mkdir(parents=True, exist_ok=True)
    _plant_fake_lms(lmstudio_home)
    installed = subprocess.run(
        [
            sys.executable,
            str(output / "install.py"),
            "--profile",
            "safe",
            "--yes",
            "--skip-deps",
            "--skip-runtime-bootstrap",
            "--codex-home",
            str(tmp_path / "isolated codex"),
            "--lmstudio-home",
            str(lmstudio_home),
            "--state-home",
            str(tmp_path / "isolated state"),
        ],
        cwd=str(output),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert installed.returncode == 0, installed.stderr or installed.stdout
    assert json.loads(installed.stdout)["mcpSmoke"]["ok"] is True


def test_manifest_inventory_and_zip_are_reproducible(tmp_path: Path) -> None:
    first, first_zip = _build(tmp_path, "first")
    second, second_zip = _build(tmp_path, "second")
    first_manifest = json.loads((first / "package-manifest.json").read_text(encoding="utf-8"))
    second_manifest = json.loads((second / "package-manifest.json").read_text(encoding="utf-8"))
    assert first_manifest == second_manifest
    assert hashlib.sha256(first_zip.read_bytes()).hexdigest() == hashlib.sha256(second_zip.read_bytes()).hexdigest()


def test_builder_rejects_source_or_nested_destinations(tmp_path: Path) -> None:
    for destination in (ROOT, ROOT / "dist" / "unsafe"):
        result = subprocess.run(
            [sys.executable, str(BUILDER), "--output", str(destination)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 1
        assert "disjoint" in result.stdout


def test_builder_error_json_is_safe_with_legacy_stdout_encoding(tmp_path: Path) -> None:
    missing_source = tmp_path / "없는 source"
    result = subprocess.run(
        [
            sys.executable,
            str(BUILDER),
            "--source",
            str(missing_source),
            "--output",
            str(tmp_path / "output"),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=30,
        env=_legacy_stdout_env(),
    )
    assert result.returncode == 1
    assert str(missing_source) in json.loads(result.stdout)["error"]
