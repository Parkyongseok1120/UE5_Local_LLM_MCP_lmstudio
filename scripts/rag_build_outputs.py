"""Own staged output names, manifest serialization, and atomic promotion."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from index_inputs import INDEX_INPUT_POLICY_FINGERPRINT, INDEX_INPUT_POLICY_VERSION
from rag_build_metadata_projection import chunk_metadata_policy
from workspace_config import canonical_workspace_root
from workspace_index_paths import resolve_engine_version


@dataclass(frozen=True)
class BuildOutputPaths:
    chunks: Path
    sqlite: Path
    manifest: Path
    staged_chunks: Path
    staged_sqlite: Path
    staged_manifest: Path

    @classmethod
    def create(cls, out_dir: Path, build_id: str) -> "BuildOutputPaths":
        return cls(
            chunks=out_dir / "chunks.jsonl",
            sqlite=out_dir / "rag.sqlite",
            manifest=out_dir / "build_manifest.json",
            staged_chunks=out_dir / f"chunks.building.{build_id}.jsonl",
            staged_sqlite=out_dir / f"rag.building.{build_id}.sqlite",
            staged_manifest=out_dir / f"build_manifest.building.{build_id}.json",
        )

    def discard_staged(self) -> None:
        for path in (self.staged_sqlite, self.staged_chunks, self.staged_manifest):
            try:
                path.unlink()
            except OSError:
                pass


def resolved_engine_version(workspace_root: Path, explicit: str = "") -> str:
    return str(explicit or "").strip() or resolve_engine_version(workspace_root)


def write_manifest(
    paths: BuildOutputPaths,
    *,
    args,
    workspace_root: Path,
    generation_id: str,
    input_paths: list[Path],
    total_chunks: int,
    engine_evidence_chunks: int,
    project_evidence_chunks: int,
) -> None:
    manifest = {
        "inputPolicyVersion": INDEX_INPUT_POLICY_VERSION,
        "inputPolicyFingerprint": INDEX_INPUT_POLICY_FINGERPRINT,
        "chunkMetadataPolicy": chunk_metadata_policy(),
        "workspaceRoot": str(canonical_workspace_root(workspace_root)),
        "engineVersion": resolved_engine_version(
            workspace_root, str(getattr(args, "engine_version", "") or "")
        ),
        "engineAssociation": str(getattr(args, "engine_association", "") or "").strip(),
        "indexingTier": str(getattr(args, "indexing_tier", "") or "").strip(),
        "generationId": generation_id,
        "corpusCapabilities": {
            "engineEvidence": engine_evidence_chunks > 0,
            "engineEvidenceChunks": engine_evidence_chunks,
            "projectEvidence": project_evidence_chunks > 0,
            "projectEvidenceChunks": project_evidence_chunks,
        },
        "builtAt": datetime.now(timezone.utc).isoformat(),
        "chunkCount": total_chunks,
        "inputs": [_input_record(path) for path in input_paths],
        "outputs": {
            "chunksJsonl": str(paths.chunks.resolve()),
            "sqlite": str(paths.sqlite.resolve()),
        },
    }
    paths.staged_manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def promote_outputs(
    paths: BuildOutputPaths,
    *,
    replace: Callable[[Path, Path], None] = os.replace,
) -> None:
    try:
        replace(paths.staged_sqlite, paths.sqlite)
    except OSError as exc:
        raise RuntimeError(
            "RAG build completed but could not atomically promote its validated index; "
            f"the existing index was left in place. Staging index: {paths.staged_sqlite}"
        ) from exc
    try:
        replace(paths.staged_chunks, paths.chunks)
        replace(paths.staged_manifest, paths.manifest)
    except OSError as exc:
        raise RuntimeError(
            "The validated RAG index was promoted, but a companion output could not be "
            "promoted. Inspect the remaining staging chunks or manifest before rebuilding."
        ) from exc


def _input_record(path: Path) -> dict:
    exists = path.exists()
    stat = path.stat() if exists else None
    return {
        "path": str(path.resolve()),
        "exists": exists,
        "sizeBytes": stat.st_size if stat else 0,
        "modifiedAt": (
            datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
            if stat
            else None
        ),
    }
