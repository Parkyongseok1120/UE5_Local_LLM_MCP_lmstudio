#!/usr/bin/env python
"""POSIX install.sh Python launcher selection tests."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALL_SH = ROOT / "install.sh"


def _write_fake_python(path: Path, version: tuple[int, int, int], *, fail: bool = False) -> None:
    major, minor, patch = version
    # Fake interpreter: honors -c probes used by install.sh, then prints marker.
    script = f"""#!/bin/sh
if [ "$1" = "-c" ]; then
  code=$2
  case "$code" in
    *'sys.version_info >= (3, 10)'*)
      if [ {major} -gt 3 ] || {{ [ {major} -eq 3 ] && [ {minor} -ge 10 ]; }}; then
        exit 0
      fi
      exit 1
      ;;
    *'sys.version_info[:3]'*|*'print(\"%d.%d.%d\"'*|*'print("%d.%d.%d"'*)
      echo "{major}.{minor}.{patch}"
      exit 0
      ;;
  esac
  exit 1
fi
if [ "{int(fail)}" = "1" ]; then
  echo "unexpected exec" >&2
  exit 99
fi
echo "SELECTED=$(basename "$0")"
# Consume install.py path and remaining args without running the real installer.
exit 0
"""
    path.write_text(script, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _run_launcher(fake_bin: Path, *, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    # Prefer the fake bin for python* discovery, but keep system dirs so `sh`
    # and core utilities remain available.
    env["PATH"] = os.pathsep.join(
        [str(fake_bin), "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    )
    env.pop("PYTHON", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["/bin/sh", str(INSTALL_SH), "--help"],
        cwd=str(ROOT),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX install.sh launcher")
def test_prefers_versioned_python_over_old_python3(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_python(fake_bin / "python3", (3, 9, 6), fail=True)
    _write_fake_python(fake_bin / "python3.13", (3, 13, 2))
    result = _run_launcher(fake_bin)
    assert result.returncode == 0, result.stderr
    assert "SELECTED=python3.13" in result.stdout


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX install.sh launcher")
def test_selects_only_available_python311(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_python(fake_bin / "python3.11", (3, 11, 9))
    result = _run_launcher(fake_bin)
    assert result.returncode == 0, result.stderr
    assert "SELECTED=python3.11" in result.stdout


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX install.sh launcher")
def test_python_env_override_wins_when_valid(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    override = fake_bin / "custom-python"
    _write_fake_python(override, (3, 12, 1))
    _write_fake_python(fake_bin / "python3.12", (3, 12, 8), fail=True)
    result = _run_launcher(fake_bin, env_extra={"PYTHON": str(override)})
    assert result.returncode == 0, result.stderr
    assert "SELECTED=custom-python" in result.stdout


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX install.sh launcher")
def test_old_python_env_is_skipped_for_newer_path(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    old = fake_bin / "old-python"
    _write_fake_python(old, (3, 9, 0), fail=True)
    _write_fake_python(fake_bin / "python3.12", (3, 12, 4))
    result = _run_launcher(fake_bin, env_extra={"PYTHON": str(old)})
    assert result.returncode == 0, result.stderr
    assert "SELECTED=python3.12" in result.stdout


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX install.sh launcher")
def test_no_usable_python_exits_127(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_python(fake_bin / "python3", (3, 9, 18), fail=True)
    result = _run_launcher(fake_bin, env_extra={"HOME": str(tmp_path / "empty-home")})
    assert result.returncode == 127
    assert "Python 3.10+ was not found." in result.stderr
    assert "Checked:" in result.stderr
    assert "python3: Python 3.9.18 (too old)" in result.stderr
    assert "macOS:" in result.stderr
    assert "PYTHON=/path/to/python3.12" in result.stderr


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX install.sh launcher")
def test_finds_uv_managed_python_outside_path(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_python(fake_bin / "python3", (3, 9, 6), fail=True)
    home = tmp_path / "home"
    uv_python = (
        home
        / ".local"
        / "share"
        / "uv"
        / "python"
        / "cpython-3.12.13-macos-aarch64-none"
        / "bin"
        / "python3.12"
    )
    uv_python.parent.mkdir(parents=True)
    _write_fake_python(uv_python, (3, 12, 13))
    result = _run_launcher(fake_bin, env_extra={"HOME": str(home)})
    assert result.returncode == 0, result.stderr
    assert "SELECTED=python3.12" in result.stdout
