#!/usr/bin/env python
"""Human-only local CLI for approving a pending high-ambiguity feature intent."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from task_api import task_approve_feature_intent


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Approve a pending feature intent outside the model-callable MCP surface."
    )
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--task-session-id", required=True)
    parser.add_argument("--intent-contract-hash", required=True)
    parser.add_argument("--note", default="")
    args = parser.parse_args()
    result = task_approve_feature_intent(
        Path(args.workspace).expanduser().resolve(),
        args.task_session_id,
        intent_contract_hash=args.intent_contract_hash,
        note=args.note,
        human_channel="local_cli",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
