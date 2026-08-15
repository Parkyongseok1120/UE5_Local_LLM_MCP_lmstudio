#!/usr/bin/env python
"""Run static Unreal compile-readiness validation on a project Source tree."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

from unreal_static_validate import (
    DEFERRED_WRITE_COUNTERPART_CODES,
    format_findings,
    has_blocking_write_errors,
    has_static_errors,
    normalize_rel_path,
    resolve_scan_roots,
    resolve_write_scope_paths,
    validate_unreal_readiness,
)
from workspace_paths import resolve_index_dir


@dataclass
class FindingPayload:
    severity: str
    path: str
    line: int
    code: str
    message: str
    blocking: bool


def resolve_project_root(path: Path) -> Path:
    resolved = path.resolve()
    if resolved.suffix.lower() == ".uproject":
        return resolved.parent
    if resolved.name.lower() == "source" and resolved.parent.is_dir():
        return resolved.parent
    return resolved


def normalize_scope_target(value: str) -> str:
    """Normalize a server-selected project path without permitting root escape."""
    normalized = str(value or "").strip().replace("\\", "/")
    path = PurePosixPath(normalized)
    parts = path.parts
    has_drive = len(normalized) >= 2 and normalized[1] == ":"
    if (
        not normalized
        or normalized.startswith("/")
        or has_drive
        or ".." in parts
        or not parts
        or parts[0].lower() not in {"source", "plugins"}
    ):
        raise ValueError(
            "--scope-target must be a project-relative path under Source/ or Plugins/"
        )
    return normalized


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Unreal project sources")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--module-graph", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--write-target",
        default=None,
        help=(
            "Relative path (from project root) of the file that was just written. "
            "When set, runs a scoped scan (write-target + paired header/cpp) and "
            "hasBlockingErrors is limited to errors on this file (excluding "
            "deferred counterpart codes); other findings are reported as advisories."
        ),
    )
    parser.add_argument(
        "--scope-target",
        action="append",
        default=[],
        help=(
            "Relative path (from project root) in the current task slice. May be "
            "repeated. Runs one scoped scan over these files and their validation "
            "counterparts; only errors on the explicitly selected task files block."
        ),
    )
    args = parser.parse_args()

    if args.write_target and args.scope_target:
        parser.error("--write-target and --scope-target cannot be combined")

    root = resolve_project_root(args.project_root)
    source_dir = root / "Source"
    scan_roots = resolve_scan_roots(root)
    if not scan_roots:
        print(
            f"[FAIL] No Unreal source roots found under Source/ or Plugins/*/Source: {root}",
            file=sys.stderr,
        )
        return 2

    module_graph = args.module_graph
    if module_graph is None:
        default_graph = resolve_index_dir() / "raw_module_graph.jsonl"
        module_graph = default_graph if default_graph.is_file() else None

    write_target = args.write_target
    try:
        scope_targets = list(
            dict.fromkeys(
                normalize_scope_target(item)
                for item in args.scope_target
                if str(item or "").strip()
            )
        )
    except ValueError as exc:
        parser.error(str(exc))
    scan_mode = "full"
    scope_kind = "full_audit"
    scoped_file_count = 0
    started = time.perf_counter()
    if write_target:
        scope = resolve_write_scope_paths(root, write_target)
        scoped_file_count = len(scope)
        scan_mode = "scoped"
        scope_kind = "write_target"
        findings = validate_unreal_readiness(root, module_graph, scope_paths=scope)
    elif scope_targets:
        scope_by_path: dict[Path, None] = {}
        for target in scope_targets:
            for scoped_path in resolve_write_scope_paths(root, target):
                scope_by_path[scoped_path.resolve()] = None
        scope = sorted(scope_by_path)
        scoped_file_count = len(scope)
        scan_mode = "scoped"
        scope_kind = "task_slice"
        findings = validate_unreal_readiness(root, module_graph, scope_paths=scope)
    else:
        findings = validate_unreal_readiness(root, module_graph)
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    has_errors = has_static_errors(findings)
    blocking_paths: set[str] = set()
    if write_target:
        blocking_paths = {normalize_rel_path(write_target)}
        has_blocking_errors = has_blocking_write_errors(findings, write_target)
        target_norm = normalize_rel_path(write_target)
        deferred_count = sum(
            1 for f in findings if f.severity == "error" and f.code in DEFERRED_WRITE_COUNTERPART_CODES
        )
        pre_existing_count = sum(
            1
            for f in findings
            if f.severity == "error"
            and f.code not in DEFERRED_WRITE_COUNTERPART_CODES
            and normalize_rel_path(f.path) != target_norm
        )
    elif scope_targets:
        # Related files are scanned for cross-file context, but only errors on
        # server-selected task files block this slice. A paired or domain-
        # expanded file outside the selected slice remains advisory until the
        # server explicitly selects it for mutation.
        blocking_paths = {normalize_rel_path(item) for item in scope_targets}
        has_blocking_errors = any(
            finding.severity == "error"
            and normalize_rel_path(finding.path) in blocking_paths
            for finding in findings
        )
        deferred_count = 0
        pre_existing_count = sum(
            1
            for finding in findings
            if finding.severity == "error"
            and normalize_rel_path(finding.path) not in blocking_paths
        )
    else:
        blocking_paths = {
            normalize_rel_path(finding.path)
            for finding in findings
            if finding.severity == "error"
        }
        has_blocking_errors = has_errors
        deferred_count = 0
        pre_existing_count = 0

    payload = {
        "projectRoot": str(root),
        "sourceDir": str(source_dir),
        "scanRoots": [str(item) for item in scan_roots],
        "writeTarget": write_target,
        "scopeTargets": scope_targets,
        "scanMode": scan_mode,
        "scopeKind": scope_kind,
        "scopedFileCount": scoped_file_count,
        "elapsedMs": elapsed_ms,
        "findingCount": len(findings),
        "hasErrors": has_errors,
        "hasBlockingErrors": has_blocking_errors,
        "deferredCount": deferred_count,
        "preExistingCount": pre_existing_count,
        "findings": [
            FindingPayload(
                severity=f.severity,
                path=f.path,
                line=f.line,
                code=f.code,
                message=f.message,
                blocking=(
                    f.severity == "error"
                    and normalize_rel_path(f.path) in blocking_paths
                    and (
                        not write_target
                        or f.code not in DEFERRED_WRITE_COUNTERPART_CODES
                    )
                ),
            ).__dict__
            for f in findings
        ],
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(format_findings(findings))

    if write_target or scope_targets:
        return 1 if has_blocking_errors else 0
    if payload["hasErrors"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
