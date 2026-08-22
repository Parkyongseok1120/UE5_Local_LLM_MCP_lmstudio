from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


CLI_PATH_FILES = (
    "scripts/build_node_catalog.py",
    "scripts/build_rag_index.py",
    "scripts/build_unreal_module_graph.py",
    "scripts/build_project_graph.py",
    "scripts/collect_build_logs.py",
    "scripts/collect_editor_metadata.py",
    "scripts/collect_game_design_docs.py",
    "scripts/collect_project_architecture.py",
    "scripts/collect_project_guidelines.py",
    "scripts/collect_unreal_docs.py",
    "scripts/collect_unreal_project_profile.py",
    "scripts/collect_unreal_projects.py",
    "scripts/collect_unreal_source.py",
    "scripts/collect_unreal_symbols.py",
    "scripts/incremental_build.py",
    "scripts/ingest_editor_exports.py",
    "scripts/promote_staging_index.ps1",
    "scripts/rag_embeddings.py",
    "scripts/run_ubt_feedback_loop.ps1",
    "scripts/validate_index.py",
    "scripts/validate_project_sources.py",
    "scripts/verify_release.py",
    "rag.ps1",
)


@pytest.fixture
def workspace_510(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    workspace = tmp_path / "workspace"
    config_dir = workspace / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "workspace.json").write_text(
        json.dumps(
            {
                "engineVersion": "5.10",
                "indexNamespace": "unreal510",
                "indexPath": "data/unreal510/rag.sqlite",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("UNREAL58_ROOT", str(workspace))
    return workspace, workspace / "data" / "unreal510"


@pytest.mark.parametrize("relative", CLI_PATH_FILES)
def test_production_cli_defaults_do_not_pin_unreal58_data_paths(relative: str) -> None:
    source = (ROOT / relative).read_text(encoding="utf-8")

    assert "data/unreal58" not in source
    assert r"data\unreal58" not in source


def _parse_with_argv(monkeypatch: pytest.MonkeyPatch, module_name: str, argv: list[str]):
    module = importlib.import_module(module_name)
    monkeypatch.setattr(sys, "argv", [f"{module_name}.py", *argv])
    return module.parse_args()


@pytest.mark.parametrize(
    ("module_name", "argv", "attribute", "filename"),
    (
        ("collect_build_logs", [], "out", "raw_build_logs.jsonl"),
        ("collect_game_design_docs", [], "out", "raw_game_design.jsonl"),
        ("collect_project_guidelines", [], "out", "raw_guidelines.jsonl"),
        ("collect_unreal_project_profile", [], "out", "raw_project_profiles.jsonl"),
        ("collect_unreal_projects", ["--root", "projects"], "out", "raw_projects.jsonl"),
        ("collect_unreal_source", ["--root", "Engine/Source"], "out", "raw_source.jsonl"),
        ("collect_unreal_symbols", ["--root", "Engine/Source"], "out", "raw_symbols.jsonl"),
    ),
)
def test_cli_defaults_follow_configured_index_directory(
    workspace_510: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    argv: list[str],
    attribute: str,
    filename: str,
) -> None:
    _workspace, data_dir = workspace_510

    args = _parse_with_argv(monkeypatch, module_name, argv)

    assert Path(str(getattr(args, attribute))).resolve() == (data_dir / filename).resolve()


def test_build_and_docs_cli_defaults_follow_configured_engine(
    workspace_510: tuple[Path, Path],
) -> None:
    workspace, data_dir = workspace_510
    build_rag_index = importlib.import_module("build_rag_index")
    collect_unreal_docs = importlib.import_module("collect_unreal_docs")

    build_args = build_rag_index.parse_args(["--input", "raw.jsonl"])
    docs_args = collect_unreal_docs.parse_args([])

    assert Path(build_args.out_dir).resolve() == data_dir.resolve()
    assert Path(docs_args.out).resolve() == (data_dir / "raw_docs.jsonl").resolve()
    assert docs_args.version == "5.10"
    assert Path(docs_args.seeds).parent == workspace / "config"


def test_explicit_cli_paths_remain_unchanged(workspace_510: tuple[Path, Path]) -> None:
    build_rag_index = importlib.import_module("build_rag_index")
    collect_unreal_docs = importlib.import_module("collect_unreal_docs")

    build_args = build_rag_index.parse_args(
        ["--input", "raw.jsonl", "--out-dir", "custom/index"]
    )
    docs_args = collect_unreal_docs.parse_args(
        ["--version", "5.7", "--seeds", "custom/seeds.txt", "--out", "custom/docs.jsonl"]
    )

    assert build_args.out_dir == "custom/index"
    assert docs_args.version == "5.7"
    assert docs_args.seeds == "custom/seeds.txt"
    assert docs_args.out == "custom/docs.jsonl"


def test_validate_index_uses_the_active_engine_selection(
    workspace_510: tuple[Path, Path],
) -> None:
    validate_index = importlib.import_module("validate_index")

    report = validate_index.validate_index(Path("missing.sqlite"), strict_manifest=False)

    assert report["expectedEngineVersion"] == "5.10"
