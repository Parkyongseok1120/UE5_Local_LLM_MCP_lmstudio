from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from verify_release import index_release_health, run_command_check, verify_tool_manifest  # noqa: E402


def test_release_index_health_requires_nonempty_integrity_checked_index(tmp_path: Path):
    index = tmp_path / "rag.sqlite"
    with sqlite3.connect(index) as connection:
        connection.execute("create table chunks(id integer primary key, text text)")
    assert index_release_health(index)["ok"] is False

    with sqlite3.connect(index) as connection:
        connection.execute("insert into chunks(text) values ('evidence')")
    health = index_release_health(index)
    assert health["ok"] is True
    assert health["quickCheck"] == "ok"
    assert health["chunkCount"] == 1


def test_release_command_check_has_timeout_and_execution_evidence():
    row = run_command_check(
        "smoke",
        [sys.executable, "-c", "print('ok')"],
        timeout_sec=10,
    )
    assert row["pass"] is True
    assert row["proofLevel"] == "executed"
    assert row["durationMs"] >= 0


def test_release_tool_manifest_matches_runtime_contract():
    result = verify_tool_manifest()
    assert result["ok"] is True, result.get("issues")
    assert result["essentialToolCount"] == 8


def test_release_query_probe_uses_only_the_direct_retrieval_path() -> None:
    source = (ROOT / "scripts" / "verify_release.py").read_text(encoding="utf-8")
    probe = (ROOT / "scripts" / "direct_rag_probe.py").read_text(encoding="utf-8")

    assert "direct_rag_probe.py" in source
    assert "evaluate_rag_queries.py" not in source
    assert "from direct_rag_search import rag_search" in probe
    assert "from rag_search import" not in probe
    assert "\nimport rag_search" not in probe
