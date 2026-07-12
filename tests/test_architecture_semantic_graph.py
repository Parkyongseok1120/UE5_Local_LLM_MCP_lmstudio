from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from architecture_map import semantic_graph_v1  # noqa: E402


def test_semantic_graph_v1_shape() -> None:
    arch = {
        "modules": [{"name": "Demo", "path": "Source/Demo"}],
        "classes": [{"name": "ADemoCharacter", "module": "Demo"}],
        "subsystems": [],
        "dataAssets": [],
    }
    graph = semantic_graph_v1(arch)
    assert graph["version"] == 1
    assert isinstance(graph.get("nodes"), list)
