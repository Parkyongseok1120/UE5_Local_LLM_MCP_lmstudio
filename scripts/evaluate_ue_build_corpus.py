"""Measure targeted static diagnostics on minimized broken/fixed build cases.

This is detection of recorded error classes, not a real compilation or a
prediction that a historical build would have succeeded after one fix.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from unreal_static_runner import validate_unreal_readiness
from engine_identity import describe_unreal_engine_identity


def evaluate(corpus: Path, engine_root: Path | None = None):
    manifest = json.loads(corpus.read_text(encoding="utf-8"))
    results = []
    identity = describe_unreal_engine_identity(engine_root) if engine_root else {"status": "unknown"}
    for case in manifest["cases"]:
        row = {"id": case["id"], "occurrences": len(case["observations"]), "targeted": bool(case["expectedCodes"])}
        for variant in ("broken", "fixed"):
            with tempfile.TemporaryDirectory(prefix="ue-build-corpus-") as temp:
                root = Path(temp)
                (root / "Corpus.uproject").write_text('{"FileVersion":3}', encoding="utf-8")
                for relative, text in case[variant].items():
                    target = root / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(text, encoding="utf-8")
                findings = validate_unreal_readiness(root, engine_identity=identity)
                codes = {f.code for f in findings}
                row[variant + "Codes"] = sorted(codes & set(case["expectedCodes"]))
        row["caught"] = bool(row["brokenCodes"]) and not row["fixedCodes"]
        results.append(row)
    return {"schemaVersion": 1, "metric": "targeted diagnostic present in broken and absent in fixed minimized source",
            "engineVersion": identity.get("version"), "engineSource": "installed" if engine_root else "unavailable",
            "cases": results, "caseCount": len(results), "failedBuilds": sum(r["occurrences"] for r in results),
            "caughtCases": sum(r["caught"] for r in results),
            "caughtFailedBuilds": sum(r["occurrences"] for r in results if r["caught"]),
            "fixedFalsePositives": sum(bool(r["fixedCodes"]) for r in results),
            "untargetedCases": [r["id"] for r in results if not r["targeted"]]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=Path(__file__).resolve().parents[1] / "tests/fixtures/ue_build_failures/corpus.json")
    parser.add_argument("--engine-root", type=Path)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.corpus, args.engine_root), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
