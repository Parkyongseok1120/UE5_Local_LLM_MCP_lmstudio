#!/usr/bin/env python
"""Report shared known-bad patterns in complete prospective source text.

The Direct MCP uses this analyzer only for advisory evidence: its findings,
absence, or execution failure never authorize or block a mutation. Hard path,
CAS, atomicity, lock, size, and deletion-approval gates remain independent.
Keeping the adapter tiny avoids duplicating the Python denylist in Node.
"""

from __future__ import annotations

import json
import sys

from unreal_api_denylist import check_denylist


def main() -> int:
    text = sys.stdin.read()
    hits = check_denylist(text)
    print(json.dumps({"ok": not hits, "hits": hits}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
