from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "evidence-first-code-audit" / "scripts"


def _load(name: str):
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_skill_installer_dry_run_changes_nothing(tmp_path: Path) -> None:
    installer = _load("install_skill")
    root = tmp_path / "skills"
    destination = installer.install(root, dry_run=True)
    assert destination == (root / "evidence-first-code-audit").resolve()
    assert not root.exists()


def test_skill_installer_copies_portable_skill_and_force_replaces(tmp_path: Path) -> None:
    installer = _load("install_skill")
    destination = installer.install(tmp_path / "skills")
    assert (destination / "SKILL.md").is_file()
    assert not list(destination.rglob("__pycache__"))

    marker = destination / "stale.txt"
    marker.write_text("stale", encoding="utf-8")
    with pytest.raises(FileExistsError):
        installer.install(tmp_path / "skills")
    installer.install(tmp_path / "skills", force=True)
    assert not marker.exists()


def test_skill_installer_rejects_destination_nested_under_source() -> None:
    installer = _load("install_skill")
    source = SCRIPTS.parent
    with pytest.raises(ValueError):
        installer.install(source / "nested")


def test_published_skill_remains_successful_when_backup_cleanup_fails(tmp_path: Path, monkeypatch, capsys) -> None:
    installer = _load("install_skill")
    root = tmp_path / "skills"
    destination = installer.install(root)
    (destination / "old.txt").write_text("old", encoding="utf-8")
    original = installer.shutil.rmtree

    def fail_backup(path, *args, **kwargs):
        target = Path(path).resolve()
        assert target.is_relative_to(root.resolve())
        if "-backup-" in target.name:
            raise PermissionError("backup locked")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(installer.shutil, "rmtree", fail_backup)
    assert installer.install(root, force=True) == destination
    assert (destination / "SKILL.md").is_file()
    assert not (destination / "old.txt").exists()
    assert list(root.glob("*-backup-*/old.txt"))
    assert "Cleanup warning" in capsys.readouterr().err


def test_failed_publication_restores_old_skill_and_preserves_primary_error(tmp_path: Path, monkeypatch) -> None:
    installer = _load("install_skill")
    root = tmp_path / "skills"
    destination = installer.install(root)
    (destination / "old.txt").write_text("old", encoding="utf-8")
    original = Path.replace

    def fail_publish(path, target):
        if "-staging-" in str(path):
            raise PermissionError("publication locked")
        return original(path, target)

    monkeypatch.setattr(Path, "replace", fail_publish)
    with pytest.raises(PermissionError, match="publication locked"):
        installer.install(root, force=True)
    assert (destination / "old.txt").read_text(encoding="utf-8") == "old"


def test_portable_rule_installer_dry_run_force_and_source_guard(tmp_path: Path) -> None:
    installer = _load("install_portable_rule")
    output = tmp_path / "agent" / "evidence-first.md"
    installer.install(output, dry_run=True)
    assert not output.exists()

    installer.install(output)
    assert "근거부터 확인" in output.read_text(encoding="utf-8")
    with pytest.raises(FileExistsError):
        installer.install(output)
    output.write_text("stale", encoding="utf-8")
    installer.install(output, force=True)
    assert "근거부터 확인" in output.read_text(encoding="utf-8")

    source = SCRIPTS.parent / "references" / "portable-rule.md"
    with pytest.raises(ValueError):
        installer.install(source, force=True)
