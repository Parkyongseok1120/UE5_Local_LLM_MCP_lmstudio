#!/usr/bin/env python
"""N-turn agent harness — validates turn contracts and expected tool sequences."""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent


def main() -> int:
    workspace = SCRIPTS.parent
    case_path = workspace / "config" / "rag_eval_agent_harness_cases.json"
    cases = json.loads(case_path.read_text(encoding="utf-8-sig")).get("cases") or []
    failed = 0
    for case in cases:
        turns = case.get("turns") or []
        sequence = case.get("expectedToolSequence") or []
        ok = len(turns) >= 3 and len(sequence) >= 5
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {case['id']} - {len(turns)} turns, {len(sequence)} tool steps")
        if not ok:
            failed += 1
    out = workspace / "data" / "baseline" / "agent-harness-kpi.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"cases": len(cases), "failed": failed}, indent=2),
        encoding="utf-8",
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
