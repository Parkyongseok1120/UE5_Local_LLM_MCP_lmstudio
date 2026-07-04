#!/usr/bin/env python
"""Summarize eval KPI JSON plus optional wrapper telemetry."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def load_run_telemetry(run_dir: Path | None) -> dict[str, Any]:
    if not run_dir or not run_dir.exists():
        return {"retryState": {}, "ragTelemetry": []}
    return {
        "retryState": load_json(run_dir / "retry_state.json"),
        "ragTelemetry": load_jsonl(run_dir / "rag_telemetry.jsonl"),
    }


def aggregate_rag_telemetry(rows: list[dict[str, Any]]) -> dict[str, Any]:
    sidecar_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    suspected_modules: list[str] = []
    symbol_graph_usage = 0
    module_resolver_usage = 0
    error_route_usage = 0
    for row in rows:
        for key, value in (row.get("sidecarCountsByType") or {}).items():
            count = int(value or 0)
            sidecar_counts[str(key)] = sidecar_counts.get(str(key), 0) + count
        for key, value in (row.get("topSources") or {}).items():
            source_counts[str(key)] = source_counts.get(str(key), 0) + int(value or 0)
        for module in row.get("suspectedModules") or []:
            module = str(module)
            if module and module not in suspected_modules:
                suspected_modules.append(module)
    symbol_graph_usage = sidecar_counts.get("symbol_graph", 0)
    module_resolver_usage = sidecar_counts.get("module_resolver", 0)
    error_route_usage = sidecar_counts.get("error_route", 0)
    return {
        "telemetryRecords": len(rows),
        "sidecarUsageCounts": dict(sorted(sidecar_counts.items())),
        "symbolGraphUsageCount": symbol_graph_usage,
        "moduleResolverHintCount": module_resolver_usage,
        "errorRouteHintCount": error_route_usage,
        "topSources": dict(sorted(source_counts.items(), key=lambda item: item[1], reverse=True)[:8]),
        "suspectedModules": suspected_modules[:10],
        "totalContextChars": sum(int(row.get("contextCharCount") or 0) for row in rows),
    }


def kpi_summary(kpi: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": kpi.get("mode", ""),
        "passAt1Count": int(kpi.get("passAt1Count") or 0),
        "passAt1Rate": float(kpi.get("passAt1Rate") or 0.0),
        "passCount": int(kpi.get("passCount") or 0),
        "total": int(kpi.get("total") or 0),
        "passRate": float(kpi.get("passRate") or 0.0),
        "averageAttempts": float(kpi.get("averageAttempts") or 0.0),
        "failedCaseIds": list(kpi.get("failedCaseIds") or []),
        "sameErrorRepeatedCount": int(kpi.get("sameErrorRepeatedCount") or 0),
        "noOpEditCount": int(kpi.get("noOpEditCount") or 0),
        "repeatedErrorCaseIds": list(kpi.get("repeatedErrorCaseIds") or []),
        "noOpCaseIds": list(kpi.get("noOpCaseIds") or []),
    }


def compare_baseline(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    if not baseline:
        return {}
    fields = {
        "passAt1RateDelta": ("passAt1Rate", 0.0),
        "passRateDelta": ("passRate", 0.0),
        "averageAttemptsDelta": ("averageAttempts", 0.0),
        "sameErrorRepeatedDelta": ("sameErrorRepeatedCount", 0),
        "noOpEditDelta": ("noOpEditCount", 0),
    }
    out: dict[str, Any] = {}
    for out_key, (field, default) in fields.items():
        out[out_key] = round(float(current.get(field, default) or 0) - float(baseline.get(field, default) or 0), 3)
    return out


def suite_display_label(suite_type: str) -> str:
    labels = {
        "fixture-only": "Fixture-only holdout",
        "live-ubt": "Live UBT holdout",
        "real-project-holdout": "Real-project holdout",
        "core": "Core eval",
        "ceiling": "Ceiling eval",
    }
    return labels.get(suite_type, suite_type or "(unspecified)")


def build_summary(
    kpi: dict[str, Any],
    run_data: dict[str, Any],
    baseline: dict[str, Any] | None = None,
    *,
    suite_name: str = "",
    suite_type: str = "",
) -> dict[str, Any]:
    current = kpi_summary(kpi)
    telemetry = aggregate_rag_telemetry(list(run_data.get("ragTelemetry") or []))
    retry_state = run_data.get("retryState") or {}
    if retry_state:
        current["sameErrorRepeatedCount"] = max(
            current["sameErrorRepeatedCount"],
            sum(1 for row in retry_state.get("attempts") or [] if row.get("sameErrorRepeated")),
        )
        current["noOpEditCount"] = max(
            current["noOpEditCount"],
            sum(1 for row in retry_state.get("attempts") or [] if row.get("noOpEdit")),
        )
    return {
        "suite": {
            "name": suite_name,
            "type": suite_type,
        },
        "kpi": current,
        "telemetry": telemetry,
        "baselineDelta": compare_baseline(current, kpi_summary(baseline or {})) if baseline else {},
    }


def render_markdown(summary: dict[str, Any], *, kpi_path: Path, run_dir: Path | None, baseline_path: Path | None) -> str:
    suite = summary.get("suite") or {}
    kpi = summary["kpi"]
    telemetry = summary["telemetry"]
    delta = summary.get("baselineDelta") or {}
    lines = [
        "# Eval KPI Telemetry Report",
        "",
        "This is observed telemetry, not an automatic model-improvement claim.",
        "",
        "## Inputs",
        "",
        f"- KPI JSON: `{kpi_path}`",
        f"- Run directory: `{run_dir}`" if run_dir else "- Run directory: `(not provided)`",
        f"- Baseline: `{baseline_path}`" if baseline_path else "- Baseline: `(not provided)`",
    ]
    if suite.get("name") or suite.get("type"):
        suite_type = str(suite.get("type") or "")
        lines.extend(
            [
                "",
                "## Suite",
                "",
                f"- Suite name: `{suite.get('name') or '(unspecified)'}`",
                f"- Suite type: `{suite.get('type') or '(unspecified)'}`",
                f"- Suite label: {suite_display_label(suite_type)}",
            ]
        )
        if suite_type == "fixture-only":
            lines.extend(
                [
                    "",
                    "> Fixture-only results do not prove live compile-fix success.",
                ]
            )
        if suite_type == "live-ubt":
            lines.extend(
                [
                    "",
                    "> Live UBT holdout reports are still single-run observations unless compared with a saved baseline.",
                ]
            )
            if kpi.get("mode") != "live":
                lines.append("> This KPI JSON is not from live mode; do not treat it as live UBT evidence.")
    lines.extend(
        [
            "",
            "## KPI",
        "",
        f"- Mode: `{kpi['mode']}`",
        f"- Pass@1: {kpi['passAt1Count']} ({kpi['passAt1Rate']:.3f})",
        f"- Pass@K: {kpi['passCount']}/{kpi['total']} ({kpi['passRate']:.3f})",
        f"- Average attempts: {kpi['averageAttempts']:.3f}",
        f"- Failed cases: {', '.join(kpi['failedCaseIds']) or '(none)'}",
        f"- Same error repeated count: {kpi['sameErrorRepeatedCount']}",
        f"- No-op edit count: {kpi['noOpEditCount']}",
        f"- Repeated error cases: {', '.join(kpi['repeatedErrorCaseIds']) or '(none)'}",
        f"- No-op cases: {', '.join(kpi['noOpCaseIds']) or '(none)'}",
        "",
        "## Sidecar Telemetry",
        "",
        f"- Telemetry records: {telemetry['telemetryRecords']}",
        f"- Sidecar usage counts: `{json.dumps(telemetry['sidecarUsageCounts'], ensure_ascii=False, sort_keys=True)}`",
        f"- Symbol graph usage count: {telemetry['symbolGraphUsageCount']}",
        f"- Module resolver hint count: {telemetry['moduleResolverHintCount']}",
        f"- Error route hint count: {telemetry['errorRouteHintCount']}",
        f"- Suspected modules: {', '.join(telemetry['suspectedModules']) or '(none)'}",
        f"- Total context chars: {telemetry['totalContextChars']}",
        ]
    )
    if delta:
        lines.extend(
            [
                "",
                "## Baseline Delta Observed",
                "",
                f"- Pass@1 rate delta: {delta['passAt1RateDelta']:+.3f}",
                f"- Pass@K rate delta: {delta['passRateDelta']:+.3f}",
                f"- Average attempts delta: {delta['averageAttemptsDelta']:+.3f}",
                f"- Same error repeated delta: {delta['sameErrorRepeatedDelta']:+.3f}",
                f"- No-op edit delta: {delta['noOpEditDelta']:+.3f}",
            ]
        )
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- Metrics-only reports validate aggregation only.",
            "- Dry-run reports still depend on UBT when compile validation is performed.",
            "- Live reports require LM Studio and UBT readiness before making workflow claims.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Render concise eval KPI telemetry report.")
    parser.add_argument("kpi_json", type=Path)
    parser.add_argument("--run-dir", type=Path, default=None)
    parser.add_argument("--baseline", type=Path, default=None)
    parser.add_argument("--suite-name", default="", help="Human-readable eval suite name.")
    parser.add_argument(
        "--suite-type",
        default="",
        choices=["", "core", "ceiling", "real-project-holdout", "fixture-only", "live-ubt"],
        help="Evidence tier/type label for this report; it does not change metric values.",
    )
    parser.add_argument("--out-md", type=Path, default=None)
    parser.add_argument("--out-json", type=Path, default=None)
    args = parser.parse_args()

    kpi = load_json(args.kpi_json)
    baseline = load_json(args.baseline) if args.baseline else None
    summary = build_summary(
        kpi,
        load_run_telemetry(args.run_dir),
        baseline,
        suite_name=args.suite_name,
        suite_type=args.suite_type,
    )
    markdown = render_markdown(summary, kpi_path=args.kpi_json, run_dir=args.run_dir, baseline_path=args.baseline)

    if args.out_md:
        args.out_md.parent.mkdir(parents=True, exist_ok=True)
        args.out_md.write_text(markdown, encoding="utf-8")
    if args.out_json:
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
