#!/usr/bin/env python3
"""Measure the canonical Node-to-Python control bridge at release boundaries."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "lmstudio-unreal-agent-mcp" / "src" / "task-control-transition.js"
SIZES_MB = (1, 8, 15, 16, 17)


NODE_SOURCE = r"""
const { commitControlTransition } = require(process.argv[1]);
const sizeMb = Number(process.argv[2]);
const state = {
  taskSessionId: `benchmark-${sizeMb}-${process.pid}`,
  status: 'running',
  mode: 'read_only',
  planRevision: '1',
  mutationGeneration: 0,
  toolRoute: { phase: 'planner', activeTools: ['search_files'] },
  transportPadding: 'x'.repeat(sizeMb * 1024 * 1024),
};
const serializedBytes = Buffer.byteLength(JSON.stringify(state), 'utf8');
const before = process.memoryUsage().rss;
const started = performance.now();
commitControlTransition(state);
const latencyMs = performance.now() - started;
const after = process.memoryUsage().rss;
process.stdout.write(JSON.stringify({
  sizeMb,
  serializedBytes,
  latencyMs,
  rssDeltaBytes: after - before,
  outputBytes: Buffer.byteLength(JSON.stringify(state), 'utf8'),
  authoritative: state.controlState?.authoritative === true,
}));
"""


def measure(size_mb: int) -> dict[str, object]:
    node = shutil.which("node")
    if not node:
        raise RuntimeError("node is unavailable")
    started = time.perf_counter()
    completed = subprocess.run(
        [node, "-e", NODE_SOURCE, str(MODULE), str(size_mb)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=140,
    )
    payload = json.loads(completed.stdout)
    payload["processWallMs"] = (time.perf_counter() - started) * 1000
    payload["pythonProcessStartups"] = 1
    payload["transport"] = "bounded_temp_file"
    payload["adapterBlocksCallingEventLoop"] = True
    return payload


def main() -> int:
    sequential = [measure(size) for size in SIZES_MB]
    with ThreadPoolExecutor(max_workers=4) as executor:
        concurrent = list(executor.map(measure, (1, 1, 1, 1)))
    print(
        json.dumps(
            {
                "schemaVersion": 1,
                "bridge": "spawnSync Python semantic owner with bounded temp-file transport",
                "timeoutMs": 120_000,
                "stdoutMaxBytes": 1024 * 1024,
                "sequential": sequential,
                "concurrent": {
                    "requests": len(concurrent),
                    "allAuthoritative": all(row["authoritative"] is True for row in concurrent),
                    "maxProcessWallMs": max(float(row["processWallMs"]) for row in concurrent),
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
