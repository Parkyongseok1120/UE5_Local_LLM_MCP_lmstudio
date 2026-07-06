#!/usr/bin/env python
"""Validate and scope Unreal refactor plans for agent and MCP tools."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

VALID_STAGES = {"R0", "R1", "R2", "R3", "R4", "r0", "r1", "r2", "r3", "r4"}
INVALID_UNREAL_LIFECYCLE_OVERRIDES = {
    "onworlddestroyed": "UWorldSubsystem does not expose OnWorldDestroyed in UE 5.8; use OnWorldEndPlay(UWorld&) or PreDeinitialize().",
    "worlddestroyed": "WorldDestroyed is not a standard UE subsystem lifecycle override; verify the direct base API before planning edits.",
}

FORBIDDEN_CODE_MARKERS = (
    "#include",
    "UCLASS(",
    "USTRUCT(",
    "GENERATED_BODY()",
    "void ",
    "bool ",
    "int32 ",
)

SSOT_MARKERS = ("ssot", "owner", "ownership", "single source")
RISK_MARKERS = ("risk", "impact", "hazard", "migration")
FILE_MARKERS = ("file", "path", ".h", ".cpp", "build.cs")
APPROVAL_MARKERS = ("approval", "approve", "human", "reviewer", "sign off", "gate")
STAGED_PATCH_MARKERS = ("stage", "step", "phased", "r0", "r1", "r2", "r3", "r4", "batch")
VALIDATION_MARKERS = (
    "ubt",
    "build",
    "automation",
    "functional test",
    "blueprint compile",
    "asset",
    "map load",
    "pie",
    "log scan",
)
MEDIUM_MARKERS = (
    "system",
    "combat",
    "inventory",
    "ability",
    "subsystem",
    "service",
    "manager",
    "api migration",
    "component api",
    "move api",
)
LARGE_MARKERS = (
    "architecture",
    "module boundary",
    "plugin",
    "blueprint",
    "asset rename",
    "dataasset",
    "data asset",
    "savegame",
    "save data",
    "network",
    "replication",
    "onrep",
    "map",
    "cook",
    "package",
)


def normalize_stage(stage: str) -> str:
    value = str(stage or "R0").strip().upper()
    if value not in {"R0", "R1", "R2", "R3", "R4"}:
        return "R0"
    return value


def _path_mentions(text: str) -> list[str]:
    return re.findall(r"\b[\w./\\-]+\.(?:h|hpp|cpp|cc|cxx|cs)\b", text or "", flags=re.IGNORECASE)


def classify_refactor_scope(
    text: str,
    *,
    file_count: int | None = None,
    impact_match_count: int | None = None,
) -> dict[str, Any]:
    """Classify refactor size so the agent can pick the right safety rails."""
    lowered = str(text or "").lower()
    explicit_files = len(set(_path_mentions(text)))
    estimated_files = max(file_count or 0, impact_match_count or 0, explicit_files)
    medium_hits = [marker for marker in MEDIUM_MARKERS if marker in lowered]
    large_hits = [marker for marker in LARGE_MARKERS if marker in lowered]
    has_multifile = any(
        marker in lowered
        for marker in (
            "callsite",
            "declaration",
            "definition",
            "override",
            "delegate",
            "interface",
            "header",
            "cpp",
            "multi-file",
            "multifile",
        )
    )

    if large_hits or estimated_files > 20:
        scope = "large_migration"
        writes_allowed = False
        requires_approval = True
        gates = [
            "impact_analysis",
            "symbol_graph_expansion",
            "architecture_rule_check",
            "human_approval_gate",
            "blueprint_asset_validation",
            "staged_patch_plan",
        ]
    elif medium_hits or estimated_files > 5:
        scope = "medium_system_local_refactor"
        writes_allowed = False
        requires_approval = True
        gates = [
            "impact_analysis",
            "symbol_graph_expansion",
            "architecture_rule_check",
            "human_approval_gate",
            "staged_patch_plan",
        ]
    elif has_multifile or estimated_files > 1:
        scope = "small_multifile_refactor"
        writes_allowed = True
        requires_approval = False
        gates = [
            "impact_analysis",
            "symbol_graph_expansion",
            "staged_patch_plan",
            "ubt_build",
        ]
    else:
        scope = "small_single_surface_refactor"
        writes_allowed = True
        requires_approval = False
        gates = ["impact_analysis", "ubt_build"]

    return {
        "scope": scope,
        "estimatedFileCount": estimated_files,
        "writesAllowedByDefault": writes_allowed,
        "requiresHumanApproval": requires_approval,
        "requiredGates": gates,
        "rationale": {
            "explicitFileMentions": explicit_files,
            "mediumMarkers": medium_hits,
            "largeMarkers": large_hits,
            "multifileSignals": has_multifile,
        },
    }


def _unique_items(items: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for item in items:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def invalid_unreal_lifecycle_mentions(text: str) -> list[str]:
    lowered = str(text or "").lower()
    issues: list[str] = []
    for marker, message in INVALID_UNREAL_LIFECYCLE_OVERRIDES.items():
        if re.search(rf"\b{re.escape(marker)}\b", lowered):
            issues.append(message)
    return issues


def validate_refactor_plan(stage: str, plan_text: str) -> dict[str, Any]:
    stage = normalize_stage(stage)
    text = str(plan_text or "").strip()
    lowered = text.lower()
    scope = classify_refactor_scope(text)
    issues: list[str] = []
    warnings: list[str] = []
    passed: list[str] = []

    if not text:
        return {
            "ok": False,
            "stage": stage,
            "refactorScope": scope,
            "issues": ["Plan text is empty."],
            "warnings": [],
            "passed": [],
        }

    issues.extend(invalid_unreal_lifecycle_mentions(text))

    if stage == "R0":
        if any(marker.lower() in lowered for marker in FORBIDDEN_CODE_MARKERS):
            issues.append("R0 must not include code snippets or UCLASS/GENERATED_BODY blocks.")
        else:
            passed.append("R0 has no obvious code blocks.")
        if any(marker in lowered for marker in SSOT_MARKERS):
            passed.append("SSOT/ownership language present.")
        else:
            issues.append("R0 should name state owners (SSOT table or ownership section).")
        if any(marker in lowered for marker in FILE_MARKERS):
            passed.append("Impact file list or path references present.")
        else:
            warnings.append("R0 should list impacted files or paths.")
        if any(marker in lowered for marker in RISK_MARKERS):
            passed.append("Risk/impact notes present.")
        else:
            warnings.append("Consider adding risks or migration hazards.")
        if scope["requiresHumanApproval"] and not any(marker in lowered for marker in APPROVAL_MARKERS):
            issues.append("Medium/large refactor R0 must include a human approval gate before code edits.")
        if scope["scope"] in {"medium_system_local_refactor", "large_migration"} and not any(
            marker in lowered for marker in STAGED_PATCH_MARKERS
        ):
            issues.append("Medium/large refactor R0 must describe staged patch boundaries.")

    if stage == "R1":
        if "interface" not in lowered and "boundary" not in lowered and "api" not in lowered:
            warnings.append("R1 should describe API/header boundaries.")
        if re.search(r"\bdelete\b|\bremove all\b", lowered):
            warnings.append("R1 should avoid mass deletion; defer removal to R4.")
        if scope["requiresHumanApproval"] and not any(marker in lowered for marker in APPROVAL_MARKERS):
            issues.append("R1 must carry forward approval requirements for medium/large refactors.")

    if stage in {"R2", "R3", "R4"}:
        file_hits = len(re.findall(r"\.(?:h|hpp|cpp|cc|cxx)\b", lowered, flags=re.IGNORECASE))
        if file_hits > 5:
            warnings.append(f"{stage} mentions many files ({file_hits}). Prefer 1-5 files per turn.")
        if not any(marker in lowered for marker in VALIDATION_MARKERS):
            warnings.append(f"{stage} should state how UBT/build verification will run.")
        if scope["requiresHumanApproval"] and not any(marker in lowered for marker in APPROVAL_MARKERS):
            issues.append(f"{stage} cannot execute medium/large refactors without an approval gate.")
        if not any(marker in lowered for marker in STAGED_PATCH_MARKERS):
            warnings.append(f"{stage} should name the current staged patch boundary.")

    if "lyra" in lowered and "example" not in lowered and "project-specific" not in lowered:
        warnings.append("Lyra names should be labeled project-specific, not universal rules.")

    ok = len(issues) == 0
    return {
        "ok": ok,
        "stage": stage,
        "refactorScope": scope,
        "issues": issues,
        "warnings": warnings,
        "passed": passed,
    }


def classify_impact_role(path: Path, line: str, symbol: str) -> str:
    lower = line.lower()
    rel = path.as_posix().lower()
    if rel.endswith(".build.cs"):
        return "module_owner"
    if "#include" in lower:
        return "include_owner"
    if "adddynamic" in lower or "adduobject" in lower or "bindufunction" in lower or ".bind" in lower:
        return "delegate_binding"
    if "broadcast" in lower or ("declare_" in lower and "delegate" in lower):
        return "delegate_surface"
    if "blueprintnativeevent" in lower or "blueprintimplementableevent" in lower or "_implementation" in lower:
        return "blueprint_event"
    if "override" in lower or "virtual" in lower:
        return "override_or_virtual"
    if rel.endswith((".h", ".hpp", ".hh")):
        if re.search(rf"\b{re.escape(symbol)}\s*\(", line):
            return "declaration"
        return "header_reference"
    if "::" in line and re.search(rf"\b{re.escape(symbol)}\s*\(", line):
        return "definition"
    if re.search(rf"\b{re.escape(symbol)}\s*\(", line):
        return "callsite"
    return "reference"


def detect_refactor_risks(path: Path, text: str) -> list[str]:
    lower = text.lower()
    risks: list[str] = []
    if any(marker in lower for marker in ("blueprintnativeevent", "blueprintimplementableevent", "blueprintcallable")):
        risks.append("blueprint_surface")
    if any(marker in lower for marker in ("tsoftobjectptr", "tsoftclassptr", "udataasset", "ulevelsequence")):
        risks.append("asset_reference_surface")
    if "unrealed" in lower or "with_editor" in lower:
        risks.append("editor_runtime_boundary")
    if "replicatedusing" in lower or "onrep_" in lower:
        risks.append("network_replication_surface")
    if "savegame" in lower:
        risks.append("savegame_schema_surface")
    if path.name.endswith(".Build.cs"):
        risks.append("module_dependency_surface")
    return risks


def _source_candidates(root: Path) -> list[Path]:
    skip = {"Binaries", "Intermediate", "Saved", "DerivedDataCache", ".git"}
    suffixes = {".h", ".hpp", ".cpp", ".c", ".cc", ".cxx", ".cs"}
    paths: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in skip for part in path.parts):
            continue
        if path.suffix.lower() not in suffixes and not path.name.endswith(".Build.cs"):
            continue
        paths.append(path)
    return paths


def scan_symbol_impact(project_root: str, symbol: str, *, max_files: int = 40) -> dict[str, Any]:
    root = Path(project_root)
    if not root.exists():
        return {"ok": False, "error": f"Project root not found: {root}", "matches": []}

    query = str(symbol or "").strip()
    if len(query) < 2:
        return {"ok": False, "error": "symbol must be at least 2 characters", "matches": []}

    # Try clangd references on first matching file when compile_commands exists.
    try:
        from clangd_helper import find_compile_commands, find_references

        cc = find_compile_commands(root)
        if cc:
            for path in root.rglob("*.h"):
                if any(p in path.parts for p in ("Intermediate", "Binaries", "Saved")):
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
                if query not in text:
                    continue
                rel = str(path.relative_to(root))
                line = next((i + 1 for i, ln in enumerate(text.splitlines()) if query in ln), 1)
                refs = find_references(root, rel, line)
                if refs.get("ok") and refs.get("references"):
                    return {
                        "ok": True,
                        "symbol": query,
                        "method": "clangd_references",
                        "matches": [{"path": str(root), "referenceCount": len(refs["references"])}],
                    }
    except Exception:
        pass

    matches: list[dict[str, Any]] = []
    role_counts: dict[str, int] = {}
    risk_counts: dict[str, int] = {}
    pattern = re.compile(re.escape(query))

    for path in _source_candidates(root):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if not pattern.search(text):
            continue
        line_hits: list[dict[str, Any]] = []
        for index, line in enumerate(text.splitlines()):
            if not pattern.search(line):
                continue
            role = classify_impact_role(path, line, query)
            role_counts[role] = role_counts.get(role, 0) + 1
            line_hits.append(
                {
                    "line": index + 1,
                    "role": role,
                    "snippet": line.strip()[:180],
                }
            )
        risks = detect_refactor_risks(path, text)
        for risk in risks:
            risk_counts[risk] = risk_counts.get(risk, 0) + 1
        matches.append(
            {
                "path": str(path),
                "relativePath": str(path.relative_to(root)) if str(path).startswith(str(root)) else str(path),
                "lineNumbers": [hit["line"] for hit in line_hits[:8]],
                "roles": sorted({hit["role"] for hit in line_hits}),
                "lineHits": line_hits[:8],
                "hitCount": len(line_hits),
                "riskFlags": risks,
            }
        )
        if len(matches) >= max_files:
            break

    return {
        "ok": True,
        "symbol": query,
        "projectRoot": str(root),
        "matchCount": len(matches),
        "roleCounts": dict(sorted(role_counts.items())),
        "riskCounts": dict(sorted(risk_counts.items())),
        "refactorScope": classify_refactor_scope(
            f"{query} {' '.join(role_counts)} {' '.join(risk_counts)}",
            impact_match_count=len(matches),
        ),
        "matches": matches,
        "truncated": len(matches) >= max_files,
    }


def _aggregate_scan_counts(scans: list[dict[str, Any]], key: str) -> dict[str, int]:
    totals: dict[str, int] = {}
    for scan in scans:
        for name, count in (scan.get(key) or {}).items():
            totals[str(name)] = totals.get(str(name), 0) + int(count or 0)
    return dict(sorted(totals.items()))


def _required_impact_roles(scope_name: str, role_counts: dict[str, int]) -> list[str]:
    roles = ["declaration", "definition"]
    if scope_name != "small_single_surface_refactor":
        roles.extend(["callsite", "include_owner"])
    if role_counts.get("delegate_binding") or role_counts.get("delegate_surface"):
        roles.extend(["delegate_surface", "delegate_binding"])
    else:
        roles.append("delegate_binding")
    if role_counts.get("override_or_virtual"):
        roles.append("override_or_virtual")
    if role_counts.get("module_owner"):
        roles.append("module_owner")
    return _unique_items(roles)


def _validation_steps(risk_counts: dict[str, int]) -> list[str]:
    steps = ["static_validate", "ubt_build", "log_scan"]
    risk_steps = {
        "blueprint_surface": "blueprint_compile_or_asset_validation",
        "asset_reference_surface": "asset_reference_audit_or_map_load",
        "editor_runtime_boundary": "editor_runtime_module_boundary_check",
        "network_replication_surface": "pie_replication_or_network_log_check",
        "savegame_schema_surface": "savegame_compatibility_check",
        "module_dependency_surface": "owner_build_cs_check",
    }
    for risk, step in risk_steps.items():
        if risk_counts.get(risk):
            steps.append(step)
    return _unique_items(steps)


def _stage_contracts(scope_name: str) -> list[dict[str, Any]]:
    staged_patch_required = scope_name != "small_single_surface_refactor"
    return [
        {
            "stage": "R0",
            "name": "impact_discovery",
            "writesAllowed": False,
            "allowedActions": [
                "classify scope",
                "identify SSOT/owners",
                "scan declaration/definition/callsite/binding/override impact",
                "list risk flags and validation plan",
            ],
            "exitCriteria": [
                "scope and owner are named",
                "impacted files are grouped by role",
                "approval gate is explicit when required",
            ],
        },
        {
            "stage": "R1",
            "name": "api_boundary_plan",
            "writesAllowed": False,
            "allowedActions": [
                "finalize public header/interface shape",
                "separate declaration changes from callsite rewires",
                "confirm staged patch boundary",
            ],
            "exitCriteria": [
                "API boundary is stable",
                "files for first implementation cluster are selected",
            ],
        },
        {
            "stage": "R2",
            "name": "implementation_cluster",
            "writesAllowed": scope_name.startswith("small"),
            "allowedActions": [
                "patch one declaration/definition cluster",
                "keep old compatibility surface when practical",
                "run static/UBT validation after patch",
            ],
            "exitCriteria": ["cluster builds or first actionable compile error is isolated"],
        },
        {
            "stage": "R3",
            "name": "callsite_and_binding_rewire",
            "writesAllowed": scope_name.startswith("small"),
            "allowedActions": [
                "rewire callers",
                "rewire delegate bindings",
                "rewire overrides/implementers",
            ],
            "exitCriteria": ["all impacted callsites are accounted for", "UBT validation is clean"],
        },
        {
            "stage": "R4",
            "name": "cleanup_only",
            "writesAllowed": scope_name.startswith("small"),
            "allowedActions": [
                "remove dead wrappers/includes after green build",
                "avoid behavior changes",
            ],
            "exitCriteria": ["cleanup is mechanically justified", "final validation passes"],
            "requiredOnlyWhen": staged_patch_required,
        },
    ]


def build_refactor_manager_plan(
    request: str,
    *,
    project_root: str | None = None,
    symbols: list[str] | None = None,
    approval: bool = False,
    max_files: int = 40,
) -> dict[str, Any]:
    """Build a deterministic manager plan that controls R0-R4 refactor execution."""
    text = str(request or "").strip()
    symbol_list = _unique_items(symbols or [])
    scans: list[dict[str, Any]] = []
    scan_status = "not_requested"

    root = str(project_root or "").strip()
    if symbol_list and root:
        scan_status = "completed"
        for symbol in symbol_list:
            scan = scan_symbol_impact(root, symbol, max_files=max_files)
            scans.append(scan)
            if not scan.get("ok"):
                scan_status = "partial_error"
    elif symbol_list:
        scan_status = "project_root_missing"
    elif root:
        scan_status = "no_symbols"

    role_counts = _aggregate_scan_counts(scans, "roleCounts")
    risk_counts = _aggregate_scan_counts(scans, "riskCounts")
    unique_paths = sorted(
        {
            str(match.get("relativePath") or match.get("path") or "")
            for scan in scans
            for match in scan.get("matches", [])
            if str(match.get("relativePath") or match.get("path") or "").strip()
        }
    )
    impact_match_count = len(unique_paths) if unique_paths else None
    scope_input = " ".join([text, " ".join(role_counts), " ".join(risk_counts)])
    scope = classify_refactor_scope(scope_input, impact_match_count=impact_match_count)
    scope_name = str(scope.get("scope") or "unknown")
    approval_required = bool(scope.get("requiresHumanApproval"))
    approval_satisfied = bool(approval) or not approval_required
    missing_roles = [
        role for role in _required_impact_roles(scope_name, role_counts)
        if role_counts.get(role, 0) == 0
    ]

    if scan_status in {"project_root_missing", "no_symbols"}:
        next_action = "collect_impact_scan_inputs"
    elif missing_roles and scope_name != "small_single_surface_refactor":
        next_action = "collect_missing_impact_roles"
    elif approval_required and not approval_satisfied:
        next_action = "request_human_approval"
    else:
        next_action = "execute_next_staged_refactor_patch"

    medium_or_large = scope_name in {"medium_system_local_refactor", "large_migration"}
    writes_allowed_now = (
        approval_satisfied
        and scan_status in {"completed", "not_requested", "no_symbols"}
        and not missing_roles
        and (
            bool(scope.get("writesAllowedByDefault"))
            or (approval and scope_name == "medium_system_local_refactor")
        )
        and scope_name != "large_migration"
    )

    return {
        "ok": True,
        "managerMode": "refactor_manager",
        "request": text,
        "scope": scope,
        "approval": {
            "required": approval_required,
            "satisfied": approval_satisfied,
            "mediumOrLarge": medium_or_large,
        },
        "writePolicy": {
            "managerOwnsWriteDecision": True,
            "writesAllowedNow": writes_allowed_now,
            "autonomousPatchLimitFiles": 5 if scope_name.startswith("small") else 0,
            "largeMigrationAutonomousWritesAllowed": False,
            "requiresStagedPatch": scope_name != "small_single_surface_refactor",
        },
        "impact": {
            "scanStatus": scan_status,
            "symbols": symbol_list,
            "projectRoot": root,
            "uniqueFileCount": len(unique_paths),
            "uniqueFiles": unique_paths[:max_files],
            "roleCounts": role_counts,
            "riskCounts": risk_counts,
            "missingRequiredRoles": missing_roles,
            "scans": scans,
        },
        "requiredEvidence": {
            "impactRoles": _required_impact_roles(scope_name, role_counts),
            "gates": _unique_items(
                [
                    "unreal_refactor_manager_plan",
                    "unreal_refactor_impact_scan",
                    "unreal_refactor_plan_validate",
                    "unreal_project_architecture",
                    *list(scope.get("requiredGates") or []),
                ]
            ),
            "validation": _validation_steps(risk_counts),
        },
        "stages": _stage_contracts(scope_name),
        "nextAction": next_action,
        "managerInstructions": [
            "Do R0/R1 planning before any code edits.",
            "Patch declaration, definition, callsite, binding, and override surfaces as separate staged clusters.",
            "For medium or large scope, require explicit human approval before R2/R3/R4 writes.",
            "Do not use compile-fix-only reasoning for API, Blueprint, asset, module, or system boundary refactors.",
        ],
    }
