from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from report_tier_kpi import SCORECARD_NAMES, build_scorecard, render_markdown  # noqa: E402


def _write(root: Path, relative: str, payload: dict) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _live_identity() -> dict:
    return {
        "generatedAt": "2026-07-30T00:00:00+00:00",
        "commitSha": "abc123",
        "modelId": "local/test-model",
        "suite": "compile-fix-v1",
    }


def test_metrics_only_results_never_become_execution_or_combined_grade(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "data/baseline/pass-at-k-kpi.json",
        {
            "mode": "metrics-only",
            "pass": True,
            "passRate": 1.0,
            "total": 36,
            "wrongFileEditCount": 0,
            "tiers": {
                "multifile_refactor": {
                    "cases": 12,
                    "pass_at_k_rate": 1.0,
                }
            },
        },
    )

    scorecard = build_scorecard(tmp_path)

    assert list(scorecard["scorecards"]) == list(SCORECARD_NAMES)
    assert scorecard["scorecards"]["compile_fix"]["status"] == "not_run"
    assert scorecard["scorecards"]["compile_fix"]["passed"] is False
    assert scorecard["scorecards"]["negative_control"]["claimAllowed"] is False
    assert scorecard["releaseReadiness"]["ready"] is False
    assert scorecard["claimPolicy"]["combinedAiGradeAllowed"] is False
    assert "estimatedGradeOutOf10" not in scorecard
    assert "claim9_0" not in scorecard


def test_static_architecture_score_is_kept_separate_from_live_claim(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "data/baseline/project-review-kpi.json",
        {
            "mode": "static",
            "pass": True,
            "total": 12,
            "aggregateRecall": 0.92,
        },
    )

    section = build_scorecard(tmp_path)["scorecards"]["architecture"]

    assert section["status"] == "supporting_only"
    assert section["passed"] is True
    assert section["claimAllowed"] is False
    assert section["metrics"]["recall"] == 0.92


def test_compile_only_multifile_results_cannot_claim_semantic_refactor(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "data/baseline/pass-at-k-kpi.json",
        {
            **_live_identity(),
            "mode": "live",
            "pass": True,
            "total": 12,
            "passAt1Rate": 0.9,
            "passRate": 1.0,
            "tiers": {
                "multifile_refactor": {
                    "cases": 12,
                    "pass_at_1_rate": 1.0,
                    "pass_at_k_rate": 1.0,
                }
            },
        },
    )

    scorecard = build_scorecard(tmp_path)

    assert scorecard["scorecards"]["compile_fix"]["claimAllowed"] is True
    assert scorecard["scorecards"]["semantic_refactor"]["status"] == "supporting_only"
    assert scorecard["scorecards"]["semantic_refactor"]["claimAllowed"] is False


def test_one_live_field_does_not_make_the_release_ready(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "data/baseline/compile-fix-kpi.json",
        {
            **_live_identity(),
            "mode": "live",
            "pass": True,
            "total": 20,
            "passAt1Rate": 0.9,
            "passRate": 1.0,
        },
    )

    scorecard = build_scorecard(tmp_path)
    markdown = render_markdown(scorecard)

    assert scorecard["scorecards"]["compile_fix"]["status"] == "live_verified"
    assert scorecard["scorecards"]["compile_fix"]["claimAllowed"] is True
    assert scorecard["releaseReadiness"]["ready"] is False
    assert "architecture" in scorecard["releaseReadiness"]["missingOrFailedLiveScorecards"]
    assert "combined AI grade" in markdown
    assert "Claim allowed" in markdown


def test_not_live_mode_is_not_promoted_by_substring_match(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "data/baseline/compile-fix-kpi.json",
        {
            **_live_identity(),
            "mode": "not-live",
            "pass": True,
            "total": 1,
            "passAt1Rate": 1.0,
            "passRate": 1.0,
        },
    )

    section = build_scorecard(tmp_path)["scorecards"]["compile_fix"]

    assert section["status"] == "not_run"
    assert section["claimAllowed"] is False


def test_live_pass_requires_auditable_identity_and_nonempty_measurements(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "data/baseline/compile-fix-kpi.json",
        {
            "mode": "live",
            "pass": True,
            "total": 0,
            "passAt1Rate": 1.0,
            "passRate": 1.0,
        },
    )

    section = build_scorecard(tmp_path)["scorecards"]["compile_fix"]

    assert section["status"] == "live_verified"
    assert section["passed"] is True
    assert section["claimAllowed"] is False
    assert section["identityComplete"] is False
    assert section["evidenceComplete"] is False
    assert any("commit/commitSha" in issue for issue in section["identityIssues"])
    assert any("positive whole number" in issue for issue in section["evidenceIssues"])


def test_malformed_live_measurement_fails_closed_without_crashing(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "data/baseline/compile-fix-kpi.json",
        {
            **_live_identity(),
            "mode": "live",
            "pass": True,
            "total": "many",
            "passAt1Rate": "perfect",
            "passRate": 1.0,
        },
    )

    section = build_scorecard(tmp_path)["scorecards"]["compile_fix"]

    assert section["metrics"]["cases"] == 0
    assert section["evidenceComplete"] is False
    assert section["claimAllowed"] is False
