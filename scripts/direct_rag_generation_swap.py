#!/usr/bin/env python
"""Swap one validated Direct RAG generation with SQLite as final commit point."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from direct_rag_atomic_replace import atomic_replace
from direct_rag_backup_restore import restore_backup_copy
from direct_rag_refresh_journal import (
    begin_refresh_journal,
    clear_refresh_journal,
    mark_refresh_backed_up,
    mark_refresh_committed,
    mark_refresh_restored,
)
from direct_rag_refresh_recovery import preserve_refresh_path


def _ordered(names: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(name for name in names if name != "rag.sqlite") + tuple(
        name for name in names if name == "rag.sqlite"
    )


def _promote(source: Path, destination: Path) -> None:
    if source.is_file() and not source.is_symlink():
        atomic_replace(source, destination, replace=os.replace)
    else:
        os.replace(source, destination)


def _restore_promoted(source: Path, destination: Path) -> None:
    if source.is_file() and not source.is_symlink():
        atomic_replace(source, destination, replace=os.replace)
    else:
        os.replace(source, destination)


def swap_refresh_generation(
    stage: Path,
    target: Path,
    planned_names: tuple[str, ...],
    previous_names: tuple[str, ...],
    prune_names: set[str],
) -> None:
    """Promote companions first and the immutable SQLite file last."""

    backup = Path(
        tempfile.mkdtemp(
            prefix=f".{target.name}.direct-refresh-backup-",
            dir=str(target.parent),
        )
    ).resolve()
    promoted: list[tuple[Path, Path]] = []
    preserved: dict[str, tuple[Path, Path]] = {}
    cleanup_backup = True
    committed = False
    journal = begin_refresh_journal(
        target,
        stage,
        backup,
        planned_names,
        previous_names,
    )
    try:
        for name in _ordered(previous_names):
            destination = target / name
            old = backup / name
            preserve_refresh_path(destination, old)
            preserved[name] = (old, destination)
        mark_refresh_backed_up(journal)

        for name in _ordered(planned_names):
            source = stage / name
            if not source.exists() and name not in prune_names:
                continue
            destination = target / name
            had_previous = destination.exists()
            previous_was_directory = had_previous and destination.is_dir() and not destination.is_symlink()
            if source.exists():
                if previous_was_directory:
                    promoted.append((destination, source))
                    shutil.rmtree(destination)
                _promote(source, destination)
                if not previous_was_directory:
                    promoted.append((destination, source))
            elif had_previous and name in prune_names:
                if destination.is_dir() and not destination.is_symlink():
                    shutil.rmtree(destination)
                else:
                    destination.unlink()
                promoted.append((destination, source))
        mark_refresh_committed(journal)
        committed = True
    except BaseException as promote_error:
        rollback_errors: list[str] = []
        for destination, source in reversed(promoted):
            try:
                if destination.exists():
                    source.parent.mkdir(parents=True, exist_ok=True)
                    _restore_promoted(destination, source)
            except OSError as exc:
                rollback_errors.append(f"remove promoted {destination.name}: {exc}")
        changed_names = {destination.name for destination, _source in promoted}
        for name in reversed(_ordered(tuple(preserved))):
            if name not in changed_names:
                continue
            old, destination = preserved[name]
            try:
                if old.exists():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    restore_backup_copy(old, destination)
            except OSError as exc:
                rollback_errors.append(f"restore {destination.name}: {exc}")
        if rollback_errors:
            cleanup_backup = False
            raise RuntimeError(
                f"refresh promotion failed ({promote_error}); rollback incomplete: "
                + "; ".join(rollback_errors)
                + f"; preserved backup: {backup}"
            ) from promote_error
        try:
            mark_refresh_restored(journal)
        except OSError as exc:
            cleanup_backup = False
            raise RuntimeError(
                f"refresh promotion failed ({promote_error}); rollback was restored but "
                f"its durable journal transition failed; preserved backup: {backup}: {exc}"
            ) from promote_error
        clear_refresh_journal(journal)
        raise RuntimeError(
            f"refresh promotion failed; prior index restored: {promote_error}"
        ) from promote_error
    finally:
        if cleanup_backup:
            shutil.rmtree(backup, ignore_errors=True)
        if committed and not backup.exists():
            clear_refresh_journal(journal)


__all__ = ["swap_refresh_generation"]
