#!/usr/bin/env python
"""Regression guard for guideline/profile sidecar retrieval."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from evaluate_rag_queries import evaluate_case  # noqa: E402
from workspace_paths import resolve_index_path  # noqa: E402

QUERY_SET = ROOT / "config" / "rag_eval_unreal_programming_queries.json"
STABILIZED_CASE_IDS = {
    "build_cs_dependency_missing",
    "c1083_include_fix_playbook",
    "active_project_profile",
    "blueprint_variable_function_call_analysis",
}


def _load_stabilized_cases() -> list[dict]:
    payload = json.loads(QUERY_SET.read_text(encoding="utf-8-sig"))
    defaults = dict(payload.get("defaults") or {})
    cases = [case for case in payload.get("cases") or [] if case.get("id") in STABILIZED_CASE_IDS]
    return defaults, cases


@pytest.fixture(scope="module")
def rag_index() -> Path:
    index = resolve_index_path(ROOT)
    if not index.is_file():
        pytest.skip("rag.sqlite missing")
    return index


@pytest.mark.parametrize("case", _load_stabilized_cases()[1], ids=lambda c: c["id"])
def test_guideline_sidecar_cases_rank_expected_docs(rag_index: Path, case: dict):
    defaults, _ = _load_stabilized_cases()
    ok, detail = evaluate_case(rag_index, defaults, case)
    assert ok, detail
