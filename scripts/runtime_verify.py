#!/usr/bin/env python
"""Single-manifest Unreal runtime verification orchestration.

The Automation test owns gameplay setup (including multi-client topology).  This
module owns environment binding, bounded process execution, fresh artifacts,
and fail-closed assertion-to-test evidence.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from editor_export_runner import resolve_editor_executable
from runtime_experiment_runner import (
    build_unreal_experiment_plan,
    run_unreal_experiment_plan,
)
from workspace_paths import (
    engine_association_version,
    resolve_engine_root_for_association,
)

SUPPORTED_SCENARIOS = frozenset(
    {"automation", "network_replication", "travel_lifecycle", "asset_contract"}
)
SUPPORTED_NET_MODES = frozenset({"standalone", "listen_server", "dedicated_server"})
_ASSERTION_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")


def _engine_version(root: Path) -> str:
    build_version = root / "Engine" / "Build" / "Build.version"
    try:
        payload = json.loads(build_version.read_text(encoding="utf-8-sig"))
        major = int(payload.get("MajorVersion"))
        minor = int(payload.get("MinorVersion"))
        return f"{major}.{minor}"
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        match = re.search(r"UE[_ -]?(\d+(?:\.\d+)+)(?:\D|$)", root.name, re.IGNORECASE)
        return match.group(1) if match else ""


def _project_descriptor(project_file: str | Path) -> tuple[Path | None, dict[str, Any], list[str]]:
    path = Path(project_file).expanduser()
    issues: list[str] = []
    if not path.is_absolute():
        path = path.resolve()
    if path.suffix.casefold() != ".uproject" or not path.is_file():
        return None, {}, ["projectFile must resolve to an existing .uproject file"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, {}, [f"projectFile is unreadable or malformed: {exc}"]
    if not isinstance(payload, dict):
        issues.append("projectFile root must be a JSON object")
    return path.resolve(), payload if isinstance(payload, dict) else {}, issues


def _normalize_assertions(value: Any) -> tuple[list[dict[str, str]], list[str]]:
    rows = value if isinstance(value, list) else []
    assertions: list[dict[str, str]] = []
    issues: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(rows):
        if not isinstance(item, dict):
            issues.append(f"assertions[{index}] must be an object")
            continue
        assertion_id = str(item.get("id") or "").strip()
        test_name = str(item.get("automationTest") or "").strip()
        if not _ASSERTION_ID_RE.fullmatch(assertion_id):
            issues.append(f"assertions[{index}].id is invalid")
        if not test_name or any(char in test_name for char in (";", ",", "\r", "\n")):
            issues.append(f"assertions[{index}].automationTest is invalid")
        key = assertion_id.casefold()
        if key in seen:
            issues.append(f"duplicate assertion id: {assertion_id}")
        seen.add(key)
        assertions.append({"id": assertion_id, "automationTest": test_name})
    if not assertions:
        issues.append("at least one assertion with an exact automationTest is required")
    return assertions, issues


def build_runtime_verify_plan(
    manifest: dict[str, Any],
    *,
    project_file: str | Path,
    engine_root: str | Path | None = None,
    editor_cmd: str | Path | None = None,
    host_platform: str | None = None,
    allow_engine_fallback: bool = False,
) -> dict[str, Any]:
    host = host_platform or sys.platform
    scenario = str(manifest.get("scenario") or "automation").strip().casefold()
    net_mode = str(manifest.get("netMode") or "standalone").strip().casefold()
    topology_owner = str(manifest.get("topologyOwner") or "").strip().casefold()
    automation_filter = str(manifest.get("automationFilter") or "").strip()
    assertions, issues = _normalize_assertions(manifest.get("assertions"))
    project, descriptor, project_issues = _project_descriptor(project_file)
    issues.extend(project_issues)
    if scenario not in SUPPORTED_SCENARIOS:
        issues.append(f"scenario must be one of: {', '.join(sorted(SUPPORTED_SCENARIOS))}")
    if net_mode not in SUPPORTED_NET_MODES:
        issues.append(f"netMode must be one of: {', '.join(sorted(SUPPORTED_NET_MODES))}")
    try:
        clients = max(1, min(8, int(manifest.get("clients") or 1)))
    except (TypeError, ValueError):
        clients = 1
        issues.append("clients must be an integer")
    assertion_ids = {item["id"].casefold() for item in assertions}
    if scenario == "network_replication":
        if clients < 2:
            issues.append("network_replication requires clients >= 2")
        if net_mode == "standalone":
            issues.append("network_replication requires listen_server or dedicated_server")
        if topology_owner != "automation_test":
            issues.append("network topology must be owned by the declared Automation test")
        for required in ("rpc_owner", "replicated_state"):
            if required not in assertion_ids:
                issues.append(f"network_replication requires assertion id: {required}")
    if scenario == "travel_lifecycle" and not any("travel" in item for item in assertion_ids):
        issues.append("travel_lifecycle requires a travel assertion id")
    if scenario == "asset_contract" and not any("asset" in item for item in assertion_ids):
        issues.append("asset_contract requires an asset assertion id")
    if not automation_filter or any(char in automation_filter for char in (";", ",", "\r", "\n")):
        issues.append("automationFilter is required and cannot contain command separators")

    association = str(descriptor.get("EngineAssociation") or "").strip()
    resolution = resolve_engine_root_for_association(
        association,
        explicit_engine_root=engine_root,
        host_platform=host,
    )
    if not bool(resolution.get("ok")):
        issues.append(str(resolution.get("error") or "ENGINE_ASSOCIATION_UNRESOLVED"))
    resolved_root_text = str(resolution.get("engineRoot") or "")
    resolved_root = Path(resolved_root_text) if resolved_root_text else Path()
    resolved_version = _engine_version(resolved_root) if str(resolved_root) not in {"", "."} else ""
    requested_version = engine_association_version(association)
    if requested_version and resolved_version != requested_version and not allow_engine_fallback:
        issues.append(
            f"exact engine binding required: project={requested_version}, resolved={resolved_version or 'missing'}"
        )
    resolved_editor = Path(editor_cmd).expanduser().resolve() if editor_cmd and str(editor_cmd).strip() else None
    if resolved_editor is None and str(resolved_root) not in {"", "."}:
        try:
            resolved_editor = resolve_editor_executable(resolved_root, host)
        except FileNotFoundError as exc:
            issues.append(str(exc))
    if (resolved_editor is None or not resolved_editor.is_file()) and bool(resolution.get("ok")):
        issues.append("UnrealEditor-Cmd executable could not be resolved")

    experiment = build_unreal_experiment_plan(
        editor_cmd=str(resolved_editor or ""),
        project_file=str(project or project_file),
        automation_filter=automation_filter,
        trace_channels=[str(item) for item in manifest.get("traceChannels") or []],
        trace_output=str(manifest.get("traceOutput") or ""),
        soak_iterations=manifest.get("soakIterations", 1),
        map_name=str(manifest.get("mapName") or ""),
        dedicated_server=net_mode == "dedicated_server",
        timeout_seconds=manifest.get("timeoutSeconds", 1800),
        automation_report_path=str(
            manifest.get("reportPath") or "Saved/Automation/RuntimeVerify"
        ),
        require_trace=manifest.get("requireTrace"),
        unreal_insights_cmd=str(manifest.get("unrealInsightsCmd") or ""),
    )
    issues.extend(str(item) for item in experiment.get("issues") or [])
    from unreal_capability_detection import detect_unreal_capabilities

    capabilities = detect_unreal_capabilities(
        project or project_file,
        engine_root=resolved_root,
        host_platform=host,
    )
    return {
        "ok": not issues,
        "errorCode": "" if not issues else str(resolution.get("errorCode") or "INVALID_RUNTIME_VERIFY_PLAN"),
        "issues": list(dict.fromkeys(issues)),
        "manifest": {
            "scenario": scenario,
            "clients": clients,
            "netMode": net_mode,
            "topologyOwner": topology_owner,
            "automationFilter": automation_filter,
            "assertions": assertions,
            "assetPaths": [str(item) for item in manifest.get("assetPaths") or []],
        },
        "environment": {
            "hostPlatform": host,
            "projectFile": str(project or ""),
            "engineAssociation": association,
            "engineVersion": resolved_version,
            "engineRoot": str(resolved_root) if str(resolved_root) != "." else "",
            "editorCmd": str(resolved_editor or ""),
            "exactEngineBinding": bool(
                bool(resolution.get("ok"))
                and (not requested_version or requested_version == resolved_version or allow_engine_fallback)
            ),
            "engineResolutionSource": str(resolution.get("source") or ""),
            "engineResolutionErrorCode": str(resolution.get("errorCode") or ""),
            "capabilities": capabilities,
        },
        "experimentPlan": experiment,
        "proofBoundary": (
            "The orchestration process does not invent gameplay topology. The exact Automation tests "
            "declared by assertions must create/observe their clients, RPC ownership, travel, or assets."
        ),
    }


def run_runtime_verify_plan(
    plan: dict[str, Any],
    *,
    runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    if not plan.get("ok"):
        return {"ok": False, "errorCode": "INVALID_RUNTIME_VERIFY_PLAN", "plan": plan}
    kwargs = {} if runner is None else {"runner": runner}
    result = run_unreal_experiment_plan(plan["experimentPlan"], **kwargs)
    assertions = list((plan.get("manifest") or {}).get("assertions") or [])
    proof: list[dict[str, Any]] = []
    for assertion in assertions:
        expected = str(assertion.get("automationTest") or "").casefold()
        iterations: list[dict[str, Any]] = []
        for run in result.get("runs") or []:
            matches = [
                item
                for item in (run.get("automationReport") or {}).get("tests") or []
                if str(item.get("name") or "").casefold() == expected
            ]
            iterations.append(
                {
                    "iteration": run.get("iteration"),
                    "executed": bool(matches),
                    "passed": bool(matches) and all(item.get("state") == "passed" for item in matches),
                }
            )
        passed = bool(iterations) and len(iterations) == int(result.get("requestedIterations") or 0) and all(
            item["executed"] and item["passed"] for item in iterations
        )
        proof.append({**assertion, "passed": passed, "iterations": iterations})
    all_assertions_passed = bool(proof) and all(item["passed"] for item in proof)
    ok = bool(result.get("ok") and all_assertions_passed)
    return {
        **result,
        "ok": ok,
        "errorCode": "" if ok else "RUNTIME_ASSERTION_FAILED",
        "scenario": (plan.get("manifest") or {}).get("scenario"),
        "environment": plan.get("environment") or {},
        "assertionProof": proof,
        "proofLevel": "RuntimeVerified" if ok else "NeedsRuntimeProof",
        "proofBoundary": plan.get("proofBoundary"),
    }
