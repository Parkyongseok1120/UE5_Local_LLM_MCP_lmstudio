#!/usr/bin/env python
"""Chunk collected documents and build a SQLite FTS RAG index."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from workspace_paths import canonical_workspace_root, find_workspace_root, normalize_locator


TOKEN_RE = re.compile(r"\S+")


def approx_tokens(text: str) -> list[str]:
    return TOKEN_RE.findall(text)


def infer_doc_type(source: str, metadata: dict) -> str:
    if source == "project_guideline":
        return "guideline"
    if source == "game_design_doc":
        return "game_design"
    if source == "unreal_symbol":
        symbol_kind = str(metadata.get("symbol_kind") or "")
        if symbol_kind == "module":
            return "module_symbol"
        if symbol_kind in {"module_dependency_graph", "include_owner", "include_edge"}:
            return "module_graph"
        if symbol_kind in {"class", "struct", "interface", "enum"}:
            return "type_symbol"
        if symbol_kind in {"function", "function_definition"}:
            return "function_symbol"
        if symbol_kind == "include_map":
            return "include_symbol"
        return "symbol"
    if source == "module_graph":
        symbol_kind = str(metadata.get("symbol_kind") or "")
        if symbol_kind == "include_owner":
            return "include_owner"
        if symbol_kind == "include_edge":
            return "include_edge"
        return "module_graph"
    if source == "project_profile":
        return "project_profile"
    if source == "project_architecture":
        return "project_architecture"
    if source == "build_log":
        return "build_error"
    if source == "epic_docs":
        return "official_doc"
    if source == "unreal_source":
        return "source_code"
    if source == "unreal_project_text":
        return "project_text"
    if source == "unreal_project_asset_path":
        return "asset_path"
    if source == "unreal_blueprint_metadata":
        return "blueprint_metadata"
    if source == "unreal_failure_memory":
        return "failure_memory"
    return source or "unknown"


def infer_layer(source: str, title: str, metadata: dict) -> str:
    if source == "epic_docs":
        return "official_docs"
    if source == "unreal_source":
        return "unreal_source"
    if source == "unreal_project_text":
        return "project_text"
    if source == "unreal_project_asset_path":
        return "project_asset_path"
    if source == "unreal_blueprint_metadata":
        return "project_architecture"
    if source == "unreal_failure_memory":
        return "failure_memory"
    if source == "game_design_doc":
        return "game_design"
    if source == "unreal_symbol":
        return "unreal_symbol"
    if source == "module_graph":
        symbol_kind = str(metadata.get("symbol_kind") or "")
        if symbol_kind in {"include_owner", "include_edge"}:
            return "module_fix"
        return "module_symbol"
    if source == "project_profile":
        return "project_profile"
    if source == "project_architecture":
        return "project_architecture"
    if source == "build_log":
        return str(metadata.get("error_kind") or "build_log")
    if source != "project_guideline":
        return "unknown"

    relative_path = str(metadata.get("relative_path") or "").replace("\\", "/")
    if relative_path.startswith("Planning/"):
        return "planning"
    if relative_path.startswith("Genre_Gameplay/"):
        return "genre"
    if relative_path.startswith("Core_Architecture/"):
        return "core_architecture"

    lowered = f"{title} {relative_path}".lower()
    if "unreal" in lowered or "damage" in lowered or "implementation" in lowered:
        return "unreal_domain"
    if "response" in lowered or "review" in lowered or "process" in lowered:
        return "core_architecture"
    return "project_rule"


def infer_genre(title: str, metadata: dict) -> str:
    value = f"{title} {metadata.get('relative_path') or ''}".lower()
    genre_markers = {
        "action_combat": ("action combat", "combat", "soulslike", "dmc"),
        "shooter": ("shooter", "fps", "tps", "hitscan", "projectile"),
        "battle_royale_extraction": ("battle royale", "extraction"),
        "platformer": ("platformer",),
        "puzzle": ("puzzle",),
        "survival_crafting": ("survival", "crafting"),
        "roguelike": ("roguelike",),
        "deckbuilder": ("deckbuilder",),
        "management_sim": ("management", "simulation"),
        "strategy_tactics": ("strategy", "tactics"),
        "stealth": ("stealth",),
        "horror": ("horror",),
        "narrative": ("narrative",),
        "rhythm": ("rhythm",),
        "racing": ("racing",),
        "tower_defense": ("tower defense",),
    }
    for genre, markers in genre_markers.items():
        if any(marker in value for marker in markers):
            return genre
    return ""


def metadata_fields(source: str, title: str, locator: str, metadata: dict) -> dict[str, str]:
    project = str(metadata.get("project") or "")
    relative_path = str(metadata.get("relative_path") or "")
    extension = str(metadata.get("extension") or Path(locator).suffix or "").lower()
    path_only = "1" if metadata.get("path_only") else "0"
    return {
        "project": project,
        "relative_path": relative_path,
        "extension": extension,
        "layer": infer_layer(source, title, metadata),
        "doc_type": infer_doc_type(source, metadata),
        "genre": str(metadata.get("genre") or infer_genre(title, metadata)) if source in {"project_guideline", "game_design_doc"} else "",
        "path_only": path_only,
        "symbol_name": str(metadata.get("symbol_name") or ""),
        "symbol_kind": str(metadata.get("symbol_kind") or ""),
        "module_name": str(metadata.get("module_name") or ""),
        "error_code": str(metadata.get("error_code") or ""),
        "error_file": str(metadata.get("error_file") or ""),
    }


def resolve_chunk_params(
    source: str,
    metadata: dict,
    *,
    default_chunk_tokens: int = 900,
    default_overlap_tokens: int = 120,
) -> tuple[int | None, int | None]:
    if source == "module_graph":
        return None, None
    if source == "unreal_symbol":
        return 300, 60
    return default_chunk_tokens, default_overlap_tokens


def chunk_text(text: str, chunk_tokens: int, overlap_tokens: int) -> list[str]:
    tokens = approx_tokens(text)
    if not tokens:
        return []
    if len(tokens) <= chunk_tokens:
        return [" ".join(tokens)]

    chunks: list[str] = []
    start = 0
    step = max(1, chunk_tokens - overlap_tokens)
    while start < len(tokens):
        end = min(start + chunk_tokens, len(tokens))
        chunks.append(" ".join(tokens[start:end]))
        if end == len(tokens):
            break
        start += step
    return chunks


def read_jsonl(paths: list[Path]):
    for path in paths:
        if not path.exists():
            print(f"[skip] missing input: {path}")
            continue
        with path.open("r", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield path, line_no, json.loads(line)
                except json.JSONDecodeError as exc:
                    print(f"[skip] {path}:{line_no} ({exc})")


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        drop table if exists chunks;
        drop table if exists chunks_fts;
        drop table if exists module_edges;
        drop table if exists include_owners;

        create table chunks (
            chunk_id text primary key,
            document_id text not null,
            source text not null,
            title text not null,
            locator text not null,
            project text not null default '',
            relative_path text not null default '',
            extension text not null default '',
            layer text not null default '',
            doc_type text not null default '',
            genre text not null default '',
            symbol_name text not null default '',
            symbol_kind text not null default '',
            module_name text not null default '',
            error_code text not null default '',
            error_file text not null default '',
            path_only integer not null default 0,
            chunk_index integer not null,
            text text not null,
            metadata_json text not null
        );

        create virtual table chunks_fts using fts5(
            title,
            locator,
            symbol_name,
            symbol_kind,
            module_name,
            error_code,
            error_file,
            text,
            content='chunks',
            content_rowid='rowid',
            tokenize='unicode61'
        );

        create trigger chunks_ai after insert on chunks begin
            insert into chunks_fts(rowid, title, locator, symbol_name, symbol_kind, module_name, error_code, error_file, text)
            values (new.rowid, new.title, new.locator, new.symbol_name, new.symbol_kind, new.module_name, new.error_code, new.error_file, new.text);
        end;

        create index chunks_source_idx on chunks(source);
        create index chunks_project_idx on chunks(project);
        create index chunks_layer_idx on chunks(layer);
        create index chunks_doc_type_idx on chunks(doc_type);
        create index chunks_genre_idx on chunks(genre);
        create index chunks_extension_idx on chunks(extension);
        create index chunks_symbol_name_idx on chunks(symbol_name);
        create index chunks_symbol_kind_idx on chunks(symbol_kind);
        create index chunks_module_name_idx on chunks(module_name);
        create index chunks_error_code_idx on chunks(error_code);
        create index chunks_error_file_idx on chunks(error_file);

        create table module_edges (
            edge_id text primary key,
            document_id text not null,
            edge_kind text not null default '',
            consumer_module text not null default '',
            owner_module text not null default '',
            include_path text not null default '',
            consumer_file text not null default '',
            dependency_visibility text not null default '',
            dependency_status text not null default '',
            title text not null default '',
            text text not null default '',
            metadata_json text not null
        );

        create table include_owners (
            owner_id text primary key,
            document_id text not null,
            include_path text not null default '',
            symbol_name text not null default '',
            module_name text not null default '',
            owner_modules_json text not null default '[]',
            title text not null default '',
            text text not null default '',
            metadata_json text not null
        );

        create index module_edges_consumer_idx on module_edges(consumer_module);
        create index module_edges_include_idx on module_edges(include_path);
        create index module_edges_owner_idx on module_edges(owner_module);
        create index module_edges_kind_idx on module_edges(edge_kind);
        create index include_owners_path_idx on include_owners(include_path);
        create index include_owners_symbol_idx on include_owners(symbol_name);
        """
    )


def ingest_module_graph(conn: sqlite3.Connection, doc: dict) -> None:
    metadata = doc.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    document_id = str(doc.get("id") or "")
    title = str(doc.get("title") or document_id or "Untitled")
    text = str(doc.get("text") or "").strip()
    symbol_kind = str(metadata.get("symbol_kind") or "")
    metadata_json = json.dumps(metadata, ensure_ascii=False)

    if symbol_kind == "include_owner":
        include_path = str(metadata.get("include_path") or metadata.get("symbol_name") or "")
        owner_modules = metadata.get("owner_modules") or []
        conn.execute(
            """
            insert into include_owners(
                owner_id,
                document_id,
                include_path,
                symbol_name,
                module_name,
                owner_modules_json,
                title,
                text,
                metadata_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document_id or stable_module_graph_id(symbol_kind, include_path, title),
                document_id,
                include_path,
                str(metadata.get("symbol_name") or include_path),
                str(metadata.get("module_name") or ""),
                json.dumps(owner_modules, ensure_ascii=False),
                title,
                text,
                metadata_json,
            ),
        )
        return

    if symbol_kind == "include_edge":
        owner_modules = [str(value) for value in metadata.get("owner_modules") or [] if value]
        primary_owner = owner_modules[0] if owner_modules else ""
        conn.execute(
            """
            insert into module_edges(
                edge_id,
                document_id,
                edge_kind,
                consumer_module,
                owner_module,
                include_path,
                consumer_file,
                dependency_visibility,
                dependency_status,
                title,
                text,
                metadata_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document_id or stable_module_graph_id(symbol_kind, str(metadata.get("include_path") or ""), title),
                document_id,
                symbol_kind,
                str(metadata.get("consumer_module") or metadata.get("module_name") or ""),
                primary_owner,
                str(metadata.get("include_path") or metadata.get("symbol_name") or ""),
                str(metadata.get("consumer_file") or ""),
                str(metadata.get("dependency_visibility") or ""),
                str(metadata.get("dependency_status") or ""),
                title,
                text,
                metadata_json,
            ),
        )
        return

    if symbol_kind == "module_dependency_graph":
        module_name = str(metadata.get("module_name") or metadata.get("symbol_name") or "")
        conn.execute(
            """
            insert into module_edges(
                edge_id,
                document_id,
                edge_kind,
                consumer_module,
                owner_module,
                include_path,
                consumer_file,
                dependency_visibility,
                dependency_status,
                title,
                text,
                metadata_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document_id or stable_module_graph_id(symbol_kind, module_name, title),
                document_id,
                symbol_kind,
                module_name,
                "",
                "",
                "",
                "",
                "",
                title,
                text,
                metadata_json,
            ),
        )
        return

    conn.execute(
        """
        insert into module_edges(
            edge_id,
            document_id,
            edge_kind,
            consumer_module,
            owner_module,
            include_path,
            consumer_file,
            dependency_visibility,
            dependency_status,
            title,
            text,
            metadata_json
        )
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            document_id or stable_module_graph_id(symbol_kind, title, text[:80]),
            document_id,
            symbol_kind or "module_graph",
            str(metadata.get("module_name") or ""),
            "",
            str(metadata.get("include_path") or ""),
            str(metadata.get("consumer_file") or ""),
            str(metadata.get("dependency_visibility") or ""),
            str(metadata.get("dependency_status") or ""),
            title,
            text,
            metadata_json,
        ),
    )


def stable_module_graph_id(symbol_kind: str, key: str, title: str) -> str:
    return hashlib.sha1(f"{symbol_kind}:{key}:{title}".encode("utf-8")).hexdigest()


def build(args: argparse.Namespace) -> None:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    chunks_path = out_dir / "chunks.jsonl"
    sqlite_path = out_dir / "rag.sqlite"
    workspace_root = Path(args.workspace_root).resolve() if args.workspace_root else find_workspace_root()

    input_paths = [Path(value) for value in args.input]

    if sqlite_path.exists():
        try:
            sqlite_path.unlink()
        except PermissionError:
            staging_path = sqlite_path.with_name(f"{sqlite_path.stem}.staging{sqlite_path.suffix}")
            if staging_path.exists():
                staging_path.unlink()
            sqlite_path = staging_path
            print(f"warning: main index locked; writing staging index to {sqlite_path}", flush=True)

    conn = sqlite3.connect(sqlite_path)
    create_schema(conn)

    total_chunks = 0
    module_edge_count = 0
    include_owner_count = 0
    document_id_counts: dict[str, int] = {}
    with chunks_path.open("w", encoding="utf-8") as chunks_file:
        for _, _, doc in read_jsonl(input_paths):
            source = str(doc.get("source") or "unknown")
            metadata = doc.get("metadata") or {}
            if isinstance(metadata, dict):
                metadata = dict(metadata)
            else:
                metadata = {}

            if source == "module_graph":
                ingest_module_graph(conn, doc)
                symbol_kind = str(metadata.get("symbol_kind") or "")
                if symbol_kind == "include_owner":
                    include_owner_count += 1
                else:
                    module_edge_count += 1
                continue

            text = str(doc.get("text") or "").strip()
            if not text:
                continue

            base_document_id = str(doc.get("id") or "")
            document_id_count = document_id_counts.get(base_document_id, 0)
            document_id_counts[base_document_id] = document_id_count + 1
            document_id = base_document_id if document_id_count == 0 else f"{base_document_id}:{document_id_count}"
            title = str(doc.get("title") or document_id or "Untitled")
            locator = normalize_locator(str(doc.get("url") or doc.get("path") or document_id), workspace_root)
            for key in ("root", "relative_path", "path", "source_path"):
                if metadata.get(key):
                    metadata[key] = normalize_locator(str(metadata[key]), workspace_root)
            fields = metadata_fields(source, title, locator, metadata)
            chunk_tokens, overlap_tokens = resolve_chunk_params(
                source,
                metadata,
                default_chunk_tokens=args.chunk_tokens,
                default_overlap_tokens=args.overlap_tokens,
            )
            if chunk_tokens is None or overlap_tokens is None:
                continue

            for index, chunk in enumerate(chunk_text(text, chunk_tokens, overlap_tokens)):
                chunk_id = f"{document_id}:{index}"
                item = {
                    "chunk_id": chunk_id,
                    "document_id": document_id,
                    "source": source,
                    "title": title,
                    "locator": locator,
                    "chunk_index": index,
                    "text": chunk,
                    "metadata": metadata,
                    **fields,
                }
                chunks_file.write(json.dumps(item, ensure_ascii=False) + "\n")
                conn.execute(
                    """
                    insert into chunks(
                        chunk_id,
                        document_id,
                        source,
                        title,
                        locator,
                        project,
                        relative_path,
                        extension,
                        layer,
                        doc_type,
                        genre,
                        symbol_name,
                        symbol_kind,
                        module_name,
                        error_code,
                        error_file,
                        path_only,
                        chunk_index,
                        text,
                        metadata_json
                    )
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk_id,
                        document_id,
                        source,
                        title,
                        locator,
                        fields["project"],
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
                        index,
                        chunk,
                        json.dumps(metadata, ensure_ascii=False),
                    ),
                )
                total_chunks += 1

    conn.commit()
    conn.close()

    manifest = {
        "workspaceRoot": str(canonical_workspace_root(workspace_root)),
        "engineVersion": os.environ.get("UNREAL_ENGINE_VERSION", "5.8"),
        "builtAt": datetime.now(timezone.utc).isoformat(),
        "chunkCount": total_chunks,
        "moduleEdgeCount": module_edge_count,
        "includeOwnerCount": include_owner_count,
        "inputs": [
            {
                "path": str(path.resolve()),
                "exists": path.exists(),
                "sizeBytes": path.stat().st_size if path.exists() else 0,
                "modifiedAt": datetime.fromtimestamp(
                    path.stat().st_mtime, timezone.utc
                ).isoformat()
                if path.exists()
                else None,
            }
            for path in input_paths
        ],
        "outputs": {
            "chunksJsonl": str(chunks_path.resolve()),
            "sqlite": str(sqlite_path.resolve()),
        },
    }
    manifest_path = out_dir / "build_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"done: wrote {total_chunks} chunks")
    print(f"module graph side tables: {module_edge_count} module_edges, {include_owner_count} include_owners")
    print(f"workspace: {workspace_root}")
    print(f"chunks: {chunks_path}")
    print(f"sqlite: {sqlite_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build SQLite RAG index from JSONL docs.")
    parser.add_argument("--input", nargs="+", required=True)
    parser.add_argument("--out-dir", default="data/unreal58")
    parser.add_argument("--workspace-root", default="", help="Normalize legacy locators to this workspace root.")
    parser.add_argument("--chunk-tokens", type=int, default=900)
    parser.add_argument("--overlap-tokens", type=int, default=120)
    return parser.parse_args()


if __name__ == "__main__":
    build(parse_args())
