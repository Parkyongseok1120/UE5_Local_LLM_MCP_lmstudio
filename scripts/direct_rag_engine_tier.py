#!/usr/bin/env python
"""Own staged engine-corpus pruning for one configured indexing tier."""

from __future__ import annotations

from pathlib import Path

ENGINE_TIER_FILES = (
    "raw_symbols.jsonl",
    "sidecar_symbols_meta.jsonl",
)


def engine_tier_prune_files(tier: str) -> tuple[str, ...]:
    normalized = str(tier or "standard").strip().casefold()
    if normalized == "full":
        return ()
    if normalized == "lite":
        return ("raw_source.jsonl", *ENGINE_TIER_FILES)
    return ("raw_source.jsonl",)


def prune_engine_inputs_for_tier(stage: Path, tier: str) -> tuple[str, ...]:
    names = engine_tier_prune_files(tier)
    for name in names:
        (stage / name).unlink(missing_ok=True)
    return names


__all__ = ["engine_tier_prune_files", "prune_engine_inputs_for_tier"]
