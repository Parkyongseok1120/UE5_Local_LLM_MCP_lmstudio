from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "build_integrated_package.py"


def _build(tmp_path: Path, name: str) -> tuple[Path, Path]:
    output = tmp_path / name
    archive = tmp_path / f"{name}.zip"
    result = subprocess.run(
        [sys.executable, str(BUILDER), "--output", str(output), "--zip", str(archive)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return output, archive


def test_package_has_all_platform_launchers_and_no_local_state(tmp_path: Path) -> None:
    output, archive = _build(tmp_path, "portable 한글 one")
    for relative in (
        "INSTALL.bat",
        "install.sh",
        "install.py",
        "skills/evidence-first-code-audit/SKILL.md",
        "skills/evidence-first-code-audit/assets/lmstudio-evidence-first.preset.json",
        "skills/evidence-first-code-audit/scripts/evidence_first_mcp.py",
        "config/evidence_first_benchmark_cases.json",
        "lmstudio-unreal-agent-mcp/src/server.js",
        "lmstudio-context-compactor-plugin/package-lock.json",
        "package-manifest.json",
    ):
        assert (output / relative).is_file(), relative
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
        "manifest.json",
    }
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
    installed = subprocess.run(
        [
            sys.executable,
            str(output / "install.py"),
            "--profile",
            "safe",
            "--yes",
            "--codex-home",
            str(tmp_path / "isolated codex"),
            "--lmstudio-home",
            str(tmp_path / "isolated lmstudio"),
            "--state-home",
            str(tmp_path / "isolated state"),
        ],
        cwd=str(output),
        capture_output=True,
        text=True,
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
