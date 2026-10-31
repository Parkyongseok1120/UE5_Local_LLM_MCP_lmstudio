from __future__ import annotations

import argparse
import copy
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from architecture_pattern_catalog import collected_catalog_current, content_hash, load_pattern_catalog, pattern_rows
from collect_project_guidelines import collect
from build_rag_index import build
from direct_rag_corpus import corpus_capabilities
from direct_rag_evidence import compact_match_refs
from direct_rag_generation_identity import RagGenerationTransitionError
from direct_rag_lexical import lexical_search
from rag_types import SearchOptions

EXPORT = ROOT / "docs/model-guidance/generated-pattern-catalog.json"


def collect_args(tmp_path, export=EXPORT):
    root = tmp_path / "guidelines"
    root.mkdir(exist_ok=True)
    (root / "rule.md").write_text("# Rule\nKeep existing owners when suitable.\n", encoding="utf-8")
    return argparse.Namespace(root=str(root), out=str(tmp_path / "raw_guidelines.jsonl"),
                              pattern_export=str(export), min_chars=1, max_bytes=250000)


def build_args(raw, output):
    return argparse.Namespace(input=[str(raw)], out_dir=str(output), workspace_root=str(output),
                              chunk_tokens=120, overlap_tokens=10, engine_version="5.7.4")


def indexed(tmp_path):
    args = collect_args(tmp_path)
    collect(args)
    output = tmp_path / "index"
    build(build_args(Path(args.out), output))
    return output / "rag.sqlite"


def test_R01_guidelines_and_canonical_export_share_one_raw_input(tmp_path):
    args = collect_args(tmp_path)
    collect(args)
    rows = [json.loads(line) for line in Path(args.out).read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 17
    assert len({row["id"] for row in rows}) == 17
    assert rows[0]["title"] == "Rule"


def test_R02_identical_export_collection_is_stable(tmp_path):
    args = collect_args(tmp_path)
    collect(args)
    first = Path(args.out).read_bytes()
    collect(args)
    assert Path(args.out).read_bytes() == first
    assert collected_catalog_current(Path(args.out), EXPORT)


def test_R03_changed_export_invalidates_raw_cache(tmp_path):
    args = collect_args(tmp_path)
    collect(args)
    changed = copy.deepcopy(load_pattern_catalog(EXPORT))
    item = changed["patterns"][0]
    item["text"] += "\nNew conceptual constraint."
    item["contentHash"] = content_hash({key: value for key, value in item.items() if key != "contentHash"})
    changed["catalogHash"] = content_hash({"schemaVersion": 1, "patterns": [
        {"patternId": item["patternId"], "contentHash": item["contentHash"]} for item in changed["patterns"]]})
    export = tmp_path / "changed.json"
    export.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
    assert not collected_catalog_current(Path(args.out), export)
    args.pattern_export = str(export)
    collect(args)
    assert collected_catalog_current(Path(args.out), export)


def test_R04_export_build_query_and_compact_refs_preserve_pattern_versions(tmp_path):
    index = indexed(tmp_path)
    rows = lexical_search(index, "Data Asset", 6, SearchOptions(mode="design", doc_types=["architecture_pattern"]))
    refs = compact_match_refs(rows)
    assert refs and all(len(row["contentHash"]) == 64 for row in refs)
    assert all(row.get("patternId") and row.get("catalogHash") for row in refs)
    coverage = corpus_capabilities(index)
    assert coverage["architecturePatterns"]["count"] == 16
    assert not coverage["engineEvidence"] and not coverage["projectEvidence"]


def test_R05_small_ref_budget_omits_whole_pattern_identity():
    row = {"source": "project_guideline", "metadata": pattern_rows(EXPORT)[0]["metadata"], "chunk_id": "one"}
    assert compact_match_refs([row], max_chars=10) == []
    ref = compact_match_refs([row], max_chars=8000)[0]
    assert ref["contentHash"] == row["metadata"]["contentHash"]


def test_R06_manifest_reader_rejects_other_generation(tmp_path):
    index = indexed(tmp_path)
    with pytest.raises(RagGenerationTransitionError):
        corpus_capabilities(index, expected_generation="another-generation")


def test_R07_old_manifest_has_unknown_pattern_coverage(tmp_path, monkeypatch):
    import direct_rag_corpus
    monkeypatch.setattr(direct_rag_corpus, "read_consistent_index_manifest", lambda *a, **k: {
        "corpusCapabilities": {"engineEvidence": True, "projectEvidence": False}})
    assert "architecturePatterns" not in corpus_capabilities(tmp_path / "old.sqlite")


def test_R08_engine_unknown_binding_remains_rejected(tmp_path):
    from direct_rag_request_binding import resolve_request_engine_binding
    result = resolve_request_engine_binding([tmp_path / "Game.uproject"], tmp_path,
        lambda *a: {"ok": True, "engineVersionStatus": "unknown", "engineVersion": "5.7.4"})
    assert result["errorCode"] == "PROJECT_ENGINE_VERSION_UNRESOLVED"


def test_R09_patterns_are_knowledge_not_project_structure():
    from rag_build_classification import infer_doc_type
    for row in pattern_rows(EXPORT):
        assert infer_doc_type(row["source"], row["metadata"]) == "architecture_pattern"
        assert not row["metadata"].get("project_root")


def test_R10_incomplete_catalog_cannot_publish_as_complete(tmp_path):
    raw = tmp_path / "raw_guidelines.jsonl"
    raw.write_text(json.dumps(pattern_rows(EXPORT)[0], ensure_ascii=False) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Incomplete"):
        build(build_args(raw, tmp_path / "index"))
    assert not (tmp_path / "index" / "rag.sqlite").exists()


def test_R11_existing_direct_tool_names_and_modes_are_reused():
    from rag_modes import MODE_ENUM
    manifest = json.loads((ROOT / "config/stable_tool_manifest.json").read_text(encoding="utf-8"))
    assert "design" in MODE_ENUM and "architecture" not in MODE_ENUM
    assert "unreal_rag_search" in json.dumps(manifest)
    assert "unreal_architecture_search" not in json.dumps(manifest)


def test_R12_known_zero_and_positive_corpus_are_distinct(tmp_path, monkeypatch):
    import direct_rag_corpus
    for count in (0, 16):
        monkeypatch.setattr(direct_rag_corpus, "read_consistent_index_manifest", lambda *a, **k: {
            "corpusCapabilities": {"architecturePatterns": {"schemaVersion": 1, "count": count}}})
        assert corpus_capabilities(tmp_path / "index")["architecturePatterns"]["count"] == count


def test_R13_export_only_and_failed_collection_preserve_prior_raw(tmp_path):
    args = collect_args(tmp_path)
    args.root = str(tmp_path / "missing")
    collect(args)
    original = Path(args.out).read_bytes()
    assert len(original.splitlines()) == 16
    broken = tmp_path / "broken.json"
    broken.write_text('{"schemaVersion":99}', encoding="utf-8")
    args.pattern_export = str(broken)
    with pytest.raises(ValueError):
        collect(args)
    assert Path(args.out).read_bytes() == original


def test_pattern_identity_survives_the_real_direct_search_and_mcp_serializer(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import direct_rag_search
    from direct_rag_result import to_mcp_tool_result
    index = indexed(tmp_path)
    monkeypatch.setattr(direct_rag_search, "resolve_active_project_path", lambda *a: None)
    result = direct_rag_search.rag_search(SimpleNamespace(index=index, workspace=tmp_path), {
        "query": "Data Asset", "mode": "design", "doc_type": ["architecture_pattern"], "scope": "auto",
        "use_active_project": False, "top_k": 2, "detailLevel": "standard", "maxChars": 16000,
    })
    payload = to_mcp_tool_result(result, tool_name="unreal_rag_search")["structuredContent"]
    assert payload["ok"], payload
    assert payload["architecturePatternCorpus"]["status"] == "matched"
    assert payload["indexGenerationId"]
    assert payload["matches"] and all(len(row["contentHash"]) == 64 for row in payload["matches"])
    missing = direct_rag_search.rag_search(SimpleNamespace(index=index, workspace=tmp_path), {
        "query": "NonexistentUniquePatternTerm", "mode": "design", "doc_type": ["architecture_pattern"],
        "use_active_project": False, "detailLevel": "standard", "maxChars": 16000,
    })
    assert to_mcp_tool_result(missing, tool_name="unreal_rag_search")["structuredContent"]["architecturePatternCorpus"]["status"] == "no_match"


def test_mixed_doc_type_status_counts_pattern_hits_not_any_match(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import direct_rag_search
    from direct_rag_evidence import pattern_search_metadata
    from direct_rag_result import to_mcp_tool_result
    args = collect_args(tmp_path)
    (Path(args.root) / "unique.md").write_text("# Unique\nQuarkfluxion guideline only.\n", encoding="utf-8")
    collect(args)
    output = tmp_path / "index"
    build(build_args(Path(args.out), output))
    index = output / "rag.sqlite"
    monkeypatch.setattr(direct_rag_search, "resolve_active_project_path", lambda *a: None)
    result = direct_rag_search.rag_search(SimpleNamespace(index=index, workspace=tmp_path), {
        "query": "Quarkfluxion", "mode": "design", "doc_type": ["architecture_pattern", "guideline"],
        "use_active_project": False, "detailLevel": "standard", "maxChars": 16000,
    })
    payload = to_mcp_tool_result(result, tool_name="unreal_rag_search")["structuredContent"]
    assert payload["ok"], payload
    assert payload["matchCount"] >= 1 and not any(row.get("patternId") for row in payload["matches"])
    corpus = payload["architecturePatternCorpus"]
    assert corpus["status"] == "no_match"
    assert corpus["patternHitCount"] == 0 and corpus["patternIdentityCount"] == 0
    # Pattern hits whose refs were clipped by the transport budget still count as matched.
    pattern_row = {"metadata": json.dumps({"guidanceKind": "architecture_pattern", "patternId": "ue.x"}), "source": "guideline"}
    generation = None
    clipped = pattern_search_metadata(index, generation, requested=True, matches=[], rows=[pattern_row])
    assert clipped["architecturePatternCorpus"]["status"] == "matched"
    assert clipped["architecturePatternCorpus"]["patternIdentityCount"] == 0
