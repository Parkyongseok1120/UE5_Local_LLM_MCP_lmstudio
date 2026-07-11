#!/usr/bin/env python
"""Run static Unreal compile-readiness validation on a project Source tree."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from unreal_static_validate import (
    DEFERRED_WRITE_COUNTERPART_CODES,
    format_findings,
    has_blocking_write_errors,
    has_static_errors,
    normalize_rel_path,
    resolve_write_scope_paths,
    validate_unreal_readiness,
)


@dataclass
class FindingPayload:
    severity: str
    path: str
    line: int
    code: str
    message: str


def resolve_project_root(path: Path) -> Path:
    resolved = path.resolve()
    if resolved.suffix.lower() == ".uproject":
        return resolved.parent
    if resolved.name.lower() == "source" and resolved.parent.is_dir():
        return resolved.parent
    return resolved


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
    args = parser.parse_args()

    root = resolve_project_root(args.project_root)
    source_dir = root / "Source"
    if not source_dir.is_dir():
        print(f"[FAIL] Source directory not found: {source_dir}", file=sys.stderr)
        return 2

    module_graph = args.module_graph
    if module_graph is None:
        default_graph = Path(__file__).resolve().parent.parent / "data" / "unreal58" / "raw_module_graph.jsonl"
        module_graph = default_graph if default_graph.is_file() else None

    write_target = args.write_target
    scan_mode = "full"
    scoped_file_count = 0
    started = time.perf_counter()
    if write_target:
        scope = resolve_write_scope_paths(root, write_target)
        scoped_file_count = len(scope)
        scan_mode = "scoped"
        findings = validate_unreal_readiness(root, module_graph, scope_paths=scope)
    else:
        findings = validate_unreal_readiness(root, module_graph)
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    has_errors = has_static_errors(findings)
    if write_target:
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
    else:
        has_blocking_errors = has_errors
        deferred_count = 0
        pre_existing_count = 0

    payload = {
        "projectRoot": str(root),
        "sourceDir": str(source_dir),
        "writeTarget": write_target,
        "scanMode": scan_mode,
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
            ).__dict__
            for f in findings
        ],
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(format_findings(findings))

    if write_target:
        return 1 if has_blocking_errors else 0
    if payload["hasErrors"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
