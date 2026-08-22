# Archived architecture semantic-graph test.
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from architecture_map import semantic_graph_v1  # noqa: E402


def test_semantic_graph_v1_shape() -> None:
    arch = {
        "modules": [{"name": "Demo", "path": "Source/Demo"}],
        "classes": [
            {
                "name": "ADemoCharacter",
                "module": "Demo",
                "path": "Source/Demo/Public/DemoCharacter.h",
                "baseClass": "ACharacter",
            }
        ],
        "subsystems": [],
        "dataAssets": [],
    }
    graph = semantic_graph_v1(arch)
    assert graph["version"] == 1
    assert {node["id"] for node in graph["nodes"]} == {
        "Demo::ADemoCharacter",
        "external::ACharacter",
    }
    assert graph["edges"] == [
        {
            "from": "Demo::ADemoCharacter",
            "to": "external::ACharacter",
            "kind": "INHERITS",
            "confidence": "inferred",
        }
    ]


def test_semantic_graph_v1_accepts_canonical_types_and_resolves_local_base() -> None:
    graph = semantic_graph_v1(
        {
            "types": [
                {"name": "UBase", "module": "Demo", "header": "Source/Demo/Public/Base.h"},
                {
                    "name": "UDerived",
                    "module": "Demo",
                    "header": "Source/Demo/Public/Derived.h",
                    "baseClass": "UBase",
                },
            ]
        }
    )
    assert {
        (edge["from"], edge["to"], edge["kind"])
        for edge in graph["edges"]
    } == {("Demo::UDerived", "Demo::UBase", "INHERITS")}
