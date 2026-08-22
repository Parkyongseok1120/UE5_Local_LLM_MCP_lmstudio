# Archived with the pre-Direct lexical ambiguity gate.
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from semantic_ambiguity import resolve_lexical_semantic_ambiguity  # noqa: E402


def test_accelerator_feature_never_auto_selects_sprint():
    payload = resolve_lexical_semantic_ambiguity(
        "엑셀레이터 기능을 구현해",
        write_intent=True,
    )
    assert payload["material"] is True
    assert payload["selectedInterpretation"] is None
    sprint = next(
        item
        for item in payload["semanticInterpretations"]
        if item["id"] == "sprint_or_boost_gameplay"
    )
    assert sprint["supportingEvidence"] == []
    assert sprint["unsupportedAssumptions"] == [
        "input binding",
        "speed policy",
        "stamina policy",
    ]


def test_animinstance_evidence_ranks_but_does_not_select_interpretation():
    payload = resolve_lexical_semantic_ambiguity(
        "엑셀레이터 기능 분석해",
        evidence_rows=[
            {
                "symbol_name": "UCPlayerCharacterAnimInstance",
                "text": "bool bIsAccelerating; float GroundSpeed;",
            }
        ],
        write_intent=False,
    )
    animation = payload["semanticInterpretations"][0]
    assert len(animation["supportingEvidence"]) == 2
    assert payload["selectedInterpretation"] is None
    assert payload["material"] is False


def test_explicit_animation_wording_is_not_cross_structure_write_ambiguity():
    payload = resolve_lexical_semantic_ambiguity(
        "가속 애니메이션 보간을 구현해",
        write_intent=True,
    )
    assert payload["requestedInterpretation"] == "animation"
    assert payload["material"] is False
    assert payload["selectedInterpretation"] is None
