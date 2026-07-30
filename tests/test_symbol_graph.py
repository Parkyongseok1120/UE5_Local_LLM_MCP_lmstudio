from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_symbol_graph  # noqa: E402
import symbol_graph  # noqa: E402


def test_symbol_graph_extracts_basic_unreal_symbols(tmp_path):
    source = tmp_path / "Source"
    module = source / "Demo"
    public = module / "Public"
    private = module / "Private"
    public.mkdir(parents=True)
    private.mkdir(parents=True)
    (module / "Demo.Build.cs").write_text(
        'PublicDependencyModuleNames.AddRange(new string[] { "Core" });',
        encoding="utf-8",
    )
    (public / "DemoActor.h").write_text(
        """
#pragma once
#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "DemoActor.generated.h"

UCLASS()
class DEMO_API ADemoActor : public AActor
{
    GENERATED_BODY()
public:
    UFUNCTION()
    void Fire();
};
""",
        encoding="utf-8",
    )
    (private / "DemoActor.cpp").write_text(
        '#include "DemoActor.h"\nvoid ADemoActor::Fire() {}\n',
        encoding="utf-8",
    )

    graph = build_symbol_graph.build_symbol_graph(source)
    summary = build_symbol_graph.summarize_graph(graph)
    matches = symbol_graph.lookup_symbol("ADemoActor", graph)

    assert summary["totalSymbols"] >= 5
    assert summary["countsByModule"]["Demo"]["class"] == 1
    assert matches[0]["base_class"] == "AActor"
    assert matches[0]["api_macro"] == "DEMO_API"
    assert matches[0]["is_reflected"] is True
    assert matches[0]["owner_build_cs"].endswith("Demo.Build.cs")


def test_symbol_graph_v2_keeps_direct_and_heuristic_evidence_separate(tmp_path):
    source = tmp_path / "Source"
    source.mkdir()
    (source / "Worker.h").write_text(
        "class FBase {};\nclass FWorker : public FBase { public: void Run(); };\n",
        encoding="utf-8",
    )
    (source / "Worker.cpp").write_text(
        '#include "Worker.h"\nvoid FWorker::Run() { Finish(); }\nvoid Finish() {}\n',
        encoding="utf-8",
    )

    graph = build_symbol_graph.build_symbol_graph(source)
    edges = graph["edges"]

    assert graph["version"] == 2
    assert graph["evidenceContract"]["heuristicEdges"] == ["calls_candidate"]
    assert any(edge["kind"] == "includes" and edge["confidence"] == "direct" for edge in edges)
    assert any(edge["kind"] == "inherits" and edge["evidence"]["kind"] == "project_source" for edge in edges)
    call = next(edge for edge in edges if edge["kind"] == "calls_candidate")
    assert call["confidence"] == "heuristic"
    assert call["evidence"]["kind"] == "generated_metadata"
    assert "does not prove dispatch" in call["proofBoundary"]

    guarded = symbol_graph.graph_claim_evidence("FWorker", graph, claim_type="behavior")
    assert guarded["ok"] is False
    assert "BehaviorPath" in guarded["requiredNextEvidence"][0]


def test_symbol_graph_supports_non_unreal_source_inventory(tmp_path):
    source = tmp_path / "portable"
    source.mkdir()
    (source / "service.py").write_text(
        "from local_base import Base\n\nclass Worker(Base):\n    pass\n\ndef run():\n    return 1\n",
        encoding="utf-8",
    )

    graph = build_symbol_graph.build_symbol_graph(source)
    symbols = {(row["symbol_kind"], row["symbol_name"]) for row in graph["symbols"]}

    assert ("import", "local_base") in symbols
    assert ("class", "Worker") in symbols
    assert ("function", "run") in symbols
    assert any(edge["kind"] == "inherits" for edge in graph["edges"])


def test_symbol_graph_ignore_policy_is_relative_case_insensitive_and_imports_resolve(tmp_path):
    project = tmp_path / "Saved" / "Project"
    source = project / "src"
    ignored = project / "binaries"
    source.mkdir(parents=True)
    ignored.mkdir()
    (source / "base.py").write_text("class Base:\n    pass\n", encoding="utf-8")
    (source / "worker.py").write_text("from base import Base\nclass Worker(Base):\n    pass\n", encoding="utf-8")
    (ignored / "Generated.cpp").write_text("void Generated() {}\n", encoding="utf-8")

    graph = build_symbol_graph.build_symbol_graph(project)
    files = {Path(item["path"]).name for item in graph["files"]}

    assert files == {"base.py", "worker.py"}
    import_edge = next(edge for edge in graph["edges"] if edge["kind"] == "imports")
    assert import_edge["confidence"] == "direct"
    assert import_edge["to"].endswith("src/base.py")


def test_symbol_lookup_prioritizes_exact_qualified_names_and_graph_claims_fail_closed(tmp_path):
    graph = {
        "version": 2,
        "symbols": [
            {"id": f"partial:{index}", "symbol_name": f"RunHelper{index}", "qualified_name": ""}
            for index in range(25)
        ] + [
            {"id": "exact", "symbol_name": "Run", "qualified_name": "FWorker::Run", "file_path": "Worker.cpp", "line_start": 1}
        ],
        "edges": [],
    }

    assert symbol_graph.lookup_symbol("FWorker::Run", graph, limit=1)[0]["id"] == "exact"
    architecture_claim = symbol_graph.graph_claim_evidence("Run", graph, claim_type="architecture")
    assert architecture_claim["ok"] is False


def test_missing_symbol_graph_results_do_not_share_mutable_lists(tmp_path):
    first = symbol_graph.load_symbol_graph(tmp_path)
    first["symbols"].append({"symbol_name": "Leaked"})
    second = symbol_graph.load_symbol_graph(tmp_path)

    assert second["symbols"] == []
