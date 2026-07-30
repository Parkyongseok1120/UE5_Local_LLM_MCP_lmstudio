#!/usr/bin/env python
"""Build evidence-separated capability scorecards without a synthetic AI grade."""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from atomic_io import atomic_write_text

SCORECARD_NAMES = (
    "compile_fix",
    "architecture",
    "semantic_refactor",
    "runtime_debug",
    "negative_control",
    "orchestration_ux",
)
LIVE_MODES = frozenset(
    {"live", "live-model", "lmstudio-live", "model-live", "runtime-live"}
)
STATIC_MODES = frozenset({"static", "fixture", "deterministic", "ci"})
NON_EXECUTION_MODES = frozenset({"metrics-only", "dry-run", "not-run"})


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def latest_report(reports_dir: Path) -> Path | None:
    if not reports_dir.is_dir():
        return None
    candidates = sorted(
        reports_dir.glob("*/summary.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _identity(data: dict[str, Any] | None) -> dict[str, Any]:
    payload = data or {}
    return {
        key: payload.get(key)
        for key in (
            "generatedAt",
            "commit",
            "commitSha",
            "model",
            "modelId",
            "profile",
            "mode",
            "tier",
            "config",
            "suite",
            "seed",
        )
        if payload.get(key) not in (None, "")
    }


def _proof_status(data: dict[str, Any] | None) -> str:
    if not data:
        return "not_run"
    mode = str(data.get("mode") or "").strip().lower().replace("_", "-")
    status = str(data.get("status") or "").strip().lower().replace("_", "-")
    if mode in NON_EXECUTION_MODES or status in {
        "metrics-only",
        "not-run",
        "not-executed",
    }:
        return "not_run"
    if mode in STATIC_MODES:
        return "static_verified"
    if mode in LIVE_MODES or (data.get("live") is True and not mode):
        return "live_verified"
    return "not_run"


def _live_identity_issues(
    data: dict[str, Any] | None,
    proof: str,
    required_proof: str,
) -> list[str]:
    if proof != required_proof or required_proof != "live_verified":
        return []
    payload = data or {}
    missing: list[str] = []
    if not str(payload.get("generatedAt") or "").strip():
        missing.append("generatedAt")
    if not str(payload.get("commit") or payload.get("commitSha") or "").strip():
        missing.append("commit/commitSha")
    if not str(payload.get("model") or payload.get("modelId") or "").strip():
        missing.append("model/modelId")
    if not str(payload.get("suite") or payload.get("config") or "").strip():
        missing.append("suite/config")
    return [f"missing live suite identity: {field}" for field in missing]


def _measurement_issues(
    *,
    cases: Any,
    required_metrics: dict[str, Any],
    metric_minimum: float = 0.0,
    metric_maximum: float | None = None,
) -> list[str]:
    issues: list[str] = []
    if (
        not isinstance(cases, (int, float))
        or isinstance(cases, bool)
        or not math.isfinite(float(cases))
        or cases <= 0
        or not float(cases).is_integer()
    ):
        issues.append("executed case count must be a positive whole number")
    for name, value in required_metrics.items():
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
        ):
            issues.append(f"required metric is missing or non-numeric: {name}")
        elif value < metric_minimum or (
            metric_maximum is not None and value > metric_maximum
        ):
            expected = (
                f"{metric_minimum}..{metric_maximum}"
                if metric_maximum is not None
                else f">= {metric_minimum}"
            )
            issues.append(f"required metric is outside {expected}: {name}")
    return issues


def _safe_int(value: Any) -> int:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
    ):
        return 0
    return int(value)


def _section(
    name: str,
    data: dict[str, Any] | None,
    *,
    metrics: dict[str, Any],
    source: Path | None,
    required_proof: str,
    note: str,
    proof_override: str | None = None,
    evidence_issues: list[str] | None = None,
) -> dict[str, Any]:
    detected_proof = _proof_status(data)
    proof = (
        proof_override
        if proof_override and detected_proof != "not_run"
        else detected_proof
    )
    passed = bool(data and data.get("pass") is True and proof != "not_run")
    identity_issues = _live_identity_issues(data, proof, required_proof)
    measurement_issues = list(evidence_issues or [])
    identity_complete = not identity_issues
    evidence_complete = not measurement_issues
    claim_allowed = (
        proof == required_proof
        and passed
        and identity_complete
        and evidence_complete
    )
    return {
        "name": name,
        "status": proof,
        "passed": passed,
        "requiredProof": required_proof,
        "claimAllowed": claim_allowed,
        "identityComplete": identity_complete,
        "identityIssues": identity_issues,
        "evidenceComplete": evidence_complete,
        "evidenceIssues": measurement_issues,
        "metrics": metrics,
        "identity": _identity(data),
        "source": str(source) if source else None,
        "note": note,
    }


def _first_existing_json(paths: list[Path]) -> tuple[dict[str, Any] | None, Path | None]:
    for path in paths:
        payload = load_json(path)
        if payload is not None:
            return payload, path
    return None, paths[0] if paths else None


def build_scorecard(root: Path) -> dict[str, Any]:
    baseline = root / "data" / "baseline"
    release_evidence = root / "docs" / "release_evidence"

    compile_data, compile_path = _first_existing_json(
        [baseline / "compile-fix-kpi.json", baseline / "pass-at-k-kpi.json"]
    )
    architecture_data, architecture_path = _first_existing_json(
        [baseline / "architecture-kpi.json", baseline / "project-review-kpi.json"]
    )
    semantic_data, semantic_path = _first_existing_json(
        [baseline / "semantic-refactor-kpi.json", baseline / "pass-at-k-kpi.json"]
    )
    runtime_data, runtime_path = _first_existing_json(
        [baseline / "runtime-debug-kpi.json", baseline / "soulslike-live-kpi.json"]
    )
    negative_data, negative_path = _first_existing_json(
        [baseline / "negative-control-kpi.json", baseline / "pass-at-k-kpi.json"]
    )
    orchestration_data, orchestration_path = _first_existing_json(
        [
            baseline / "orchestration-ux-kpi.json",
            release_evidence / "qwen3_6-27b_lmstudio_eval.json",
        ]
    )

    pass_tiers = (semantic_data or {}).get("tiers") or {}
    multifile = pass_tiers.get("multifile_refactor") or {}
    compile_cases = (compile_data or {}).get("total")
    architecture_cases = (architecture_data or {}).get("total")
    semantic_cases = multifile.get("cases")
    runtime_cases = (runtime_data or {}).get("total")
    negative_cases = (negative_data or {}).get("total")
    orchestration_calls = (orchestration_data or {}).get("callCount")
    scorecards = {
        "compile_fix": _section(
            "compile_fix",
            compile_data,
            metrics={
                "cases": _safe_int(compile_cases),
                "passAt1Rate": (compile_data or {}).get("passAt1Rate"),
                "passAtKRate": (compile_data or {}).get("passRate"),
                "averageAttempts": (compile_data or {}).get("averageAttempts"),
            },
            source=compile_path,
            required_proof="live_verified",
            note="Requires an actual LM Studio/model run plus UBT/static validation.",
            evidence_issues=_measurement_issues(
                cases=compile_cases,
                required_metrics={
                    "passAt1Rate": (compile_data or {}).get("passAt1Rate"),
                    "passRate": (compile_data or {}).get("passRate"),
                },
                metric_maximum=1.0,
            ),
        ),
        "architecture": _section(
            "architecture",
            architecture_data,
            metrics={
                "cases": _safe_int(architecture_cases),
                "recall": (architecture_data or {}).get("aggregateRecall"),
            },
            source=architecture_path,
            required_proof="live_verified",
            note="Static source fixtures are useful regression evidence but not model-live architecture proof.",
            proof_override=(
                "supporting_only"
                if architecture_path and architecture_path.name != "architecture-kpi.json"
                else None
            ),
            evidence_issues=_measurement_issues(
                cases=architecture_cases,
                required_metrics={
                    "aggregateRecall": (architecture_data or {}).get("aggregateRecall")
                },
                metric_maximum=1.0,
            ),
        ),
        "semantic_refactor": _section(
            "semantic_refactor",
            semantic_data,
            metrics={
                "cases": _safe_int(semantic_cases),
                "passAt1Rate": multifile.get("pass_at_1_rate"),
                "passAtKRate": multifile.get("pass_at_k_rate"),
                "averageAttempts": multifile.get("avg_attempts"),
            },
            source=semantic_path,
            required_proof="live_verified",
            note="Requires a semantic oracle; compile success alone is insufficient.",
            proof_override=(
                "supporting_only"
                if semantic_path and semantic_path.name != "semantic-refactor-kpi.json"
                else None
            ),
            evidence_issues=_measurement_issues(
                cases=semantic_cases,
                required_metrics={
                    "pass_at_1_rate": multifile.get("pass_at_1_rate"),
                    "pass_at_k_rate": multifile.get("pass_at_k_rate"),
                },
                metric_maximum=1.0,
            ),
        ),
        "runtime_debug": _section(
            "runtime_debug",
            runtime_data,
            metrics={
                "cases": _safe_int(runtime_cases),
                "beforeRedAfterGreenRate": (runtime_data or {}).get(
                    "beforeRedAfterGreenRate"
                ),
            },
            source=runtime_path,
            required_proof="live_verified",
            note="Requires the same-observer before-red/after-green runtime oracle.",
            proof_override=(
                "supporting_only"
                if runtime_path and runtime_path.name != "runtime-debug-kpi.json"
                else None
            ),
            evidence_issues=_measurement_issues(
                cases=runtime_cases,
                required_metrics={
                    "beforeRedAfterGreenRate": (runtime_data or {}).get(
                        "beforeRedAfterGreenRate"
                    )
                },
                metric_maximum=1.0,
            ),
        ),
        "negative_control": _section(
            "negative_control",
            negative_data,
            metrics={
                "cases": _safe_int(negative_cases),
                "wrongFileEditCount": _safe_int(
                    (negative_data or {}).get("wrongFileEditCount")
                ),
                "buildCsFalsePositiveCount": _safe_int(
                    (negative_data or {}).get("buildCsFalsePositiveCount")
                ),
                "forbiddenPatchHitCount": _safe_int(
                    (negative_data or {}).get("forbiddenPatchHitCount")
                ),
                "sameErrorRepeatedCount": _safe_int(
                    (negative_data or {}).get("sameErrorRepeatedCount")
                ),
                "noOpEditCount": _safe_int(
                    (negative_data or {}).get("noOpEditCount")
                ),
            },
            source=negative_path,
            required_proof="live_verified",
            note="Zero counters in metrics-only mode are not execution proof.",
            proof_override=(
                "supporting_only"
                if negative_path and negative_path.name != "negative-control-kpi.json"
                else None
            ),
            evidence_issues=_measurement_issues(
                cases=negative_cases,
                required_metrics={
                    key: (negative_data or {}).get(key)
                    for key in (
                        "wrongFileEditCount",
                        "buildCsFalsePositiveCount",
                        "forbiddenPatchHitCount",
                        "sameErrorRepeatedCount",
                        "noOpEditCount",
                    )
                },
            ),
        ),
        "orchestration_ux": _section(
            "orchestration_ux",
            orchestration_data,
            metrics={
                key: (orchestration_data or {}).get(key)
                for key in (
                    "routingAccuracy",
                    "toolSelectionAccuracy",
                    "argumentAccuracy",
                    "recoveryAccuracy",
                    "callCount",
                )
            },
            source=orchestration_path,
            required_proof="live_verified",
            note="Requires recorded per-call routing, tool, argument, and recovery outcomes.",
            proof_override=(
                "supporting_only"
                if orchestration_path
                and orchestration_path.name != "orchestration-ux-kpi.json"
                else None
            ),
            evidence_issues=_measurement_issues(
                cases=orchestration_calls,
                required_metrics={
                    key: (orchestration_data or {}).get(key)
                    for key in (
                        "routingAccuracy",
                        "toolSelectionAccuracy",
                        "argumentAccuracy",
                        "recoveryAccuracy",
                    )
                },
                metric_maximum=1.0,
            ),
        ),
    }

    release_ready = all(
        scorecards[name]["claimAllowed"] is True
        for name in SCORECARD_NAMES
    )
    missing_live = [
        name
        for name in SCORECARD_NAMES
        if scorecards[name]["claimAllowed"] is not True
    ]
    return {
        "schemaVersion": 2,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "scorecards": scorecards,
        "releaseReadiness": {
            "ready": release_ready,
            "requiredScorecards": list(SCORECARD_NAMES),
            "missingOrFailedLiveScorecards": missing_live,
        },
        "claimPolicy": {
            "combinedAiGradeAllowed": False,
            "crossModelEquivalenceAllowed": False,
            "metricsOnlyCountsAsExecution": False,
            "rule": (
                "Report each field with its own suite identity and proof level. "
                "Do not merge these scorecards into one AI score."
            ),
        },
    }


def _format_metric(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float) and 0 <= value <= 1:
        return f"{value:.1%}"
    return str(value)


def render_markdown(scorecard: dict[str, Any]) -> str:
    lines = [
        "# Capability KPI Scorecards",
        "",
        f"Generated: {scorecard['generatedAt']}",
        "",
        "| Field | Evidence | Passed | Claim allowed | Key metrics |",
        "|---|---|---:|---:|---|",
    ]
    for name in SCORECARD_NAMES:
        section = scorecard["scorecards"][name]
        metrics = ", ".join(
            f"{key}={_format_metric(value)}"
            for key, value in section["metrics"].items()
        )
        lines.append(
            f"| `{name}` | `{section['status']}` | "
            f"{'YES' if section['passed'] else 'NO'} | "
            f"{'YES' if section['claimAllowed'] else 'NO'} | {metrics} |"
        )
    readiness = scorecard["releaseReadiness"]
    lines.extend(
        [
            "",
            "## Release evidence",
            "",
            f"- All required live scorecards ready: {'YES' if readiness['ready'] else 'NO'}",
            "- Missing or failed: "
            + (
                ", ".join(readiness["missingOrFailedLiveScorecards"])
                if readiness["missingOrFailedLiveScorecards"]
                else "none"
            ),
            "",
            "> Metrics-only and skipped runs are not execution proof. "
            "These fields must not be collapsed into a combined AI grade or model-equivalence claim.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Report evidence-separated capability KPI scorecards."
    )
    parser.add_argument("--root", default="")
    parser.add_argument(
        "--require-release-ready",
        action="store_true",
        help="Exit non-zero unless every required field has passing live evidence.",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent.parent
    baseline = root / "data" / "baseline"
    baseline.mkdir(parents=True, exist_ok=True)
    scorecard = build_scorecard(root)
    out_json = baseline / "tier-kpi-latest.json"
    out_md = baseline / "tier-kpi-latest.md"
    atomic_write_text(
        out_json,
        json.dumps(scorecard, ensure_ascii=False, indent=2) + "\n",
    )
    atomic_write_text(out_md, render_markdown(scorecard))
    print(f"Wrote {out_json}")
    print(f"Wrote {out_md}")
    print(
        "Live scorecards ready: "
        f"{len(SCORECARD_NAMES) - len(scorecard['releaseReadiness']['missingOrFailedLiveScorecards'])}"
        f"/{len(SCORECARD_NAMES)}"
    )
    if args.require_release_ready and not scorecard["releaseReadiness"]["ready"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
