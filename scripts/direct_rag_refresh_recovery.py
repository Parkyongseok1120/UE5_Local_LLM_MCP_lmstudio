#!/usr/bin/env python
"""Crash recovery journal for Direct RAG multi-file promotion."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Iterable

from direct_rag_backup_restore import restore_backup_copy
from direct_rag_refresh_journal import (
    begin_refresh_journal,
    clear_refresh_journal,
    mark_refresh_backed_up,
    mark_refresh_committed,
    mark_refresh_restored,
    read_refresh_journal,
    refresh_journal_path,
)


def _validated_aux_path(raw: object, target: Path, marker: str) -> Path:
    path = Path(str(raw or "")).expanduser().resolve()
    if path.parent != target.parent or not path.name.startswith(f".{target.name}.{marker}-"):
        raise RuntimeError(f"unsafe Direct RAG recovery path in journal: {path}")
    return path


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def preserve_refresh_path(source: Path, backup: Path) -> None:
    """Preserve a live generation without removing readable files from service."""

    backup.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir() and not source.is_symlink():
        shutil.copytree(source, backup)
        return
    try:
        os.link(source, backup)
    except OSError:
        shutil.copy2(source, backup)


def recover_interrupted_refresh(index_dir: Path, managed_names: Iterable[str]) -> dict:
    """Restore the previous generation or finish cleanup after a committed swap."""

    target = index_dir.expanduser().resolve()
    journal = refresh_journal_path(target)
    if not journal.is_file():
        return {"recovered": False, "reason": "journal_missing"}
    try:
        payload = read_refresh_journal(journal)
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Direct RAG refresh journal is unreadable: {journal}: {exc}") from exc
    version = int(payload.get("version") or 0)
    if version not in {1, 2} or Path(str(payload.get("target") or "")).resolve() != target:
        raise RuntimeError(f"Direct RAG refresh journal identity mismatch: {journal}")

    allowed = set(managed_names)
    planned = {str(name) for name in payload.get("plannedNames") or []}
    previous = {str(name) for name in payload.get("previousNames") or []}
    if not planned <= allowed or not previous <= planned:
        raise RuntimeError(f"Direct RAG refresh journal contains unmanaged paths: {journal}")
    stage = _validated_aux_path(payload.get("stage"), target, "direct-refresh")
    backup = _validated_aux_path(payload.get("backup"), target, "direct-refresh-backup")

    if payload.get("state") == "committed":
        _remove_path(backup)
        _remove_path(stage)
        clear_refresh_journal(journal)
        return {"recovered": True, "reason": "committed_cleanup"}
    state = payload.get("state")
    if version == 2 and state == "restored":
        _remove_path(backup)
        _remove_path(stage)
        clear_refresh_journal(journal)
        return {"recovered": True, "reason": "restored_cleanup"}
    if version == 2 and state == "prepared":
        _remove_path(backup)
        _remove_path(stage)
        clear_refresh_journal(journal)
        return {"recovered": True, "reason": "prepared_cleanup"}
    if state not in {"prepared", "backed_up"}:
        raise RuntimeError(f"Direct RAG refresh journal has an unknown state: {journal}")

    missing_backups = sorted(name for name in previous if not (backup / name).exists())
    if missing_backups:
        raise RuntimeError(
            "Direct RAG refresh backup is incomplete; refusing destructive recovery: "
            + ", ".join(missing_backups)
        )

    target.mkdir(parents=True, exist_ok=True)
    ordered = sorted(name for name in planned if name != "rag.sqlite")
    if "rag.sqlite" in planned:
        ordered.append("rag.sqlite")
    for name in ordered:
        destination = target / name
        old = backup / name
        if name in previous:
            restore_backup_copy(old, destination)
        else:
            _remove_path(destination)
    if version == 2:
        mark_refresh_restored(journal)
    _remove_path(backup)
    _remove_path(stage)
    clear_refresh_journal(journal)
    return {"recovered": True, "reason": "previous_generation_restored"}


__all__ = [
    "begin_refresh_journal",
    "clear_refresh_journal",
    "mark_refresh_backed_up",
    "mark_refresh_committed",
    "mark_refresh_restored",
    "preserve_refresh_path",
    "recover_interrupted_refresh",
    "refresh_journal_path",
]
