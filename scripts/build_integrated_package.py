#!/usr/bin/env python3
"""Build a relocatable, cross-platform integrated installer package (allowlist)."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_ROOT = "Evidence-First-Integrated"

# Only these top-level directories may enter a portable package.
ALLOWED_TOP_LEVEL_DIRS = frozenset(
    {
        "config",
        "docs",
        "Game_Design_Docs",
        "installer",
        "lmstudio-context-compactor-plugin",
        "lmstudio-unreal-agent-mcp",
        "mcp-tools",
        "prompts",
        "RAG_Project_Guidelines",
        "scripts",
        "skills",
        "tools",
    }
)

# Only these root files may enter a portable package.
ALLOWED_ROOT_FILES = frozenset(
    {
        "CONTRIBUTING.md",
        "EPIC_NOTICE.md",
        "INSTALL.bat",
        "LICENSE",
        "README.ko.md",
        "README.md",
        "SECURITY.md",
        "install.py",
        "install.sh",
        "rag.ps1",
        "requirements.txt",
    }
)

ANY_DIR_EXCLUDES = frozenset({".agent", "__pycache__", "node_modules", "dist", "release_evidence"})
LOCAL_CONFIG_NAMES = frozenset(
    {
        "agent-mcp.json",
        "cline-workspace.json",
        "lmstudio-mcp-unreal-agent.json",
        "lmstudio_mcp_unreal_rag.json",
        "unreal-workspace.json",
        "workspace.json",
        "workspace.local.json",
    }
)

# Development marathon / personal campaign / debug runners excluded even under scripts/.
SCRIPTS_NAME_DENY = re.compile(
    r"(?ix)^("
    r"local_ai_.*"
    r"|omock_.*"
    r"|run_omock_.*"
    r"|supervisor_local_ai_.*"
    r"|lmstudio_e2e_.*"
    r"|lmstudio_marathon_.*"
    r"|stage_campaign_marathon.*"
    r"|stage_campaign_(report|state)\.json$"
    r"|mcp_.*_(report|audit|aggregate)\.json$"
    r"|mcp_stale_task_quarantine_report\.json$"
    r"|.*_session\.json$"
    r"|.*\.out\.log$"
    r"|.*\.runner\.log$"
    r"|.*\.shell\.log$"
    r"|MIDPOINT_.*"
    r"|STAGE3_7_.*"
    r"|INFRA_STALE_.*"
    r"|_tmp_.*"
    r")$"
)

FORBIDDEN_PACKAGE_MARKERS = re.compile(
    r"(?ix)("
    r"local_ai_prompt_"
    r"|_session\.json$"
    r"|\\.out\\.log$"
    r"|omock_"
    r"|stage_campaign_marathon"
    r"|marathon17"
    r")"
)

REQUIRED_RUNTIME_FILES = (
    "scripts/phase_tool_router.py",
    "scripts/approve_feature_intent.py",
    "scripts/mutation_semantic_guard.py",
    "scripts/unreal_api_denylist.py",
    "lmstudio-unreal-agent-mcp/src/route-watcher.js",
    "lmstudio-unreal-agent-mcp/src/mutation-semantic-guard.js",
)

# Absolute home-path shapes across Windows / macOS / Linux.
_WIN_USERS_BS = "C:" + "\\" + "Users" + "\\"
_WIN_USERS_FS = "C:" + "/" + "Users" + "/"
_UNIX_USERS = "/" + "Users" + "/"
_UNIX_HOME = "/" + "home" + "/"
PRIVATE_PATH_RE = re.compile(
    rf"(?ix)("
    rf"{re.escape(_WIN_USERS_BS)}(?!Public\\)[A-Za-z][^\\\s\"'`<>]*"
    rf"|{re.escape(_WIN_USERS_FS)}(?!Public/)[A-Za-z][^/\s\"'`<>]*"
    rf"|{re.escape(_UNIX_USERS)}(?!Shared(?:/|\b))[A-Za-z][^/\s\"'`<>]*"
    rf"|{re.escape(_UNIX_HOME)}[A-Za-z][^/\s\"'`<>]*"
    rf")"
)

FORBIDDEN_INVENTORY_RE = re.compile(
    r"(?ix)("
    r"(^|/)local_ai_"
    r"|(^|/)omock_"
    r"|_session\.json$"
    r"|\\.out\\.log$"
    r"|\\.runner\\.log$"
    r"|stage_campaign_marathon"
    r"|supervisor_local_ai"
    r"|lmstudio_e2e_driver"
    r"|(^|/)MIDPOINT_AUDIT_"
    r"|(^|/)STAGE3_7_"
    r")"
)


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

    if len(parts) == 1:
        return relative.name in ALLOWED_ROOT_FILES

    if parts[0] not in ALLOWED_TOP_LEVEL_DIRS:
        if include_index and relative.as_posix() == "data/unreal58/rag.sqlite":
            return True
        return False

    if any(part in ANY_DIR_EXCLUDES for part in parts):
        return False
    if relative.name in LOCAL_CONFIG_NAMES:
        return False

    lower = relative.name.lower()
    if lower.endswith((".pyc", ".pyo", ".log", ".tmp", ".bak")) or ".bak-" in lower:
        return False
    if lower.endswith((".sqlite", ".sqlite3", ".db")) and not (
        include_index and relative.as_posix() == "data/unreal58/rag.sqlite"
    ):
        return False

    if parts[0] == "scripts" and SCRIPTS_NAME_DENY.match(relative.name):
        return False
    if FORBIDDEN_PACKAGE_MARKERS.search(relative.as_posix()):
        return False

    # Keep product scripts; omit installer-support PowerShell helpers that are Windows-dev only.
    if parts[:2] == ("scripts", "installer_support"):
        return False

    return True


def _source_files(source: Path, *, include_index: bool) -> Iterable[tuple[Path, Path]]:
    """Prefer git-tracked files so ignored local overlays never enter the ZIP."""
    selected: list[tuple[Path, Path]] = []
    tracked: list[str] = []
    try:
        tracked = subprocess.check_output(
            ["git", "-C", str(source), "ls-files", "-z"],
            text=False,
        ).split(b"\0")
        tracked_paths = [Path(item.decode("utf-8")) for item in tracked if item]
    except (OSError, subprocess.CalledProcessError, UnicodeDecodeError):
        tracked_paths = []

    if tracked_paths:
        for relative in sorted(tracked_paths, key=lambda item: item.as_posix().lower()):
            if not _include(relative, include_index=include_index):
                continue
            path = source / relative
            if not path.is_file():
                continue
            if path.is_symlink():
                raise ValueError(f"symlinks are not allowed in portable packages: {relative}")
            selected.append((path, relative))
        if include_index:
            index_rel = Path("data/unreal58/rag.sqlite")
            index_path = source / index_rel
            if index_path.is_file() and _include(index_rel, include_index=True):
                selected.append((index_path, index_rel))
        yield from sorted(selected, key=lambda item: item[1].as_posix().lower())
        return

    for directory, dirnames, filenames in os.walk(source, topdown=True, followlinks=False):
        directory_path = Path(directory)
        relative_directory = directory_path.relative_to(source)
        kept_dirs: list[str] = []
        for name in dirnames:
            candidate = relative_directory / name
            parts = candidate.parts
            if not parts:
                continue
            if parts[0] not in ALLOWED_TOP_LEVEL_DIRS and not (
                include_index and parts[0] == "data"
            ):
                continue
            if name in ANY_DIR_EXCLUDES:
                continue
            if parts[:2] == ("scripts", "installer_support"):
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
    shutil.copy2(ROOT / "INSTALL.bat", staging / "INSTALL.bat")
    target = staging / "install.sh"
    shutil.copy2(ROOT / "install.sh", target)
    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    (staging / "PORTABLE-INSTALL.md").write_text(
        "# Integrated portable installer\n\n"
        "## Prerequisites\n\n"
        "- **Python 3.10+ must already be installed and on PATH** (or set `PYTHON=/path/to/python3.12`) "
        "before `./install.sh` can start. The installer then bootstraps managed Python 3.12.\n"
        "- Node.js 20+/npm is downloaded only for Unreal/context components and PowerShell 7 "
        "(`pwsh`) only for an opt-in RAG build. Runtime archives are pinned by SHA-256 and safely "
        "extracted for the host CPU architecture (arm64/x64).\n"
        "- FULL context compaction also requires the LM Studio `lms` CLI.\n\n"
        "## Host support\n\n"
        "- **Windows**: supported for LM Studio and Unreal-integrated profiles.\n"
        "- **Ubuntu 22.04/24.04 with glibc**: supported; musl/Alpine is not.\n"
        "- **Apple Silicon macOS**: installer available; LM Studio live certification is still pending "
        "(unsigned/notarization not claimed).\n"
        "- **Intel macOS (x86_64)**: LM Studio is not supported by LM Studio upstream. "
        "LM Studio / Unreal / context-compactor installs abort early. "
        "Custom Codex / portable-rule / Cline-only installs remain allowed.\n\n"
        "## Launch\n\n"
        "- Windows: `INSTALL.bat`\n"
        "- Ubuntu Linux and Apple Silicon macOS: `./install.sh`\n\n"
        "The installer asks for SAFE, STANDARD, FULL, or CUSTOM. All profiles remain "
        "read-only unless agent mode and its separate risk acknowledgement are both supplied.\n"
        "Run `python3 install.py --help` for automation flags. Generated indexes and machine "
        "configuration are not bundled by default. RAG indexing uses the bootstrapped `pwsh`; "
        "custom Unreal installs can be supplied with `--engine-root` or `UNREAL_ENGINE_ROOT`.\n",
        encoding="utf-8",
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
        "supportedHosts": ["windows", "linux", "macos-apple-silicon"],
        "hostNotes": {
            "macos-apple-silicon": "LM Studio installer path uncertified; Python 3.10+ required to bootstrap",
            "macos-intel": "LM Studio configuration unsupported; custom/Cline-only allowed",
        },
        "defaultProfile": "safe",
        "indexIncluded": include_index,
        "inventory": inventory,
    }


def _scan_private_paths(staging: Path) -> None:
    for path in staging.rglob("*"):
        if not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for match in PRIVATE_PATH_RE.finditer(text):
            snippet = match.group(0)
            # Ignore documentation placeholders such as <name> / YOUR_NAME.
            if "<" in snippet or "YOUR_NAME" in snippet.upper() or "USERNAME" in snippet.upper():
                continue
            raise ValueError(
                f"private home path leaked into package: {path.relative_to(staging)}"
            )


def _assert_clean_inventory(manifest: dict[str, object]) -> list[str]:
    inventory = manifest.get("inventory")
    if not isinstance(inventory, list):
        raise ValueError("package manifest inventory missing")
    forbidden: list[str] = []
    for row in inventory:
        if not isinstance(row, dict):
            continue
        rel = str(row.get("path") or "")
        if FORBIDDEN_INVENTORY_RE.search(rel):
            forbidden.append(rel)
    if forbidden:
        raise ValueError(
            "forbidden files present in portable inventory ("
            f"{len(forbidden)}): " + ", ".join(forbidden[:20])
        )
    return [str(row.get("path") or "") for row in inventory if isinstance(row, dict)]


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
    missing_required = [
        relative for relative in REQUIRED_RUNTIME_FILES
        if not (source / relative).is_file()
    ]
    if missing_required:
        raise FileNotFoundError(
            "required integrated runtime files are missing: "
            + ", ".join(missing_required)
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}-staging-", dir=output.parent))
    try:
        for path, relative in _source_files(source, include_index=include_index):
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        _write_launchers(staging)
        missing_staged = [
            relative for relative in REQUIRED_RUNTIME_FILES
            if not (staging / relative).is_file()
        ]
        if missing_staged:
            raise FileNotFoundError(
                "required runtime files were not packaged: "
                + ", ".join(missing_staged)
            )
        manifest = _manifest(staging, include_index=include_index)
        inventory_paths = _assert_clean_inventory(manifest)
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
        "forbiddenInventoryCount": 0,
        "inventorySample": inventory_paths[:40],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--zip", dest="zip_path", type=Path)
    parser.add_argument("--include-index", action="store_true")
    parser.add_argument(
        "--print-inventory",
        action="store_true",
        help="Print the full packaged inventory paths to stdout after a successful build.",
    )
    args = parser.parse_args()
    try:
        result = build(args.source, args.output, args.zip_path, include_index=args.include_index)
    except (OSError, ValueError, FileNotFoundError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=True, indent=2))
        return 1
    if args.print_inventory:
        manifest = json.loads(Path(result["output"], "package-manifest.json").read_text(encoding="utf-8"))
        for row in manifest["inventory"]:
            print(row["path"])
        print(
            json.dumps(
                {
                    "ok": True,
                    "files": result["files"],
                    "forbiddenInventoryCount": 0,
                },
                ensure_ascii=True,
            ),
            file=sys.stderr,
        )
    else:
        print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
