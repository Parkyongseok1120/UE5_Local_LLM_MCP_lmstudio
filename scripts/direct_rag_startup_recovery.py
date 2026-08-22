#!/usr/bin/env python
"""Recover validated base and sibling refresh journals without cross-shard blocking."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from direct_rag_refresh_journal import read_refresh_journal, refresh_journal_path
from direct_rag_refresh_lock import DirectRagRefreshBusyError, index_refresh_lock
from direct_rag_refresh_transaction import recover_interrupted_refresh
from direct_rag_shard_selection import candidate_indexes
from direct_rag_unbuilt_shard import is_shard_namespace


def _validated_journal_target(journal: Path, data_root: Path) -> Path | None:
    try:
        payload = read_refresh_journal(journal)
        if int(payload.get("version") or 0) not in {1, 2}:
            return None
        target = Path(str(payload.get("target") or "")).expanduser().resolve()
    except (OSError, ValueError, TypeError):
        return None
    root = data_root.expanduser().resolve()
    if target == root or target.parent != root or target.is_symlink():
        return None
    if refresh_journal_path(target) != journal.expanduser().resolve():
        return None
    return target


def startup_recovery_targets(base_index: Path) -> list[Path]:
    base = base_index.expanduser().resolve()
    target = base.parent
    if (
        base.name.casefold() != "rag.sqlite"
        or not is_shard_namespace(target.name)
    ):
        return [target]
    data_root = target.parent.resolve()
    targets = {target}
    for candidate in candidate_indexes(base)[1:]:
        sibling = candidate.parent.resolve()
        journal = refresh_journal_path(sibling)
        if journal.is_file() and _validated_journal_target(journal, data_root) == sibling:
            targets.add(sibling)
    for journal in sorted(data_root.glob(".*.direct-refresh-journal.json")):
        sibling = _validated_journal_target(journal, data_root)
        if sibling is not None and (
            sibling == target or is_shard_namespace(sibling.name)
        ):
            targets.add(sibling)
    return [target, *sorted(targets - {target}, key=lambda path: path.name.casefold())]


def recover_startup_refreshes(base_index: Path) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for target in startup_recovery_targets(base_index):
        try:
            with index_refresh_lock(target / "rag.sqlite"):
                recovered = recover_interrupted_refresh(target)
            results.append({"indexDir": str(target), **recovered})
        except DirectRagRefreshBusyError as exc:
            results.append(
                {
                    "indexDir": str(target),
                    "recovered": False,
                    "reason": "refresh_busy",
                    "error": str(exc),
                }
            )
        except Exception as exc:
            results.append(
                {
                    "indexDir": str(target),
                    "recovered": False,
                    "reason": "recovery_failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    return results


__all__ = ["recover_startup_refreshes", "startup_recovery_targets"]
