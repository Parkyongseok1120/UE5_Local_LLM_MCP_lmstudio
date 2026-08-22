#!/usr/bin/env python
"""Archived lexical interpretation gate from the pre-Direct runtime."""

from __future__ import annotations

import re
import unicodedata
from typing import Any


_ACCELERATOR_RE = re.compile(
    r"(?:엑셀레이터|액셀레이터|accelerator|acceleration|가속)",
    re.IGNORECASE,
)
_ANIMATION_RE = re.compile(
    r"(?:anim(?:ation|instance)?|lean|locomotion|애니메이션|애님|린|보간)",
    re.IGNORECASE,
)
_GAMEPLAY_RE = re.compile(
    r"(?:sprint|boost|dash|stamina|스프린트|부스트|대시|스태미나)",
    re.IGNORECASE,
)


def _evidence_text(rows: list[dict[str, Any]] | None) -> str:
    values: list[str] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        for key in (
            "symbol_name",
            "symbolName",
            "qualified_name",
            "qualifiedName",
            "title",
            "text",
            "file_path",
            "filePath",
        ):
            value = row.get(key)
            if value:
                values.append(str(value))
    return unicodedata.normalize("NFKC", "\n".join(values)).casefold()


def resolve_lexical_semantic_ambiguity(
    request: str,
    *,
    evidence_rows: list[dict[str, Any]] | None = None,
    write_intent: bool = False,
) -> dict[str, Any]:
    """Return conservative interpretations; source evidence never auto-selects."""

    source = unicodedata.normalize("NFKC", str(request or ""))
    if not _ACCELERATOR_RE.search(source):
        return {}
    evidence = _evidence_text(evidence_rows)

    animation_support = [
        observation
        for marker, observation in (
            ("bisaccelerating", "bIsAccelerating exists in verified symbol/source evidence"),
            ("groundspeed", "GroundSpeed exists in verified symbol/source evidence"),
        )
        if marker in evidence
    ]
    lean_support = [
        observation
        for marker, observation in (
            ("lean", "Lean-related symbol/source evidence exists"),
            ("start", "Start-related animation symbol/source evidence exists"),
        )
        if marker in evidence
    ]
    gameplay_support = [
        observation
        for marker, observation in (
            ("sprint", "Sprint symbol/source evidence exists"),
            ("inputaction", "Input action evidence exists"),
            ("maxwalkspeed", "Movement speed policy evidence exists"),
            ("stamina", "Stamina policy evidence exists"),
        )
        if marker in evidence
    ]
    interpretations = [
        {
            "id": "animation_acceleration_smoothing",
            "label": "가속 기반 이동 애니메이션 보간",
            "supportingEvidence": animation_support,
            "unsupportedAssumptions": [
                item
                for item, present in (
                    ("bIsAccelerating state", "bisaccelerating" in evidence),
                    ("GroundSpeed update", "groundspeed" in evidence),
                )
                if not present
            ],
        },
        {
            "id": "acceleration_lean_start_animation",
            "label": "가속 시 lean/start 애니메이션",
            "supportingEvidence": lean_support,
            "unsupportedAssumptions": [
                item
                for item, present in (
                    ("lean animation target", "lean" in evidence),
                    ("start animation target", "start" in evidence),
                )
                if not present
            ],
        },
        {
            "id": "sprint_or_boost_gameplay",
            "label": "스프린트 또는 부스트 게임플레이",
            "supportingEvidence": gameplay_support,
            "unsupportedAssumptions": [
                item
                for item, present in (
                    ("input binding", "inputaction" in evidence or "inputmapping" in evidence),
                    ("speed policy", "maxwalkspeed" in evidence),
                    ("stamina policy", "stamina" in evidence),
                )
                if not present
            ],
        },
    ]
    explicit_animation = bool(_ANIMATION_RE.search(source))
    explicit_gameplay = bool(_GAMEPLAY_RE.search(source))
    lexical_material = not (explicit_animation ^ explicit_gameplay)
    material = bool(write_intent and lexical_material)
    return {
        "version": 1,
        "status": "unresolved" if lexical_material else "scoped_by_user_wording",
        "material": material,
        "requestedInterpretation": (
            "animation"
            if explicit_animation and not explicit_gameplay
            else "gameplay"
            if explicit_gameplay and not explicit_animation
            else None
        ),
        "semanticInterpretations": interpretations,
        "selectedInterpretation": None,
        "evidenceVerifiedByServer": bool(evidence_rows),
        "recommendedAction": "ask_user_once" if material else "explain_candidates",
        "proofBoundary": (
            "Supporting source symbols rank interpretations only; they do not prove "
            "the user's intended feature or authorize a write target."
        ),
    }
