from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_stale_symbol_graph_ranking_sidecar_deferred_wording_absent():
    docs_text = "\n".join(path.read_text(encoding="utf-8", errors="replace") for path in (ROOT / "docs").rglob("*.md"))

    assert "symbol graph RAG ranking sidecar" not in docs_text


def test_live_holdout_baseline_docs_include_artifact_convention():
    text = (ROOT / "docs" / "Live_Eval_Checklist.md").read_text(encoding="utf-8")

    assert "Live Holdout Baseline Run" in text
    assert "data/baseline/live_holdout/" in text
    assert "--suite-type live-ubt" in text
    assert "Do not compare fixture-only results as if they were live UBT results" in text
