#!/usr/bin/env python
"""Generate Phase 0 audit matrix from git ls-files (read-only)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "release_evidence" / "e6396af_audit_matrix.md"

PRIORITY_MAP = {
    "edit-bundle.js": "P0-A",
    "write-locks.js": "P0-A",
    "task-auth.js": "P0-A",
    "wrapper_job_manager.py": "P0-B",
    "task_api.py": "P0-B",
    "server.js": "P0-C",
    "unreal-detect.js": "P0-C",
    "build-proof.js": "P0-C",
    "validation-dirty.js": "P0-D",
    "atomic-io.js": "P1",
    "atomic_io.py": "P1",
    "tool-exposure.js": "P1",
    "tool_exposure.py": "P1",
    "Install-PathHelpers.ps1": "P1",
    "agent_orchestrator.py": "P2",
}


def role_for(path: str) -> str:
    if path.endswith(".js") and "lmstudio-unreal-agent-mcp/src" in path.replace("\\", "/"):
        return "agent-mcp-runtime"
    if path.endswith(".py") and path.replace("\\", "/").startswith("scripts/"):
        return "rag-orchestration"
    if path.endswith(".ps1"):
        return "installer"
    if path.replace("\\", "/").startswith("tests/"):
        return "test"
    if path.replace("\\", "/").startswith("config/"):
        return "config"
    if path.replace("\\", "/").startswith(".github/"):
        return "ci"
    return "support"


def main() -> int:
    proc = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    scopes = (
        "scripts/",
        "lmstudio-unreal-agent-mcp/src/",
        "lmstudio-unreal-agent-mcp/test/",
        "installer/",
        "config/",
        "tests/",
        "prompts/",
        ".github/workflows/",
    )
    selected = [p for p in lines if any(p.replace("\\", "/").startswith(s) for s in scopes)]
    rows = ["# e6396af Audit Matrix", "", f"Total tracked scope files: {len(selected)}", ""]
    rows.append(
        "| path | type | role | priority | tests | uncovered failures |"
    )
    rows.append("|---|---|---|---|---|---|")
    for rel in selected:
        name = Path(rel).name
        ext = Path(rel).suffix.lower()
        priority = PRIORITY_MAP.get(name, "P2" if "test" in rel else "P1")
        test_glob = f"tests/test_{Path(rel).stem}" if ext == ".py" and rel.startswith("scripts/") else ""
        uncovered = "see plan" if priority.startswith("P0") else "routine"
        rows.append(f"| `{rel}` | {ext or 'none'} | {role_for(rel)} | {priority} | {test_glob or 'partial'} | {uncovered} |")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(rows) + "\n", encoding="utf-8")
    print(f"Wrote {OUT} ({len(selected)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
