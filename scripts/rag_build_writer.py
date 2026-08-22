"""Transform collected documents into chunk rows and their JSONL mirror."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import TextIO

from rag_build_input import chunk_text, resolve_chunk_params
from rag_build_metadata import metadata_fields
from rag_build_metadata_projection import compact_metadata
from rag_build_schema import CHUNK_INSERT_SQL, schema_counts
from workspace_config import canonical_workspace_root, find_workspace_root
from workspace_locator import normalize_locator_impl


def _normalize_locator(locator: str, workspace_root: Path) -> str:
    return normalize_locator_impl(
        locator,
        workspace_root,
        host_platform=None,
        legacy_prefixes=(),
        find_workspace_root_fn=find_workspace_root,
        canonical_workspace_root_fn=canonical_workspace_root,
    )


class ChunkIndexWriter:
    """Own batching and per-document transformation for one staged generation."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        chunks_file: TextIO,
        workspace_root: Path,
        *,
        chunk_tokens: int,
        overlap_tokens: int,
        batch_size: int = 500,
    ) -> None:
        self.conn = conn
        self.chunks_file = chunks_file
        self.workspace_root = workspace_root
        self.chunk_tokens = chunk_tokens
        self.overlap_tokens = overlap_tokens
        self.batch_size = batch_size
        self.total_chunks = 0
        self.engine_evidence_chunks = 0
        self.project_evidence_chunks = 0
        self._document_id_counts: dict[str, int] = {}
        self._batch: list[tuple] = []

    def add(self, doc: dict) -> None:
        source = str(doc.get("source") or "unknown")
        if source == "unreal_failure_memory":
            return
        metadata_value = doc.get("metadata") or {}
        metadata = dict(metadata_value) if isinstance(metadata_value, dict) else {}
        text = str(doc.get("text") or "").strip()
        if not text:
            return

        document_id = self._unique_document_id(str(doc.get("id") or ""))
        title = str(doc.get("title") or document_id or "Untitled")
        locator = _normalize_locator(
            str(doc.get("url") or doc.get("path") or document_id),
            self.workspace_root,
        )
        for key in ("root", "relative_path", "path", "source_path"):
            if metadata.get(key):
                metadata[key] = _normalize_locator(str(metadata[key]), self.workspace_root)
        fields = metadata_fields(source, title, locator, metadata)
        if fields["project_root"]:
            metadata["project_root"] = fields["project_root"]
        stored_metadata = compact_metadata(metadata, fields)
        chunk_tokens, overlap_tokens = resolve_chunk_params(
            source,
            metadata,
            default_chunk_tokens=self.chunk_tokens,
            default_overlap_tokens=self.overlap_tokens,
        )
        for chunk_index, chunk in enumerate(chunk_text(text, chunk_tokens, overlap_tokens)):
            self._append_chunk(
                document_id,
                source,
                title,
                locator,
                stored_metadata,
                fields,
                chunk_index,
                chunk,
            )

    def finish(self) -> None:
        self._flush()
        if self.total_chunks <= 0:
            raise RuntimeError(
                "RAG build produced zero searchable chunks; existing index was preserved"
            )
        stored, engine, project = schema_counts(self.conn)
        if stored != self.total_chunks:
            raise RuntimeError(
                f"RAG build validation failed: expected {self.total_chunks} chunks, stored {stored}"
            )
        self.engine_evidence_chunks = engine
        self.project_evidence_chunks = project

    def _unique_document_id(self, base: str) -> str:
        count = self._document_id_counts.get(base, 0)
        self._document_id_counts[base] = count + 1
        return base if count == 0 else f"{base}:{count}"

    def _append_chunk(
        self,
        document_id: str,
        source: str,
        title: str,
        locator: str,
        metadata: dict,
        fields: dict[str, str],
        chunk_index: int,
        chunk: str,
    ) -> None:
        chunk_id = f"{document_id}:{chunk_index}"
        item = {
            "chunk_id": chunk_id,
            "document_id": document_id,
            "source": source,
            "title": title,
            "locator": locator,
            "chunk_index": chunk_index,
            "text": chunk,
            "metadata": metadata,
            **fields,
        }
        self.chunks_file.write(json.dumps(item, ensure_ascii=False) + "\n")
        self._batch.append(
            (
                chunk_id,
                document_id,
                source,
                title,
                locator,
                fields["project"],
                fields["project_root"],
                fields["relative_path"],
                fields["extension"],
                fields["layer"],
                fields["doc_type"],
                fields["genre"],
                fields["symbol_name"],
                fields["symbol_kind"],
                fields["module_name"],
                fields["error_code"],
                fields["error_file"],
                int(fields["path_only"]),
                chunk_index,
                chunk,
                json.dumps(metadata, ensure_ascii=False),
            )
        )
        self.total_chunks += 1
        if len(self._batch) >= self.batch_size:
            self._flush()

    def _flush(self) -> None:
        if self._batch:
            self.conn.executemany(CHUNK_INSERT_SQL, self._batch)
            self._batch.clear()
