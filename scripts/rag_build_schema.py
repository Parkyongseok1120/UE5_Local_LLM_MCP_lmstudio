"""Own the SQLite schema and insert contract for generated RAG indexes."""

from __future__ import annotations

import sqlite3

CHUNK_INSERT_SQL = """
insert into chunks(
    chunk_id, document_id, source, title, locator, project, project_root,
    relative_path, extension, layer, doc_type, genre, symbol_name, symbol_kind,
    module_name, error_code, error_file, path_only, chunk_index, text, metadata_json
) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        drop table if exists chunks;
        drop table if exists chunks_fts;
        drop table if exists index_meta;

        create table index_meta (
            key text primary key,
            value text not null
        );
        create table chunks (
            chunk_id text primary key,
            document_id text not null,
            source text not null,
            title text not null,
            locator text not null,
            project text not null default '',
            project_root text not null default '',
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
            title, locator, symbol_name, symbol_kind, module_name, error_code,
            error_file, text, content='chunks', content_rowid='rowid',
            tokenize='unicode61'
        );
        create trigger chunks_ai after insert on chunks begin
            insert into chunks_fts(
                rowid, title, locator, symbol_name, symbol_kind, module_name,
                error_code, error_file, text
            ) values (
                new.rowid, new.title, new.locator, new.symbol_name,
                new.symbol_kind, new.module_name, new.error_code,
                new.error_file, new.text
            );
        end;
        create trigger chunks_ad after delete on chunks begin
            insert into chunks_fts(
                chunks_fts, rowid, title, locator, symbol_name, symbol_kind,
                module_name, error_code, error_file, text
            ) values (
                'delete', old.rowid, old.title, old.locator, old.symbol_name,
                old.symbol_kind, old.module_name, old.error_code,
                old.error_file, old.text
            );
        end;
        create trigger chunks_au after update on chunks begin
            insert into chunks_fts(
                chunks_fts, rowid, title, locator, symbol_name, symbol_kind,
                module_name, error_code, error_file, text
            ) values (
                'delete', old.rowid, old.title, old.locator, old.symbol_name,
                old.symbol_kind, old.module_name, old.error_code,
                old.error_file, old.text
            );
            insert into chunks_fts(
                rowid, title, locator, symbol_name, symbol_kind, module_name,
                error_code, error_file, text
            ) values (
                new.rowid, new.title, new.locator, new.symbol_name,
                new.symbol_kind, new.module_name, new.error_code,
                new.error_file, new.text
            );
        end;
        create index chunks_source_idx on chunks(source);
        create index chunks_project_idx on chunks(project);
        create index chunks_project_root_idx on chunks(project_root);
        create index chunks_layer_idx on chunks(layer);
        create index chunks_doc_type_idx on chunks(doc_type);
        create index chunks_genre_idx on chunks(genre);
        create index chunks_extension_idx on chunks(extension);
        create index chunks_symbol_name_idx on chunks(symbol_name);
        create index chunks_symbol_kind_idx on chunks(symbol_kind);
        create index chunks_module_name_idx on chunks(module_name);
        create index chunks_error_code_idx on chunks(error_code);
        create index chunks_error_file_idx on chunks(error_file);
        create index chunks_source_title_idx on chunks(source, title);
        create index chunks_title_idx on chunks(title);
        """
    )


def schema_counts(conn: sqlite3.Connection) -> tuple[int, int, int]:
    stored = int(conn.execute("select count(*) from chunks").fetchone()[0])
    engine = int(
        conn.execute(
            "select count(*) from chunks where project_root = '' "
            "and source in ('unreal_symbol', 'unreal_source', 'epic_docs')"
        ).fetchone()[0]
    )
    project = int(
        conn.execute("select count(*) from chunks where project_root != ''").fetchone()[0]
    )
    return stored, engine, project
