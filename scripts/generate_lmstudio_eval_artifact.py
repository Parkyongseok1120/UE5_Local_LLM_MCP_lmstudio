#!/usr/bin/env python
"""Generate LM Studio model reliability eval artifact template (manual gate)."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "docs" / "release_evidence"
DEFAULT_MODELS = ("qwen3.5-9b", "qwen3.6-27b")
PASS_RATE_THRESHOLD = 0.95


def build_report(
    model: str,
    *,
    calls: int,
    pass_rate: float,
    routing_accuracy: float,
    recovery_ok: bool,
    executed: bool,
) -> dict:
    if not executed:
        status = "NOT RUN"
    elif pass_rate >= PASS_RATE_THRESHOLD and recovery_ok:
        status = "PASS"
    else:
        status = "FAIL"
    return {
        "model": model,
        "generatedAt": datetime.now(tz=timezone.utc).isoformat(),
        "gate": "lmstudio_model_reliability",
        "metrics": {
            "routingMarkerAccuracy": routing_accuracy,
            "toolSelectionValidRate": pass_rate,
            "argumentValidityRate": pass_rate,
            "recoveryAfterError": recovery_ok,
            "toolsCallSuccessRate": pass_rate,
            "callsExecuted": calls,
        },
        "status": status,
        "notes": "Populate after manual LM Studio session with 100-call matrix per model.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", action="append", default=None, help="Model slug to record (repeatable).")
    parser.add_argument("--all-default-models", action="store_true", help="Write both default Qwen eval artifacts.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--calls", type=int, default=100)
    parser.add_argument("--pass-rate", type=float, default=0.0)
    parser.add_argument("--routing-accuracy", type=float, default=0.0)
    parser.add_argument("--recovery-ok", action="store_true")
    parser.add_argument("--executed", action="store_true", help="Mark artifact as measured (not template-only).")
    args = parser.parse_args()

    if args.all_default_models:
        models = list(DEFAULT_MODELS)
    elif args.model:
        models = list(args.model)
    else:
        parser.error("Specify --model <slug> or --all-default-models")

    executed = args.executed or args.pass_rate > 0 or args.routing_accuracy > 0 or args.recovery_ok
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for model in models:
        slug = model.replace(".", "_").replace("/", "_")
        report = build_report(
            model,
            calls=args.calls,
            pass_rate=args.pass_rate,
            routing_accuracy=args.routing_accuracy,
            recovery_ok=args.recovery_ok,
            executed=executed,
        )
        out = args.out_dir / f"{slug}_lmstudio_eval.json"
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
