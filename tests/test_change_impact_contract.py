from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from change_impact_contract import build_change_impact_contract  # noqa: E402


def test_impact_contract_separates_direct_source_and_candidate_calls(tmp_path: Path) -> None:
    public = tmp_path / "Source" / "Demo" / "Public"
    private = tmp_path / "Source" / "Demo" / "Private"
    tests = tmp_path / "Tests"
    public.mkdir(parents=True)
    private.mkdir(parents=True)
    tests.mkdir()
    (public / "Worker.h").write_text("class FWorker { public: void Run(); };\n", encoding="utf-8")
    (private / "Worker.cpp").write_text(
        '#include "Worker.h"\nvoid FWorker::Run() { Finish(); }\nvoid Finish() {}\n',
        encoding="utf-8",
    )
    (tests / "WorkerTests.cpp").write_text("void TestWorker() { FWorker Worker; }\n", encoding="utf-8")

    result = build_change_impact_contract(tmp_path, ["FWorker", "Finish"])

    assert result["ok"] is True
    direct_paths = {item["path"] for item in result["directImpacts"]}
    assert "Source/Demo/Public/Worker.h" in direct_paths
    assert "Source/Demo/Private/Worker.cpp" in direct_paths
    assert result["candidateImpacts"]
    assert result["candidateImpacts"][0]["confidence"] == "heuristic"
    targeted = next(item for item in result["regressionPlan"] if item["kind"] == "targeted_regression")
    assert targeted["candidates"][0]["path"] == "Tests/WorkerTests.cpp"
    assert result["rootCauseGuard"]["status"] == "hypothesis_only"


def test_impact_contract_marks_test_coverage_gap_and_unmatched_symbol(tmp_path: Path) -> None:
    source = tmp_path / "Source"
    source.mkdir()
    (source / "One.cpp").write_text("void Known() {}\n", encoding="utf-8")

    result = build_change_impact_contract(tmp_path, ["Known", "Missing"])

    assert result["ok"] is False
    assert result["unmatchedSymbols"] == ["Missing"]
    assert "not found" in result["issues"][0]
    targeted = next(item for item in result["regressionPlan"] if item["kind"] == "targeted_regression")
    assert targeted["status"] == "coverage_gap"


def test_impact_contract_requires_symbol_and_existing_project(tmp_path: Path) -> None:
    no_symbol = build_change_impact_contract(tmp_path, [])
    missing_project = build_change_impact_contract(tmp_path / "missing", ["Thing"])

    assert no_symbol["ok"] is False
    assert "target symbol" in no_symbol["issues"][0].lower()
    assert missing_project["ok"] is False


def test_impact_contract_does_not_mark_an_exact_limit_as_truncated(tmp_path: Path) -> None:
    source = tmp_path / "Source"
    source.mkdir()
    (source / "One.cpp").write_text("void Known() {}\n", encoding="utf-8")

    result = build_change_impact_contract(tmp_path, ["Known"], max_files=1)

    assert result["ok"] is True
    assert result["truncated"] is False
    assert len(result["directImpacts"]) == 1


def test_impact_contract_includes_project_plugin_source(tmp_path: Path) -> None:
    plugin_source = tmp_path / "Plugins" / "DemoPlugin" / "Source" / "DemoPlugin"
    plugin_source.mkdir(parents=True)
    (plugin_source / "PluginWorker.cpp").write_text("void PluginWorker() {}\n", encoding="utf-8")

    result = build_change_impact_contract(tmp_path, ["PluginWorker"])

    assert result["ok"] is True
    assert result["directImpacts"][0]["path"].startswith("Plugins/DemoPlugin/Source/")


def test_impact_contract_includes_config_redirects_and_requires_asset_validation(tmp_path: Path) -> None:
    source = tmp_path / "Source" / "Demo"
    config = tmp_path / "Config"
    source.mkdir(parents=True)
    config.mkdir()
    (source / "OldActor.h").write_text(
        "UCLASS()\nclass AOldActor : public AActor {};\n",
        encoding="utf-8",
    )
    (config / "DefaultEngine.ini").write_text(
        '+ActiveClassRedirects=(OldClassName="AOldActor",NewClassName="ANewActor")\n',
        encoding="utf-8",
    )

    result = build_change_impact_contract(tmp_path, ["AOldActor"])

    direct_paths = {item["path"] for item in result["directImpacts"]}
    assert "Config/DefaultEngine.ini" in direct_paths
    assert result["textSurfaceImpacts"][0]["kind"] == "config_reference"
    assert result["assetInspectionRequired"] is True
    assert result["assetCoverage"] == "editor_or_asset_registry_required"
    kinds = {item["kind"] for item in result["regressionPlan"]}
    assert "asset_registry_reference_scan" in kinds
    assert "blueprint_compile_or_load_validation" in kinds
