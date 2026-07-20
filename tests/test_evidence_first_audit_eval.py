from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "evidence_first_audit_cases.json"
EVALUATOR = ROOT / "scripts" / "eval_evidence_first_audit.py"


def _load_evaluator():
    spec = importlib.util.spec_from_file_location("eval_evidence_first_audit", EVALUATOR)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_holdouts_are_multi_language_and_project_neutral() -> None:
    payload = json.loads(CONFIG.read_text(encoding="utf-8"))
    cases = payload["cases"]
    assert len(cases) >= 4
    assert len({case["language"] for case in cases}) >= 3
    serialized = json.dumps(payload, ensure_ascii=False)
    for project_term in (
        "Unreal",
        "HealthComponent",
        "TakeDamage",
        "Blueprint",
        "ProjectMJS",
        "C:\\\\Users\\\\",
    ):
        assert project_term not in serialized


def test_good_fixtures_pass_and_bad_fixtures_fail() -> None:
    evaluator = _load_evaluator()
    cases = evaluator.load_cases(CONFIG)
    result = evaluator.evaluate_fixtures(cases)
    assert result["ok"] is True
    assert result["goodFixtures"]["passed"] == len(cases)
    assert result["badFixturesRejected"] == len(cases)


def test_missing_answer_fails_closed() -> None:
    evaluator = _load_evaluator()
    case = evaluator.load_cases(CONFIG)[0]
    result = evaluator.evaluate_answer(case, "")
    assert result["passed"] is False
    assert result["missingFindings"]
    assert result["missingOutputPatterns"]
