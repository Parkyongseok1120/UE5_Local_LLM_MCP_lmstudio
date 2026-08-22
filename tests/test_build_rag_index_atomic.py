from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_rag_index  # noqa: E402


def _args(input_path: Path, out_dir: Path) -> argparse.Namespace:
    return argparse.Namespace(
        input=[str(input_path)],
        out_dir=str(out_dir),
        workspace_root=str(out_dir),
        chunk_tokens=900,
        overlap_tokens=120,
    )


def test_full_build_replaces_outputs_only_after_success(tmp_path: Path) -> None:
    input_path = tmp_path / "raw.jsonl"
    input_path.write_text(
        json.dumps(
            {
                "id": "doc-1",
                "source": "project_guideline",
                "title": "Rule",
                "text": "Keep module dependencies acyclic.",
                "metadata": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "index"
    out_dir.mkdir()
    (out_dir / "rag.sqlite").write_bytes(b"old-index")
    (out_dir / "chunks.jsonl").write_text("old-chunks\n", encoding="utf-8")

    build_rag_index.build(_args(input_path, out_dir))

    with sqlite3.connect(out_dir / "rag.sqlite") as conn:
        assert conn.execute("select count(*) from chunks").fetchone()[0] == 1
    assert "Keep module dependencies acyclic." in (
        out_dir / "chunks.jsonl"
    ).read_text(encoding="utf-8")
    assert not list(out_dir.glob("*.building.*"))


def test_full_build_preserves_existing_outputs_on_reader_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_path = tmp_path / "raw.jsonl"
    input_path.write_text("{}\n", encoding="utf-8")
    out_dir = tmp_path / "index"
    out_dir.mkdir()
    index_path = out_dir / "rag.sqlite"
    chunks_path = out_dir / "chunks.jsonl"
    index_path.write_bytes(b"old-index")
    chunks_path.write_text("old-chunks\n", encoding="utf-8")

    def _broken_reader(_paths: list[Path]):
        raise RuntimeError("reader failed")
        yield

    monkeypatch.setattr(build_rag_index, "read_jsonl", _broken_reader)

    with pytest.raises(RuntimeError, match="reader failed"):
        build_rag_index.build(_args(input_path, out_dir))

    assert index_path.read_bytes() == b"old-index"
    assert chunks_path.read_text(encoding="utf-8") == "old-chunks\n"
    assert not list(out_dir.glob("*.building.*"))


def test_full_build_rejects_empty_index_and_preserves_existing_outputs(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "raw.jsonl"
    input_path.write_text(
        json.dumps({"id": "empty", "source": "project_guideline", "text": ""}) + "\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "index"
    out_dir.mkdir()
    index_path = out_dir / "rag.sqlite"
    chunks_path = out_dir / "chunks.jsonl"
    index_path.write_bytes(b"old-index")
    chunks_path.write_text("old-chunks\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="zero searchable chunks"):
        build_rag_index.build(_args(input_path, out_dir))

    assert index_path.read_bytes() == b"old-index"
    assert chunks_path.read_text(encoding="utf-8") == "old-chunks\n"
    assert not list(out_dir.glob("*.building.*"))


def test_full_build_preserves_existing_index_when_promotion_is_locked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_path = tmp_path / "raw.jsonl"
    input_path.write_text(
        json.dumps(
            {
                "id": "doc-1",
                "source": "project_guideline",
                "text": "Validate before promotion.",
                "metadata": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "index"
    out_dir.mkdir()
    index_path = out_dir / "rag.sqlite"
    index_path.write_bytes(b"old-index")

    def _locked_replace(_source: Path, _target: Path) -> None:
        raise PermissionError("locked")

    monkeypatch.setattr(build_rag_index.os, "replace", _locked_replace)

    with pytest.raises(RuntimeError, match="existing index was left in place"):
        build_rag_index.build(_args(input_path, out_dir))

    assert index_path.read_bytes() == b"old-index"
    assert list(out_dir.glob("rag.building.*.sqlite"))


def test_malformed_jsonl_fails_closed_with_source_line_and_preserves_generation(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "raw-broken.jsonl"
    input_path.write_text(
        json.dumps(
            {
                "id": "valid-before-error",
                "source": "project_guideline",
                "text": "This staged row must never be promoted.",
            }
        )
        + "\n"
        + '{"id":"truncated"\n',
        encoding="utf-8",
    )
    out_dir = tmp_path / "index"
    out_dir.mkdir()
    protected = {
        "rag.sqlite": b"old-index",
        "chunks.jsonl": b"old-chunks\n",
        "build_manifest.json": b'{"generationId":"old"}\n',
    }
    for name, content in protected.items():
        (out_dir / name).write_bytes(content)

    with pytest.raises(
        build_rag_index.JsonlInputError,
        match=r"raw-broken\.jsonl:2:.*",
    ):
        build_rag_index.build(_args(input_path, out_dir))

    assert {name: (out_dir / name).read_bytes() for name in protected} == protected
    assert not list(out_dir.glob("*.building.*"))


def test_full_build_does_not_duplicate_export_graphs_into_every_chunk(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "raw-blueprints.jsonl"
    graph_nodes = [
        {"id": index, "payload": "x" * 4096}
        for index in range(400)
    ]
    input_path.write_text(
        json.dumps(
            {
                "id": "bp-large",
                "source": "unreal_blueprint_metadata",
                "title": "BP_Large",
                "text": "Blueprint searchable graph summary.",
                "metadata": {
                    "project": "Demo",
                    "project_root": str(tmp_path / "Demo"),
                    "asset_path": "/Game/BP_Large",
                    "asset_type": "Blueprint",
                    "graph_nodes": graph_nodes,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "index"

    build_rag_index.build(_args(input_path, out_dir))

    chunk = json.loads((out_dir / "chunks.jsonl").read_text(encoding="utf-8"))
    metadata = chunk["metadata"]
    assert metadata["project"] == "Demo"
    assert metadata["asset_path"] == "/Game/BP_Large"
    assert metadata["graph_nodes_count"] == 400
    assert "graph_nodes" not in metadata
    encoded = json.dumps(metadata, ensure_ascii=False).encode("utf-8")
    assert len(encoded) <= 8 * 1024
    assert (out_dir / "chunks.jsonl").stat().st_size < 32 * 1024
    with sqlite3.connect(out_dir / "rag.sqlite") as connection:
        stored = connection.execute("select metadata_json from chunks").fetchone()[0]
    assert len(stored.encode("utf-8")) <= 8 * 1024
    manifest = json.loads((out_dir / "build_manifest.json").read_text(encoding="utf-8"))
    assert manifest["chunkMetadataPolicy"] == {
        "version": 1,
        "maxBytes": 8 * 1024,
        "nestedValues": "count_only",
    }


def test_module_graph_input_is_rejected_instead_of_silently_laundered(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "raw.jsonl"
    rows = [
        {
            "id": "owner-row",
            "source": "module_graph",
            "title": "Engine/Public/Foo.h owner",
            "text": "Foo belongs to Engine.",
            "metadata": {
                "symbol_kind": "include_owner",
                "include_path": "Engine/Public/Foo.h",
            },
        },
        {
            "id": "searchable-row",
            "source": "project_guideline",
            "title": "Rule",
            "text": "Keep module dependencies acyclic.",
            "metadata": {},
        },
    ]
    input_path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    out_dir = tmp_path / "index"

    with pytest.raises(build_rag_index.JsonlInputError, match="module_graph"):
        build_rag_index.build(_args(input_path, out_dir))

    assert not (out_dir / "rag.sqlite").exists()
    assert not (out_dir / "chunks.jsonl").exists()
    assert not (out_dir / "build_manifest.json").exists()
