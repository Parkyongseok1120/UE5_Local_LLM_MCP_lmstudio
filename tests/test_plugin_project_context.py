#!/usr/bin/env python
from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from plugin_project_context import (  # noqa: E402
    build_plugin_project_context,
    fallback_scan_roots,
    iter_scan_root_files,
    FLAT_FIXTURE_SKIP_DIRS,
    validate_uplugin_descriptor,
)
from plan_slice_state import mark_slice_complete, init_slice_state  # noqa: E402
from read_query_history import (  # noqa: E402
    query_fingerprint,
    record_query_delivery,
    reset_query_history,
    reset_query_history_for_index,
)


def _fixture_tree(tmp_path: Path) -> Path:
    root = tmp_path / "DemoGame"
    plugin = root / "Plugins" / "DemoPlugin"
    (plugin / "Source" / "DemoPlugin" / "Private").mkdir(parents=True)
    (plugin / "Source" / "DemoPlugin" / "Public").mkdir(parents=True)
    (plugin / "Source" / "DemoPlugin" / "Private" / "DemoPlugin.cpp").write_text(
        "#include \"DemoPlugin/Public/DemoPluginModule.h\"\n",
        encoding="utf-8",
    )
    (plugin / "DemoPlugin.uplugin").write_text(
        json.dumps(
            {
                "FileVersion": 3,
                "FriendlyName": "DemoPlugin",
                "Installed": False,
                "Modules": [{"Name": "DemoPlugin", "Type": "Runtime"}],
            }
        ),
        encoding="utf-8",
    )
    (root / "Source" / "DemoGame").mkdir(parents=True)
    (root / "DemoGame.uproject").write_text(
        json.dumps({"Modules": [{"Name": "DemoGame", "Type": "Runtime"}]}),
        encoding="utf-8",
    )
    return root


def test_plugin_context_discovers_local_plugin_module(tmp_path: Path):
    root = _fixture_tree(tmp_path)
    ctx = build_plugin_project_context(root)
    names = {module.name for module in ctx.modules}
    assert "DemoPlugin" in names
    assert "DemoGame" in names
    scan_roots = ctx.scan_roots()
    assert any("Plugins" in str(path) for path in scan_roots)


def test_uplugin_duplicate_module_detected(tmp_path: Path):
    path = tmp_path / "Bad.uplugin"
    path.write_text(
        json.dumps({"Modules": [{"Name": "A", "Type": "Runtime"}, {"Name": "A", "Type": "Editor"}]}),
        encoding="utf-8",
    )
    findings = validate_uplugin_descriptor(path)
    assert any(item.get("code") == "UPLUGIN_MODULE_DUPLICATE" for item in findings)


def test_slice_complete_rejects_empty_writes(tmp_path: Path):
    state = init_slice_state([{"slice_id": "s1", "title": "t"}])
    updated = mark_slice_complete(
        state,
        project_root=tmp_path,
        written_paths=[],
        plan_slices=[{"slice_id": "s1"}],
    )
    assert updated["slices"][0]["status"] == "failed"


def test_query_history_index_reset(tmp_path: Path):
    reset_query_history()
    index_path = tmp_path / "rag.sqlite"
    index_path.write_bytes(b"abc")
    fp = query_fingerprint(
        tool="unreal_rag_search",
        active_project="Demo",
        query="test",
        mode="auto",
        scope="auto",
        detail_level="compact",
        top_k=6,
        hybrid=False,
        index_path=index_path,
    )
    record_query_delivery(fp, detail_level="compact", match_count=1, index_path=index_path)
    dropped = reset_query_history_for_index(index_path)
    assert dropped == 1


def test_orphan_plugin_source_tree_is_scanned(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "Plugins" / "HoldoutPlugin" / "Source" / "HoldoutPlugin" / "Public"
    plugin_dir.mkdir(parents=True)
    header = plugin_dir / "HoldoutPluginComponent.h"
    header.write_text("#pragma once\nclass UHoldoutPluginComponent {};\n", encoding="utf-8")

    ctx = build_plugin_project_context(tmp_path)
    roots = ctx.scan_roots()
    assert any("Plugins" in str(path) for path in roots)
    files: list[Path] = []
    for scan_root in roots:
        files.extend(iter_scan_root_files(scan_root, skip_dirs=FLAT_FIXTURE_SKIP_DIRS))
    assert any(path.name == "HoldoutPluginComponent.h" for path in files)


def test_fallback_scan_roots_avoids_repo_wide_scan(tmp_path: Path):
    repo_like = tmp_path / "repo"
    repo_like.mkdir()
    (repo_like / "README.md").write_text("docs", encoding="utf-8")
    nested = repo_like / "tests" / "nested"
    nested.mkdir(parents=True)
    (nested / "Example.cpp").write_text("void X(){}", encoding="utf-8")

    assert fallback_scan_roots(repo_like) == []
    assert iter_scan_root_files(repo_like) == []


def test_flat_fixture_root_scan_is_shallow(tmp_path: Path):
    fixture = tmp_path / "flat_case"
    fixture.mkdir()
    (fixture / "Holdout.cpp").write_text("void Holdout(){}", encoding="utf-8")
    deep = fixture / "nested" / "deep"
    deep.mkdir(parents=True)
    (deep / "Hidden.cpp").write_text("void Hidden(){}", encoding="utf-8")

    roots = fallback_scan_roots(fixture)
    assert roots == [fixture]
    files = iter_scan_root_files(fixture)
    assert any(path.name == "Holdout.cpp" for path in files)
    assert not any(path.name == "Hidden.cpp" for path in files)
