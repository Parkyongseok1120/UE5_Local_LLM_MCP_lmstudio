"""engine_local also returns the implementation (.cpp) behind an inherited declaration."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import direct_rag_engine_local as local  # noqa: E402
import direct_rag_symbol as symbol  # noqa: E402
import engine_definition_evidence as definitions  # noqa: E402
import engine_header_evidence as headers  # noqa: E402

ACTOR_H = """#pragma once
class ENGINE_API AActor : public UObject
{
public:
\t/** Apply damage. */
\tvirtual float TakeDamage(float DamageAmount, struct FDamageEvent const& DamageEvent, AController* EventInstigator, AActor* DamageCauser);
\tvoid Unrelated();
};
"""
PAWN_H = """#pragma once
#include "GameFramework/Actor.h"
class ENGINE_API APawn : public AActor
{
public:
\tvoid PossessedBy(AController* NewController);
};
"""
ACTOR_CPP = """#include "GameFramework/Actor.h"

void AActor::Unrelated()
{
\t// float AActor::TakeDamage(float) { return 0.f; }  <- a comment, not a definition
\tconst char* Text = "AActor::TakeDamage(1) {";
}

float AActor::TakeDamage(float DamageAmount, struct FDamageEvent const& DamageEvent, AController* EventInstigator, AActor* DamageCauser)
{
\tfloat ActualDamage = DamageAmount;
\tif (ActualDamage != 0.f)
\t{
\t\tOnTookDamage(ActualDamage);
\t}
\treturn ActualDamage;
}
"""
PAWN_CPP = """#include "GameFramework/Pawn.h"

float APawn::Wrapper(float Amount)
{
\treturn AActor::TakeDamage(Amount, FDamageEvent(), nullptr, nullptr);
}
"""


@pytest.fixture
def engine(tmp_path, monkeypatch):
    root = tmp_path / "AnyInstall"
    build = root / "Engine/Build/Build.version"
    build.parent.mkdir(parents=True)
    build.write_text(json.dumps({"MajorVersion": 5, "MinorVersion": 3, "PatchVersion": 0, "Changelist": 1}))
    module = root / "Engine/Source/Runtime/Engine"
    (module / "Classes/GameFramework").mkdir(parents=True)
    (module / "Private").mkdir(parents=True)
    (module / "Engine.Build.cs").write_text("// module rules\n")
    (module / "Classes/GameFramework/Actor.h").write_text(ACTOR_H)
    (module / "Classes/GameFramework/Pawn.h").write_text(PAWN_H)
    (module / "Private/Actor.cpp").write_text(ACTOR_CPP)
    (module / "Private/Pawn.cpp").write_text(PAWN_CPP)
    project = tmp_path / "Game.uproject"
    project.write_text('{"EngineAssociation":"5.3"}')
    monkeypatch.setattr(local, "resolve_engine_root_for_association", lambda *a: {"ok": True, "engineRoot": str(root)})
    monkeypatch.setattr(symbol, "resolve_request_index", lambda *a, **k: pytest.fail("local requested an index"))
    headers.clear_engine_header_catalog_cache()
    yield SimpleNamespace(workspace=tmp_path, index=tmp_path / "absent.sqlite"), root, project, module
    headers.clear_engine_header_catalog_cache()


def lookup(engine, query="APawn::TakeDamage", **extra):
    runtime, _, project, _ = engine
    return symbol.symbol_lookup_capability(runtime, {"query": query, "project": str(project),
                                                     "sourceMode": "engine_local", **extra}).payload


def test_inherited_method_returns_the_implementation_body_of_the_declaring_type(engine):
    result = lookup(engine)
    assert result["ok"] and result["matchCount"] == 1
    assert result["matches"][0]["declaredIn"] == "AActor"
    assert result["matches"][0]["inheritancePath"] == ["APawn", "AActor"]
    [candidate] = result["definitionCandidates"]
    assert candidate["source"].endswith("Actor.cpp") and candidate["declaredIn"] == "AActor"
    assert candidate["definitionStatus"] == "lexical_definition_candidate"
    assert candidate["fingerprint"]["hashScope"] == "file" and len(candidate["fingerprint"]["sha256"]) == 64
    assert "excerpt" not in candidate
    evidence = result["evidence"]
    assert "[definition candidate]" in evidence and "float ActualDamage = DamageAmount;" in evidence
    assert "return ActualDamage;" in evidence
    # Comments, strings and explicit calls are not definitions.
    assert "<- a comment" not in evidence and "FDamageEvent(), nullptr" not in evidence
    search = result["matches"][0]["definitionSearch"]
    assert search["status"] == "candidates_found" and search["absenceVerified"] is False
    assert search["scope"] == "declaring_module_private_cpp"
    assert result["coverage"]["apiAbsenceVerified"] is False


def test_line_numbers_point_at_the_definition(engine):
    [candidate] = lookup(engine)["definitionCandidates"]
    cpp = (engine[3] / "Private/Actor.cpp").read_text().splitlines()
    assert cpp[candidate["line"] - 1].startswith("float AActor::TakeDamage(")
    assert cpp[candidate["endLine"] - 1] == "}"


def test_missing_definition_is_reported_as_not_found_in_searched_scope_never_absent(engine):
    (engine[3] / "Private/Actor.cpp").write_text("void AActor::Unrelated() {}\n")
    result = lookup(engine)
    assert "definitionCandidates" not in result
    search = result["matches"][0]["definitionSearch"]
    assert search["status"] == "none_found_in_searched_files"
    assert search["absenceVerified"] is False and search["filesRead"] >= 1


def test_unknown_module_or_missing_private_tree_is_a_named_reason(engine):
    import shutil
    shutil.rmtree(engine[3] / "Private")
    search = lookup(engine)["matches"][0]["definitionSearch"]
    assert search["status"] == "none_found_in_searched_files"
    assert "definition_private_tree_missing" in search["reasons"]


def test_definition_search_respects_the_shared_file_budget(engine, monkeypatch):
    monkeypatch.setattr(headers, "_INHERITANCE_MAX_FILES", 2)
    result = lookup(engine)
    search = result["matches"][0]["definitionSearch"]
    assert "file_limit" in search["reasons"] or search["status"] == "candidates_found"
    assert result["coverage"]["completeness"] == "partial"


def test_fields_and_types_get_no_definition_search(engine):
    result = lookup(engine, query="AActor")
    assert result["ok"] and "definitionCandidates" not in result


def test_find_definitions_rejects_calls_and_requires_a_body():
    lexical = headers._lexical_source
    text = ("void X::Run() { Y::Run(); }\n"
            "void Y::Run() const\n{\n}\n"
            "int Z = Y::Run(1);\n"
            "void Y::Run();\n")
    found = definitions.find_definitions(text, "Y", "Run", lexical)
    assert [item["line"] for item in found] == [2]
    assert definitions.find_definitions("void A::B(){}", "Y", "Run", lexical) == []


GAMEMODE_H = """#pragma once
class ENGINE_API AGameModeBase : public AActor
{
public:
\tUFUNCTION(BlueprintNativeEvent, Category=Game)
\tAActor* ChoosePlayerStart(AController* Player);
};
"""
GAMEMODE_CPP = """#include "GameFramework/GameModeBase.h"

AActor* AGameModeBase::ChoosePlayerStart_Implementation(AController* Player)
{
\treturn FindPlayerStartFor(Player);
}
"""


def _with_game_mode(engine):
    module = engine[3]
    (module / "Classes/GameFramework/GameModeBase.h").write_text(GAMEMODE_H)
    (module / "Private/GameModeBase.cpp").write_text(GAMEMODE_CPP)
    headers.clear_engine_header_catalog_cache()


def test_implementation_suffix_resolves_the_reflected_declaration_and_its_thunk_body(engine):
    _with_game_mode(engine)
    result = lookup(engine, "AGameModeBase::ChoosePlayerStart_Implementation")
    assert result["ok"] and result["matchCount"] == 1
    assert result["matches"][0]["symbol_name"] == "ChoosePlayerStart"
    assert result["reflectionThunk"] == {"requestedSymbol": "ChoosePlayerStart_Implementation",
                                         "declaredSymbol": "ChoosePlayerStart", "suffix": "_Implementation",
                                         "reflectionSpecifierObserved": True}
    [candidate] = result["definitionCandidates"]
    assert candidate["symbol_name"] == "ChoosePlayerStart_Implementation"
    assert "return FindPlayerStartFor(Player);" in result["evidence"]


def test_plain_reflected_query_also_finds_the_thunk_definition(engine):
    _with_game_mode(engine)
    result = lookup(engine, "AGameModeBase::ChoosePlayerStart")
    assert "reflectionThunk" not in result
    assert [c["symbol_name"] for c in result["definitionCandidates"]] == ["ChoosePlayerStart_Implementation"]
    assert result["matches"][0]["definitionSearch"]["definitionNames"] == [
        "ChoosePlayerStart", "ChoosePlayerStart_Implementation"]


def test_zero_matches_offer_an_engine_text_search_not_an_absence(engine):
    result = lookup(engine, "AActor::NoSuchThing")
    assert result["ok"] and result["matchCount"] == 0
    assert result["coverage"]["apiAbsenceVerified"] is False
    assert result["suggestion"] == {"tool": "search_files", "scope": "observed_owner_module", "args": {
        "path": "engine://Engine/Source/Runtime/Engine", "query": r"\bNoSuchThing\b", "regex": True}}


def test_space_separated_query_names_the_owner_symbol_form(engine):
    result = lookup(engine, "AGameModeBase RestartPlayer")
    assert result["ok"] is False and result["errorCode"] == "INVALID_TOOL_ARGUMENTS"
    assert "Did you mean AGameModeBase::RestartPlayer?" in result["message"]
    assert result["suggestion"]["args"]["query"] == "AGameModeBase::RestartPlayer"


def test_explicit_derived_implementation_wins_over_reflected_parent(engine):
    _with_game_mode(engine)
    module = engine[3]
    (module / "Classes/GameFramework/GameMode.h").write_text("""#pragma once
class ENGINE_API AGameMode : public AGameModeBase
{
public:
    virtual AActor* ChoosePlayerStart_Implementation(AController* Player) override;
};
""")
    (module / "Private/GameMode.cpp").write_text("""AActor* AGameMode::ChoosePlayerStart_Implementation(AController* Player)
{
    return DerivedStart;
}
""")
    headers.clear_engine_header_catalog_cache()
    result = lookup(engine, "AGameMode::ChoosePlayerStart_Implementation")
    assert result["matchCount"] == 1
    assert result["matches"][0]["declaredIn"] == "AGameMode"
    assert result["matches"][0]["symbol_name"] == "ChoosePlayerStart_Implementation"
    assert "reflectionThunk" not in result
    assert [candidate["declaredIn"] for candidate in result["definitionCandidates"]] == ["AGameMode"]
    assert "return DerivedStart;" in result["evidence"]
    assert "return FindPlayerStartFor(Player);" not in result["evidence"]


@pytest.mark.parametrize("suffix", ["Implementation", "Validate"])
def test_suffix_does_not_match_an_ordinary_method(engine, suffix):
    result = lookup(engine, f"AActor::TakeDamage_{suffix}")
    assert result["matchCount"] == 0
    assert result["status"] == "coverage_limited"
    assert "definitionCandidates" not in result
    assert "reflectionThunk" not in result
    assert result["coverage"]["apiAbsenceVerified"] is False


@pytest.mark.parametrize("specifiers,expected", [("Server, Reliable", 0), ("Server, Reliable, WithValidation", 1),
                                                ("BlueprintNativeEvent", 0)])
def test_validate_fallback_requires_observed_validation_specifier(engine, specifiers, expected):
    module = engine[3]
    (module / "Classes/GameFramework/Actor.h").write_text(ACTOR_H.replace(
        "void Unrelated();", f"UFUNCTION({specifiers})\n\tvoid Submit();"))
    (module / "Private/Actor.cpp").write_text("""void AActor::Submit_Implementation() { DoWork(); }
bool AActor::Submit_Validate() { return true; }
""")
    headers.clear_engine_header_catalog_cache()
    result = lookup(engine, "AActor::Submit_Validate")
    assert result["matchCount"] == expected
    if expected:
        assert result["reflectionThunk"]["reflectionSpecifierObserved"] is True
        assert [c["symbol_name"] for c in result["definitionCandidates"]] == ["Submit_Validate"]
        assert "DoWork" not in result["evidence"]


@pytest.mark.parametrize("specifiers,suffix", [
    ('BlueprintCallable, Category="Server"', "Implementation"),
    ('BlueprintCallable, meta=(DisplayName="BlueprintNativeEvent")', "Implementation"),
    ('BlueprintCallable, meta=(Server, WithValidation)', "Implementation"),
    ('BlueprintCallable, meta=(Server, WithValidation)', "Validate"),
    ('Server=CustomImpl, Reliable', "Implementation"),
    ('Server, WithValidation=CustomValidation', "Validate"),
])
def test_metadata_and_custom_names_do_not_imply_default_thunk_names(engine, specifiers, suffix):
    module = engine[3]
    (module / "Classes/GameFramework/Actor.h").write_text(ACTOR_H.replace(
        "void Unrelated();", f"UFUNCTION({specifiers})\n\tvoid Ordinary();"))
    headers.clear_engine_header_catalog_cache()
    result = lookup(engine, f"AActor::Ordinary_{suffix}")
    assert result["matchCount"] == 0
    assert "reflectionThunk" not in result


ACTOR_URI = "engine://Engine/Source/Runtime/Engine/Private/Actor.cpp"


def _long_take_damage(engine, lines=60):
    body = "".join(f"\tif (DamageAmount > {i}.f) {{ ActualDamage += {i}.f; }}\n" for i in range(lines))
    (engine[3] / "Private/Actor.cpp").write_text(
        "float AActor::TakeDamage(float DamageAmount, struct FDamageEvent const& DamageEvent, "
        "AController* EventInstigator, AActor* DamageCauser)\n{\n\tfloat ActualDamage = DamageAmount;\n"
        + body + "\treturn LateBranchResult;\n}\n")
    return lines + 5


def test_excerpt_limit_offers_the_exact_function_range_on_engine_uri(engine):
    end = _long_take_damage(engine)
    [candidate] = lookup(engine)["definitionCandidates"]
    assert candidate["excerptTruncated"] is True and candidate["endLine"] == end
    assert candidate["rangeSuggestion"] == {"tool": "read_file_range", "args": {
        "path": ACTOR_URI, "startLine": 1, "endLine": end}}


def test_short_complete_definition_gets_no_extra_read(engine):
    [candidate] = lookup(engine)["definitionCandidates"]
    assert candidate["excerptTruncated"] is False
    assert "rangeSuggestion" not in candidate and "evidenceExcerpt" not in candidate


def test_envelope_clipping_marks_the_cut_definition_instead_of_leaving_it_complete(engine):
    from direct_rag_evidence import render_model_json
    _long_take_damage(engine, lines=30)
    full = lookup(engine)
    assert full["definitionCandidates"][0]["excerptTruncated"] is False and "evidenceEnvelopeTruncated" not in full
    result = lookup(engine, maxChars=len(render_model_json(full)) - 300)
    assert result["evidenceEnvelopeTruncated"] is True
    assert "return LateBranchResult;" not in result["evidence"]
    [candidate] = result["definitionCandidates"]
    assert candidate["evidenceExcerpt"] in ("partial", "omitted")
    assert candidate["rangeSuggestion"]["args"] == {"path": ACTOR_URI, "startLine": candidate["line"],
                                                    "endLine": candidate["endLine"]}


def test_envelope_clipping_marks_a_cut_declaration_without_inventing_its_range(engine):
    from direct_rag_evidence import render_model_json
    long_doc = "\t/** " + "Long documentation text. " * 40 + "*/\n"
    (engine[3] / "Classes/GameFramework/Actor.h").write_text(ACTOR_H.replace("\t/** Apply damage. */\n", long_doc))
    headers.clear_engine_header_catalog_cache()
    full = lookup(engine)
    assert "evidenceExcerpt" not in full["matches"][0]
    result = lookup(engine, maxChars=len(render_model_json(full)) - len(full["evidence"]) + 600)
    assert result["evidenceEnvelopeTruncated"] is True
    [row] = result["matches"]
    assert row["evidenceExcerpt"] == "partial"
    # A declaration has no proven end line, so no read range is guessed for it.
    assert "rangeSuggestion" not in row
    [candidate] = result["definitionCandidates"]
    assert candidate["evidenceExcerpt"] == "omitted" and candidate["rangeSuggestion"]["args"]["path"] == ACTOR_URI


def test_bom_file_keeps_definition_lines_and_excerpt_aligned(engine):
    (engine[3] / "Private/Actor.cpp").write_bytes(b"\xef\xbb\xbf" + ACTOR_CPP.replace("\n", "\r\n").encode("utf-8"))
    [candidate] = lookup(engine)["definitionCandidates"]
    lines = ACTOR_CPP.split("\n")
    assert lines[candidate["line"] - 1].startswith("float AActor::TakeDamage(")
    assert lines[candidate["endLine"] - 1] == "}"
    evidence = lookup(engine)["evidence"]
    assert "﻿" not in evidence and f"Actor.cpp:{candidate['line']}\nfloat AActor::TakeDamage(" in evidence


def test_multibyte_text_at_the_envelope_cut_stays_valid_and_is_marked(engine):
    from direct_rag_evidence import render_model_json
    from direct_rag_result import to_mcp_tool_result
    body = "".join(f"\t// 한글 주석 🙂🙂 {i}\n\tActualDamage += {i}.f;\n" for i in range(15))
    (engine[3] / "Private/Actor.cpp").write_text(
        "float AActor::TakeDamage(float DamageAmount, struct FDamageEvent const& DamageEvent, "
        "AController* EventInstigator, AActor* DamageCauser)\n{\n\tfloat ActualDamage = 0.f;\n"
        + body + "\treturn ActualDamage;\n}\n", encoding="utf-8")
    full = lookup(engine)
    assert full["definitionCandidates"][0]["excerptTruncated"] is False
    limit = len(render_model_json(full)) - 250
    result = lookup(engine, maxChars=limit)
    result["evidence"].encode("utf-8")  # no lone surrogate survives the cut
    assert result["definitionCandidates"][0]["evidenceExcerpt"] == "partial"
    rendered = to_mcp_tool_result(symbol.symbol_lookup_capability(engine[0], {
        "query": "APawn::TakeDamage", "project": str(engine[2]), "sourceMode": "engine_local", "maxChars": limit}),
        tool_name="unreal_symbol_lookup")
    assert len(rendered["content"][0]["text"]) <= limit
    json.loads(rendered["content"][0]["text"])


@pytest.mark.parametrize("separator",["\u2028", "\x0c", "\x1e"])
def test_excerpt_lines_use_the_same_newline_unit_as_line_numbers(engine, separator):
    (engine[3] / "Private/Actor.cpp").write_text(
        f"// note{separator}continued\r\n" + ACTOR_CPP.replace("\n", "\r\n"), encoding="utf-8", newline="")
    [candidate] = lookup(engine)["definitionCandidates"]
    lines = (engine[3] / "Private/Actor.cpp").read_bytes().decode("utf-8").split("\n")
    assert lines[candidate["line"] - 1].startswith("float AActor::TakeDamage(")
    assert f"{ACTOR_URI[9:].rsplit('/', 1)[-1]}:{candidate['line']}\nfloat AActor::TakeDamage(" in lookup(engine)["evidence"]


def test_overloads_keep_each_locator_and_mark_only_the_bodies_that_were_cut(engine):
    from direct_rag_evidence import render_model_json
    filler = "".join(f"\tActualDamage += {i}.f;\n" for i in range(30))
    (engine[3] / "Private/Actor.cpp").write_text(
        "float AActor::TakeDamage(float DamageAmount, struct FDamageEvent const& DamageEvent, "
        "AController* EventInstigator, AActor* DamageCauser)\n{\n\treturn FirstBody;\n}\n\n"
        "float AActor::TakeDamage(float DamageAmount, int Unused)\n{\n\tfloat ActualDamage = 0.f;\n"
        + filler + "\treturn SecondBody;\n}\n")
    full = lookup(engine)
    assert [c["line"] for c in full["definitionCandidates"]] == [1, 6]
    result = lookup(engine, maxChars=len(render_model_json(full)) - 200)
    first, second = result["definitionCandidates"]
    assert "return FirstBody;" in result["evidence"] and "evidenceExcerpt" not in first
    assert "rangeSuggestion" not in first
    assert second["evidenceExcerpt"] in ("partial", "omitted") and second["line"] == 6
    assert second["rangeSuggestion"]["args"] == {"path": ACTOR_URI, "startLine": 6, "endLine": second["endLine"]}


def test_thunk_fallback_keeps_the_first_lookups_coverage_limits(engine, monkeypatch):
    _with_game_mode(engine)
    original = local.lookup_engine_header_evidence
    calls = []
    def first_limited(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(result["readBytes"])
        if len(calls) == 1:
            result = {**result, "coverage": {**result["coverage"], "reasons": ["deadline"]}}
        return result
    monkeypatch.setattr(local, "lookup_engine_header_evidence", first_limited)
    result = lookup(engine, "AGameModeBase::ChoosePlayerStart_Implementation")
    assert result["matchCount"] == 1 and len(calls) == 2
    assert result["readBytes"] == sum(calls)
    # The explicit suffixed declaration was not searched to completion; the
    # fallback's own coverage must not hide that.
    assert "deadline" in result["coverage"]["reasons"]


def test_signature_view_returns_declarations_and_proven_definition_ranges_without_bodies(engine):
    full = lookup(engine)
    assert full["definitionCandidates"]
    result = lookup(engine, view="signature")
    assert result["ok"] and result["matchCount"] == 1
    assert result["view"] == "signature" and result["definitionBodiesOmitted"] is True
    assert "[definition candidate]" not in result["evidence"]
    assert "OnTookDamage" not in json.dumps(result)
    [candidate] = result["definitionCandidates"]
    assert candidate["evidenceExcerpt"] == "omitted"
    assert candidate["rangeSuggestion"] == {"tool": "read_file_range", "args": {
        "path": "engine://Engine/Source/Runtime/Engine/Private/Actor.cpp",
        "startLine": full["definitionCandidates"][0]["line"],
        "endLine": full["definitionCandidates"][0]["endLine"],
    }}
    assert "virtual float TakeDamage(float DamageAmount" in result["evidence"]
    assert len(result["evidence"]) < len(full["evidence"])
    assert lookup(engine, view="bogus")["errorCode"] == "INVALID_TOOL_ARGUMENTS"
