#!/usr/bin/env python
"""Apply mode-specific retrieval layer bonuses from config/retrieval_profiles.json."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


@lru_cache(maxsize=1)
def load_profiles() -> dict[str, Any]:
    path = Path(__file__).resolve().parent.parent / "config" / "retrieval_profiles.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def layer_for_source(source: str, layers: dict[str, Any]) -> str | None:
    for layer_name, spec in layers.items():
        sources = spec.get("sources") or []
        if "*" in sources or source in sources:
            return layer_name
    return None


def apply_retrieval_layer_bonus(rows: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    data = load_profiles()
    profiles = data.get("profiles") or {}
    layers = data.get("layers") or {}
    profile = profiles.get(mode) or profiles.get("compile_fix")
    if not profile:
        return rows
    order = profile.get("layer_order") or []
    order_rank = {name: idx for idx, name in enumerate(order)}
    updated: list[dict[str, Any]] = []
    for row in rows:
        copy = dict(row)
        source = str(copy.get("source") or "")
        layer_name = layer_for_source(source, layers)
        bonus = 0.0
        if layer_name and layer_name in layers:
            bonus = float(layers[layer_name].get("bonus") or 0.0)
        if layer_name in order_rank:
            bonus += (len(order) - order_rank[layer_name]) * 0.5
        if source == "unreal_failure_memory":
            bonus *= 0.15
        copy["rank_score"] = float(copy.get("rank_score") or 0.0) - bonus
        updated.append(copy)
    updated.sort(key=lambda r: (float(r.get("rank_score") or 0.0), float(r.get("score") or 0.0)))
    return updated
