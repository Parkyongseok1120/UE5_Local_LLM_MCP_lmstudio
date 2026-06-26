#!/usr/bin/env python
"""Evaluate refactor plan validation cases."""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from refactor_plan import validate_refactor_plan  # noqa: E402


def main() -> int:
    workspace = SCRIPTS.parent
    case_path = workspace / "config" / "unreal_refactor_eval_cases.json"
    cases = json.loads(case_path.read_text(encoding="utf-8"))["cases"]
    failed = 0
    for case in cases:
        result = validate_refactor_plan(case["stage"], case["planText"])
        ok = result["ok"] == case.get("expectOk", True)
        if case.get("expectWarning") and not result.get("warnings"):
            ok = False
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {case['id']}")
        if not ok:
            failed += 1
            print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
