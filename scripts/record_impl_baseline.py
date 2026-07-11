#!/usr/bin/env python
"""Record pre/post implementation baseline metadata."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    branch = subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip()
    payload = {
        "recordedAt": datetime.now(timezone.utc).isoformat(),
        "branch": branch,
        "sha": sha,
        "note": "Develop b3ce33f completion implementation baseline",
    }
    out = ROOT / "Reports" / "eval" / "pre_impl_baseline.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
