#!/usr/bin/env python
"""Embedding sidecar for hybrid v2 retrieval (fastembed)."""

from __future__ import annotations

import json
import sqlite3
import struct
import time
from pathlib import Path
from typing import Any, Iterable

EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
EMBED_DIM = 384
DEFAULT_BATCH_SIZE = 256
PRIORITY_SOURCES = (
    "project_guideline",
    "unreal_project_text",
    "unreal_symbol",
    "project_profile",
    "build_log",
    "module_graph",
    "game_design_doc",
    "epic_docs",
)

# Connection pool: reuse connections across calls instead of open/close per query.
_embed_conn_cache: dict[str, sqlite3.Connection] = {}


def _get_embed_conn(path: Path) -> sqlite3.Connection:
    key = str(path.resolve())
    conn = _embed_conn_cache.get(key)
    if conn is None:
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        _embed_conn_cache[key] = conn
    return conn


def embedding_available() -> bool:
    try:
        import fastembed  # noqa: F401

        return True
    except ImportError:
        return False


def sidecar_path(index: Path) -> Path:
    return index.with_suffix(".embeddings.sqlite")


def embedding_status(index: Path) -> dict[str, Any]:
    sidecar = sidecar_path(index)
    info: dict[str, Any] = {
        "available": embedding_available(),
        "model": EMBED_MODEL,
        "sidecarPath": str(sidecar),
        "sidecarExists": sidecar.exists(),
        "hybridV2Ready": False,
        "embeddedChunkCount": 0,
        "installHint": "pip install fastembed && python scripts/rag_embeddings.py build",
    }
    if sidecar.exists():
        conn = sqlite3.connect(sidecar)
        try:
            count = int(conn.execute("select count(*) from chunk_embeddings").fetchone()[0])
            info["embeddedChunkCount"] = count
            info["hybridV2Ready"] = embedding_available() and count > 0
            meta = conn.execute("select key, value from embed_meta").fetchall()
            info["meta"] = {str(k): str(v) for k, v in meta}
        finally:
            conn.close()
    return info


def _ensure_sidecar_schema(side: sqlite3.Connection) -> None:
    side.execute(
        "create table if not exists chunk_embeddings (chunk_id text primary key, vector blob not null)"
    )
    side.execute("create table if not exists embed_meta (key text primary key, value text not null)")


def _pack_vector(values: Iterable[float]) -> bytes:
    return struct.pack(f"{len(list(values))}f", *values)


def _unpack_vector(blob: bytes) -> list[float]:
    count = len(blob) // 4
    return list(struct.unpack(f"{count}f", blob))


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def iter_index_chunks(
    index: Path,
    *,
    sources: list[str] | None = None,
    limit: int | None = None,
) -> Iterable[tuple[str, str]]:
    conn = sqlite3.connect(index)
    try:
        if sources:
            placeholders = ",".join("?" for _ in sources)
            query = f"select chunk_id, text from chunks where source in ({placeholders}) order by chunk_id"
            rows = conn.execute(query, sources)
        else:
            rows = conn.execute("select chunk_id, text from chunks order by chunk_id")
        count = 0
        for chunk_id, text in rows:
            yield str(chunk_id), str(text or "")
            count += 1
            if limit and count >= limit:
                break
    finally:
        conn.close()


def build_embeddings(
    index: Path,
    *,
    limit: int | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    sources: list[str] | None = None,
    priority_only: bool = False,
) -> dict[str, Any]:
    if not embedding_available():
        return {
            "ok": False,
            "error": "fastembed is not installed",
            "installHint": "pip install fastembed",
        }

    from fastembed import TextEmbedding

    if priority_only and not sources:
        sources = list(PRIORITY_SOURCES)

    sidecar = sidecar_path(index)
    side = sqlite3.connect(sidecar)
    _ensure_sidecar_schema(side)
    model = TextEmbedding(model_name=EMBED_MODEL)

    inserted = 0
    started = time.time()
    batch_ids: list[str] = []
    batch_texts: list[str] = []

    def flush_batch() -> None:
        nonlocal inserted
        if not batch_texts:
            return
        vectors = list(model.embed(batch_texts))
        for chunk_id, vector in zip(batch_ids, vectors):
            side.execute(
                "insert or replace into chunk_embeddings(chunk_id, vector) values (?, ?)",
                (chunk_id, vector.tobytes()),
            )
            inserted += 1
        side.commit()
        batch_ids.clear()
        batch_texts.clear()
        if inserted % 2000 == 0:
            elapsed = max(time.time() - started, 0.001)
            print(f"embedded {inserted} chunks ({inserted / elapsed:.1f}/s)", flush=True)

    for chunk_id, text in iter_index_chunks(index, sources=sources, limit=limit):
        batch_ids.append(chunk_id)
        batch_texts.append(text[:4000])
        if len(batch_texts) >= batch_size:
            flush_batch()
    flush_batch()

    side.execute("insert or replace into embed_meta(key, value) values (?, ?)", ("model", EMBED_MODEL))
    side.execute(
        "insert or replace into embed_meta(key, value) values (?, ?)",
        ("builtAt", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
    )
    side.execute(
        "insert or replace into embed_meta(key, value) values (?, ?)",
        ("sources", json.dumps(sources or ["all"])),
    )
    side.commit()
    side.close()
    return {
        "ok": True,
        "inserted": inserted,
        "sidecarPath": str(sidecar),
        "priorityOnly": priority_only,
        "sources": sources,
    }


def search_embeddings(
    index: Path,
    query: str,
    *,
    top_k: int = 12,
    chunk_ids: list[str] | None = None,
    fts_prefilter: int = 80,
) -> list[dict[str, Any]]:
    sidecar = sidecar_path(index)
    if not embedding_available() or not sidecar.exists():
        return []

    from fastembed import TextEmbedding

    from rag_search import SearchOptions, search

    if chunk_ids is None:
        fts_rows = search(
            index,
            query,
            top_k=fts_prefilter,
            options=SearchOptions(candidate_limit=fts_prefilter),
        )
        chunk_ids = [str(row["chunk_id"]) for row in fts_rows]
        if not chunk_ids:
            return []

    model = TextEmbedding(model_name=EMBED_MODEL)
    query_vec = list(next(model.embed([query or ""])))

    side = _get_embed_conn(sidecar)
    main = _get_embed_conn(index)

    placeholders = ",".join("?" for _ in chunk_ids)
    rows = side.execute(
        f"select chunk_id, vector from chunk_embeddings where chunk_id in ({placeholders})",
        chunk_ids,
    ).fetchall()

    scored: list[tuple[float, str]] = []
    for chunk_id, blob in rows:
        vec = _unpack_vector(blob)
        scored.append((_cosine(query_vec, vec), str(chunk_id)))
    scored.sort(reverse=True)
    top = scored[: max(top_k * 3, top_k)]

    # Batch fetch chunk metadata in a single query instead of N+1 individual queries.
    top_ids = [cid for _, cid in top[:top_k]]
    if not top_ids:
        return []
    meta_placeholders = ",".join("?" for _ in top_ids)
    meta_rows = main.execute(
        f"""
        select chunk_id, document_id, source, title, locator, text, project, layer, doc_type,
               symbol_name, symbol_kind, module_name
        from chunks where chunk_id in ({meta_placeholders})
        """,
        top_ids,
    ).fetchall()
    row_map: dict[str, Any] = {str(r[0]): r for r in meta_rows}

    results: list[dict[str, Any]] = []
    for score, chunk_id in top[:top_k]:
        row = row_map.get(chunk_id)
        if not row:
            continue
        results.append(
            {
                "chunk_id": row[0],
                "document_id": row[1],
                "source": row[2],
                "title": row[3],
                "locator": row[4],
                "text": row[5],
                "project": row[6],
                "layer": row[7],
                "doc_type": row[8],
                "symbol_name": row[9],
                "symbol_kind": row[10],
                "module_name": row[11],
                "embedding_score": score,
                "semantic_score": score,
            }
        )
    return results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=Path, default=Path("data/unreal58/rag.sqlite"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--priority-only", action="store_true")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()

    if args.status:
        print(json.dumps(embedding_status(args.index), ensure_ascii=False, indent=2))
    else:
        print(
            json.dumps(
                build_embeddings(
                    args.index,
                    limit=args.limit or None,
                    batch_size=args.batch_size,
                    priority_only=args.priority_only,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
