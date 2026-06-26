#!/usr/bin/env python
"""Resolve Genre Adapter ids from user request text."""

from __future__ import annotations

import re
from pathlib import Path

GENRE_RULES: list[tuple[str, list[str]]] = [
    ("shooter", ["shooter", "fps", "tps", "third person shooter", "gun", "line trace", "hitscan", "슈터", "총"]),
    ("action_combat", ["action combat", "melee", "combo", "stamina", "soulslike", "액션", "근접", "콤보"]),
    ("platformer", ["platformer", "jump", "side scroll", "2d platform", "플랫포머", "점프"]),
    ("survival", ["survival", "craft", "hunger", "resource", "생존", "크래프트"]),
    ("roguelike", ["roguelike", "rogue-like", "run based", "permadeath", "로그라이크"]),
    ("deckbuilder", ["deckbuilder", "deck builder", "card game", "덱빌더", "카드"]),
    ("strategy", ["strategy", "tactics", "turn based", "rts", "전략", "턴제"]),
    ("stealth", ["stealth", "hide", "detection", "은신", "잠입"]),
    ("horror", ["horror", "fear", "sanity", "공포", "호러"]),
    ("narrative", ["narrative", "dialogue", "story", "visual novel", "내러티브", "스토리"]),
    ("racing", ["racing", "vehicle", "lap", "레이싱", "차량"]),
    ("tower_defense", ["tower defense", "td", "tower", "타워 디펜스"]),
    ("battle_royale", ["battle royale", "br", "last player", "배틀로얄"]),
    ("extraction", ["extraction", "extract", "loot risk", "익스트랙션"]),
    ("management", ["management", "simulation", "city builder", "경영", "시뮬"]),
    ("puzzle", ["puzzle", "match three", "퍼즐"]),
    ("rhythm", ["rhythm", "beat", "리듬"]),
]


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def resolve_genre_adapters(request: str, explicit: list[str] | None = None, max_genres: int = 3) -> list[str]:
    if explicit:
        cleaned = [g.strip().lower().replace(" ", "_") for g in explicit if g.strip()]
        return cleaned[:max_genres]

    text = normalize(request)
    scores: dict[str, int] = {}
    for genre_id, keywords in GENRE_RULES:
        for keyword in keywords:
            if keyword in text:
                scores[genre_id] = scores.get(genre_id, 0) + 1

    if not scores:
        return []

    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return [genre_id for genre_id, _ in ranked[:max_genres]]


def registry_path(rag_root: Path | None = None) -> Path:
    root = rag_root or Path(__file__).resolve().parent.parent
    return root / "RAG_Project_Guidelines" / "Genre_Gameplay" / "00_Genre_Adapter_Registry.md"
