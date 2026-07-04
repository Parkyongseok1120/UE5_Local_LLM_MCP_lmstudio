from __future__ import annotations

import json
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


def test_readme_links_sonnet_gap_eval_docs():
    text = (ROOT / "README.md").read_text(encoding="utf-8", errors="replace")

    assert "[docs/Sonnet5_Gap_Plan.md](docs/Sonnet5_Gap_Plan.md)" in text
    assert "[docs/Eval_Metrics_Sonnet5_Gap.md](docs/Eval_Metrics_Sonnet5_Gap.md)" in text
    assert "[docs/Holdout_Eval_Guide.md](docs/Holdout_Eval_Guide.md)" in text
    assert "[docs/Live_Eval_Checklist.md](docs/Live_Eval_Checklist.md)" in text


def test_gitignore_ignores_local_live_holdout_config():
    text = (ROOT / ".gitignore").read_text(encoding="utf-8", errors="replace")

    assert "config/rag_eval_real_project_holdout_cases.local.json" in text


def test_local_holdout_example_is_public_safe_json():
    path = ROOT / "config" / "rag_eval_real_project_holdout_cases.local.example.json"
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)

    assert data["suite"] == "real-project-holdout-local-v0"
    assert len(data["cases"]) == 5
    assert "C:/Users/" not in text
    assert "C:\\Users\\" not in text
    assert "/Users/" not in text
    assert "/home/" not in text


def test_live_checklist_mentions_local_config_flow():
    text = (ROOT / "docs" / "Live_Eval_Checklist.md").read_text(encoding="utf-8")

    assert "config/rag_eval_real_project_holdout_cases.local.json" in text
    assert "real-project-holdout-local-v0" in text
    assert "Start with 5 local cases" in text
