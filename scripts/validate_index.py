#!/usr/bin/env python
"""Validate RAG index schema and manifest engineVersion against workspace config."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from workspace_paths import load_workspace_config, resolve_index_path

REQUIRED_CHUNK_COLUMNS = {
    "chunk_id",
    "document_id",
    "source",
    "title",
    "locator",
    "project",
    "relative_path",
    "extension",
    "layer",
    "doc_type",
    "genre",
    "symbol_name",
    "symbol_kind",
    "module_name",
    "error_code",
    "error_file",
    "path_only",
    "chunk_index",
    "text",
    "metadata_json",
}


def load_manifest(data_dir: Path) -> dict[str, Any] | None:
    manifest_path = data_dir / "build_manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid build_manifest.json: {exc}") from exc


def check_schema(index: Path) -> list[str]:
    errors: list[str] = []
    if not index.is_file():
        return [f"index file missing: {index}"]

    conn = sqlite3.connect(index)
    try:
        tables = {
            str(row[0])
            for row in conn.execute("select name from sqlite_master where type='table'")
        }
        if "chunks" not in tables:
            errors.append("missing table: chunks")
            return errors

        columns = {
            str(row[1]) for row in conn.execute("pragma table_info(chunks)")
        }
        missing = sorted(REQUIRED_CHUNK_COLUMNS - columns)
        if missing:
            errors.append(f"chunks table missing columns: {', '.join(missing)}")

        chunk_count = int(conn.execute("select count(*) from chunks").fetchone()[0])
        if chunk_count <= 0:
            errors.append("chunks table is empty")

        if "chunks_fts" not in tables:
            errors.append("missing virtual table: chunks_fts")
    finally:
        conn.close()

    return errors


def check_manifest_engine_version(data_dir: Path, expected_version: str) -> list[str]:
    errors: list[str] = []
    manifest = load_manifest(data_dir)
    if manifest is None:
        errors.append(f"build_manifest.json missing in {data_dir}")
        return errors

    manifest_version = str(manifest.get("engineVersion") or "").strip()
    if not manifest_version:
        errors.append("build_manifest.json missing engineVersion")
        return errors

    if manifest_version != expected_version:
        errors.append(
            f"manifest engineVersion {manifest_version!r} != workspace engineVersion {expected_version!r}"
        )
    return errors


def validate_index(index: Path | None = None, *, strict_manifest: bool = True) -> dict[str, Any]:
    index_path = (index or resolve_index_path()).resolve()
    data_dir = index_path.parent
    cfg = load_workspace_config()
    expected_version = str(cfg.get("engineVersion") or "5.8").strip()

    schema_errors = check_schema(index_path)
    manifest_errors = check_manifest_engine_version(data_dir, expected_version) if strict_manifest else []

    report: dict[str, Any] = {
        "indexPath": str(index_path),
        "dataDir": str(data_dir),
        "expectedEngineVersion": expected_version,
        "schemaOk": not schema_errors,
        "manifestOk": not manifest_errors,
        "schemaErrors": schema_errors,
        "manifestErrors": manifest_errors,
        "pass": not schema_errors and (not strict_manifest or not manifest_errors),
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate RAG index schema and manifest.")
    parser.add_argument("--index", type=Path, default=None, help="Path to rag.sqlite (default: workspace indexPath)")
    parser.add_argument(
        "--skip-manifest",
        action="store_true",
        help="Only check sqlite schema; skip build_manifest engineVersion",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON report")
    args = parser.parse_args()

    report = validate_index(args.index, strict_manifest=not args.skip_manifest)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        status = "PASS" if report["pass"] else "FAIL"
        print(f"[{status}] index: {report['indexPath']}")
        print(f"  expected engineVersion: {report['expectedEngineVersion']}")
        for err in report["schemaErrors"]:
            print(f"  schema: {err}")
        for err in report["manifestErrors"]:
            print(f"  manifest: {err}")

    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
