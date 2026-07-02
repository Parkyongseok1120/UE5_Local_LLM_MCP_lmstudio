#!/usr/bin/env python
"""Aggregate Tier A/B KPI baselines into a single scorecard."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def load_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None


def latest_report(reports_dir: Path) -> Path | None:
    if not reports_dir.is_dir():
        return None
    candidates = sorted(reports_dir.glob("*/summary.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def gate_score(data: dict | None) -> tuple[int, int]:
    if not data:
        return 0, 0
    total = int(data.get("total") or 0)
    passed = int(data.get("passCount") or sum(1 for r in data.get("results") or [] if r.get("pass")))
    return passed, total


def main() -> int:
    parser = argparse.ArgumentParser(description="Report Tier A/B KPI scorecard.")
    parser.add_argument("--root", default="")
    args = parser.parse_args()

    root = Path(args.root) if args.root else Path(__file__).resolve().parent.parent
    baseline = root / "data" / "baseline"

    sonnet = load_json(baseline / "sonnet-tier-latest.json")
    reasoning = load_json(baseline / "reasoning-kpi.json")
    project_review = load_json(baseline / "project-review-kpi.json")
    soulslike = load_json(baseline / "soulslike-live-kpi.json")
    pass_at_k = load_json(baseline / "pass-at-k-kpi.json")

    harness_path = latest_report(root / "Reports")
    harness = load_json(harness_path) if harness_path else None

    gate_pass, gate_total = gate_score(sonnet)
    harness_pass, harness_total = gate_score(harness)

    reasoning_score = float(reasoning.get("score") or 0) if reasoning else 0
    review_recall = float(project_review.get("aggregateRecall") or 0) if project_review else 0
    pass_rate = float(pass_at_k.get("passRate") or 0) if pass_at_k else None

    tier_a_ok = gate_pass == gate_total and gate_total >= 12
    soulslike_pass = soulslike.get("pass") if soulslike else None
    project_review_live_pass = (
        project_review.get("pass")
        if project_review and project_review.get("mode") == "live"
        else None
    )
    tier_b_ok = (
        soulslike_pass is True
        and project_review_live_pass is True
        and (pass_rate is None or pass_rate >= 0.67)
    )

    estimated = 8.5
    if tier_a_ok:
        estimated = 8.6
    if tier_a_ok and reasoning_score >= 80:
        estimated = 8.7
    if tier_a_ok and pass_rate is not None and pass_rate >= 1.0:
        estimated = 8.9
    if tier_a_ok and tier_b_ok and (pass_rate or 0) >= 0.67:
        estimated = 9.0

    scorecard = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "tierA": {"gatePass": gate_pass, "gateTotal": gate_total, "harnessPass": harness_pass, "harnessTotal": harness_total},
        "tierB": {
            "soulslikeLive": soulslike_pass,
            "projectReviewLive": project_review_live_pass,
            "passAtKRate": pass_rate,
        },
        "reasoningScore": reasoning_score,
        "projectReviewRecall": review_recall,
        "estimatedGradeOutOf10": estimated,
        "claim9_0": tier_a_ok and tier_b_ok and (pass_rate or 0) >= 0.67,
        "interpretation": {
            "scoreMeaning": "Internal UE RAG/MCP/UBT scorecard only; not an external standardized benchmark.",
            "recommendedPublicClaim": (
                "For UE C++ compile-fix/project-review only, the system showed practical behavior "
                "near upper Sonnet 3.7 to lower Sonnet 4 range under this RAG/MCP/UBT validation loop."
            ),
            "forbiddenOverclaim": "Do not state that Qwen 27B itself is Sonnet 4-grade or objectively 9.6/10.",
            "validationNextStep": "Run 20 unseen real-project errors and report Pass@1, Pass@3, and failure categories.",
            "forwardTarget": "Sonnet 4.5-oriented local Unreal workflow; target only, not a current model-grade claim.",
        },
        "sources": {
            "sonnetTier": str(baseline / "sonnet-tier-latest.json"),
            "harness": str(harness_path) if harness_path else None,
            "reasoning": str(baseline / "reasoning-kpi.json"),
            "projectReview": str(baseline / "project-review-kpi.json"),
            "soulslikeLive": str(baseline / "soulslike-live-kpi.json"),
            "passAtK": str(baseline / "pass-at-k-kpi.json"),
        },
    }

    out_json = baseline / "tier-kpi-latest.json"
    out_md = baseline / "tier-kpi-latest.md"
    out_json.write_text(json.dumps(scorecard, ensure_ascii=False, indent=2), encoding="utf-8")

    md_lines = [
        "# Tier KPI Scorecard",
        f"Generated: {scorecard['generatedAt']}",
        "",
        f"- Tier A gate: {gate_pass}/{gate_total}",
        f"- Eval harness: {harness_pass}/{harness_total}",
        f"- Reasoning: {reasoning_score}",
        f"- Project review recall: {review_recall:.0%}" if project_review else "- Project review: n/a",
        f"- Pass@K rate: {pass_rate:.0%}" if pass_rate is not None else "- Pass@K: not run",
        f"- **Estimated grade: {estimated}/10**",
        f"- 9.0 claim: {'YES' if scorecard['claim9_0'] else 'NO'}",
        "",
        "## Interpretation guardrail",
        "",
        "- This is an internal UE RAG/MCP/UBT scorecard, not an external standardized benchmark.",
        "- Do not claim that Qwen 27B itself is Sonnet 4-grade.",
        "- Safer wording: UE C++ compile-fix/project-review behavior approached upper Sonnet 3.7 to lower Sonnet 4 range inside this validation loop.",
        "- Next required check: 20 unseen real-project errors with Pass@1, Pass@3, and failure-type reporting.",
        "- Forward target: Sonnet 4.5-oriented local Unreal workflow. This is a target, not a current model-grade claim.",
    ]
    out_md.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    print(f"Wrote {out_json}")
    print(f"Wrote {out_md}")
    print(f"Estimated grade: {estimated}/10")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
