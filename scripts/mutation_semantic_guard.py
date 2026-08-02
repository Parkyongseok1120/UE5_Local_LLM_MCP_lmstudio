#!/usr/bin/env python
"""Check the complete prospective source text for shared known-bad patterns.

The MCP write server calls this with source text on stdin immediately before a
single-file mutation, or immediately after an atomic bundle commit while the
transaction can still roll back. Keeping this adapter tiny lets writes consult
the same denylist as the code-sketch gate without duplicating regexes in Node.
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
