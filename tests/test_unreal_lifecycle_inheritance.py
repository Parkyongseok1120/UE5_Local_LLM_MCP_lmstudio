"""Inheritance-aware Unreal API evidence and lifecycle override checks.

Regression baseline for the UE correctness plan (S1): a lifecycle override
inherited from an indirect base must not be reported as invalid, and engine
member lookup must follow the declared parent chain.
"""
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
import engine_header_evidence as headers  # noqa: E402
from unreal_static_lifecycle import validate_unreal_lifecycle_overrides  # noqa: E402

CODE = "INVALID_UNREAL_LIFECYCLE_OVERRIDE"


def lifecycle_findings(tmp_path: Path, text: str, bases: dict[str, str] | None = None) -> list[str]:
    path = tmp_path / "Source/Game/Public/Subject.h"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    kwargs = {"bases": bases} if bases is not None else {}
    findings = validate_unreal_lifecycle_overrides(path, text, tmp_path, **kwargs)
    return [f"{f.code}:{f.message.split(';')[1].split(' is ')[0].strip()}" for f in findings if f.code == CODE]


def test_direct_base_invalid_override_is_still_reported(tmp_path):
    text = "class UMySub : public UWorldSubsystem {\n  virtual void OnWorldDestroyed() override;\n};\n"
    assert lifecycle_findings(tmp_path, text) == [f"{CODE}:OnWorldDestroyed"]



def test_uobject_overrides_inherited_by_a_subsystem_are_valid(tmp_path):
    text = ("class UMySub : public UWorldSubsystem {\n"
            "  virtual void BeginDestroy() override;\n  virtual void PostLoad() override;\n};\n")
    assert lifecycle_findings(tmp_path, text) == []



def test_engine_intermediate_base_resolves_to_its_parent_contract(tmp_path):
    text = ("class UMyTickable : public UTickableWorldSubsystem {\n"
            "  virtual void OnWorldBeginPlay(UWorld& World) override;\n"
            "  virtual void BeginDestroy() override;\n"
            "  virtual void OnWorldDestroyed() override;\n};\n")
    assert lifecycle_findings(tmp_path, text) == [f"{CODE}:OnWorldDestroyed"]



def test_project_intermediate_base_is_followed(tmp_path):
    text = "class UDerived : public UProjectBase {\n  virtual void OnWorldDestroyed() override;\n};\n"
    assert lifecycle_findings(tmp_path, text, bases={"UProjectBase": "UWorldSubsystem"}) == [f"{CODE}:OnWorldDestroyed"]


@pytest.fixture
def engine(tmp_path, monkeypatch):
    root = tmp_path / "UE"
    build = root / "Engine/Build/Build.version"
    build.parent.mkdir(parents=True)
    build.write_text(json.dumps({"MajorVersion": 5, "MinorVersion": 8, "PatchVersion": 2, "Changelist": 1}))
    framework = root / "Engine/Source/Runtime/Engine/Classes/GameFramework"
    framework.mkdir(parents=True)
    (framework / "Actor.h").write_text(
        "class ENGINE_API AActor : public UObject {\npublic:\n"
        "  FTimerManager& GetWorldTimerManager() const;\n};\n", encoding="utf-8")
    (framework / "Pawn.h").write_text(
        "class ENGINE_API APawn : public AActor {\npublic:\n  void AddMovementInput(FVector Direction);\n};\n",
        encoding="utf-8")
    project = tmp_path / "Game.uproject"
    project.write_text('{"EngineAssociation":"5.8"}')
    monkeypatch.setattr(local, "resolve_engine_root_for_association", lambda *a: {"ok": True, "engineRoot": str(root)})
    monkeypatch.setattr(symbol, "resolve_request_index", lambda *a, **k: pytest.fail("local requested an index"))
    headers.clear_engine_header_catalog_cache()
    yield SimpleNamespace(index=tmp_path / "absent.sqlite", workspace=tmp_path), project
    headers.clear_engine_header_catalog_cache()


def engine_query(engine, query: str) -> dict:
    runtime, project = engine
    return symbol.symbol_lookup_capability(runtime, {"query": query, "project": str(project),
                                                     "sourceMode": "engine_local"}).payload


def test_direct_member_is_found_without_inheritance(engine):
    result = engine_query(engine, "APawn::AddMovementInput")
    assert result["ok"] and result["matchCount"] == 1



def test_inherited_member_is_found_on_the_declaring_parent(engine):
    result = engine_query(engine, "APawn::GetWorldTimerManager")
    assert result["ok"] and result["matchCount"] == 1
    row = result["matches"][0]
    assert row["requestedOwner"] == "APawn"
    assert row["declaredIn"] == "AActor"
    assert row["inheritancePath"] == ["APawn", "AActor"]
    assert row["inheritanceVerification"] == "parents_searched"
    assert row["moduleOwner"]["module"] == "Engine"
    assert row["deprecated"]["observed"] is False
    assert "const" in row["specifiers"]["qualifiers"]
    assert result["targetResolution"]["status"] == "resolved"
    assert result["callabilityVerification"].startswith("not_verified")
    assert result["coverage"]["apiAbsenceVerified"] is False


def test_nearest_declaration_hides_parent_and_deprecation_is_observed(engine):
    framework = engine[1].parent / "UE/Engine/Source/Runtime/Engine/Classes/GameFramework"
    (framework / "Pawn.h").write_text(
        "class ENGINE_API APawn : public AActor {\npublic:\n"
        "  UE_DEPRECATED(5.8, \"Use the actor timer manager\")\n"
        "  FTimerManager& GetWorldTimerManager() const;\n};\n", encoding="utf-8")
    headers.clear_engine_header_catalog_cache()
    result = engine_query(engine, "APawn::GetWorldTimerManager")
    assert [row["declaredIn"] for row in result["matches"]] == ["APawn"]
    assert result["matches"][0]["deprecated"] == {
        "observed": True, "version": "5.8", "message": "Use the actor timer manager", "source": "UE_DEPRECATED"}


def test_missing_parent_and_absent_member_stay_unverified(engine):
    framework = engine[1].parent / "UE/Engine/Source/Runtime/Engine/Classes/GameFramework"
    (framework / "Pawn.h").write_text("class ENGINE_API APawn : public AUnknownBase {\npublic:\n};\n", encoding="utf-8")
    headers.clear_engine_header_catalog_cache()
    result = engine_query(engine, "APawn::GetWorldTimerManager")
    assert result["ok"] and result["matchCount"] == 0
    inheritance = result["coverage"]["inheritance"]
    assert inheritance["verification"] == "partial"
    assert inheritance["unresolvedTypes"] == ["AUnknownBase"]
    assert result["coverage"]["apiAbsenceVerified"] is False


def test_inheritance_cycle_terminates(engine):
    framework = engine[1].parent / "UE/Engine/Source/Runtime/Engine/Classes/GameFramework"
    (framework / "Pawn.h").write_text("class ENGINE_API APawn : public AActor {};\n", encoding="utf-8")
    (framework / "Actor.h").write_text("class ENGINE_API AActor : public APawn {};\n", encoding="utf-8")
    headers.clear_engine_header_catalog_cache()
    result = engine_query(engine, "APawn::Missing")
    assert result["ok"] and result["matchCount"] == 0
    assert result["coverage"]["inheritance"]["visitedTypes"] == ["APawn", "AActor"]
