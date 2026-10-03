from __future__ import annotations
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import direct_rag_engine_local as local
import direct_rag_symbol as symbol
import engine_header_evidence as headers


@pytest.fixture
def source(tmp_path, monkeypatch):
    root = tmp_path / "UE"
    build = root / "Engine/Build/Build.version"
    build.parent.mkdir(parents=True)
    build.write_text(json.dumps({"MajorVersion": 5, "MinorVersion": 7, "PatchVersion": 4, "Changelist": 123}))
    (root / "Engine/Source/Runtime/Engine/Public").mkdir(parents=True)
    plugin = root / "Engine/Plugins/EnhancedInput/Source/EnhancedInput/Public"
    plugin.mkdir(parents=True)
    header = plugin / "EnhancedInputSubsystems.h"
    header.write_text("class UEnhancedInputLocalPlayerSubsystem {\npublic:\n void AddMappingContext(int value);\n};\n")
    project = tmp_path / "Game.uproject"
    project.write_text('{"EngineAssociation":"5.7"}')
    monkeypatch.setattr(local, "resolve_engine_root_for_association", lambda *a: {"ok": True, "engineRoot": str(root)})
    monkeypatch.setattr(symbol, "resolve_request_index", lambda *a, **k: pytest.fail("local requested an index"))
    headers.clear_engine_header_catalog_cache()
    yield SimpleNamespace(index=tmp_path / "absent.sqlite", workspace=tmp_path), root, project, header
    headers.clear_engine_header_catalog_cache()


def query(source, **extra):
    runtime, _, project, _ = source
    return symbol.symbol_lookup_capability(runtime, {"query": "UEnhancedInputLocalPlayerSubsystem::AddMappingContext",
        "project": str(project), "sourceMode": "engine_local", **extra}).payload


def test_local_plugin_works_without_index_and_returns_scoped_range_provenance(source):
    result = query(source)
    assert result["ok"]
    assert result["canonicalProject"] == str(source[2])
    assert result["observedEngineIdentity"]["version"] == "5.7.4"
    assert result["sourceVersion"].startswith("observed:")
    assert result["targetResolution"]["status"] == "resolved"
    assert "sha256" not in result
    row = result["matches"][0]
    assert row["observedOwner"] == "UEnhancedInputLocalPlayerSubsystem"
    assert row["fingerprint"]["sha256"] == hashlib.sha256(source[3].read_bytes()).hexdigest()
    assert row["fingerprint"]["hashScope"] == "file"
    assert result["coverage"]["apiAbsenceVerified"] is False


def test_local_never_borrows_signature_from_other_or_nested_class(source):
    source[3].write_text('class UEnhancedInputLocalPlayerSubsystem { class Nested { void AddMappingContext(); }; };\n'
                         'class Other { void AddMappingContext(); };\n'
                         '// class UEnhancedInputLocalPlayerSubsystem { void AddMappingContext(); };\n')
    assert query(source)["matchCount"] == 0


def test_changed_source_is_reread_and_partial_file_hash_is_not_full_sha(source):
    assert query(source)["matchCount"] == 1
    source[3].write_bytes(source[3].read_bytes() + b"\n" + b" " * 300000)
    row = query(source)["matches"][0]
    assert row["fingerprint"]["readBytes"] == 262144
    assert row["fingerprint"]["hashScope"] == "byte_range"
    assert row["fingerprint"]["completeFile"] is False
    source[3].write_text("class UEnhancedInputLocalPlayerSubsystem { void SomethingElse(); };\n")
    assert query(source)["matchCount"] == 0


def test_wrong_version_fails_before_header_reads(source, monkeypatch):
    (source[1] / "Engine/Build/Build.version").write_text('{"MajorVersion":5,"MinorVersion":8}')
    monkeypatch.setattr(local, "lookup_engine_header_evidence", lambda *a, **k: pytest.fail("mismatched source read"))
    result = query(source)
    assert result["errorCode"] == "ENGINE_SOURCE_VERSION_MISMATCH"
    assert result["sourceMode"] == "engine_local" and result["canonicalProject"] == str(source[2])


@pytest.mark.parametrize("body", ["int value = Other::AddMappingContext();", "friend void AddMappingContext();",
                                  "decltype(AddMappingContext()) value;", "static_assert(AddMappingContext());"])
def test_member_expression_or_friend_is_not_an_owner_declaration(source, body):
    source[3].write_text("class UEnhancedInputLocalPlayerSubsystem { " + body + " };\n")
    assert query(source)["matchCount"] == 0


def test_reflection_annotations_are_not_confused_with_member_expressions(source):
    source[3].write_text('class UEnhancedInputLocalPlayerSubsystem {\n'
                         'UFUNCTION(BlueprintCallable, Category="Input", meta=(Keywords="Mapping"))\n'
                         'void AddMappingContext(int value);\n};\n')
    assert query(source)["matchCount"] == 1
    enum = source[3].parent / "InputActionValue.h"
    enum.write_text('enum class EInputActionValueType { Boolean = 0, Axis1D UMETA(DisplayName="1D"), Axis2D UMETA(DisplayName="2D") };')
    headers.clear_engine_header_catalog_cache()
    assert query(source, query="EInputActionValueType::Axis2D")["matchCount"] == 1


@pytest.mark.parametrize("extra", [{"query": "find all APIs"}, {"owner": "Other"}, {"sourceMode": "anything"},
                                   {"query": "X", "owner": "../path"}])
def test_direct_capability_rejects_unsupported_queries_before_index(source, extra):
    assert query(source, **extra)["errorCode"] == "INVALID_TOOL_ARGUMENTS"


def test_deadline_and_catalog_limit_are_coverage_not_negative_api_proof(source, monkeypatch):
    times = iter([0.0, 10.0])
    monkeypatch.setattr(headers.time, "monotonic", lambda: next(times, 10.0))
    result = query(source)
    assert result["ok"] and result["matchCount"] == 0
    assert "deadline" in result["coverage"]["reasons"]
    assert not headers._LOCAL_CATALOGS


def test_transport_keeps_metadata_inside_actual_configured_envelope(source, monkeypatch):
    from direct_rag_result import to_mcp_tool_result
    monkeypatch.setenv("MCP_TOOL_RESULT_MAX_CHARS", "4000")
    result = symbol.symbol_lookup_capability(source[0], {"query": "UEnhancedInputLocalPlayerSubsystem::AddMappingContext",
          "project": str(source[2]), "sourceMode": "engine_local"})
    rendered = to_mcp_tool_result(result, tool_name="unreal_symbol_lookup")
    assert len(rendered["content"][0]["text"]) <= 4000
    assert rendered["structuredContent"]["sourceMode"] == "engine_local"


def test_lookup_started_before_clear_cannot_repopulate_later_module_catalogs(source, monkeypatch):
    original = headers._local_catalog
    cleared = False
    def clear_during_lookup(*args, **kwargs):
        nonlocal cleared
        if not cleared:
            cleared = True
            headers.clear_engine_header_catalog_cache()
        return original(*args, **kwargs)
    monkeypatch.setattr(headers, "_local_catalog", clear_during_lookup)
    assert query(source)["ok"]
    assert cleared and not headers._LOCAL_CATALOGS
