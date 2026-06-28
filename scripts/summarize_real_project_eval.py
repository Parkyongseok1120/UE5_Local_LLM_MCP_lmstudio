#!/usr/bin/env python
"""Summarize unseen real-project UE validation results."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def truthy(value: Any) -> bool:
    return value is True or str(value).strip().lower() in {"true", "yes", "1", "pass"}


def case_pass_at(case: dict[str, Any], k: int) -> bool:
    if k == 1 and "passAt1" in case:
        return truthy(case.get("passAt1"))
    if k == 3 and "passAt3" in case:
        return truthy(case.get("passAt3"))
    if truthy(case.get("finalPass")):
        attempts = int(case.get("attemptsUsed") or case.get("attempts") or 999)
        return attempts <= k
    return False


def summarize(config: dict[str, Any]) -> dict[str, Any]:
    cases = list(config.get("cases") or [])
    total = len(cases)
    pass_at_1 = sum(1 for case in cases if case_pass_at(case, 1))
    pass_at_3 = sum(1 for case in cases if case_pass_at(case, 3))
    final_pass = sum(1 for case in cases if truthy(case.get("finalPass")) or case_pass_at(case, 3))
    unseen = sum(1 for case in cases if truthy(case.get("unseen")))
    leakage_checked = sum(1 for case in cases if truthy(case.get("fixtureLeakageChecked")))
    validated = sum(
        1
        for case in cases
        if truthy(case.get("ubtValidated")) or truthy(case.get("editorValidated")) or truthy(case.get("validated"))
    )
    categories = Counter(str(case.get("category") or "unknown") for case in cases)
    failure_categories = Counter(
        str(case.get("failureCategory") or case.get("category") or "unknown")
        for case in cases
        if not (truthy(case.get("finalPass")) or case_pass_at(case, 3))
    )
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "projectName": config.get("projectName") or "",
        "engineVersion": config.get("engineVersion") or "",
        "total": total,
        "passAt1": {"count": pass_at_1, "rate": round(pass_at_1 / total, 3) if total else 0.0},
        "passAt3": {"count": pass_at_3, "rate": round(pass_at_3 / total, 3) if total else 0.0},
        "finalPass": {"count": final_pass, "rate": round(final_pass / total, 3) if total else 0.0},
        "unseenCases": unseen,
        "fixtureLeakageChecked": leakage_checked,
        "ubtOrEditorValidated": validated,
        "categoryCounts": dict(categories),
        "failureCategoryCounts": dict(failure_categories),
        "interpretation": {
            "strongPracticalClaim": total >= 20 and unseen == total and leakage_checked == total and pass_at_3 / total >= 0.7 if total else False,
            "warning": "Report Pass@1 separately. Do not convert this internal result into a global Sonnet 4 claim.",
        },
    }


def write_markdown(summary: dict[str, Any], out_path: Path) -> None:
    total = int(summary["total"])
    lines = [
        "# Real Project Eval Summary",
        f"Generated: {summary['generatedAt']}",
        "",
        f"- Project: {summary.get('projectName') or 'n/a'}",
        f"- Engine: {summary.get('engineVersion') or 'n/a'}",
        f"- Cases: {total}",
        f"- Pass@1: {summary['passAt1']['count']}/{total} ({summary['passAt1']['rate']:.0%})",
        f"- Pass@3: {summary['passAt3']['count']}/{total} ({summary['passAt3']['rate']:.0%})",
        f"- Final pass: {summary['finalPass']['count']}/{total} ({summary['finalPass']['rate']:.0%})",
        f"- Unseen cases: {summary['unseenCases']}/{total}",
        f"- Fixture leakage checked: {summary['fixtureLeakageChecked']}/{total}",
        f"- UBT or Editor validated: {summary['ubtOrEditorValidated']}/{total}",
        "",
        "## Category Counts",
        "",
    ]
    for key, value in sorted((summary.get("categoryCounts") or {}).items()):
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Failure Category Counts", ""])
    failures = summary.get("failureCategoryCounts") or {}
    if failures:
        for key, value in sorted(failures.items()):
            lines.append(f"- {key}: {value}")
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- This is a real-project workflow score, not a global model benchmark.",
            "- Pass@1 and Pass@3 must be read separately.",
            "- Blueprint/Material/Animation claims require Editor-side validation.",
        ]
    )
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize real-project validation results.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--out-dir", type=Path, default=Path("Reports/real_project_eval/latest"))
    args = parser.parse_args()

    config = load_json(args.input)
    summary = summarize(config)
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(summary, out_dir / "summary.md")
    print(f"Wrote {out_dir / 'summary.json'}")
    print(f"Wrote {out_dir / 'summary.md'}")
    print(f"Pass@1: {summary['passAt1']['rate']:.0%}")
    print(f"Pass@3: {summary['passAt3']['rate']:.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
