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
