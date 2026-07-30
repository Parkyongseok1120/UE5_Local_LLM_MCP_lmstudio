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
FLOW_KEYWORDS = {"if", "for", "while", "switch", "catch", "return", "sizeof", "new", "def", "function"}


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
        for line_no, line in _iter_executable_lines(function, lines, start):
            for match in STATE_ASSIGNMENT_RE.finditer(line):
                if len(transitions) >= limit:
                    truncated = True
                    break
                transitions.append(
                    {
                        "kind": "state_assignment_candidate",
                        "function": function.get("qualified_name") or function.get("symbol_name"),
                        "stateField": match.group("field"),
                        "fromState": "unknown",
                        "toState": match.group("value").strip()[:160],
                        "evidence": _source_evidence(path, line_no, digest),
                        "confidence": "source_text_candidate",
                    }
                )
            for match in SET_STATE_RE.finditer(line):
                if len(transitions) >= limit:
                    truncated = True
                    break
                transitions.append(
                    {
                        "kind": "state_setter_candidate",
                        "function": function.get("qualified_name") or function.get("symbol_name"),
                        "stateField": match.group("field"),
                        "fromState": "unknown",
                        "toState": match.group("value").strip()[:160],
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
        "transitions": transitions,
        "truncated": truncated,
        "proofBoundary": (
            "A state-looking assignment/setter is not a complete state transition proof. Current state, guard conditions, "
            "event ordering, replication, and runtime reachability remain unknown until independently verified."
        ),
        "requiredForStateClaim": ["initialization/read site", "transition guard/dispatch", "observer/effect", "runtime or focused test evidence when behavior matters"],
    }


def validate_architecture_proposal(proposal: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any]:
    """Validate a design/implementation proposal against explicit architecture obligations."""
    plan = proposal if isinstance(proposal, dict) else {}
    issues: list[str] = []
    warnings: list[str] = []
    if not isinstance(proposal, dict):
        issues.append("architecture proposal must be an object")
    decision = plan.get("decision")
    if not isinstance(decision, str) or not decision.strip():
        issues.append("architecture proposal field decision must be a non-empty string")
    for field in ("invariants", "impactedSurfaces", "validationPlan", "alternatives"):
        value = plan.get(field)
        if not isinstance(value, list) or not value:
            issues.append(f"architecture proposal field {field} must be a non-empty array")
        elif any(not isinstance(item, str) or not item.strip() for item in value):
            issues.append(f"architecture proposal field {field} must contain non-empty strings")
    impacted = plan.get("impactedSurfaces") if isinstance(plan.get("impactedSurfaces"), list) else []
    implementation_value = plan.get("implementationFiles")
    if implementation_value is not None and (
        not isinstance(implementation_value, list)
        or any(not isinstance(item, str) or not item.strip() for item in implementation_value)
    ):
        issues.append("implementationFiles must be an array of non-empty strings when supplied")
    implementation = implementation_value if isinstance(implementation_value, list) else []
    known_paths = {
        file_path
        for owner in (analysis.get("topology") or {}).get("owners", [])
        if isinstance(owner, dict)
        for file_path in (owner.get("files") or [])
    }
    for path in [*impacted, *implementation]:
        raw = str(path or "").replace("\\", "/")
        if raw and known_paths and raw not in known_paths:
            warnings.append(f"proposal surface not found in analyzed source topology: {raw}")
    boundary_changes = plan.get("boundaryChanges") or []
    if boundary_changes and not isinstance(boundary_changes, list):
        issues.append("boundaryChanges must be an array when supplied")
    for index, change in enumerate(boundary_changes if isinstance(boundary_changes, list) else []):
        if not isinstance(change, dict) or not str(change.get("from") or "") or not str(change.get("to") or ""):
            issues.append(f"boundaryChanges[{index}] needs from/to owners")
        elif not str(change.get("reason") or ""):
            warnings.append(f"boundaryChanges[{index}] should state its reason and ownership rule")
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
        warnings.append("Source dependency cycle(s) detected; do not add another cross-boundary dependency without resolving or explicitly accepting the cycle.")
    return {
        "ok": not issues,
        "issues": issues,
        "warnings": warnings,
        "implementationGate": {
            "writesAllowed": not issues and not bool(cycles),
            "requiresReadBeforeWrite": True,
            "requiresStagedImplementation": len(implementation) > 1 or bool(boundary_changes),
            "requiresArchitectureApproval": bool(boundary_changes) or bool(cycles),
            "requiredValidation": ["static validation", "build/compile", "targeted regression", "architecture-claim review"],
        },
        "proofBoundary": "A complete plan validates planning shape only. It does not prove the design is correct or that an implementation passes validation.",
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
        "dataFlow": analyze_data_flow(active_graph, focus_symbols),
        "stateTransitions": analyze_state_transitions(active_graph, focus_symbols),
        "proofBoundary": (
            "Architecture topology uses direct source includes/imports. Data/state results are candidates. "
            "Use direct reads plus static/build/test/runtime evidence before making behavior or ownership conclusions."
        ),
    }
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
