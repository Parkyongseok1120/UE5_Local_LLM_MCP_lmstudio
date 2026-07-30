#!/usr/bin/env python
"""Graph-backed impact, regression, and repair-loop contract for code changes.

The module is intentionally read-only and framework-neutral.  It narrows the
files and tests that must be inspected after a symbol-targeted change, while
keeping heuristic call edges separate from direct source evidence.  Existing
compile/retry loops remain the executors; this module supplies the missing
evidence and regression scope they should consume.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from build_symbol_graph import _iter_source_files, build_symbol_graph, graph_is_fresh_for_root
from symbol_graph import lookup_symbol, related_edges, source_evidence_for_symbol

TEST_DIR_MARKERS = {"test", "tests", "spec", "specs", "__tests__"}
TEST_NAME_RE = re.compile(r"(?:^|[_\-.])(test|tests|spec)(?:[_\-.]|$)", re.IGNORECASE)


def _resolve_root(project_root: str | Path) -> Path | None:
    if not str(project_root or "").strip():
        return None
    candidate = Path(project_root).expanduser().resolve()
    if candidate.is_file() and candidate.suffix.lower() == ".uproject":
        return candidate.parent
    return candidate if candidate.is_dir() else None


def _source_root(root: Path) -> Path:
    # Scan the project root so project plugins, tests, and non-Unreal source
    # trees are not silently omitted when a Source/ directory also exists.
    return root


def _relative(root: Path, path: str | Path) -> str:
    try:
        return Path(path).resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path).replace("\\", "/")


def _node_path(node_id: str, graph: dict[str, Any]) -> str:
    if node_id.startswith("file:"):
        for row in graph.get("files") or []:
            if isinstance(row, dict) and row.get("id") == node_id:
                return str(row.get("path") or "")
    for row in graph.get("symbols") or []:
        if isinstance(row, dict) and row.get("id") == node_id:
            return str(row.get("file_path") or "")
    return ""


def _paired_source_paths(root: Path, matched_symbols: list[dict[str, Any]]) -> list[Path]:
    candidates: list[Path] = []
    for row in matched_symbols:
        path = Path(str(row.get("file_path") or ""))
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        rel = _relative(root, path)
        if suffix in {".h", ".hpp", ".hh"}:
            guesses = [path.with_suffix(ext) for ext in (".cpp", ".cc", ".cxx")]
            if "/Public/" in f"/{rel}":
                guesses.extend((root / rel.replace("/Public/", "/Private/")).with_suffix(ext) for ext in (".cpp", ".cc", ".cxx"))
        elif suffix in {".cpp", ".cc", ".cxx", ".c"}:
            guesses = [path.with_suffix(ext) for ext in (".h", ".hpp", ".hh")]
            if "/Private/" in f"/{rel}":
                guesses.extend((root / rel.replace("/Private/", "/Public/")).with_suffix(ext) for ext in (".h", ".hpp", ".hh"))
        else:
            guesses = []
        candidates.extend(candidate for candidate in guesses if candidate.is_file())
    return sorted(set(candidates))


def _test_files(root: Path, symbols: list[str], *, limit: int = 20) -> list[dict[str, Any]]:
    """Find source-based test candidates; absence is an explicit gap, not a pass."""
    matches: list[dict[str, Any]] = []
    needles = [symbol.lower() for symbol in symbols if len(symbol) >= 2]
    if not needles:
        return matches
    for path in _iter_source_files(root):
        if len(matches) >= limit:
            break
        path_parts = {part.lower() for part in path.parts}
        is_test_path = bool(path_parts & TEST_DIR_MARKERS) or bool(TEST_NAME_RE.search(path.name))
        if not is_test_path:
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace").lower()
        except OSError:
            continue
        mentions = [symbol for symbol in symbols if symbol.lower() in content]
        if not mentions:
            continue
        matches.append(
            {
                "path": _relative(root, path),
                "symbols": mentions[:8],
                "evidence": {
                    "kind": "project_source",
                    "location": f"{path}:1",
                    "observation": "Test-like source file references an impacted symbol.",
                },
            }
        )
    return matches


def build_change_impact_contract(
    project_root: str | Path,
    symbols: list[str],
    *,
    max_files: int = 40,
    graph: dict[str, Any] | None = None,
    build_if_needed: bool = True,
) -> dict[str, Any]:
    """Build a conservative contract for multifile change/review and regression work."""
    root = _resolve_root(project_root)
    queries = list(dict.fromkeys(str(symbol).strip() for symbol in (symbols or []) if str(symbol).strip()))
    if not root:
        return {
            "ok": False,
            "issues": [f"project root not found: {project_root}"],
            "directImpacts": [],
            "candidateImpacts": [],
            "regressionPlan": [],
        }
    if not queries:
        return {
            "ok": False,
            "projectRoot": str(root),
            "issues": ["At least one target symbol is required for graph-backed impact analysis."],
            "directImpacts": [],
            "candidateImpacts": [],
            "regressionPlan": [],
        }
    if max_files < 1:
        return {
            "ok": False,
            "projectRoot": str(root),
            "issues": ["max_files must be at least 1"],
            "directImpacts": [],
            "candidateImpacts": [],
            "regressionPlan": [],
        }

    supplied_graph_root = ""
    if isinstance(graph, dict):
        supplied_graph_root = str(graph.get("sourceRoot") or "")
    graph_matches_root = (
        bool(supplied_graph_root)
        and Path(supplied_graph_root).resolve() == root.resolve()
        and graph_is_fresh_for_root(graph, root)
    )
    if not graph_matches_root and not build_if_needed:
        return {
            "ok": False,
            "projectRoot": str(root),
            "sourceRoot": str(_source_root(root)),
            "symbols": queries,
            "unmatchedSymbols": [],
            "issues": ["a fresh full-project symbol graph is required before graph-backed impact analysis"],
            "directImpacts": [],
            "candidateImpacts": [],
            "truncated": False,
            "graphRefreshRequired": True,
            "regressionPlan": [
                {"kind": "static_validation", "required": True},
                {"kind": "build_or_compile", "required": True},
                {
                    "kind": "targeted_regression",
                    "required": True,
                    "status": "coverage_unknown",
                },
            ],
        }
    active_graph = graph if graph_matches_root else build_symbol_graph(_source_root(root))
    direct: dict[str, dict[str, Any]] = {}
    candidates: dict[str, dict[str, Any]] = {}
    unmatched: list[str] = []
    graph_incomplete = not bool((active_graph.get("analysis") or {}).get("complete", True))
    truncated = False

    def add(bucket: dict[str, dict[str, Any]], path: str, *, reason: str, evidence: dict[str, Any], confidence: str) -> None:
        nonlocal truncated
        if not path:
            return
        relative = _relative(root, path)
        if len(bucket) >= max_files and relative not in bucket:
            truncated = True
            return
        entry = bucket.setdefault(
            relative,
            {"path": relative, "reasons": [], "evidence": [], "confidence": confidence},
        )
        if reason not in entry["reasons"]:
            entry["reasons"].append(reason)
        if evidence and evidence not in entry["evidence"]:
            entry["evidence"].append(evidence)

    for query in queries:
        matches = lookup_symbol(query, active_graph, limit=13)
        if len(matches) > 12:
            truncated = True
            matches = matches[:12]
        if not matches:
            unmatched.append(query)
            continue
        for row in matches:
            add(
                direct,
                str(row.get("file_path") or ""),
                reason=f"target symbol {row.get('qualified_name') or row.get('symbol_name')}",
                evidence=source_evidence_for_symbol(row),
                confidence="direct",
            )
        for pair in _paired_source_paths(root, matches):
            add(
                direct,
                str(pair),
                reason=f"paired declaration/definition surface for {query}",
                evidence={"kind": "project_source", "location": f"{pair}:1", "observation": "Paired source surface."},
                confidence="direct",
            )
        edge_limit = max_files * 3
        related = related_edges(query, active_graph, limit=edge_limit + 1)
        if len(related) > edge_limit:
            truncated = True
            related = related[:edge_limit]
        for edge in related:
            path = str((edge.get("evidence") or {}).get("filePath") or "") or _node_path(str(edge.get("from") or ""), active_graph)
            kind = str(edge.get("kind") or "")
            if kind == "calls_candidate":
                add(
                    candidates,
                    path,
                    reason="candidate caller/callee from conservative call resolution",
                    evidence=dict(edge.get("evidence") or {}),
                    confidence="heuristic",
                )
            else:
                add(
                    direct,
                    path,
                    reason=f"{kind} source relation",
                    evidence=dict(edge.get("evidence") or {}),
                    confidence="direct",
                )

    test_candidates = _test_files(root, queries)
    regression_steps: list[dict[str, Any]] = [
        {"kind": "static_validation", "required": True, "reason": "Catch local source/contract regressions after each staged patch."},
        {"kind": "build_or_compile", "required": True, "reason": "Graph relations are not build proof."},
    ]
    if test_candidates:
        regression_steps.append(
            {
                "kind": "targeted_regression",
                "required": True,
                "reason": "Existing test-like files reference the impacted symbol(s).",
                "candidates": test_candidates,
            }
        )
    else:
        regression_steps.append(
            {
                "kind": "targeted_regression",
                "required": True,
                "status": "coverage_gap",
                "reason": "No source-discoverable targeted test was found; define a focused regression check before claiming behavior is preserved.",
            }
        )

    direct_rows = sorted(direct.values(), key=lambda item: item["path"])
    candidate_rows = sorted(candidates.values(), key=lambda item: item["path"])
    issues = []
    if unmatched:
        issues.append(f"target symbol(s) not found in the project graph: {', '.join(unmatched)}")
    if graph_incomplete:
        issues.append("one or more project source files could not be read; impact evidence is incomplete")
    if truncated:
        issues.append("impact scope exceeded max_files or per-symbol relation limits; narrow the symbol/scope before writing")
    return {
        "ok": bool(direct_rows) and not truncated and not unmatched and not graph_incomplete,
        "projectRoot": str(root),
        "sourceRoot": str(_source_root(root)),
        "symbols": queries,
        "unmatchedSymbols": unmatched,
        "issues": issues,
        "directImpacts": direct_rows,
        "candidateImpacts": candidate_rows,
        "truncated": truncated,
        "graphIncomplete": graph_incomplete,
        "suppliedGraphAccepted": bool(graph is not None and graph_matches_root),
        "suppliedGraphRebuilt": bool(graph is not None and not graph_matches_root),
        "graphRefreshRequired": False,
        "rootCauseGuard": {
            "status": "hypothesis_only",
            "requiredChecks": [
                "Read each direct impact before modifying it.",
                "Treat candidate call impacts as review targets, not confirmed runtime callers.",
                "For a behavior/bug conclusion, record an entry-to-effect BehaviorPath and one checked alternative/counterevidence path.",
            ],
            "forbiddenConclusion": "Do not label a graph relation as the root cause without direct source evidence and the relevant static/build/test/runtime proof.",
        },
        "regressionPlan": regression_steps,
        "autofixLoopContract": {
            "executor": "existing compile/retry loop",
            "beforePatch": ["read direct impacts", "record intended invariant and target surfaces"],
            "afterPatch": ["static validation", "build/compile", "run targeted regression plan"],
            "onNoOpOrRepeatedError": "force_new_evidence; do not repeat the same patch route",
            "stopWhen": ["build/test evidence passes", "impact scope is truncated", "root-cause alternatives remain unresolved"],
        },
        "proofBoundary": (
            "Direct impacts are source-text relationships. Candidate call impacts are heuristic. Neither proves data flow, "
            "state transitions, runtime execution, or a root-cause conclusion by itself."
        ),
    }
