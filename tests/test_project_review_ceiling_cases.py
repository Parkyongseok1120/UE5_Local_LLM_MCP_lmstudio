#!/usr/bin/env python
"""Validate ceiling project-review case definitions."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CEILING = ROOT / "config" / "rag_eval_project_review_ceiling_cases.json"


def test_ceiling_review_cases_are_stricter_than_core():
    core = json.loads((ROOT / "config" / "rag_eval_project_review_cases.json").read_text(encoding="utf-8-sig"))
    ceiling = json.loads(CEILING.read_text(encoding="utf-8-sig"))
    core_defaults = core.get("defaults") or {}
    ceiling_defaults = ceiling.get("defaults") or {}
    assert float(ceiling_defaults.get("minRecall") or 0) >= float(core_defaults.get("minRecall") or 0)
    assert ceiling_defaults.get("requireCitation") is True
    for case in ceiling.get("cases") or []:
        assert len(case.get("mustDetect") or []) >= 3
        assert len(case.get("snippets") or []) <= 1
