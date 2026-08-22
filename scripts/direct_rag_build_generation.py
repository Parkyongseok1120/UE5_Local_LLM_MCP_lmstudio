#!/usr/bin/env python
"""Build index companions inside one hidden, non-live generation directory."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any

from index_inputs import existing_input_paths


def build_generation(
    index_dir: Path,
    workspace: Path,
    *,
    engine_version: str = "",
    engine_association: str = "",
    indexing_tier: str = "",
) -> dict[str, Any]:
    target = index_dir.expanduser().resolve()
    inputs = existing_input_paths(target)
    if not inputs:
        return {
            "ok": False,
            "errorCode": "RAG_RAW_INPUTS_MISSING",
            "error": "No raw input JSONL files were available for the staged generation.",
        }
    cmd = [
        sys.executable,
        str(workspace / "scripts" / "build_rag_index.py"),
        "--out-dir", str(target),
        "--workspace-root", str(workspace.resolve()),
        *(["--engine-version", engine_version] if engine_version else []),
        *(["--engine-association", engine_association] if engine_association else []),
        *(["--indexing-tier", indexing_tier] if indexing_tier else []),
        "--input", *[str(path) for path in inputs],
    ]
    proc = subprocess.run(
        cmd,
        cwd=str(workspace),
        text=True,
        capture_output=True,
        check=False,
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "command": cmd,
        "outputTail": output[-4000:] if output else "",
        **(
            {}
            if proc.returncode == 0
            else {
                "errorCode": "RAG_INDEX_BUILD_FAILED",
                "error": "The staged RAG generation build failed.",
            }
        ),
    }


def _is_refresh_stage(path: Path) -> bool:
    return ".direct-refresh-" in path.name and path.parent.is_dir()


def main() -> int:
    parser = argparse.ArgumentParser(description="Build one hidden Direct RAG generation.")
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--workspace", type=Path, default=None)
    parser.add_argument("--engine-version", default="")
    parser.add_argument("--engine-association", default="")
    parser.add_argument("--indexing-tier", choices=("lite", "standard", "full"), default="")
    args = parser.parse_args()
    target = args.out_dir.expanduser().resolve()
    if not _is_refresh_stage(target):
        print("error: generation builder requires a Direct refresh stage", file=sys.stderr)
        return 2
    workspace = (
        args.workspace.expanduser().resolve()
        if args.workspace is not None
        else Path(__file__).resolve().parent.parent
    )
    result = build_generation(
        target,
        workspace,
        engine_version=str(args.engine_version or "").strip(),
        engine_association=str(args.engine_association or "").strip(),
        indexing_tier=str(args.indexing_tier or "").strip(),
    )
    if result.get("outputTail"):
        print(result["outputTail"], end="" if str(result["outputTail"]).endswith("\n") else "\n")
    if result.get("ok") is not True:
        print(str(result.get("error") or "generation build failed"), file=sys.stderr)
        return int(result.get("returncode") or 1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_generation"]
