#!/usr/bin/env python
"""Poll the active Unreal project for changes and refresh local RAG metadata."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable

from workspace_paths import find_workspace_root, resolve_active_project_path

SOURCE_SUFFIXES = {".h", ".hpp", ".cpp", ".c", ".cc", ".cs", ".ini", ".uproject", ".uplugin"}
ASSET_SUFFIXES = {".uasset", ".umap"}
SKIP_DIRS = {"Binaries", "DerivedDataCache", "Intermediate", "Saved", ".git"}


def iter_files(root: Path, suffixes: set[str]) -> Iterable[Path]:
    if root.is_file():
        if root.suffix.lower() in suffixes:
            yield root
        return
    if not root.is_dir():
        return
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in suffixes:
            yield path


def newest_mtime(paths: Iterable[Path]) -> float | None:
    newest: float | None = None
    for path in paths:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        newest = mtime if newest is None else max(newest, mtime)
    return newest


def project_state(uproject: Path) -> dict[str, float | None]:
    root = uproject.parent
    source_roots = [uproject, root / "Source", root / "Config", root / "Plugins"]
    asset_roots = [root / "Content"]
    return {
        "source": newest_mtime(path for item in source_roots for path in iter_files(item, SOURCE_SUFFIXES)),
        "asset": newest_mtime(path for item in asset_roots for path in iter_files(item, ASSET_SUFFIXES)),
    }


def run_command(workspace: Path, command: str, *, dry_run: bool = False) -> int:
    rag = workspace / "rag.ps1"
    args = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(rag),
        command,
    ]
    print("[watch]", " ".join(args), flush=True)
    if dry_run:
        return 0
    proc = subprocess.run(args, cwd=str(workspace), check=False)
    return proc.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Watch active Unreal project changes and refresh RAG metadata.")
    parser.add_argument("--project", default="", help="Path to .uproject. Defaults to shared activeProject.")
    parser.add_argument("--poll-sec", type=float, default=5.0)
    parser.add_argument("--debounce-sec", type=float, default=15.0)
    parser.add_argument("--once", action="store_true", help="Scan once and report state without looping.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-source-sync", action="store_true")
    parser.add_argument("--no-asset-sync", action="store_true")
    args = parser.parse_args()

    workspace = find_workspace_root()
    uproject = Path(args.project).expanduser().resolve() if args.project else resolve_active_project_path()
    if not uproject or not uproject.is_file():
        raise SystemExit("No active .uproject found. Run .\\rag.ps1 pick-project first or pass --project.")

    last = project_state(uproject)
    print(f"[watch] project={uproject}", flush=True)
    print(f"[watch] initial={last}", flush=True)
    if args.once:
        return 0

    pending_source = False
    pending_asset = False
    first_change_at: float | None = None
    while True:
        time.sleep(max(args.poll_sec, 1.0))
        current = project_state(uproject)
        source_changed = bool(current["source"] and current["source"] != last.get("source"))
        asset_changed = bool(current["asset"] and current["asset"] != last.get("asset"))
        if source_changed and not args.no_source_sync:
            pending_source = True
            first_change_at = first_change_at or time.time()
            print(f"[watch] source/config changed: {last.get('source')} -> {current['source']}", flush=True)
        if asset_changed and not args.no_asset_sync:
            pending_asset = True
            first_change_at = first_change_at or time.time()
            print(f"[watch] content asset changed: {last.get('asset')} -> {current['asset']}", flush=True)
        last = current
        if first_change_at and time.time() - first_change_at >= max(args.debounce_sec, 1.0):
            if pending_source:
                run_command(workspace, "sync-active-project", dry_run=args.dry_run)
            if pending_asset:
                run_command(workspace, "sync-editor-metadata", dry_run=args.dry_run)
            pending_source = False
            pending_asset = False
            first_change_at = None


if __name__ == "__main__":
    raise SystemExit(main())
