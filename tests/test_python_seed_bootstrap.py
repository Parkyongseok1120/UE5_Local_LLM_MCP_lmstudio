from __future__ import annotations

import json
import os
import platform
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "installer" / "runtime-manifest.json").read_text(encoding="utf-8"))
POSIX_SEED = ROOT / "installer" / "bootstrap_python.sh"
WINDOWS_SEED = ROOT / "installer" / "bootstrap_python.ps1"


def test_posix_seed_pins_match_runtime_manifest() -> None:
    source = POSIX_SEED.read_text(encoding="utf-8")
    python = MANIFEST["runtimes"]["python"]
    uv = MANIFEST["runtimes"]["uv"]

    assert re.search(rf"^PYTHON_VERSION={re.escape(python['version'])}$", source, re.MULTILINE)
    assert re.search(rf"^UV_VERSION={re.escape(uv['version'])}$", source, re.MULTILINE)
    for asset in uv["assets"]:
        if asset["platform"] not in {"darwin", "linux"}:
            continue
        assert asset["filename"] in source
        assert asset["sha256"] in source


def test_seed_helpers_preserve_runtime_security_contract() -> None:
    posix = POSIX_SEED.read_text(encoding="utf-8")
    windows = WINDOWS_SEED.read_text(encoding="utf-8")
    combined = f"{posix}\n{windows}"

    assert "--skip-runtime-bootstrap" in posix
    assert "--skip-runtime-bootstrap" in windows
    assert "SHA-256 mismatch" in posix
    assert "SHA-256 mismatch" in windows
    assert "runtime-manifest.json" in windows
    assert "UV_PYTHON_INSTALL_DIR" in posix
    assert "UV_PYTHON_INSTALL_DIR" in windows
    assert "python.org/downloads/windows" in windows
    assert "Get-FileHash" not in windows
    assert "Expand-Archive" not in windows
    assert "winget" not in combined.lower()
    assert "choco" not in combined.lower()


def test_windows_seed_parses_in_available_powershell() -> None:
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if not powershell:
        pytest.skip("PowerShell is unavailable")
    command = (
        "$errors=$null; $tokens=$null; "
        f"[System.Management.Automation.Language.Parser]::ParseFile('{WINDOWS_SEED}',"
        "[ref]$tokens,[ref]$errors)|Out-Null; "
        "if($errors.Count){$errors|ForEach-Object{[Console]::Error.WriteLine($_)}; exit 1}"
    )
    result = subprocess.run(
        [powershell, "-NoLogo", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_windows_seed_skip_flag_prevents_download() -> None:
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if not powershell:
        pytest.skip("PowerShell is unavailable")
    result = subprocess.run(
        [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(WINDOWS_SEED),
            "-InstallerPath",
            str(ROOT / "install.py"),
            "--skip-runtime-bootstrap",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 127
    assert "forbids the automatic Python bootstrap" in result.stderr


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell contract")
def test_posix_seed_parses_and_skip_flag_prevents_download() -> None:
    shell = shutil.which("sh")
    assert shell, "supported POSIX hosts must provide sh"
    parsed = subprocess.run([shell, "-n", str(POSIX_SEED)], capture_output=True, text=True, check=False)
    assert parsed.returncode == 0, parsed.stderr

    skipped = subprocess.run(
        [shell, str(POSIX_SEED), str(ROOT / "install.py"), "--skip-runtime-bootstrap"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert skipped.returncode == 127
    assert "forbids an automatic download" in skipped.stderr


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell contract")
def test_posix_seed_reuses_pinned_uv_and_dispatches_installer(tmp_path: Path) -> None:
    shell = shutil.which("sh")
    assert shell, "supported POSIX hosts must provide sh"
    state_home = tmp_path / "state home"
    uv = state_home / "runtimes" / "uv" / "uv"
    fake_python = state_home / "runtimes" / "python" / "python3.12"
    uv.parent.mkdir(parents=True)
    fake_python.parent.mkdir(parents=True)
    architecture = (
        "arm64"
        if platform.machine().lower() in {"arm64", "aarch64"}
        else "x64"
    )
    uv.write_text(
        f"""#!/bin/sh
if [ "$1" = "--version" ]; then
  echo "uv 0.12.1 (test build metadata)"
  exit 0
fi
if [ "$1 $2" = "python install" ]; then
  exit 0
fi
if [ "$1 $2" = "python find" ]; then
  echo "{fake_python}"
  exit 0
fi
exit 91
""",
        encoding="utf-8",
    )
    fake_python.write_text(
        f"""#!/bin/sh
if [ "$1" = "-c" ]; then
  case "$2" in
    *sys.version*) echo "3.12.13" ;;
    *platform.machine*) echo "{architecture}" ;;
    *) exit 92 ;;
  esac
  exit 0
fi
echo "SEED_DISPATCH=$*"
exit 0
""",
        encoding="utf-8",
    )
    for executable in (uv, fake_python):
        executable.chmod(
            executable.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )

    result = subprocess.run(
        [
            shell,
            str(POSIX_SEED),
            str(ROOT / "install.py"),
            "--state-home",
            str(state_home),
            "--version",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "SEED_DISPATCH=" in result.stdout
    assert "--state-home" in result.stdout
    assert "--version" in result.stdout
    assert "Installing pinned uv" not in result.stderr


def test_windows_launcher_dispatches_missing_python_to_seed_helper() -> None:
    launcher = (ROOT / "INSTALL.bat").read_text(encoding="utf-8")
    assert "installer\\bootstrap_python.ps1" in launcher
    assert "-InstallerPath \"%~dp0install.py\" %*" in launcher
    assert "set \"INSTALL_EXIT=%ERRORLEVEL%\"" in launcher
    assert "Bootstrapping managed Python 3.12... 1>&2" in launcher


def test_posix_launcher_dispatches_missing_python_to_seed_helper() -> None:
    launcher = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert "installer/bootstrap_python.sh" in launcher
    assert 'exec /bin/sh "$python_seed" "$INSTALL_PY" "$@"' in launcher
