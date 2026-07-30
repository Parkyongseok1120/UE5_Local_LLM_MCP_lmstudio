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


def test_replace_project_rolls_back_deletes_when_input_processing_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_path = tmp_path / "raw.jsonl"
    input_path.write_text(
        json.dumps(
            {
                "id": "project-a",
                "source": "unreal_project_text",
                "title": "Project A",
                "text": "Original project content.",
                "metadata": {"project": "ProjectA"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "index"
    build_rag_index.build(_args(input_path, out_dir))

    def _broken_reader(_paths: list[Path]):
        raise RuntimeError("reader failed")
        yield

    monkeypatch.setenv("ENABLE_REPLACE_PROJECT", "1")
    monkeypatch.setattr(build_rag_index, "read_jsonl", _broken_reader)
    args = _args(input_path, out_dir)
    args.replace_project = "ProjectA"

    with pytest.raises(RuntimeError, match="reader failed"):
        build_rag_index.build_replace_project(args)

    with sqlite3.connect(out_dir / "rag.sqlite") as conn:
        assert conn.execute(
            "select count(*) from chunks where project = ?",
            ("ProjectA",),
        ).fetchone()[0] == 1
