#!/usr/bin/env python
"""Own durable state transitions for one Direct RAG refresh journal."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from atomic_io import atomic_write_text


def refresh_journal_path(index_dir: Path) -> Path:
    target = index_dir.expanduser().resolve()
    return target.parent / f".{target.name}.direct-refresh-journal.json"


def read_refresh_journal(journal: Path) -> dict[str, Any]:
    payload = json.loads(journal.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Direct RAG refresh journal must contain an object: {journal}")
    return payload


def _write(journal: Path, payload: dict[str, Any]) -> None:
    atomic_write_text(
        journal,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )


def begin_refresh_journal(
    index_dir: Path,
    stage: Path,
    backup: Path,
    planned_names: Iterable[str],
    previous_names: Iterable[str],
) -> Path:
    target = index_dir.resolve()
    journal = refresh_journal_path(target)
    _write(
        journal,
        {
            "version": 2,
            "state": "prepared",
            "target": str(target),
            "stage": str(stage.resolve()),
            "backup": str(backup.resolve()),
            "plannedNames": sorted(set(planned_names)),
            "previousNames": sorted(set(previous_names)),
        },
    )
    return journal


def _mark(journal: Path, state: str) -> None:
    payload = read_refresh_journal(journal)
    payload["state"] = state
    _write(journal, payload)


def mark_refresh_backed_up(journal: Path) -> None:
    _mark(journal, "backed_up")


def mark_refresh_committed(journal: Path) -> None:
    _mark(journal, "committed")


def mark_refresh_restored(journal: Path) -> None:
    _mark(journal, "restored")


def clear_refresh_journal(journal: Path) -> None:
    journal.unlink(missing_ok=True)


__all__ = [
    "begin_refresh_journal",
    "clear_refresh_journal",
    "mark_refresh_backed_up",
    "mark_refresh_committed",
    "mark_refresh_restored",
    "read_refresh_journal",
    "refresh_journal_path",
]
