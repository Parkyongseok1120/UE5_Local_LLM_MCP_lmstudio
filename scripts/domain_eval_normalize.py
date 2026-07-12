#!/usr/bin/env python
"""Normalize domain eval cases and execute structural planner assertions."""

from __future__ import annotations

import copy
from typing import Any


def normalize_eval_case(defaults: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    """Merge defaults and case without mutating inputs."""
    base = copy.deepcopy(defaults or {})
    incoming = copy.deepcopy(case or {})
    merged = copy.deepcopy(base)
    for key, value in incoming.items():
        if key == "checks" and isinstance(value, dict):
            checks = dict(merged.get("checks") or {})
            checks.update(value)
            merged["checks"] = checks
        else:
            merged[key] = value
    if "mode" in base and "mode" not in incoming and "mode" not in merged:
        merged["mode"] = base["mode"]
    for field in ("request", "mode"):
        if field in merged and not isinstance(merged[field], str):
            raise TypeError(f"case field {field} must be str")
    if "checks" in merged and not isinstance(merged["checks"], dict):
        raise TypeError("case checks must be dict")
    return merged


def evaluate_domain_case(case: dict[str, Any]) -> dict[str, Any]:
    from agent_orchestrator import build_agent_plan

    request = str(case.get("request") or "")
    mode = str(case.get("mode") or "auto")
    plan = build_agent_plan(request, mode)
    payload = plan.to_dict()
    checks = dict(case.get("checks") or {})
    results: dict[str, Any] = {"ok": True, "failures": []}

    def expect(field: str, expected: Any) -> None:
        actual = payload.get(field)
        if field == "taskKind":
            actual = plan.task_kind
        elif field == "primaryDomain":
            actual = (payload.get("domainProfile") or {}).get("primary")
        elif field == "executablePlanSize":
            actual = len(plan.executable_plan_slices or plan.plan_slices or [])
        elif field == "informationalPlanSize":
            actual = len(plan.informational_plan_slices or [])
        elif field == "writesAllowed":
            actual = (payload.get("writeGate") or {}).get("writesAllowed")
        if actual != expected:
            results["ok"] = False
            results["failures"].append({"field": field, "expected": expected, "actual": actual})

    for field, expected in checks.items():
        if field == "domainKind":
            actual = plan.domain_kind
            if actual != expected:
                results["ok"] = False
                results["failures"].append({"field": field, "expected": expected, "actual": actual})
            continue
        if field == "minPlanSlices":
            actual = len(plan.executable_plan_slices or [])
            if actual < int(expected):
                results["ok"] = False
                results["failures"].append({"field": field, "expected": expected, "actual": actual})
            continue
        if field == "maxExecutablePlanSlices":
            actual = len(plan.executable_plan_slices or [])
            if actual > int(expected):
                results["ok"] = False
                results["failures"].append({"field": field, "expected": expected, "actual": actual})
            continue
        expect(field, expected)
    results["plan"] = {
        "taskKind": plan.task_kind,
        "domainKind": plan.domain_kind,
        "executablePlanSize": len(plan.executable_plan_slices or []),
        "informationalPlanSize": len(plan.informational_plan_slices or []),
        "writesAllowed": (payload.get("writeGate") or {}).get("writesAllowed"),
    }
    return results
