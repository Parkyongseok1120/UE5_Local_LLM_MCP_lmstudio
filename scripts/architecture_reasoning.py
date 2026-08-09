#!/usr/bin/env python
"""Portable source-text architecture, data-flow, and state-transition analysis.

This is a dependency-free analysis layer for any project supported by the
conservative symbol graph.  It can expose source topology and candidate flows,
and validate that an architecture proposal declares the information needed for
safe implementation.  It never writes source, invokes a compiler, or claims a
candidate flow is a runtime fact.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from architecture_portfolio import (
    compare_architecture_alternatives,
    generate_architecture_portfolio,
)
from asset_migration_contract import validate_asset_migration
from build_symbol_graph import (
    _find_body_end,
    _mask_comments_and_strings,
    _source_evidence,
    build_symbol_graph,
    graph_is_fresh_for_root,
)
from symbol_graph import lookup_symbol

ASSIGNMENT_RE = re.compile(
    r"\b(?P<target>(?:(?:self|this)(?:\.|->))?[A-Za-z_]\w*(?:(?:\.|->)[A-Za-z_]\w*)*)"
    r"\s*=(?!=)\s*(?P<value>[^;\n]+)"
)
STATE_ASSIGNMENT_RE = re.compile(
    r"\b(?P<field>(?:self\.|this->|this\.)?[A-Za-z_]\w*(?:State|state))\s*=(?!=)\s*(?P<value>[^;\n]+)",
    re.IGNORECASE,
)
SET_STATE_RE = re.compile(r"\b(?:Set|TransitionTo)(?P<field>[A-Za-z_]*State)\s*\(\s*(?P<value>[^,)]+)")
RETURN_RE = re.compile(r"\breturn\s+(?P<value>[^;\n]+)")
CALL_RE = re.compile(r"\b(?P<name>[A-Za-z_$][\w$]*)\s*\(")
IF_GUARD_RE = re.compile(
    r"\bif\s*(?:\((?P<cpp>[^)]{1,240})\)|(?P<python>[^:\n]{1,240})\s*:)"
)
FLOW_KEYWORDS = {"if", "for", "while", "switch", "catch", "return", "sizeof", "new", "def", "function"}
LIFECYCLE_PHASES = {
    "beginplay": "runtime_start",
    "endplay": "runtime_stop",
    "initialize": "initialize",
    "deinitialize": "deinitialize",
    "onregister": "register",
    "onunregister": "unregister",
    "startplay": "runtime_start",
    "shutdown": "runtime_stop",
    "startupmodule": "module_start",
    "shutdownmodule": "module_stop",
}
BOUNDARY_CALL_KINDS = {
    "asynctask": "async_dispatch",
    "async": "async_dispatch",
    "settimer": "timer_schedule",
    "settimerfornexttick": "timer_schedule",
    "cleartimer": "timer_cleanup",
    "adddynamic": "delegate_binding",
    "adduobject": "delegate_binding",
    "bindufunction": "delegate_binding",
    "bindlambda": "delegate_binding",
    "removeall": "delegate_cleanup",
    "unbind": "delegate_cleanup",
    "broadcast": "event_dispatch",
    "enqueue": "queue_boundary",
}


def _resolve_root(project_root: str | Path) -> Path | None:
    if not str(project_root or "").strip():
        return None
    candidate = Path(project_root).expanduser().resolve()
    if candidate.is_file() and candidate.suffix.lower() == ".uproject":
        return candidate.parent
    return candidate if candidate.is_dir() else None


def _source_root(root: Path) -> Path:
    # A project-level architecture view must include plugin and test source,
    # not just Source/, while the graph's ignore policy excludes generated
    # build/state directories.
    return root


def _relative(root: Path, path: str | Path) -> str:
    try:
        return Path(path).resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path).replace("\\", "/")


def _owner_for_relative(relative: str) -> str:
    parts = Path(relative).parts
    if not parts:
        return "_root"
    if parts[0].lower() == "source" and len(parts) >= 2:
        return f"module:{parts[1]}"
    if parts[0].lower() == "plugins" and len(parts) >= 4 and parts[2].lower() == "source":
        return f"plugin_module:{parts[1]}/{parts[3]}"
    return f"directory:{parts[0]}" if len(parts) > 1 else "_root"


def _file_by_id(graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("id") or ""): item
        for item in graph.get("files") or []
        if isinstance(item, dict) and item.get("id")
    }


def _topology(root: Path, graph: dict[str, Any]) -> dict[str, Any]:
    files_by_id = _file_by_id(graph)
    owners: dict[str, set[str]] = defaultdict(set)
    for item in graph.get("files") or []:
        if not isinstance(item, dict):
            continue
        rel = _relative(root, str(item.get("path") or ""))
        owners[_owner_for_relative(rel)].add(rel)

    dependencies: dict[tuple[str, str], dict[str, Any]] = {}
    for edge in graph.get("edges") or []:
        if not isinstance(edge, dict) or str(edge.get("kind") or "") not in {"includes", "imports"}:
            continue
        source_file = files_by_id.get(str(edge.get("from") or ""))
        target_file = files_by_id.get(str(edge.get("to") or ""))
        if not source_file or not target_file:
            continue
        source_rel = _relative(root, str(source_file.get("path") or ""))
        target_rel = _relative(root, str(target_file.get("path") or ""))
        source_owner = _owner_for_relative(source_rel)
        target_owner = _owner_for_relative(target_rel)
        if source_owner == target_owner:
            continue
        key = (source_owner, target_owner)
        relation = dependencies.setdefault(
            key,
            {
                "from": source_owner,
                "to": target_owner,
                "kind": "source_text_dependency",
                "relationCount": 0,
                "evidence": [],
                "files": [],
            },
        )
        relation["relationCount"] += 1
        evidence = dict(edge.get("evidence") or {})
        if evidence and evidence not in relation["evidence"] and len(relation["evidence"]) < 12:
            relation["evidence"].append(evidence)
        pair = {"from": source_rel, "to": target_rel}
        if pair not in relation["files"] and len(relation["files"]) < 12:
            relation["files"].append(pair)

    adjacency: dict[str, set[str]] = defaultdict(set)
    for source, target in dependencies:
        adjacency[source].add(target)
    cycles: list[list[str]] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str, stack: list[str]) -> None:
        if node in visiting:
            closed_cycle = stack[stack.index(node):]
            open_cycle = closed_cycle[:-1]
            canonical_open = min(
                tuple(open_cycle[index:] + open_cycle[:index])
                for index in range(len(open_cycle))
            )
            normalized = [*canonical_open, canonical_open[0]]
            if normalized not in cycles:
                cycles.append(normalized)
            return
        if node in visited:
            return
        visiting.add(node)
        for target in sorted(adjacency.get(node, set())):
            visit(target, [*stack, target])
        visiting.discard(node)
        visited.add(node)

    for owner in sorted(owners):
        visit(owner, [owner])
    return {
        "owners": [
            {
                "id": owner,
                "fileCount": len(paths),
                "files": sorted(paths)[:24],
                "filesTruncated": len(paths) > 24,
            }
            for owner, paths in sorted(owners.items())
        ],
        "boundaryDependencies": [
            {
                **item,
                "evidenceTruncated": item["relationCount"] > len(item["evidence"]),
                "filesTruncated": item["relationCount"] > len(item["files"]),
            }
            for item in sorted(dependencies.values(), key=lambda item: (item["from"], item["to"]))
        ],
        "sourceDependencyCycles": cycles,
    }


def _function_rows(
    graph: dict[str, Any],
    symbols: list[str],
    *,
    limit: int = 24,
) -> tuple[list[dict[str, Any]], bool]:
    functions = [row for row in graph.get("symbols") or [] if isinstance(row, dict) and row.get("symbol_kind") == "function"]
    if not symbols:
        return functions[:limit], len(functions) > limit
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    truncated = False
    for query in symbols:
        direct = lookup_symbol(query, graph, limit=limit + 1)
        if len(direct) > limit:
            truncated = True
            direct = direct[:limit]
        for row in direct:
            candidates = [row] if row.get("symbol_kind") == "function" else [
                function for function in functions
                if function.get("file_path") == row.get("file_path")
            ]
            for function in candidates:
                identifier = str(function.get("id") or f"{function.get('file_path')}:{function.get('line_start')}")
                if identifier not in selected_ids:
                    selected_ids.add(identifier)
                    selected.append(function)
                    if len(selected) >= limit:
                        return selected, True
    return selected, truncated


def _function_body(row: dict[str, Any]) -> tuple[list[str], int] | None:
    path = Path(str(row.get("file_path") or ""))
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None
    raw_lines = raw.splitlines()
    lines = _mask_comments_and_strings(raw).splitlines()
    start = int(row.get("line_start") or 1)
    language = str(row.get("language") or "")
    if language == "python":
        if start - 1 >= len(raw_lines):
            return None
        indent = len(raw_lines[start - 1]) - len(raw_lines[start - 1].lstrip())
        end = start
        for index in range(start, len(raw_lines)):
            raw_line = raw_lines[index]
            if raw_line.strip() and (len(raw_line) - len(raw_line.lstrip())) <= indent:
                break
            end = index + 1
    else:
        end = _find_body_end(lines, start, language)
    if not end:
        return None
    return lines[start - 1:end], start


def _iter_executable_lines(
    function: dict[str, Any],
    lines: list[str],
    start: int,
):
    """Yield masked body text without treating a declaration as a self-call."""
    language = str(function.get("language") or "")
    if language == "python":
        for offset, line in enumerate(lines[1:], start=1):
            yield start + offset, line
        return
    body_started = False
    for offset, line in enumerate(lines):
        if not body_started:
            opening = line.find("{")
            if opening < 0:
                continue
            body_started = True
            line = line[opening + 1:]
        yield start + offset, line


def _function_name(function: dict[str, Any]) -> str:
    return str(function.get("qualified_name") or function.get("symbol_name") or "")


def _function_owner(function: dict[str, Any]) -> str:
    qualified = _function_name(function)
    if "::" in qualified:
        return qualified.split("::", 1)[0]
    if "." in qualified:
        return qualified.rsplit(".", 1)[0]
    return str(function.get("file_path") or "")


def _normalized_state_field(raw: str) -> str:
    value = str(raw or "").strip()
    for prefix in ("this->", "this.", "self."):
        if value.startswith(prefix):
            return value[len(prefix):]
    return value


def analyze_data_flow(graph: dict[str, Any], symbols: list[str] | None = None, *, limit: int = 80) -> dict[str, Any]:
    """Return source-text flow candidates for selected function bodies."""
    selected, selection_truncated = _function_rows(graph, list(symbols or []))
    flows: list[dict[str, Any]] = []
    truncated = False
    for function in selected:
        body = _function_body(function)
        if not body:
            continue
        lines, start = body
        path = Path(str(function.get("file_path") or ""))
        digest = str(function.get("file_hash") or "")
        for line_no, line in _iter_executable_lines(function, lines, start):
            for match in ASSIGNMENT_RE.finditer(line):
                if len(flows) >= limit:
                    truncated = True
                    break
                flows.append(
                    {
                        "kind": "assignment_candidate",
                        "function": function.get("qualified_name") or function.get("symbol_name"),
                        "from": match.group("value").strip()[:180],
                        "to": match.group("target").strip(),
                        "evidence": _source_evidence(path, line_no, digest),
                        "confidence": "source_text_candidate",
                    }
                )
            for match in RETURN_RE.finditer(line):
                if len(flows) >= limit:
                    truncated = True
                    break
                flows.append(
                    {
                        "kind": "return_candidate",
                        "function": function.get("qualified_name") or function.get("symbol_name"),
                        "from": match.group("value").strip()[:180],
                        "to": "return",
                        "evidence": _source_evidence(path, line_no, digest),
                        "confidence": "source_text_candidate",
                    }
                )
            for match in CALL_RE.finditer(line):
                if len(flows) >= limit:
                    truncated = True
                    break
                name = match.group("name")
                if name.lower() in FLOW_KEYWORDS:
                    continue
                flows.append(
                    {
                        "kind": "call_argument_boundary_candidate",
                        "function": function.get("qualified_name") or function.get("symbol_name"),
                        "from": "arguments/receiver unknown",
                        "to": name,
                        "evidence": _source_evidence(path, line_no, digest),
                        "confidence": "source_text_candidate",
                    }
                )
            if truncated:
                break
        if truncated:
            break
    return {
        "ok": True,
        "functionCount": len(selected),
        "functionSelectionTruncated": selection_truncated,
        "flows": flows,
        "truncated": truncated,
        "proofBoundary": (
            "Assignments, returns, and call boundaries are source-text candidates only. They do not prove value identity, "
            "aliasing, branch feasibility, ordering, async delivery, or runtime data flow."
        ),
        "requiredForBehavioralClaim": ["entry-to-effect BehaviorPath", "counterevidence/alternative path", "static/build/test/runtime evidence appropriate to the claim"],
    }


def analyze_state_transitions(graph: dict[str, Any], symbols: list[str] | None = None, *, limit: int = 60) -> dict[str, Any]:
    """Find state-looking assignments/calls without claiming a complete state machine."""
    selected, selection_truncated = _function_rows(graph, list(symbols or []))
    transitions: list[dict[str, Any]] = []
    truncated = False
    for function in selected:
        body = _function_body(function)
        if not body:
            continue
        lines, start = body
        path = Path(str(function.get("file_path") or ""))
        digest = str(function.get("file_hash") or "")
        recent_guard: dict[str, Any] | None = None
        for line_no, line in _iter_executable_lines(function, lines, start):
            guard_match = IF_GUARD_RE.search(line)
            if guard_match:
                condition = str(guard_match.group("cpp") or guard_match.group("python") or "").strip()
                recent_guard = {
                    "condition": condition,
                    "evidence": _source_evidence(path, line_no, digest),
                    "scopeConfidence": "nearby_source_candidate",
                }
            elif recent_guard and line_no - int(
                (recent_guard.get("evidence") or {}).get("lineStart") or line_no
            ) > 4:
                recent_guard = None
            for match in STATE_ASSIGNMENT_RE.finditer(line):
                if len(transitions) >= limit:
                    truncated = True
                    break
                item = {
                    "kind": "state_assignment_candidate",
                    "function": _function_name(function),
                    "ownerCandidate": _function_owner(function),
                    "stateField": match.group("field"),
                    "fromState": "unknown",
                    "toState": match.group("value").strip()[:160],
                    "evidence": _source_evidence(path, line_no, digest),
                    "confidence": "source_text_candidate",
                }
                if recent_guard:
                    item["guardCandidate"] = recent_guard
                transitions.append(item)
            for match in SET_STATE_RE.finditer(line):
                if len(transitions) >= limit:
                    truncated = True
                    break
                item = {
                    "kind": "state_setter_candidate",
                    "function": _function_name(function),
                    "ownerCandidate": _function_owner(function),
                    "stateField": match.group("field"),
                    "fromState": "unknown",
                    "toState": match.group("value").strip()[:160],
                    "evidence": _source_evidence(path, line_no, digest),
                    "confidence": "source_text_candidate",
                }
                if recent_guard:
                    item["guardCandidate"] = recent_guard
                transitions.append(item)
            if truncated:
                break
        if truncated:
            break

    ownership: dict[tuple[str, str], dict[str, Any]] = {}
    for item in transitions:
        owner = str(item.get("ownerCandidate") or "")
        field = _normalized_state_field(str(item.get("stateField") or ""))
        key = (owner, field)
        row = ownership.setdefault(
            key,
            {
                "ownerCandidate": owner,
                "stateField": field,
                "writerFunctions": [],
                "writerFiles": [],
                "writeCount": 0,
                "evidence": [],
            },
        )
        row["writeCount"] += 1
        function_name = str(item.get("function") or "")
        evidence = dict(item.get("evidence") or {})
        file_path = str(evidence.get("filePath") or "")
        if function_name and function_name not in row["writerFunctions"]:
            row["writerFunctions"].append(function_name)
        if file_path and file_path not in row["writerFiles"]:
            row["writerFiles"].append(file_path)
        if evidence and len(row["evidence"]) < 8:
            row["evidence"].append(evidence)
    ownership_candidates = []
    for row in ownership.values():
        row["multipleWriters"] = len(row["writerFunctions"]) > 1
        row["confidence"] = "source_text_candidate"
        ownership_candidates.append(row)

    return {
        "ok": True,
        "functionCount": len(selected),
        "functionSelectionTruncated": selection_truncated,
        "transitions": transitions,
        "stateOwnershipCandidates": ownership_candidates,
        "multipleWriterCandidateCount": sum(
            1 for item in ownership_candidates if item.get("multipleWriters")
        ),
        "truncated": truncated,
        "proofBoundary": (
            "A state-looking assignment/setter is not a complete state transition proof. Current state, guard conditions, "
            "guard scope, event ordering, replication, and runtime reachability remain unknown until independently verified."
        ),
        "requiredForStateClaim": ["initialization/read site", "transition guard/dispatch", "observer/effect", "runtime or focused test evidence when behavior matters"],
    }


def analyze_lifecycle_boundaries(
    graph: dict[str, Any],
    symbols: list[str] | None = None,
    *,
    limit: int = 60,
) -> dict[str, Any]:
    """Return lifecycle and async/event boundary candidates without claiming runtime reachability."""
    selected, selection_truncated = _function_rows(graph, list(symbols or []))
    lifecycle: list[dict[str, Any]] = []
    boundaries: list[dict[str, Any]] = []
    truncated = False
    owner_phases: dict[str, set[str]] = defaultdict(set)

    for function in selected:
        function_name = _function_name(function)
        short_name = function_name.rsplit("::", 1)[-1].rsplit(".", 1)[-1].lower()
        owner = _function_owner(function)
        path = Path(str(function.get("file_path") or ""))
        digest = str(function.get("file_hash") or "")
        phase = LIFECYCLE_PHASES.get(short_name)
        if phase:
            lifecycle.append(
                {
                    "kind": "lifecycle_callback_candidate",
                    "phase": phase,
                    "function": function_name,
                    "ownerCandidate": owner,
                    "evidence": _source_evidence(
                        path,
                        int(function.get("line_start") or 1),
                        digest,
                    ),
                    "confidence": "source_symbol_candidate",
                }
            )
            owner_phases[owner].add(phase)

        body = _function_body(function)
        if not body:
            continue
        lines, start = body
        for line_no, line in _iter_executable_lines(function, lines, start):
            for match in CALL_RE.finditer(line):
                call_name = match.group("name")
                call_lower = call_name.lower()
                kind = BOUNDARY_CALL_KINDS.get(call_lower)
                if not kind:
                    if call_lower.startswith("server_"):
                        kind = "network_server_boundary"
                    elif call_lower.startswith("client_"):
                        kind = "network_client_boundary"
                    elif call_lower.startswith("multicast_"):
                        kind = "network_multicast_boundary"
                    elif call_lower.startswith("onrep_"):
                        kind = "replication_observer"
                if not kind:
                    continue
                if len(boundaries) >= limit:
                    truncated = True
                    break
                boundaries.append(
                    {
                        "kind": kind,
                        "call": call_name,
                        "function": function_name,
                        "ownerCandidate": owner,
                        "evidence": _source_evidence(path, line_no, digest),
                        "confidence": "source_text_candidate",
                    }
                )
            if truncated:
                break
        if truncated:
            break

    pair_gaps: list[dict[str, Any]] = []
    expected_pairs = (
        ("runtime_start", "runtime_stop"),
        ("initialize", "deinitialize"),
        ("register", "unregister"),
        ("module_start", "module_stop"),
    )
    for owner, phases in sorted(owner_phases.items()):
        for setup, cleanup in expected_pairs:
            if setup in phases and cleanup not in phases:
                pair_gaps.append(
                    {
                        "ownerCandidate": owner,
                        "observedPhase": setup,
                        "missingCandidatePhase": cleanup,
                        "severity": "review_required",
                    }
                )

    return {
        "ok": True,
        "functionCount": len(selected),
        "functionSelectionTruncated": selection_truncated,
        "callbacks": lifecycle,
        "asyncEventBoundaries": boundaries,
        "pairingGaps": pair_gaps,
        "truncated": truncated,
        "proofBoundary": (
            "Lifecycle names and async/event calls are source candidates. Absence does not prove missing cleanup, "
            "and presence does not prove ordering, thread affinity, ownership, or runtime execution."
        ),
        "requiredForLifecycleClaim": [
            "registration/initialization site",
            "cleanup/termination site",
            "ownership and thread/world context",
            "runtime or focused test evidence when behavior matters",
        ],
    }


def _nonempty_string_list(value: Any) -> list[str] | None:
    if not isinstance(value, list):
        return None
    result = [str(item).strip() for item in value if isinstance(item, str) and item.strip()]
    return result if len(result) == len(value) else None


def _normalize_project_relative_path(value: str) -> tuple[str, str]:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        return "", "must be a non-empty project-relative path"
    if "\x00" in raw:
        return "", "must not contain a NUL character"
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        return "", "must be project-relative, not absolute"
    parts = raw.split("/")
    if any(part == ".." for part in parts):
        return "", "must not contain parent traversal ('..')"
    normalized = "/".join(part for part in parts if part not in {"", "."})
    if not normalized:
        return "", "must identify a file inside the project"
    return normalized, ""


def _slice_dependency_cycle(rows: list[dict[str, Any]]) -> list[str]:
    adjacency = {
        str(row.get("sliceId") or ""): [str(item) for item in row.get("dependsOn") or []]
        for row in rows
    }
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str, stack: list[str]) -> list[str]:
        if node in visiting:
            return [*stack[stack.index(node):]]
        if node in visited:
            return []
        visiting.add(node)
        for dependency in adjacency.get(node, []):
            cycle = visit(dependency, [*stack, dependency])
            if cycle:
                return cycle
        visiting.remove(node)
        visited.add(node)
        return []

    for slice_id in adjacency:
        cycle = visit(slice_id, [slice_id])
        if cycle:
            return cycle
    return []


def validate_architecture_proposal(proposal: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any]:
    """Validate design traceability and staged implementation obligations."""
    plan = proposal if isinstance(proposal, dict) else {}
    issues: list[str] = []
    warnings: list[str] = []
    if not isinstance(proposal, dict):
        issues.append("architecture proposal must be an object")

    decision = plan.get("decision")
    if not isinstance(decision, str) or not decision.strip():
        issues.append("architecture proposal field decision must be a non-empty string")

    string_fields: dict[str, list[str]] = {}
    for field in ("invariants", "impactedSurfaces", "validationPlan"):
        parsed = _nonempty_string_list(plan.get(field))
        if not parsed:
            issues.append(f"architecture proposal field {field} must be a non-empty string array")
            parsed = []
        string_fields[field] = parsed

    alternatives_value = plan.get("alternatives")
    alternatives = alternatives_value if isinstance(alternatives_value, list) else []
    if not alternatives:
        issues.append("architecture proposal field alternatives must be a non-empty array")
    structured_alternatives = 0
    for index, alternative in enumerate(alternatives):
        if isinstance(alternative, str) and alternative.strip():
            continue
        if not isinstance(alternative, dict) or not str(alternative.get("name") or "").strip():
            issues.append(f"alternatives[{index}] must be a non-empty string or an object with name")
            continue
        structured_alternatives += 1
        if not str(alternative.get("rationale") or "").strip():
            warnings.append(f"alternatives[{index}] should include rationale")
        scores = alternative.get("scores")
        if scores is not None:
            if not isinstance(scores, dict):
                issues.append(f"alternatives[{index}].scores must be an object")
            else:
                for metric in ("complexity", "maintainability", "performance", "risk"):
                    value = scores.get(metric)
                    if not isinstance(value, (int, float)) or isinstance(value, bool) or not 1 <= value <= 5:
                        issues.append(
                            f"alternatives[{index}].scores.{metric} must be a number from 1 to 5"
                        )

    impacted = string_fields["impactedSurfaces"]
    implementation_value = plan.get("implementationFiles")
    implementation = _nonempty_string_list(implementation_value) if implementation_value is not None else []
    if implementation_value is not None and implementation is None:
        issues.append("implementationFiles must be an array of non-empty strings when supplied")
        implementation = []
    normalized_implementation: list[str] = []
    for index, file_path in enumerate(implementation or []):
        normalized, path_issue = _normalize_project_relative_path(file_path)
        if path_issue:
            issues.append(f"implementationFiles[{index}] {path_issue}")
            continue
        normalized_implementation.append(normalized)
    implementation = list(dict.fromkeys(normalized_implementation))

    known_paths = {
        str(file_path).replace("\\", "/")
        for owner in (analysis.get("topology") or {}).get("owners", [])
        if isinstance(owner, dict)
        for file_path in (owner.get("files") or [])
    }
    for surface in [*impacted, *implementation]:
        raw = str(surface or "").replace("\\", "/")
        if raw and known_paths and raw not in known_paths:
            warnings.append(f"proposal surface not found in analyzed source topology: {raw}")

    boundary_value = plan.get("boundaryChanges")
    boundary_changes = boundary_value if isinstance(boundary_value, list) else []
    if boundary_value is not None and not isinstance(boundary_value, list):
        issues.append("boundaryChanges must be an array when supplied")
    for index, change in enumerate(boundary_changes):
        if not isinstance(change, dict) or not str(change.get("from") or "") or not str(change.get("to") or ""):
            issues.append(f"boundaryChanges[{index}] needs from/to owners")
        elif not str(change.get("reason") or ""):
            warnings.append(f"boundaryChanges[{index}] should state its reason and ownership rule")

    slices_value = plan.get("implementationSlices")
    slices = slices_value if isinstance(slices_value, list) else []
    if slices_value is not None and not isinstance(slices_value, list):
        issues.append("implementationSlices must be an array when supplied")
    normalized_slices: list[dict[str, Any]] = []
    slice_ids: set[str] = set()
    covered_files: set[str] = set()
    file_slice_owner: dict[str, str] = {}
    for index, row in enumerate(slices):
        if not isinstance(row, dict):
            issues.append(f"implementationSlices[{index}] must be an object")
            continue
        slice_id = str(row.get("sliceId") or "").strip()
        files = _nonempty_string_list(row.get("files"))
        invariants = _nonempty_string_list(row.get("invariants"))
        validation = _nonempty_string_list(row.get("validation"))
        depends_on = _nonempty_string_list(row.get("dependsOn") or [])
        if not slice_id:
            issues.append(f"implementationSlices[{index}].sliceId is required")
        elif slice_id in slice_ids:
            issues.append(f"implementationSlices has duplicate sliceId: {slice_id}")
        else:
            slice_ids.add(slice_id)
        if not files:
            issues.append(f"implementationSlices[{index}].files must be a non-empty string array")
            files = []
        normalized_files: list[str] = []
        for file_index, file_path in enumerate(files):
            normalized, path_issue = _normalize_project_relative_path(file_path)
            if path_issue:
                issues.append(
                    f"implementationSlices[{index}].files[{file_index}] {path_issue}"
                )
                continue
            normalized_files.append(normalized)
            previous_owner = file_slice_owner.get(normalized)
            if previous_owner and previous_owner != slice_id:
                issues.append(
                    f"implementation file assigned to multiple slices: {normalized} "
                    f"({previous_owner}, {slice_id or '<missing>'})"
                )
            elif slice_id:
                file_slice_owner[normalized] = slice_id
        files = list(dict.fromkeys(normalized_files))
        if not invariants:
            issues.append(f"implementationSlices[{index}].invariants must be a non-empty string array")
            invariants = []
        if not validation:
            issues.append(f"implementationSlices[{index}].validation must be a non-empty string array")
            validation = []
        if depends_on is None:
            issues.append(f"implementationSlices[{index}].dependsOn must be a string array")
            depends_on = []
        undeclared_slice_invariants = [
            invariant for invariant in invariants if invariant not in string_fields["invariants"]
        ]
        if undeclared_slice_invariants:
            issues.append(
                f"implementationSlices[{index}].invariants not declared by proposal: "
                + ", ".join(undeclared_slice_invariants)
            )
        covered_files.update(files)
        normalized_slices.append(
            {
                "sliceId": slice_id,
                "files": files,
                "invariants": invariants,
                "validation": validation,
                "dependsOn": depends_on,
            }
        )

    for row in normalized_slices:
        for dependency in row["dependsOn"]:
            if dependency == row["sliceId"]:
                issues.append(f"implementation slice {row['sliceId']} cannot depend on itself")
            elif dependency not in slice_ids:
                issues.append(
                    f"implementation slice {row['sliceId']} has unknown dependency: {dependency}"
                )
    slice_cycle = _slice_dependency_cycle(normalized_slices) if normalized_slices else []
    if slice_cycle:
        issues.append("implementation slice dependency cycle: " + " -> ".join(slice_cycle))

    uncovered_files = [path for path in implementation if path not in covered_files]
    if slices and uncovered_files:
        issues.append(
            "implementationFiles not covered by implementationSlices: "
            + ", ".join(uncovered_files)
        )
    undeclared_slice_files = sorted(covered_files - set(implementation))
    if implementation and undeclared_slice_files:
        issues.append(
            "implementationSlices contain files not declared in implementationFiles: "
            + ", ".join(undeclared_slice_files)
        )

    matrix_value = plan.get("validationMatrix")
    matrix = matrix_value if isinstance(matrix_value, list) else []
    if matrix_value is not None and not isinstance(matrix_value, list):
        issues.append("validationMatrix must be an array when supplied")
    covered_invariants: set[str] = set()
    matrix_checks: list[str] = []
    declared_invariants = string_fields["invariants"]
    for index, row in enumerate(matrix):
        if not isinstance(row, dict):
            issues.append(f"validationMatrix[{index}] must be an object")
            continue
        invariant = str(row.get("invariant") or "").strip()
        checks = _nonempty_string_list(row.get("checks"))
        if invariant not in declared_invariants:
            issues.append(
                f"validationMatrix[{index}].invariant must exactly match a declared invariant"
            )
        else:
            covered_invariants.add(invariant)
        if not checks:
            issues.append(f"validationMatrix[{index}].checks must be a non-empty string array")
        else:
            matrix_checks.extend(checks)
    uncovered_invariants = [
        invariant for invariant in declared_invariants if invariant not in covered_invariants
    ]

    proposal_text = json.dumps(plan, ensure_ascii=False).lower()
    network_markers = (
        "multiplayer", "network", "replication", "replicated", "rpc", "server",
        "client", "authority", "authoritative", "멀티플레이", "네트워크", "복제",
        "서버", "클라이언트", "권한",
    )
    networked_proposal = any(marker in proposal_text for marker in network_markers)
    networking = plan.get("networking") if isinstance(plan.get("networking"), dict) else {}
    state_inventory = plan.get("stateInventory") if isinstance(plan.get("stateInventory"), list) else []
    lifecycle_transitions = (
        plan.get("lifecycleTransitions")
        if isinstance(plan.get("lifecycleTransitions"), list)
        else []
    )
    networking_complete = True
    rpc_path_concrete = True
    duplicate_truth_sources: list[str] = []
    lifecycle_coverage_gaps: list[str] = []
    framework_contracts = (
        analysis.get("frameworkContracts")
        if isinstance(analysis.get("frameworkContracts"), dict)
        else {}
    )
    unreal_detected = (
        framework_contracts.get("detected") is True
        and framework_contracts.get("kind") == "UnrealGameFramework"
    )

    if networked_proposal:
        required_network_fields = ("authorityOwner", "clientInitiated", "replicatedState")
        missing_network_fields = [
            field for field in required_network_fields if field not in networking
            or (
                field != "clientInitiated"
                and not networking.get(field)
            )
        ]
        if missing_network_fields:
            networking_complete = False
            issues.append(
                "networked architecture proposal is missing networking fields: "
                + ", ".join(missing_network_fields)
            )

        client_language = any(
            marker in proposal_text
            for marker in ("client request", "client calls", "clientinitiated", "rpc", "클라이언트")
        )
        client_initiated = networking.get("clientInitiated") is True
        if client_language and networking.get("clientInitiated") is False:
            issues.append(
                "proposal describes a client request but networking.clientInitiated is false"
            )
            networking_complete = False
        if client_language and "clientInitiated" not in networking:
            client_initiated = True

        if client_initiated:
            request_path = _nonempty_string_list(networking.get("requestPath")) or []
            missing_rpc_fields = [
                field
                for field in ("rpcOwner", "owningConnection", "serverValidation")
                if not str(networking.get(field) or "").strip()
            ]
            if len(request_path) < 3:
                missing_rpc_fields.append("requestPath[>=3 concrete hops]")
            vague_path = any(
                re.search(r"\b(?:or|either|tbd|unknown|optional)\b", hop, re.IGNORECASE)
                or "또는" in hop
                for hop in request_path
            )
            if vague_path:
                rpc_path_concrete = False
                issues.append(
                    "networking.requestPath contains an unresolved alternative; select one callable path"
                )
            if missing_rpc_fields:
                networking_complete = False
                rpc_path_concrete = False
                issues.append(
                    "client-initiated network design is missing a callable RPC ownership contract: "
                    + ", ".join(missing_rpc_fields)
                )
            rpc_owner = str(networking.get("rpcOwner") or "").strip()
            if re.search(r"game\s*mode|gamemode|game\s*state|gamestate", rpc_owner, re.IGNORECASE):
                networking_complete = False
                rpc_path_concrete = False
                issues.append(
                    "client-initiated Server RPC owner is server-only/server-owned and is not callable "
                    "from a remote client; choose an invoking actor/component owned by that connection "
                    "and show the complete source-backed path to the authority owner"
                )
            owning_connection = str(networking.get("owningConnection") or "").strip().lower()
            if owning_connection and not any(
                marker in owning_connection
                for marker in ("owning connection", "owned by", "net owner", "netconnection", "possessed")
            ):
                networking_complete = False
                rpc_path_concrete = False
                issues.append(
                    "networking.owningConnection does not prove that the invoking RPC owner is owned by "
                    "the requesting client's connection; a path from that actor to the server authority is "
                    "not an ownership proof"
                )
            rpc_type_match = re.search(r"\b[AU][A-Z][A-Za-z0-9_]+", rpc_owner)
            if rpc_type_match:
                rpc_type = rpc_type_match.group(0)
                declared_surface_text = json.dumps(
                    {
                        "impactedSurfaces": impacted,
                        "implementationFiles": implementation,
                        "boundaryChanges": boundary_changes,
                        "migrationPlan": plan.get("migrationPlan"),
                    },
                    ensure_ascii=False,
                ).lower()
                if rpc_type.lower() not in declared_surface_text:
                    issues.append(
                        f"networking.rpcOwner {rpc_type} is absent from impacted surfaces/migration scope; "
                        "include the existing or new RPC-bearing surface and its validation slice"
                    )
            for symbol_fact in framework_contracts.get("requestPathSymbols") or []:
                if not isinstance(symbol_fact, dict):
                    continue
                required_surfaces = [
                    str(item).replace("\\", "/")
                    for item in symbol_fact.get("requiredImplementationSurfaces") or []
                    if str(item).strip()
                ]
                missing_surfaces = [
                    path for path in required_surfaces if path not in implementation
                ]
                if missing_surfaces:
                    rpc_path_concrete = False
                    issues.append(
                        f"networking.requestPath symbol {symbol_fact.get('symbol')} is not source-complete; "
                        "the method has no existing declaration/definition for this path and its required "
                        "implementation surface(s) are absent: " + ", ".join(missing_surfaces)
                    )

        replicated_state = _nonempty_string_list(networking.get("replicatedState")) or []
        if unreal_detected:
            game_mode_types = {
                str(item).lower() for item in framework_contracts.get("gameModeTypes") or []
            }
            for index, state_ref in enumerate(replicated_state):
                owner_match = re.match(r"^\s*([AU][A-Za-z0-9_]+)::", state_ref)
                if not owner_match:
                    issues.append(
                        f"networking.replicatedState[{index}] must be an owner-qualified Type::Field "
                        "reference so replication ownership can be checked"
                    )
                    continue
                if owner_match.group(1).lower() in game_mode_types:
                    issues.append(
                        f"networking.replicatedState[{index}] assigns replicated client-visible state to "
                        f"{owner_match.group(1)}, but Unreal GameMode exists only on the authority; place the "
                        "replicated view on a replicated framework owner and keep the mutation decision server-only"
                    )

        if not state_inventory:
            issues.append(
                "networked architecture proposal requires stateInventory to prove one truth source per state"
            )
        authoritative_owners: dict[str, set[str]] = defaultdict(set)
        inventory_state_names: list[str] = []
        participant_inventory_indexes: list[int] = []
        identity_inventory_indexes: list[int] = []
        has_player_array_contract = any(
            str(row.get("symbol") or "") == "AGameStateBase::PlayerArray"
            for row in framework_contracts.get("inheritedStateCollections") or []
            if isinstance(row, dict)
        )
        for index, row in enumerate(state_inventory):
            if not isinstance(row, dict):
                issues.append(f"stateInventory[{index}] must be an object")
                continue
            missing = [
                field for field in ("state", "owner", "lifetime", "authority", "source", "cleanup")
                if not str(row.get(field) or "").strip()
            ]
            if missing:
                issues.append(
                    f"stateInventory[{index}] is missing: " + ", ".join(missing)
                )
                continue
            state_name = re.sub(r"[^a-z0-9]", "", str(row.get("state") or "").lower())
            inventory_state_names.append(state_name)
            owner = str(row.get("owner") or "").strip()
            authority = str(row.get("authority") or "").lower()
            source = str(row.get("source") or "").lower()
            if source == "derived" and not str(row.get("derivedFrom") or "").strip():
                issues.append(f"stateInventory[{index}] derived state requires derivedFrom")
            is_authoritative = "author" in authority or "server" in authority
            if is_authoritative and re.search(r"\s(?:and|plus)\s|\+|/", owner, re.IGNORECASE):
                issues.append(
                    f"stateInventory[{index}] declares multiple/ambiguous owners for one authoritative state; "
                    "name one canonical truth owner and describe mutators or replicated views elsewhere"
                )
            participant_state = any(
                marker in state_name
                for marker in (
                    "participant", "membership", "memberroster", "playerroster", "lobbyroster"
                )
            )
            if participant_state and source == "existing" and not str(
                row.get("sourceEvidence") or ""
            ).strip():
                issues.append(
                    f"stateInventory[{index}] claims an existing participant/roster truth source without "
                    "sourceEvidence; cite the direct project symbol or inherited engine collection and state "
                    "whether this row is canonical or derived"
                )
            if participant_state:
                participant_inventory_indexes.append(index)
                source_evidence = str(row.get("sourceEvidence") or "").strip()
                if source == "existing" and source_evidence and not (
                    "::" in source_evidence
                    or re.search(r"\.(?:h|hpp|cpp|cxx)(?::\d+)?\b", source_evidence, re.IGNORECASE)
                ):
                    issues.append(
                        f"stateInventory[{index}].sourceEvidence does not identify a concrete field/collection "
                        "or source location; cite Type::Symbol or a project source path, or mark the state "
                        "derived and name derivedFrom"
                    )
                framework_relation = str(row.get("frameworkRelation") or "").strip()
                if has_player_array_contract and source == "new" and not framework_relation:
                    issues.append(
                        f"stateInventory[{index}] proposes a new authoritative participant/roster collection "
                        "without reconciling it with inherited AGameStateBase::PlayerArray; add frameworkRelation "
                        "that proves a non-overlapping responsibility, or mark it derived/existing and cite PlayerArray"
                    )
                if (
                    has_player_array_contract
                    and source == "new"
                    and "playerarray" in framework_relation.lower().replace(" ", "")
                    and not any(
                        marker in framework_relation.lower()
                        for marker in (
                            "non-overlap", "nonoverlap", "reservation", "pre-login", "prelogin",
                            "not connected", "연결 전", "예약", "비중복",
                        )
                    )
                ):
                    issues.append(
                        f"stateInventory[{index}] calls participant membership a new truth source while relating "
                        "it to AGameStateBase::PlayerArray; use source='derived' with derivedFrom, reuse the existing "
                        "collection, or prove a genuinely non-overlapping lifetime"
                    )
            identity_state = any(
                marker in state_name
                for marker in ("identifier", "identity", "participantid", "playerid")
            )
            if identity_state:
                identity_inventory_indexes.append(index)
                missing_identity_contract = [
                    field for field in ("validValues", "assignmentPolicy", "reusePolicy", "invalidValue")
                    if not str(row.get(field) or "").strip()
                ]
                if missing_identity_contract:
                    issues.append(
                        f"stateInventory[{index}] identity lifecycle contract is missing: "
                        + ", ".join(missing_identity_contract)
                    )
                valid_values = str(row.get("validValues") or "")
                invalid_value = str(row.get("invalidValue") or "")
                range_match = re.search(r"(-?\d+)\s*\.\.\s*(-?\d+)", valid_values)
                invalid_match = re.search(r"-?\d+", invalid_value)
                if range_match and invalid_match:
                    lower, upper = int(range_match.group(1)), int(range_match.group(2))
                    invalid_number = int(invalid_match.group(0))
                    if lower <= invalid_number <= upper:
                        issues.append(
                            f"stateInventory[{index}].invalidValue {invalid_number} is inside the valid "
                            f"identifier range {lower}..{upper}; cleanup cannot be distinguished from an active ID"
                        )
                invariant_range = re.search(
                    r"[\[(]\s*(-?\d+)\s*\.\.\s*[^\])]+[\])]",
                    " ".join(string_fields["invariants"]),
                )
                cleanup_text = str(row.get("cleanup") or "").lower()
                if invariant_range and int(invariant_range.group(1)) == 0 and re.search(
                    r"(?:=|to|as)\s*0\b|\b0\s*(?:으로|로)", cleanup_text
                ):
                    issues.append(
                        f"stateInventory[{index}] cleanup assigns 0 even though 0 begins the valid identifier range; "
                        "use an out-of-range invalid sentinel or an unambiguous removal/reassignment policy"
                    )
            if "author" in authority or "server" in authority or "권한" in authority:
                authoritative_owners[state_name].add(str(row.get("owner") or "").strip())
        for state_name, owners in authoritative_owners.items():
            if state_name and len(owners) > 1:
                duplicate_truth_sources.append(
                    f"{state_name}: " + ", ".join(sorted(owners))
                )
        if duplicate_truth_sources:
            issues.append(
                "multiple authoritative truth sources declared: "
                + "; ".join(duplicate_truth_sources)
            )
        participant_intent_text = json.dumps(
            {
                "decision": plan.get("decision"),
                "invariants": plan.get("invariants"),
                "ownership": plan.get("ownership"),
                "lifecycleTransitions": plan.get("lifecycleTransitions"),
                "migrationPlan": plan.get("migrationPlan"),
            },
            ensure_ascii=False,
        ).lower()
        participant_intent = any(
            marker in participant_intent_text
            for marker in (
                "participant", "membership", "roster", "join", "postlogin", "logout",
                "lobby player", "참가자", "참가", "퇴장", "로비 인원",
            )
        )
        if participant_intent and not participant_inventory_indexes:
            framework_hint = (
                " For Unreal GameState projects, inspect and reconcile inherited "
                "AGameStateBase::PlayerArray before proposing another collection."
                if has_player_array_contract else ""
            )
            issues.append(
                "participant membership/roster is part of the design but is absent from stateInventory; "
                "identify its canonical or derived source, owner, lifecycle, and cleanup policy."
                + framework_hint
            )
        identity_required = any(
            marker in " ".join(string_fields["invariants"]).lower()
            for marker in (
                "identifier", "identity", "identif", "unique id", "participant id", "playerid"
            )
        )
        if identity_required and not any(
            any(marker in state_name for marker in ("identifier", "identity", "participantid", "playerid"))
            for state_name in inventory_state_names
        ):
            issues.append(
                "participant identity/uniqueness is declared as an invariant but is absent from stateInventory; "
                "identify its canonical owner, allowed range, assignment point, and cleanup/reuse policy"
            )

        inventory_text_compact = re.sub(
            r"[^a-z0-9]",
            "",
            " ".join(
                str(row.get("state") or "").lower()
                for row in state_inventory
                if isinstance(row, dict)
            ),
        )
        tracking_scope = json.dumps(
            {
                "decision": plan.get("decision"),
                "implementationFiles": plan.get("implementationFiles"),
                "networking": plan.get("networking"),
                "lifecycleTransitions": plan.get("lifecycleTransitions"),
                "validationMatrix": plan.get("validationMatrix"),
                "migrationPlan": plan.get("migrationPlan"),
                "implementationSlices": plan.get("implementationSlices"),
            },
            ensure_ascii=False,
        )
        tracking_identifiers = set(re.findall(
            r"\b([A-Za-z_][A-Za-z0-9_]*?(?:Participants|Roster|Members)|"
            r"(?:Occupied|Assigned|Used|Allocated)[A-Za-z0-9_]*Ids|LobbyPlayers)\b",
            tracking_scope,
            re.IGNORECASE,
        ))
        for identifier in sorted(tracking_identifiers, key=str.lower):
            compact_identifier = re.sub(r"[^a-z0-9]", "", identifier.lower())
            singular_identifier = re.sub(r"(?:participants|players|members|ids)$", lambda m: {
                "participants": "participant", "players": "player", "members": "member", "ids": "id"
            }[m.group(0)], compact_identifier)
            if (
                compact_identifier not in inventory_text_compact
                and singular_identifier not in inventory_text_compact
            ):
                issues.append(
                    f"mutable tracking collection {identifier} appears in the design but is absent from "
                    "stateInventory; declare its owner/source/cleanup and prove it is not a duplicate truth source"
                )

        if not lifecycle_transitions:
            issues.append(
                "networked architecture proposal requires lifecycleTransitions with commit and failure recovery"
            )
        lifecycle_text = " ".join(
            str(row.get("event") or "").lower()
            for row in lifecycle_transitions
            if isinstance(row, dict)
        )
        lifecycle_text = re.sub(r"[_-]+", " ", lifecycle_text)
        lifecycle_contract_text = json.dumps(lifecycle_transitions, ensure_ascii=False).lower()
        if participant_inventory_indexes and re.search(
            r"\b(?:add|remove|clear)\b.{0,40}\b(?:lobby|participant|member)\s+tracking\b",
            lifecycle_contract_text,
        ):
            issues.append(
                "lifecycleTransitions imply a separate mutable participant/lobby tracking collection, but "
                "stateInventory does not name it as a new canonical truth source or derive membership from "
                "a cited existing collection"
            )
        lifecycle_intent_text = json.dumps(
            {
                "decision": plan.get("decision"),
                "invariants": plan.get("invariants"),
                "impactedSurfaces": plan.get("impactedSurfaces"),
                "migrationPlan": plan.get("migrationPlan"),
                "boundaryChanges": plan.get("boundaryChanges"),
            },
            ensure_ascii=False,
        ).lower()

        def has_event_marker(text: str, marker: str) -> bool:
            if marker.isascii():
                inflections = {
                    "join": r"\bjoin(?:s|ed|ing)?\b",
                    "leave": r"\b(?:leave|leaves|leaving|left)\b",
                    "restart": r"\brestart(?:s|ed|ing|game)?\b",
                    "login": r"\blogin\b|\blogged\s+in\b",
                    "logout": r"\blogout\b|\blogged\s+out\b",
                    "connect": r"\bconnect(?:s|ed|ing|ion)?\b",
                    "disconnect": r"\bdisconnect(?:s|ed|ing|ion)?\b",
                }
                return bool(re.search(inflections.get(marker, rf"\b{re.escape(marker)}\b"), text))
            return marker in text

        event_groups = {
            "join": ("join", "login", "connect", "참가", "접속"),
            "leave": ("leave", "logout", "disconnect", "퇴장", "연결 해제"),
            "restart": ("restart", "reset", "재시작", "초기화"),
            "travel": ("travel", "transition", "servertravel", "seamless travel", "전환", "이동"),
        }
        for event_name, markers in event_groups.items():
            if any(has_event_marker(lifecycle_intent_text, marker) for marker in markers) and not any(
                has_event_marker(lifecycle_text, marker) for marker in markers
            ):
                lifecycle_coverage_gaps.append(event_name)
        if lifecycle_coverage_gaps:
            issues.append(
                "lifecycleTransitions missing proposal event coverage: "
                + ", ".join(lifecycle_coverage_gaps)
            )
        for index, row in enumerate(lifecycle_transitions):
            if not isinstance(row, dict):
                issues.append(f"lifecycleTransitions[{index}] must be an object")
                continue
            missing = [
                field
                for field in ("event", "owner", "preconditions", "commitPoint", "failureRecovery", "cleanup")
                if not row.get(field)
            ]
            if missing:
                issues.append(
                    f"lifecycleTransitions[{index}] is missing: " + ", ".join(missing)
                )
            event_text = str(row.get("event") or "").lower()
            if "travel" in event_text or "servertravel" in event_text:
                travel_contract = " ".join(
                    str(row.get(field) or "")
                    for field in (
                        "preconditions", "commitPoint", "failureRecovery", "cleanup"
                    )
                ).lower()
                if "before travel" in travel_contract or "no rollback" in travel_contract:
                    issues.append(
                        f"lifecycleTransitions[{index}] commits authoritative match state before travel "
                        "without rollback; define the post-success commit point or an explicit retry/rollback path"
                    )
                travel_mode = str(row.get("travelMode") or "").strip().lower()
                if travel_mode not in {"seamless", "non-seamless"}:
                    issues.append(
                        f"lifecycleTransitions[{index}].travelMode must explicitly select "
                        "seamless/non-seamless"
                    )
                if "servertravel" in (event_text + " " + travel_contract) and "level streaming" in travel_contract:
                    issues.append(
                        f"lifecycleTransitions[{index}] conflates ServerTravel with level streaming; these are "
                        "different Unreal world-transition mechanisms and need separate lifecycle contracts"
                    )
                if travel_mode == "non-seamless":
                    for field in ("reconstructionSource", "completionSignal"):
                        if not str(row.get(field) or "").strip():
                            issues.append(
                                f"lifecycleTransitions[{index}] non-seamless travel requires {field} so "
                                "destroyed world-owned state can be reconstructed and completion/failure observed"
                            )

        validation_text = " ".join([*string_fields["validationPlan"], *matrix_checks]).lower()
        if client_initiated and not any(
            marker in validation_text
            for marker in ("rpc ownership", "owning connection", "rpc caller", "rpc call path")
        ):
            issues.append(
                "network validation must test RPC caller ownership / owning-connection callability"
            )

    staged_implementation = len(implementation) > 1 or bool(boundary_changes) or len(slices) > 1
    ownership = plan.get("ownership")
    required_ownership_fields = (
        "stateOwner",
        "dataOwner",
        "lifecycleOwner",
        "failurePolicy",
        "recoveryPolicy",
    )
    missing_ownership = [
        field
        for field in required_ownership_fields
        if not isinstance(ownership, dict) or not str(ownership.get(field) or "").strip()
    ]
    migration_plan = _nonempty_string_list(plan.get("migrationPlan"))
    asset_surfaces = [surface for surface in impacted if surface.startswith("/Game/")]
    asset_migration_value = plan.get("assetMigration")
    asset_migration = (
        validate_asset_migration(asset_migration_value)
        if asset_migration_value is not None or asset_surfaces
        else None
    )
    if asset_surfaces and asset_migration_value is None:
        issues.append(
            "assetMigration is required when impactedSurfaces contains /Game assets"
        )
    if asset_migration and not asset_migration["ok"]:
        issues.extend(
            f"asset migration: {issue}" for issue in asset_migration["issues"]
        )
    alternative_comparison = compare_architecture_alternatives(
        alternatives,
        selected_alternative=str(plan.get("selectedAlternative") or ""),
        selection_rationale=str(plan.get("selectionRationale") or ""),
    )

    if staged_implementation:
        if len(alternatives) < 2:
            issues.append("staged architecture proposals require at least two alternatives")
        if missing_ownership:
            issues.append(
                "staged architecture proposal ownership is missing: "
                + ", ".join(missing_ownership)
            )
        if not migration_plan:
            issues.append("staged architecture proposals require a non-empty migrationPlan")
        if not slices:
            issues.append("staged architecture proposals require implementationSlices")
        if not matrix:
            issues.append("staged architecture proposals require validationMatrix")
        elif uncovered_invariants:
            issues.append(
                "declared invariants missing validation coverage: "
                + ", ".join(uncovered_invariants)
            )
        if not alternative_comparison["selectionValid"]:
            issues.extend(
                "architecture alternative selection: " + issue
                for issue in alternative_comparison["selectionIssues"]
            )
    elif declared_invariants and uncovered_invariants:
        warnings.append(
            "Add validationMatrix coverage for invariant(s): "
            + ", ".join(uncovered_invariants)
        )

    cycles = (analysis.get("topology") or {}).get("sourceDependencyCycles") or []
    graph_incomplete = not bool((analysis.get("graphEvidence") or {}).get("complete", True))
    if graph_incomplete:
        issues.append("source graph is incomplete because one or more source files could not be read")
    if int((analysis.get("graphEvidence") or {}).get("sourceFileCount") or 0) == 0:
        issues.append("no supported project source files were found for architecture validation")
    unmatched_focus = (analysis.get("focus") or {}).get("unmatchedSymbols") or []
    if unmatched_focus:
        issues.append(
            "focused architecture symbol(s) were not found: "
            + ", ".join(str(item) for item in unmatched_focus)
        )
    if cycles:
        warnings.append(
            "Source dependency cycle(s) detected; do not add another cross-boundary dependency "
            "without resolving or explicitly accepting the cycle."
        )

    required_validation = list(
        dict.fromkeys(
            [
                "static validation",
                "build/compile",
                "targeted regression",
                "architecture-claim review",
                *matrix_checks,
            ]
        )
    )
    repair_requirements: list[dict[str, str]] = []
    repair_path_rules = (
        ("absent from impacted surfaces/migration scope", "impactedSurfaces"),
        ("Server RPC owner", "networking.rpcOwner"),
        ("network validation must test", "validationPlan"),
        ("staged architecture proposals require a non-empty migrationPlan", "migrationPlan"),
        ("declared invariants missing validation coverage", "validationMatrix"),
        ("networking.replicatedState[", "networking.replicatedState"),
        ("requestPath", "networking.requestPath"),
        ("rpcOwner", "networking.rpcOwner"),
        ("owningConnection", "networking.owningConnection"),
        ("networking fields", "networking"),
        ("participant membership/roster", "stateInventory"),
        ("mutable tracking collection", "stateInventory"),
        ("stateInventory[", "stateInventory"),
        ("travelMode", "lifecycleTransitions"),
        ("lifecycleTransitions[", "lifecycleTransitions"),
        ("lifecycleTransitions", "lifecycleTransitions"),
        ("implementationSlices", "implementationSlices"),
        ("validationMatrix[", "validationMatrix"),
        ("ownership fields", "ownership"),
        ("selectedAlternative", "selectedAlternative"),
        ("alternatives", "alternatives"),
    )
    for issue in issues:
        json_paths: list[str] = []
        if "implementationFiles not covered by implementationSlices" in issue:
            # Either extend the slices or narrow the declared implementation scope.
            # Both are valid, coupled repairs, so expose both paths atomically instead
            # of forcing one implementation strategy on the caller.
            json_paths.extend(["implementationSlices", "implementationFiles"])
        if "required implementation surface(s) are absent" in issue:
            json_paths.extend(
                ["networking.requestPath", "impactedSurfaces", "implementationSlices"]
            )
        for marker, candidate_path in repair_path_rules:
            if marker in issue:
                json_paths.append(candidate_path)
                break
        if not json_paths:
            json_paths.append("proposal")
        for json_path in dict.fromkeys(json_paths):
            repair_requirements.append({"jsonPath": json_path, "constraint": issue})
    writes_allowed = not issues and not bool(cycles)
    return {
        "ok": not issues,
        "issues": issues,
        "warnings": warnings,
        "repairRequirements": repair_requirements,
        "designContract": {
            "stagedImplementation": staged_implementation,
            "alternativeCount": len(alternatives),
            "structuredAlternativeCount": structured_alternatives,
            "implementationSliceCount": len(normalized_slices),
            "implementationFilesCovered": not uncovered_files,
            "uncoveredImplementationFiles": uncovered_files,
            "undeclaredSliceFiles": undeclared_slice_files,
            "invariantCount": len(declared_invariants),
            "invariantCoverageCount": len(covered_invariants),
            "uncoveredInvariants": uncovered_invariants,
            "ownershipComplete": not missing_ownership,
            "missingOwnershipFields": missing_ownership,
            "migrationPlanPresent": bool(migration_plan),
            "sliceDependencyCycle": slice_cycle,
            "alternativeComparison": alternative_comparison,
            "assetMigration": asset_migration,
            "networkedProposal": networked_proposal,
            "networkingComplete": networking_complete,
            "rpcPathConcrete": rpc_path_concrete,
            "stateInventoryCount": len(state_inventory),
            "duplicateTruthSources": duplicate_truth_sources,
            "lifecycleTransitionCount": len(lifecycle_transitions),
            "lifecycleCoverageGaps": lifecycle_coverage_gaps,
        },
        "implementationGate": {
            "writesAllowed": writes_allowed,
            "requiresReadBeforeWrite": True,
            "requiresStagedImplementation": staged_implementation,
            "requiresArchitectureApproval": bool(boundary_changes) or bool(cycles),
            "requiredValidation": required_validation,
            "nextAction": (
                "resolve_architecture_contract_issues"
                if issues
                else ("resolve_source_dependency_cycle" if cycles else "implement_next_slice")
            ),
        },
        "proofBoundary": (
            "A complete design contract proves planning shape and traceability only. It does not prove "
            "the design is correct, the source candidates execute at runtime, or the implementation passes validation."
        ),
    }


def _unreal_framework_contracts(
    root: Path,
    graph: dict[str, Any],
    proposal: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Expose bounded Unreal GameFramework contracts and proposal symbol coverage.

    Detection comes from project class inheritance, so this does not depend on a
    project name, drive letter, engine install path, or operating system.
    """
    class_rows = [
        row for row in graph.get("symbols") or []
        if isinstance(row, dict) and row.get("symbol_kind") == "class"
    ]
    canonical_class_rows: dict[str, dict[str, Any]] = {}
    for row in class_rows:
        name = str(row.get("symbol_name") or "").strip()
        if not name:
            continue
        previous = canonical_class_rows.get(name)
        if previous is None or (
            not str(previous.get("base_class") or "").strip()
            and str(row.get("base_class") or "").strip()
        ):
            canonical_class_rows[name] = row
    bases = {
        name: str(row.get("base_class") or "").strip()
        for name, row in canonical_class_rows.items()
    }

    def derives(type_name: str, targets: set[str]) -> bool:
        current = type_name
        visited: set[str] = set()
        while current and current not in visited:
            visited.add(current)
            base = bases.get(current, current if current != type_name else "")
            base = re.split(r"\s|,|<", base, maxsplit=1)[0].strip()
            if base in targets:
                return True
            current = base
        return False

    game_state_types = sorted(
        name for name in bases
        if derives(name, {"AGameState", "AGameStateBase"})
    )
    game_mode_types = sorted(
        name for name in bases
        if derives(name, {"AGameMode", "AGameModeBase"})
    )
    player_state_types = sorted(
        name for name in bases
        if derives(name, {"APlayerState"})
    )
    player_controller_types = sorted(
        name for name in bases
        if derives(name, {"APlayerController"})
    )
    detected = bool(game_state_types or game_mode_types or player_state_types or player_controller_types)
    if not detected:
        return {"detected": False, "kind": "none", "requestPathSymbols": []}

    class_files = {
        name: _relative(root, str(row.get("file_path") or ""))
        for name, row in canonical_class_rows.items()
    }
    function_rows = [
        row for row in graph.get("symbols") or []
        if isinstance(row, dict) and row.get("symbol_kind") == "function"
    ]
    request_path = (
        ((proposal or {}).get("networking") or {}).get("requestPath")
        if isinstance((proposal or {}).get("networking"), dict)
        else []
    )
    references: list[str] = []
    for hop in request_path or []:
        for type_name, method_name in re.findall(
            r"\b([AU][A-Za-z0-9_]+)::([A-Za-z_][A-Za-z0-9_]*)", str(hop)
        ):
            reference = f"{type_name}::{method_name}"
            if reference not in references:
                references.append(reference)

    symbol_facts: list[dict[str, Any]] = []
    for reference in references:
        type_name, _method_name = reference.split("::", 1)
        if type_name not in bases:
            continue
        matches = [
            row for row in function_rows
            if str(row.get("qualified_name") or "").lower() == reference.lower()
        ]
        declaration_files = sorted({
            _relative(root, str(row.get("file_path") or ""))
            for row in matches
            if Path(str(row.get("file_path") or "")).suffix.lower() in {".h", ".hh", ".hpp", ".hxx"}
        })
        definition_files = sorted({
            _relative(root, str(row.get("file_path") or ""))
            for row in matches
            if Path(str(row.get("file_path") or "")).suffix.lower() in {".c", ".cc", ".cpp", ".cxx"}
        })
        declaration_file = class_files.get(type_name, "")
        companion_sources: list[str] = []
        if declaration_file:
            header = root / Path(declaration_file)
            for suffix in (".cpp", ".cc", ".cxx"):
                candidate = header.with_suffix(suffix)
                if candidate.is_file():
                    companion_sources.append(_relative(root, candidate))
        required_surfaces: list[str] = []
        if not declaration_files and declaration_file:
            required_surfaces.append(declaration_file)
        if not definition_files:
            required_surfaces.extend(companion_sources or ([declaration_file] if declaration_file else []))
        symbol_facts.append({
            "symbol": reference,
            "declarationFiles": declaration_files,
            "definitionFiles": definition_files,
            "requiredImplementationSurfaces": list(dict.fromkeys(required_surfaces)),
        })

    return {
        "detected": True,
        "kind": "UnrealGameFramework",
        "gameModeTypes": game_mode_types,
        "gameStateTypes": game_state_types,
        "playerStateTypes": player_state_types,
        "playerControllerTypes": player_controller_types,
        "inheritedStateCollections": (
            [{
                "symbol": "AGameStateBase::PlayerArray",
                "appliesTo": game_state_types,
                "contract": (
                    "GameStateBase maintains the replicated PlayerState collection for connected players; "
                    "a second authoritative membership collection needs an explicit non-overlap contract, "
                    "otherwise membership should reuse or derive from PlayerArray."
                ),
            }]
            if game_state_types else []
        ),
        "requestPathSymbols": symbol_facts,
        "proofBoundary": (
            "Framework contracts are activated only by direct project inheritance evidence. "
            "Project-specific behavior still requires direct source, build, test, or runtime proof."
        ),
    }


def analyze_architecture(
    project_root: str | Path,
    *,
    symbols: list[str] | None = None,
    proposal: dict[str, Any] | None = None,
    graph: dict[str, Any] | None = None,
    validate_supplied_graph: bool = True,
) -> dict[str, Any]:
    root = _resolve_root(project_root)
    if not root:
        return {"ok": False, "error": f"project root not found: {project_root}"}
    supplied_graph_root = str(graph.get("sourceRoot") or "") if isinstance(graph, dict) else ""
    graph_matches_root = (
        bool(supplied_graph_root)
        and Path(supplied_graph_root).resolve() == root.resolve()
        and (
            not validate_supplied_graph
            or graph_is_fresh_for_root(graph, root)
        )
    )
    active_graph = graph if graph_matches_root else build_symbol_graph(_source_root(root))
    focus_symbols = list(dict.fromkeys(str(symbol).strip() for symbol in (symbols or []) if str(symbol).strip()))
    unmatched_symbols = [
        symbol
        for symbol in focus_symbols
        if not lookup_symbol(symbol, active_graph, limit=1)
    ]
    analysis = {
        "ok": not unmatched_symbols,
        "projectRoot": str(root),
        "focus": {
            "symbols": focus_symbols,
            "unmatchedSymbols": unmatched_symbols,
            "complete": not unmatched_symbols,
        },
        "graphEvidence": {
            "version": active_graph.get("version"),
            "sourceRoot": active_graph.get("sourceRoot"),
            "complete": (active_graph.get("analysis") or {}).get("complete", True),
            "sourceFileCount": len(active_graph.get("files") or []),
            "skippedFileCount": (active_graph.get("analysis") or {}).get("skippedFileCount", 0),
            "limitations": (active_graph.get("analysis") or {}).get("limitations") or [],
            "suppliedGraphAccepted": bool(graph is not None and graph_matches_root),
            "suppliedGraphRebuilt": bool(graph is not None and not graph_matches_root),
        },
        "topology": _topology(root, active_graph),
        "frameworkContracts": _unreal_framework_contracts(root, active_graph, proposal),
        "dataFlow": analyze_data_flow(active_graph, focus_symbols),
        "stateTransitions": analyze_state_transitions(active_graph, focus_symbols),
        "lifecycle": analyze_lifecycle_boundaries(active_graph, focus_symbols),
        "proofBoundary": (
            "Architecture topology uses direct source includes/imports. Data/state/lifecycle results are candidates. "
            "Use direct reads plus static/build/test/runtime evidence before making behavior or ownership conclusions."
        ),
    }
    proposal_payload = proposal if isinstance(proposal, dict) else {}
    analysis["candidatePortfolio"] = generate_architecture_portfolio(
        analysis,
        objective=str(proposal_payload.get("decision") or ""),
        constraints=list(proposal_payload.get("invariants") or []),
    )
    if proposal is not None:
        analysis["proposalValidation"] = validate_architecture_proposal(proposal, analysis)
    return analysis


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze source architecture, candidate data flow, and candidate state transitions.")
    parser.add_argument("--project-root", required=True, help="Project root or .uproject path.")
    parser.add_argument("--symbol", action="append", default=[], help="Optional symbol to focus on; repeatable.")
    parser.add_argument("--proposal", type=Path, default=None, help="Optional JSON architecture proposal to validate.")
    parser.add_argument("--out", type=Path, default=None, help="Optional output JSON file.")
    args = parser.parse_args()
    proposal = json.loads(args.proposal.read_text(encoding="utf-8-sig")) if args.proposal else None
    payload = analyze_architecture(args.project_root, symbols=list(args.symbol or []), proposal=proposal)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
