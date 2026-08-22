#!/usr/bin/env python
"""Rebuild the RAG index only when raw inputs or manifest are stale."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from index_inputs import (
    FORBIDDEN_RAW_INPUT_FILES,
    INDEX_INPUT_POLICY_FINGERPRINT,
    RAW_INPUT_FILES,
    existing_input_paths,
)
from rag_build_metadata_projection import chunk_metadata_policy
from workspace_paths import find_workspace_root, resolve_index_dir


def input_paths(data_dir: Path) -> list[Path]:
    return existing_input_paths(data_dir)


def _missing_recorded_inputs(manifest: dict, inputs: list[Path]) -> list[str]:
    recorded = {
        str(item.get("path") or ""): item
        for item in manifest.get("inputs", [])
        if isinstance(item, dict) and str(item.get("path") or "").strip()
    }
    current_inputs = {str(path.resolve()) for path in inputs}
    return sorted(
        Path(recorded_path).name
        for recorded_path, info in recorded.items()
        if (
            Path(recorded_path).name in RAW_INPUT_FILES
            or Path(recorded_path).name in FORBIDDEN_RAW_INPUT_FILES
        )
        and info.get("exists", True) is not False
        and recorded_path not in current_inputs
    )


def manifest_stale(data_dir: Path, manifest_path: Path, sqlite_path: Path) -> tuple[bool, str]:
    if not sqlite_path.exists():
        return True, "index-missing"

    inputs = input_paths(data_dir)
    manifest: dict = {}
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return True, "manifest-invalid"
        if manifest.get("inputPolicyFingerprint") != INDEX_INPUT_POLICY_FINGERPRINT:
            return True, "input-policy-changed"
        if manifest.get("chunkMetadataPolicy") != chunk_metadata_policy():
            return True, "chunk-metadata-policy-changed"
    if not inputs:
        if manifest:
            missing_recorded = _missing_recorded_inputs(manifest, inputs)
            if missing_recorded:
                return True, f"manifest-input-missing ({', '.join(missing_recorded)})"
        return False, "no-inputs"

    newest_input = max(inputs, key=lambda path: path.stat().st_mtime)
    index_mtime = sqlite_path.stat().st_mtime
    if newest_input.stat().st_mtime > index_mtime:
        return True, f"input-newer-than-index ({newest_input.name})"

    chunks_jsonl = data_dir / "chunks.jsonl"
    if chunks_jsonl.exists() and chunks_jsonl.stat().st_mtime > index_mtime:
        return True, "chunks-jsonl-newer-than-index"

    if not manifest_path.exists():
        return True, "manifest-missing"

    recorded = {
        str(item.get("path") or ""): item
        for item in manifest.get("inputs", [])
        if isinstance(item, dict) and str(item.get("path") or "").strip()
    }
    missing_recorded = _missing_recorded_inputs(manifest, inputs)
    if missing_recorded:
        return True, f"manifest-input-missing ({', '.join(missing_recorded)})"
    for path in inputs:
        resolved = str(path.resolve())
        info = recorded.get(resolved)
        if not info:
            return True, f"manifest-missing-input ({path.name})"
        if not path.exists():
            continue
        stat = path.stat()
        if int(info.get("sizeBytes") or 0) != stat.st_size:
            return True, f"input-size-changed ({path.name})"
        recorded_mtime = info.get("modifiedAt")
        if recorded_mtime:
            current = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
            if current != recorded_mtime:
                return True, f"input-modified ({path.name})"

    workspace_root = manifest.get("workspaceRoot")
    current_root = str(find_workspace_root().resolve())
    if workspace_root and workspace_root != current_root:
        return True, "workspace-root-changed"

    return False, "up-to-date"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=None, help="Output directory (default: configured RAG data directory).")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--engine-version", default=None)
    parser.add_argument("--engine-association", default=None)
    parser.add_argument("--project", type=Path, default=None, help="Exact .uproject engine provenance.")
    args = parser.parse_args()

    workspace = find_workspace_root()
    from direct_rag_build_binding import resolve_build_binding

    binding = resolve_build_binding(
        workspace,
        args.project,
        args.engine_version,
        args.engine_association,
    )
    if binding.get("ok") is not True:
        print(f"error: {binding.get('error')}", file=sys.stderr)
        return 2
    data_dir = args.out_dir.resolve() if args.out_dir and args.out_dir.is_absolute() else (
        (workspace / args.out_dir).resolve() if args.out_dir else resolve_index_dir(workspace)
    )
    from direct_rag_public_build import build_public_index

    result = build_public_index(
        workspace=workspace,
        index_dir=data_dir,
        force=args.force,
        stale_check=manifest_stale,
        engine_version=binding.get("engineVersion"),
        engine_association=binding.get("engineAssociation"),
        engine_root=str(binding.get("engineRoot") or ""),
    )
    if result.get("skipped"):
        print(f"skip: {result.get('reason') or 'up-to-date'}")
    elif result.get("ok"):
        print(f"rebuild: {result.get('reason') or 'stale'}")
        output = str((result.get("build") or {}).get("outputTail") or "")
        if output:
            print(output, end="" if output.endswith("\n") else "\n")
    else:
        print(f"error: {result.get('error') or 'RAG build failed'}", file=sys.stderr)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
