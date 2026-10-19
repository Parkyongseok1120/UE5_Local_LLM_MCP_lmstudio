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


# The default text search covers Runtime only; the header lookup also reads Editor and Plugins.
RUNTIME_DEFAULT_EXCLUDES = ["Engine/Source/Editor", "Engine/Source/Developer", "Engine/Source/Programs", "Engine/Plugins"]


def _wrong_owner_module(source, *, build_cs=True):
    module = source[3].parent.parent
    if build_cs:
        (module / "EnhancedInput.Build.cs").write_text("// module rules\n")
    (module / "Public/EnhancedInputUserSettings.h").write_text(
        "class UEnhancedInputUserSettings {\npublic:\n void ResolveOperation(int value);\n};\n")
    headers.clear_engine_header_catalog_cache()
    return module


def test_wrong_owner_zero_suggests_unqualified_token_search_in_the_observed_module(source):
    _wrong_owner_module(source)
    result = query(source, query="UEnhancedInputLocalPlayerSubsystem::ResolveOperation")
    assert result["ok"] and result["matchCount"] == 0 and "declaredIn" not in json.dumps(result["matches"])
    assert result["coverage"]["apiAbsenceVerified"] is False
    assert result["coverage"]["observedTypeCandidates"] == [{
        "typeName": "UEnhancedInputLocalPlayerSubsystem",
        "headerUri": "engine://Engine/Plugins/EnhancedInput/Source/EnhancedInput/Public/EnhancedInputSubsystems.h",
        "moduleUri": "engine://Engine/Plugins/EnhancedInput/Source/EnhancedInput",
        "meaning": "type_observed_member_not_found"}]
    # The failed owner is not repeated; the observed module is searched for the bare token.
    assert result["suggestion"] == {"tool": "search_files", "scope": "observed_owner_module", "args": {
        "path": "engine://Engine/Plugins/EnhancedInput/Source/EnhancedInput",
        "query": r"\bResolveOperation\b", "regex": True}}


def test_unobserved_owner_or_module_keeps_the_engine_runtime_scope_without_inventing_one(source):
    _wrong_owner_module(source, build_cs=False)
    result = query(source, query="UEnhancedInputLocalPlayerSubsystem::ResolveOperation")
    [observed] = result["coverage"]["observedTypeCandidates"]
    assert "moduleUri" not in observed and observed["headerUri"].endswith("/EnhancedInputSubsystems.h")
    assert result["suggestion"] == {"tool": "search_files", "scope": "engine_runtime_default_module_unknown",
        "scopeLimit": {"excludes": RUNTIME_DEFAULT_EXCLUDES}, "args": {
        "path": "engine://Engine/Source/Runtime", "query": r"\bResolveOperation\b", "regex": True, "maxFiles": 50000}}
    missing = query(source, query="UNoSuchOwner::ResolveOperation")
    assert "observedTypeCandidates" not in missing["coverage"]
    assert missing["suggestion"]["scope"] == "engine_runtime_default_module_unknown"
    bare = query(source, query="ResolveOperation")
    assert bare["matchCount"] == 0 and "observedTypeCandidates" not in bare["coverage"]
    assert bare["suggestion"]["args"]["query"] == r"\bResolveOperation\b"


def test_editor_module_owner_is_observed_with_its_own_module_uri(source):
    module = source[1] / "Engine/Source/Editor/UnrealEd"
    (module / "Public").mkdir(parents=True)
    (module / "UnrealEd.Build.cs").write_text("// rules\n")
    (module / "Public/EdOwner.h").write_text("class UEdOwner {\npublic:\n void Other();\n};\n")
    headers.clear_engine_header_catalog_cache()
    result = query(source, query="UEdOwner::ResolveOperation")
    [observed] = result["coverage"]["observedTypeCandidates"]
    assert observed["moduleUri"] == "engine://Engine/Source/Editor/UnrealEd"
    assert result["suggestion"]["args"]["path"] == "engine://Engine/Source/Editor/UnrealEd"
    assert "scopeLimit" not in result["suggestion"]


def test_owner_chain_across_two_modules_keeps_the_default_scope_and_says_why(source):
    _wrong_owner_module(source)
    engine = source[1] / "Engine/Source/Runtime/Engine"
    (engine / "Engine.Build.cs").write_text("// rules\n")
    (engine / "Public/GameOwner.h").write_text(
        "class UGameOwner : public UEnhancedInputLocalPlayerSubsystem {\npublic:\n void Own();\n};\n")
    headers.clear_engine_header_catalog_cache()
    result = query(source, query="UGameOwner::ResolveOperation")
    observed = result["coverage"]["observedTypeCandidates"]
    assert [item["typeName"] for item in observed] == ["UGameOwner", "UEnhancedInputLocalPlayerSubsystem"]
    assert {item["moduleUri"] for item in observed} == {
        "engine://Engine/Source/Runtime/Engine", "engine://Engine/Plugins/EnhancedInput/Source/EnhancedInput"}
    suggestion = result["suggestion"]
    assert suggestion["scope"] == "engine_runtime_default_module_unknown"
    assert suggestion["scopeLimit"] == {"excludes": RUNTIME_DEFAULT_EXCLUDES, "observedModules": 2}


def test_same_named_header_without_the_type_is_not_reported_as_observed(source):
    _wrong_owner_module(source)
    other = source[1] / "Engine/Plugins/Other/Source/Other/Public"
    other.mkdir(parents=True)
    (other / "EnhancedInputSubsystems.h").write_text("// no class here\n")
    headers.clear_engine_header_catalog_cache()
    result = query(source, query="UEnhancedInputLocalPlayerSubsystem::ResolveOperation")
    [observed] = result["coverage"]["observedTypeCandidates"]
    assert observed["headerUri"] == ("engine://Engine/Plugins/EnhancedInput/Source/EnhancedInput/Public/"
                                     "EnhancedInputSubsystems.h")
    assert "Other" not in json.dumps(result["coverage"]) and "Other" not in json.dumps(result["suggestion"])


def test_found_member_keeps_its_result_without_observed_type_or_suggestion(source):
    _wrong_owner_module(source)
    result = query(source)
    assert result["matchCount"] == 1 and "suggestion" not in result
    assert "observedTypeCandidates" not in result["coverage"]


def test_observed_type_metadata_is_dropped_whole_when_it_does_not_fit(source, monkeypatch):
    _wrong_owner_module(source)
    fits = local.evidence_metadata_fits
    monkeypatch.setattr(local, "evidence_metadata_fits", lambda payload, max_chars: fits(payload, max_chars=max_chars)
                        and "observedTypeCandidates" not in payload["coverage"])
    result = query(source, query="UEnhancedInputLocalPlayerSubsystem::ResolveOperation")
    assert result["ok"] and "observedTypeCandidates" not in result["coverage"]
    assert result["coverage"]["observedTypeCandidatesOmitted"] == 1
    assert result["suggestion"]["args"]["path"] == "engine://Engine/Plugins/EnhancedInput/Source/EnhancedInput"


def test_related_contracts_are_ids_and_summaries_marked_unvalidated_for_other_engines(source):
    runtime, root, project, _ = source
    classes = root / "Engine/Source/Runtime/Engine/Classes/GameFramework"
    classes.mkdir(parents=True)
    (classes / "Actor.h").write_text("class ENGINE_API AActor : public UObject\n{\npublic:\n virtual void Destroyed();\n};\n")
    (classes / "Pawn.h").write_text("class ENGINE_API APawn : public AActor\n{\npublic:\n void Restart();\n};\n")
    result = symbol.symbol_lookup_capability(runtime, {"query": "APawn::Destroyed", "project": str(project),
                                                      "sourceMode": "engine_local"}).payload
    assert result["ok"] and result["matches"][0]["declaredIn"] == "AActor"
    related = result["relatedContracts"]
    assert related["revision"].startswith("unreal_contracts@")
    assert related["engineVersionValidated"] is False  # fixture engine is 5.7; contracts are validated on 5.8
    assert [item["id"] for item in related["items"]] == ["ue.actor.lifecycle"]
    assert related["items"][0]["summary"].endswith(".") and "statement" not in related["items"][0]
    assert "relatedContracts" not in query(source)


def test_declaration_excerpt_ends_at_the_balanced_signature(source):
    source[3].write_text(
        "class UEnhancedInputLocalPlayerSubsystem {\npublic:\n"
        " UFUNCTION(BlueprintCallable, Category=\"Game|Damage\",\n   meta=(AutoCreateRefTerm=\"IgnoreActors\"))\n"
        " static float ApplyRadialDamageWithFalloff(const UObject* WorldContextObject,\n"
        "   float BaseDamage, float MinimumDamage, const FVector& Origin,\n"
        "   float DamageInnerRadius, float DamageOuterRadius,\n"
        "   float DamageFalloff, TSubclassOf<UDamageType> DamageTypeClass,\n"
        "   const TArray<AActor*>& IgnoreActors,\n"
        "   AActor* DamageCauser = NULL,\n"
        "   AController* InstigatedByController = NULL,\n"
        "   ECollisionChannel DamagePreventionChannel = ECC_Visibility);\n"
        " void UnrelatedNextMember();\n};\n")
    headers.clear_engine_header_catalog_cache()
    result = query(source, query="UEnhancedInputLocalPlayerSubsystem::ApplyRadialDamageWithFalloff")
    [row] = result["matches"]
    assert row["signatureComplete"] is True and row["excerptTruncated"] is False
    assert "ECollisionChannel DamagePreventionChannel = ECC_Visibility);" in result["evidence"]
    assert "UFUNCTION(BlueprintCallable" in result["evidence"]
    assert "UnrelatedNextMember" not in result["evidence"]
    assert row["declarationEndLine"] == 12


def test_field_and_enum_declarations_end_at_their_own_terminator(source):
    source[3].write_text("class UEnhancedInputLocalPlayerSubsystem {\npublic:\n UPROPERTY(EditAnywhere)\n float\n   MaxHealth = 100.f;\n int32 Other;\n};\n"
                         "enum class EMode : uint8 {\n Alpha UMETA(DisplayName=\"A, first\"),\n Beta\n};\n")
    headers.clear_engine_header_catalog_cache()
    field = query(source, query="UEnhancedInputLocalPlayerSubsystem::MaxHealth")
    assert "MaxHealth = 100.f;" in field["evidence"] and "int32 Other" not in field["evidence"]
    assert field["matches"][0]["declarationEndLine"] == 5


@pytest.mark.parametrize("separator", [" ", "\x0c"])
def test_declaration_excerpt_uses_newline_line_units(source, separator):
    source[3].write_text(f"// note{separator}continued\nclass UEnhancedInputLocalPlayerSubsystem {{\npublic:\n void AddMappingContext(int value);\n}};\n",
                         encoding="utf-8", newline="")
    headers.clear_engine_header_catalog_cache()
    result = query(source)
    assert result["matches"][0]["line"] == 4
    # The excerpt starts one line above the declaration (line 3), counted in "\n" units.
    assert result["evidence"].split("\n")[1:3] == ["public:", " void AddMappingContext(int value);"]
