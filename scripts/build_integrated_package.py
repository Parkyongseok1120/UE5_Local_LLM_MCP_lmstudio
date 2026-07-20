#!/usr/bin/env python3
"""Build a relocatable, cross-platform integrated installer package."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_ROOT = "Evidence-First-Integrated"
TOP_LEVEL_EXCLUDES = {
    ".agents",
    ".continue",
    ".git",
    ".github",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "Reports",
    "__pycache__",
    "data",
    "tests",
}
ANY_DIR_EXCLUDES = {".agent", "__pycache__", "node_modules"}
ROOT_FILE_EXCLUDES = {".clinerules", ".gitignore", "PORTABLE_ROOT.txt", "pytest_result.txt"}
LOCAL_CONFIG_NAMES = {
    "agent-mcp.json",
    "cline-workspace.json",
    "lmstudio-mcp-unreal-agent.json",
    "lmstudio_mcp_unreal_rag.json",
    "unreal-workspace.json",
    "workspace.json",
    "workspace.local.json",
}


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _validate_destination(path: Path, source: Path) -> Path:
    resolved = path.expanduser().resolve()
    source = source.resolve()
    if resolved == source or _within(resolved, source) or _within(source, resolved):
        raise ValueError(f"package destination must be disjoint from source: {resolved}")
    anchor = Path(resolved.anchor)
    if resolved == anchor:
        raise ValueError(f"refusing to use a filesystem root: {resolved}")
    return resolved


def _include(relative: Path, *, include_index: bool) -> bool:
    parts = relative.parts
    if not parts:
        return False
    if parts[0] in TOP_LEVEL_EXCLUDES:
        if not (
            include_index
            and relative.as_posix() == "data/unreal58/rag.sqlite"
        ):
            return False
    if any(part in ANY_DIR_EXCLUDES for part in parts[:-1]):
        return False
    if len(parts) == 1 and relative.name in ROOT_FILE_EXCLUDES:
        return False
    if relative.name in LOCAL_CONFIG_NAMES:
        return False
    lower = relative.name.lower()
    if lower.endswith((".pyc", ".pyo", ".log", ".tmp")) or ".bak-" in lower:
        return False
    if lower.endswith((".sqlite", ".sqlite3", ".db")) and not (
        include_index and relative.as_posix() == "data/unreal58/rag.sqlite"
    ):
        return False
    if parts[:2] == ("lmstudio-context-compactor-plugin", "dist"):
        return False
    return True


def _source_files(source: Path, *, include_index: bool) -> Iterable[tuple[Path, Path]]:
    selected: list[tuple[Path, Path]] = []
    for directory, dirnames, filenames in os.walk(source, topdown=True, followlinks=False):
        directory_path = Path(directory)
        relative_directory = directory_path.relative_to(source)
        kept_dirs: list[str] = []
        for name in dirnames:
            candidate = relative_directory / name
            parts = candidate.parts
            excluded_top = parts and parts[0] in TOP_LEVEL_EXCLUDES
            allow_index_path = include_index and parts[:2] in {("data",), ("data", "unreal58")}
            excluded_compactor_dist = parts[:2] == ("lmstudio-context-compactor-plugin", "dist")
            if name in ANY_DIR_EXCLUDES or excluded_compactor_dist or (excluded_top and not allow_index_path):
                continue
            path = directory_path / name
            if path.is_symlink():
                raise ValueError(f"symlinks are not allowed in portable packages: {candidate}")
            kept_dirs.append(name)
        dirnames[:] = sorted(kept_dirs, key=str.lower)
        for name in sorted(filenames, key=str.lower):
            path = directory_path / name
            relative = path.relative_to(source)
            if not _include(relative, include_index=include_index):
                continue
            if path.is_symlink():
                raise ValueError(f"symlinks are not allowed in portable packages: {relative}")
            selected.append((path, relative))
    yield from sorted(selected, key=lambda item: item[1].as_posix().lower())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_launchers(staging: Path) -> None:
    (staging / "INSTALL.bat").write_text(
        "@echo off\r\n"
        "setlocal\r\n"
        "cd /d \"%~dp0\"\r\n"
        "where py >nul 2>nul\r\n"
        "if errorlevel 1 goto use_python\r\n"
        "py -3 install.py %*\r\n"
        "exit /b %ERRORLEVEL%\r\n"
        ":use_python\r\n"
        "python install.py %*\r\n",
        encoding="ascii",
    )
    shell = (
        "#!/usr/bin/env sh\n"
        "set -eu\n"
        "SCRIPT_DIR=$(CDPATH= cd -- \"$(dirname -- \"$0\")\" && pwd)\n"
        "exec python3 \"$SCRIPT_DIR/install.py\" \"$@\"\n"
    )
    target = staging / "install.sh"
    target.write_text(shell, encoding="utf-8", newline="\n")
    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    (staging / "PORTABLE-INSTALL.md").write_text(
        "# Integrated portable installer\n\n"
        "Requirements: Python 3.10+. STANDARD/FULL also require Node.js 20+. "
        "FULL context compaction requires npm and the LM Studio `lms` CLI.\n\n"
        "- Windows: `INSTALL.bat`\n"
        "- Linux and macOS: `./install.sh`\n\n"
        "The installer asks for SAFE, STANDARD, FULL, or CUSTOM. All profiles remain "
        "read-only unless agent mode and its separate risk acknowledgement are both supplied.\n"
        "Run `python3 install.py --help` for automation flags. Generated indexes and machine "
        "configuration are not bundled by default.\n",
        encoding="utf-8",
        newline="\n",
    )


def _manifest(staging: Path, *, include_index: bool) -> dict[str, object]:
    inventory = []
    for path in sorted(staging.rglob("*"), key=lambda item: item.as_posix().lower()):
        if path.is_file() and path.name != "package-manifest.json":
            inventory.append(
                {
                    "path": path.relative_to(staging).as_posix(),
                    "size": path.stat().st_size,
                    "sha256": _sha256(path),
                }
            )
    return {
        "schemaVersion": 1,
        "name": "evidence-first-integrated-coding",
        "portable": True,
        "supportedHosts": ["windows", "linux", "macos"],
        "defaultProfile": "safe",
        "indexIncluded": include_index,
        "inventory": inventory,
    }


def _scan_private_paths(staging: Path) -> None:
    home_markers = {
        str(Path.home()),
        str(Path.home()).replace("\\", "/"),
    }
    home_markers = {marker for marker in home_markers if len(marker) > 3}
    for path in staging.rglob("*"):
        if not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for marker in home_markers:
            if marker in text:
                raise ValueError(f"private home path leaked into package: {path.relative_to(staging)}")


def _write_deterministic_zip(staging: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    if temporary.exists():
        temporary.unlink()
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path in sorted(staging.rglob("*"), key=lambda item: item.as_posix().lower()):
                if not path.is_file():
                    continue
                relative = Path(ARCHIVE_ROOT) / path.relative_to(staging)
                info = zipfile.ZipInfo(relative.as_posix(), date_time=(2026, 1, 1, 0, 0, 0))
                mode = 0o755 if os.access(path, os.X_OK) else 0o644
                info.external_attr = (mode & 0xFFFF) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, path.read_bytes())
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def build(source: Path, output: Path, zip_path: Path | None, *, include_index: bool) -> dict[str, object]:
    source = source.expanduser().resolve()
    output = _validate_destination(output, source)
    if zip_path is not None:
        zip_path = _validate_destination(zip_path, source)
        if _within(zip_path, output):
            raise ValueError("zip path must not be inside the staging directory")
    if not (source / "install.py").is_file():
        raise FileNotFoundError(f"integrated installer not found under source: {source}")

    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}-staging-", dir=output.parent))
    try:
        for path, relative in _source_files(source, include_index=include_index):
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        _write_launchers(staging)
        manifest = _manifest(staging, include_index=include_index)
        (staging / "package-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        _scan_private_paths(staging)
        if output.exists():
            shutil.rmtree(output) if output.is_dir() else output.unlink()
        staging.replace(output)
        if zip_path is not None:
            _write_deterministic_zip(output, zip_path)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise

    return {
        "ok": True,
        "output": str(output),
        "zip": str(zip_path or ""),
        "files": len(manifest["inventory"]),
        "indexIncluded": include_index,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--zip", dest="zip_path", type=Path)
    parser.add_argument("--include-index", action="store_true")
    args = parser.parse_args()
    try:
        result = build(args.source, args.output, args.zip_path, include_index=args.include_index)
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
