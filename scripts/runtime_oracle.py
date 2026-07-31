#!/usr/bin/env python
"""Deterministic ranking and metric oracles for Unreal runtime evidence."""

from __future__ import annotations

from typing import Any

RUNTIME_EVIDENCE_KINDS = frozenset(
    {"runtime", "log", "trace", "debugger", "automation"}
)
OBSERVER_COMPARISONS = frozenset(
    {"increase", "decrease", "equals", "absent", "present", "boolean"}
)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _bounded(value: Any, minimum: float, maximum: float, default: float) -> float:
    return max(minimum, min(maximum, _number(value, default)))


def normalize_runtime_policy(value: Any) -> dict[str, Any]:
    row = value if isinstance(value, dict) else {}
    return {
        "requireTrace": bool(row.get("requireTrace")),
        "requireMetricOracle": bool(row.get("requireMetricOracle")),
        "requireArtifactHash": bool(row.get("requireArtifactHash")),
        "minSamples": max(1, int(_number(row.get("minSamples"), 1))),
        "minSoakIterations": max(
            1,
            int(_number(row.get("minSoakIterations"), 1)),
        ),
        "minDurationSec": max(0.0, _number(row.get("minDurationSec"), 0.0)),
        "maxErrors": max(0, int(_number(row.get("maxErrors"), 0))),
        "maxCrashes": max(0, int(_number(row.get("maxCrashes"), 0))),
        "maxTimeouts": max(0, int(_number(row.get("maxTimeouts"), 0))),
    }


def normalize_runtime_evidence(value: Any) -> dict[str, Any]:
    row = value if isinstance(value, dict) else {}
    signal_value = row.get("signalValue")
    default_samples = 1 if row.get("observation") else 0
    return {
        "kind": str(row.get("kind") or "").strip().lower(),
        "location": str(row.get("location") or "").strip(),
        "observation": str(row.get("observation") or "").strip(),
        "artifactHash": str(
            row.get("artifactHash") or row.get("artifact_hash") or ""
        ).strip(),
        "signalValue": signal_value,
        "unit": str(row.get("unit") or "").strip(),
        "sampleCount": max(
            0,
            int(_number(row.get("sampleCount"), default_samples)),
        ),
        "soakIterations": max(
            0,
            int(_number(row.get("soakIterations"), default_samples)),
        ),
        "durationSec": max(0.0, _number(row.get("durationSec"), 0.0)),
        "errorCount": max(0, int(_number(row.get("errorCount"), 0))),
        "crashCount": max(0, int(_number(row.get("crashCount"), 0))),
        "timeoutCount": max(0, int(_number(row.get("timeoutCount"), 0))),
        "traceSummary": (
            dict(row.get("traceSummary"))
            if isinstance(row.get("traceSummary"), dict)
            else {}
        ),
    }


def rank_runtime_hypotheses(values: Any) -> list[dict[str, Any]]:
    rows = values if isinstance(values, list) else []
    ranked: list[dict[str, Any]] = []
    for index, item in enumerate(rows):
        if not isinstance(item, dict):
            continue
        claim = str(item.get("claim") or "").strip()
        if not claim:
            continue
        supporting = [
            str(value).strip()
            for value in (item.get("supportingEvidence") or [])
            if str(value).strip()
        ]
        counter = [
            str(value).strip()
            for value in (item.get("counterEvidence") or [])
            if str(value).strip()
        ]
        confidence = _bounded(item.get("priorConfidence"), 0.0, 1.0, 0.5)
        cost = _bounded(item.get("estimatedCost"), 1.0, 5.0, 3.0)
        blast_radius = _bounded(item.get("blastRadius"), 1.0, 5.0, 3.0)
        information_gain = _bounded(item.get("informationGain"), 1.0, 5.0, 3.0)
        evidence_balance = max(-5, min(5, len(supporting) - len(counter)))
        score = (
            confidence * 40.0
            + information_gain * 8.0
            + evidence_balance * 3.0
            - cost * 4.0
            - blast_radius * 3.0
        )
        ranked.append(
            {
                "id": str(item.get("id") or f"h{index + 1}").strip(),
                "claim": claim,
                "falsification": str(
                    item.get("falsification")
                    or item.get("falsificationPlan")
                    or ""
                ).strip(),
                "supportingEvidence": supporting,
                "counterEvidence": counter,
                "priorConfidence": confidence,
                "estimatedCost": cost,
                "blastRadius": blast_radius,
                "informationGain": information_gain,
                "priorityScore": round(score, 3),
                "status": str(item.get("status") or "open"),
            }
        )
    ranked.sort(key=lambda item: (-item["priorityScore"], item["id"]))
    for rank, item in enumerate(ranked, start=1):
        item["rank"] = rank
    return ranked


def evaluate_runtime_oracle(
    *,
    observer: dict[str, Any],
    baseline_evidence: dict[str, Any],
    after_evidence: dict[str, Any],
    runtime_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    policy = normalize_runtime_policy(runtime_policy)
    baseline = normalize_runtime_evidence(baseline_evidence)
    after = normalize_runtime_evidence(after_evidence)
    comparison = str(observer.get("comparison") or "").strip().lower()
    tolerance = max(0.0, _number(observer.get("tolerance"), 0.0))
    target = observer.get("targetValue")
    issues: list[str] = []

    if after["kind"] not in RUNTIME_EVIDENCE_KINDS:
        issues.append("after evidence kind is not runtime-verifiable")
    if after["sampleCount"] < policy["minSamples"]:
        issues.append("sample count is below runtime policy")
    if after["soakIterations"] < policy["minSoakIterations"]:
        issues.append("soak iteration count is below runtime policy")
    if after["durationSec"] < policy["minDurationSec"]:
        issues.append("duration is below runtime policy")
    if after["errorCount"] > policy["maxErrors"]:
        issues.append("error count exceeds runtime policy")
    if after["crashCount"] > policy["maxCrashes"]:
        issues.append("crash count exceeds runtime policy")
    if after["timeoutCount"] > policy["maxTimeouts"]:
        issues.append("timeout count exceeds runtime policy")
    if policy["requireTrace"] and (
        after["kind"] != "trace" or not after["traceSummary"]
    ):
        issues.append("trace summary is required by runtime policy")
    if policy["requireArtifactHash"] and not after["artifactHash"]:
        issues.append("artifact hash is required by runtime policy")
    if policy["requireMetricOracle"] and not comparison:
        issues.append("observer comparison is required by runtime policy")

    relation_passed: bool | None = None
    trace_metric = str(observer.get("traceMetric") or "").strip()
    baseline_value = baseline.get("signalValue")
    after_value = after.get("signalValue")
    if trace_metric:
        baseline_value = baseline["traceSummary"].get(trace_metric)
        after_value = after["traceSummary"].get(trace_metric)
    if comparison:
        if comparison not in OBSERVER_COMPARISONS:
            issues.append("observer comparison is unsupported")
        elif comparison in {"increase", "decrease"}:
            if baseline_value is None or after_value is None:
                issues.append("baseline and after signalValue are required")
            else:
                before = _number(baseline_value)
                current = _number(after_value)
                relation_passed = (
                    current > before + tolerance
                    if comparison == "increase"
                    else current < before - tolerance
                )
        elif comparison == "equals":
            if after_value is None or target is None:
                issues.append("after signalValue and observer.targetValue are required")
            elif isinstance(target, (int, float)) or isinstance(after_value, (int, float)):
                relation_passed = abs(_number(after_value) - _number(target)) <= tolerance
            else:
                relation_passed = str(after_value) == str(target)
        elif comparison == "absent":
            relation_passed = (
                after_value is None
                or after_value == ""
                or after_value is False
                or after_value == 0
            )
        elif comparison == "present":
            relation_passed = not (
                after_value is None or after_value == "" or after_value is False
            )
        elif comparison == "boolean":
            relation_passed = bool(after_value) is bool(target)
        if relation_passed is False:
            issues.append("observer relation did not meet the expected condition")

    return {
        "resolved": not issues,
        "relationEvaluated": relation_passed is not None,
        "relationPassed": relation_passed,
        "issues": issues,
        "policy": policy,
        "baselineValue": baseline_value,
        "afterValue": after_value,
        "traceMetric": trace_metric,
        "delta": (
            _number(after_value) - _number(baseline_value)
            if isinstance(after_value, (int, float))
            and isinstance(baseline_value, (int, float))
            else None
        ),
        "proofBoundary": (
            "Runtime evidence summary was evaluated; binary .utrace contents are not "
            "independently parsed by this oracle."
        ),
    }
