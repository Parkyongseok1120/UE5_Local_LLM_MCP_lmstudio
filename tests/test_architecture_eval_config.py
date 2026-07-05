from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "rag_eval_architecture_cases.json"


def test_architecture_eval_config_is_public_safe_and_structured() -> None:
    payload = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
    cases = payload.get("cases") or []

    assert len(cases) >= 7
    ids = [case.get("id") for case in cases]
    assert len(ids) == len(set(ids))
    for case in cases:
        assert case.get("id")
        assert case.get("prompt")
        assert case.get("requiredMentions")
        assert case.get("forbiddenClaims")
        assert case.get("requiredEvidenceTypes")
        text = json.dumps(case, ensure_ascii=False)
        assert ("C:" + "\\Users\\") not in text
        assert ("C:" + "/Users/") not in text
        assert "Project_MJS" not in text


def test_architecture_eval_config_covers_expected_review_categories() -> None:
    text = CONFIG.read_text(encoding="utf-8-sig")

    for marker in (
        "combat",
        "input",
        "component",
        "subsystem",
        "Blueprint-facing",
        "DataAsset",
        "runtime/editor boundary",
        "safe refactor",
    ):
        assert marker in text
