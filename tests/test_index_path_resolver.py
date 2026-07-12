from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from workspace_paths import find_workspace_root  # noqa: E402


def test_workspace_index_path_not_hardcoded_unreal58_only() -> None:
    cfg_path = find_workspace_root() / "config" / "workspace.json"
    if not cfg_path.is_file():
        return
    cfg = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
    index_path = str(cfg.get("indexPath") or "")
    namespace = str(cfg.get("indexNamespace") or "")
    assert index_path or namespace
    if namespace:
        assert namespace in index_path or f"data/{namespace}/" in index_path.replace("\\", "/")
