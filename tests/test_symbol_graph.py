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


def test_symbol_graph_qualifies_member_declarations_inside_header_class(tmp_path):
    source = tmp_path / "Source"
    source.mkdir()
    (source / "GomokuGameState.h").write_text(
        """
class AGomokuGameState : public AGameStateBase
{
public:
    void AdvanceTurn();
    bool IsGameOver() const;
};
""",
        encoding="utf-8",
    )

    graph = build_symbol_graph.build_symbol_graph(source)
    functions = {
        (row["symbol_name"], row["qualified_name"])
        for row in graph["symbols"]
        if row["symbol_kind"] == "function"
    }

    assert ("AdvanceTurn", "AGomokuGameState::AdvanceTurn") in functions
    assert ("IsGameOver", "AGomokuGameState::IsGameOver") in functions


def test_symbol_graph_parses_next_line_qualified_definition_without_promoting_calls(
    tmp_path,
):
    source = tmp_path / "Source"
    source.mkdir()
    (source / "Worker.cpp").write_text(
        """
void FWorker::Run()
{
    return Finish();
}

void FWorker::Finish()
{
}
""",
        encoding="utf-8",
    )

    graph = build_symbol_graph.build_symbol_graph(source)
    functions = [
        (row["symbol_name"], row["qualified_name"])
        for row in graph["symbols"]
        if row["symbol_kind"] == "function"
    ]

    assert ("Run", "FWorker::Run") in functions
    assert ("Finish", "FWorker::Finish") in functions
    assert functions.count(("Finish", "FWorker::Finish")) == 1


def test_symbol_graph_does_not_promote_control_flow_statements_to_functions(tmp_path):
    source = tmp_path / "Source"
    source.mkdir()
    (source / "Worker.cpp").write_text(
        """
void FWorker::Run()
{
    if (bReady)
        Start();
    else Finish();
    do Poll(); while (bWaiting);
    co_await Resume();
}
""",
        encoding="utf-8",
    )

    graph = build_symbol_graph.build_symbol_graph(source)
    functions = {
        row["symbol_name"]
        for row in graph["symbols"]
        if row["symbol_kind"] == "function"
    }

    assert "Run" in functions
    assert functions.isdisjoint({"Finish", "Poll", "Resume"})


def test_symbol_graph_captures_override_and_qualified_constructor_destructor(
    tmp_path,
):
    source = tmp_path / "Source"
    source.mkdir()
    (source / "Worker.h").write_text(
        """
class FWorker
{
public:
    virtual void BeginPlay() override;
};
""",
        encoding="utf-8",
    )
    (source / "Worker.cpp").write_text(
        """
FWorker::FWorker()
    : Value(0)
{
}

FWorker::~FWorker()
{
    ClearTimer();
}
""",
        encoding="utf-8",
    )

    graph = build_symbol_graph.build_symbol_graph(source)
    functions = {
        (row["symbol_name"], row["qualified_name"])
        for row in graph["symbols"]
        if row["symbol_kind"] == "function"
    }

    assert ("BeginPlay", "FWorker::BeginPlay") in functions
    assert ("FWorker", "FWorker::FWorker") in functions
    assert ("~FWorker", "FWorker::~FWorker") in functions


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


def test_owner_build_cs_path_matching_uses_host_filesystem_rules() -> None:
    graph = {
        "symbols": [
            {
                "file_path": "Source/Demo/Worker.cpp",
                "owner_build_cs": "Source/Demo/Demo.Build.cs",
            }
        ]
    }

    assert (
        symbol_graph.owner_build_cs_for_file(
            "source/demo/worker.cpp",
            graph,
            host_platform="linux",
        )
        == ""
    )
    assert symbol_graph.owner_build_cs_for_file(
        "source/demo/worker.cpp",
        graph,
        host_platform="win32",
    ).endswith("Demo.Build.cs")


def test_owner_build_cs_rejects_unicode_casefold_path_alias() -> None:
    composed = "Source/Demo/\u0130mplementation.cpp"
    decomposed = "Source/Demo/I\u0307mplementation.cpp"
    assert composed.casefold() == decomposed.casefold()
    graph = {
        "symbols": [
            {
                "file_path": decomposed,
                "owner_build_cs": "Source/Alias/Alias.Build.cs",
            }
        ]
    }

    for host_platform in ("linux", "win32"):
        assert (
            symbol_graph.owner_build_cs_for_file(
                composed,
                graph,
                host_platform=host_platform,
            )
            == ""
        )


def test_symbol_graph_path_keys_and_root_signatures_use_host_rules(
    tmp_path: Path,
) -> None:
    upper = tmp_path / "UpperSource"
    lower = tmp_path / "uppersource"
    assert build_symbol_graph._path_key(
        upper,
        host_platform="linux",
    ) != build_symbol_graph._path_key(lower, host_platform="linux")
    assert build_symbol_graph._path_key(
        upper,
        host_platform="win32",
    ) == build_symbol_graph._path_key(lower, host_platform="win32")
    assert build_symbol_graph.source_inventory_signature(
        upper,
        host_platform="linux",
    ) != build_symbol_graph.source_inventory_signature(
        lower,
        host_platform="linux",
    )
    assert build_symbol_graph.source_inventory_signature(
        upper,
        host_platform="win32",
    ) == build_symbol_graph.source_inventory_signature(
        lower,
        host_platform="win32",
    )


def test_symbol_graph_root_identity_preserves_unicode_spelling(tmp_path: Path) -> None:
    composed = tmp_path / "\u0130Source"
    decomposed = tmp_path / "I\u0307Source"
    assert str(composed).casefold() == str(decomposed).casefold()

    for host_platform in ("linux", "win32"):
        assert build_symbol_graph._path_key(
            composed,
            host_platform=host_platform,
        ) != build_symbol_graph._path_key(
            decomposed,
            host_platform=host_platform,
        )
        assert build_symbol_graph.source_inventory_signature(
            composed,
            host_platform=host_platform,
        ) != build_symbol_graph.source_inventory_signature(
            decomposed,
            host_platform=host_platform,
        )


def test_symbol_graph_path_keys_do_not_use_unicode_normcase(tmp_path: Path) -> None:
    upper = tmp_path / "\u00c4Source"
    lower = tmp_path / "\u00e4Source"
    assert str(upper).lower() == str(lower).lower()

    for host_platform in ("linux", "win32"):
        assert build_symbol_graph._path_key(
            upper,
            host_platform=host_platform,
        ) != build_symbol_graph._path_key(
            lower,
            host_platform=host_platform,
        )
        assert build_symbol_graph.source_inventory_signature(
            upper,
            host_platform=host_platform,
        ) != build_symbol_graph.source_inventory_signature(
            lower,
            host_platform=host_platform,
        )
