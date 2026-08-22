#!/usr/bin/env python
"""Tests for the requiredReads snippet-injection helpers in wrapper_guards.py."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from wrapper_guards import (  # noqa: E402
    required_read_file_snippets,
    resolve_existing_relative_paths,
    restore_changed_paths,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_resolve_existing_relative_paths_keeps_only_real_files(tmp_path: Path) -> None:
    _write(tmp_path / "Source" / "Holdout" / "Public" / "Foo.h", "#pragma once\n")
    candidates = [
        "Source/Holdout/Public/Foo.h",
        "Source/Holdout/Private/DoesNotExist.cpp",
        "matching cpp definition",
        "",
        "../../etc/passwd",
    ]

    resolved = resolve_existing_relative_paths(tmp_path, candidates)

    assert resolved == ["Source/Holdout/Public/Foo.h"]


def test_resolve_existing_relative_paths_dedupes_and_preserves_order(tmp_path: Path) -> None:
    _write(tmp_path / "Source" / "Holdout" / "Public" / "Foo.h", "#pragma once\n")
    _write(tmp_path / "Source" / "Holdout" / "Private" / "Bar.cpp", "// bar\n")
    candidates = [
        "Source/Holdout/Public/Foo.h",
        "Source/Holdout/Private/Bar.cpp",
        "Source/Holdout/Public/Foo.h",
    ]

    resolved = resolve_existing_relative_paths(tmp_path, candidates)

    assert resolved == ["Source/Holdout/Public/Foo.h", "Source/Holdout/Private/Bar.cpp"]


def test_required_read_file_snippets_includes_full_content(tmp_path: Path) -> None:
    cpp_path = tmp_path / "Source" / "Holdout" / "Private" / "Registration.cpp"
    _write(
        cpp_path,
        '#include "Foo.h"\n\nusing FCallback = void (*)(int32, bool);\n',
    )

    block = required_read_file_snippets(tmp_path, ["Source/Holdout/Private/Registration.cpp"])

    assert "Requested file contents" in block
    assert "Source/Holdout/Private/Registration.cpp" in block
    assert 'using FCallback = void (*)(int32, bool);' in block


def test_required_read_file_snippets_truncates_large_files(tmp_path: Path) -> None:
    cpp_path = tmp_path / "Source" / "Holdout" / "Private" / "Big.cpp"
    _write(cpp_path, "x" * 5000)

    block = required_read_file_snippets(tmp_path, ["Source/Holdout/Private/Big.cpp"], max_chars_per_file=100)

    assert "(truncated)" in block
    assert len(block) < 5000


def test_required_read_file_snippets_caps_file_count(tmp_path: Path) -> None:
    paths = []
    for idx in range(5):
        rel = f"Source/Holdout/Private/File{idx}.cpp"
        _write(tmp_path / rel, f"// file {idx}\n")
        paths.append(rel)

    block = required_read_file_snippets(tmp_path, paths, max_files=2)

    assert block.count("## Source/Holdout/Private/File") == 2


def test_required_read_file_snippets_empty_when_no_paths(tmp_path: Path) -> None:
    assert required_read_file_snippets(tmp_path, []) == ""


def test_restore_changed_paths_reverts_modified_file(tmp_path: Path) -> None:
    rel = "Source/Holdout/Public/Foo.h"
    path = tmp_path / rel
    _write(path, "original\n")
    snapshot = {rel: "original\n"}
    path.write_text("corrupted\n", encoding="utf-8")

    restore_changed_paths(tmp_path, snapshot, [rel])

    assert path.read_text(encoding="utf-8") == "original\n"


def test_restore_changed_paths_deletes_file_not_in_snapshot(tmp_path: Path) -> None:
    rel = "Source/Holdout/Public/NewlyCreated.h"
    path = tmp_path / rel
    _write(path, "created by autofix\n")

    restore_changed_paths(tmp_path, {}, [rel])

    assert not path.exists()
