"""Tests for the meaning-preserving Unreal refactor guard."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from semantic_refactor_guard import (  # noqa: E402
    capture_semantic_snapshot,
    compare_semantic_refactor,
)


HEADER = """#pragma once
#include "CoreMinimal.h"
#include "Thing.generated.h"

DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(FOnThingChanged, int32, Value);

UCLASS(BlueprintType)
class TEST_API UThing : public UObject
{
    GENERATED_BODY()
public:
    UFUNCTION(BlueprintCallable)
    int32 GetValue() const;

    UPROPERTY(EditAnywhere, BlueprintReadWrite)
    int32 Value = 0;
};
"""

BUILD_CS = """using UnrealBuildTool;
public class Test : ModuleRules
{
    public Test(ReadOnlyTargetRules Target) : base(Target)
    {
        PublicDependencyModuleNames.AddRange(new string[] { "Core", "Engine" });
        PrivateDependencyModuleNames.Add("Slate");
    }
}
"""


def _make_project(root: Path) -> None:
    (root / "Source" / "Test").mkdir(parents=True)
    (root / "Config").mkdir(parents=True)
    (root / "Source" / "Test" / "Thing.h").write_text(HEADER, encoding="utf-8")
    (root / "Source" / "Test" / "Thing.cpp").write_text(
        '#include "Thing.h"\nint32 UThing::GetValue() const { return Value; }\n',
        encoding="utf-8",
    )
    (root / "Source" / "Test" / "Test.Build.cs").write_text(
        BUILD_CS,
        encoding="utf-8",
    )
    (root / "Config" / "DefaultGame.ini").write_text(
        "[/Script/EngineSettings.GameMapsSettings]\nGameDefaultMap=/Game/Maps/Main\n",
        encoding="utf-8",
    )


def _roots(tmp_path: Path) -> tuple[Path, Path]:
    before = tmp_path / "project"
    after = tmp_path / "candidate"
    _make_project(before)
    shutil.copytree(before, after)
    return before, after


def _probe(
    before: Path,
    after: Path,
    changed_files: list[str],
) -> dict:
    return compare_semantic_refactor(
        before,
        after,
        changed_files=changed_files,
        diff_hash="",
        invariants=[],
        static_proof={},
        build_proof={},
    )


def _valid_compare(
    before: Path,
    after: Path,
    changed_files: list[str],
    *,
    runtime_sensitive: bool = False,
    migration_contract: dict | None = None,
) -> dict:
    probe = _probe(before, after, changed_files)
    diff_hash = probe["diffHash"]
    normalized_files = probe["changedFiles"]
    common_proof = {
        "ok": True,
        "diffHash": diff_hash,
        "changedFiles": normalized_files,
    }
    invariant = {
        "id": "same-observable",
        "description": "The selected public behavior remains unchanged.",
        "comparison": "equals",
        "runtimeSensitive": runtime_sensitive,
        "beforeObserver": {
            "observer": "Thing.GetValue",
            "artifactHash": "before-observer",
            "snapshotHash": probe["beforeSnapshot"]["snapshotHash"],
            "value": {"return": 7},
        },
        "afterObserver": {
            "observer": "Thing.GetValue",
            "artifactHash": "after-observer",
            "snapshotHash": probe["afterSnapshot"]["snapshotHash"],
            "value": {"return": 7},
        },
    }
    return compare_semantic_refactor(
        before,
        after,
        changed_files=changed_files,
        diff_hash=diff_hash,
        invariants=[invariant],
        static_proof={**common_proof, "artifactHash": "static-proof"},
        build_proof={**common_proof, "artifactHash": "build-proof"},
        runtime_proof=(
            {**common_proof, "artifactHash": "runtime-proof"}
            if runtime_sensitive
            else None
        ),
        migration_compatibility_contract=migration_contract,
    )


def _coverage_for(probe: dict) -> dict:
    return {
        "coverage": [
            {
                "surfaceId": surface["surfaceId"],
                "strategy": "compatibility",
                "rationale": "A deprecated adapter preserves the old call shape.",
                "validation": "Compile old and new consumers in the isolated candidate.",
                "rollback": "Restore the previous declaration and implementation.",
            }
            for surface in probe["semanticDelta"]["breaking"]
        ]
    }


def test_snapshot_is_deterministic_and_captures_unreal_surfaces(
    tmp_path: Path,
) -> None:
    before, _ = _roots(tmp_path)

    first = capture_semantic_snapshot(before)
    second = capture_semantic_snapshot(before)

    assert first["ok"] is True
    assert first["snapshotHash"] == second["snapshotHash"]
    surface_types = {
        surface["type"]
        for file_entry in first["files"]
        for surface in file_entry["surfaces"]
    }
    assert {
        "reflection_uclass",
        "reflection_ufunction",
        "reflection_uproperty",
        "delegate",
        "public_type",
        "public_signature",
        "module_dependency",
        "config",
    } <= surface_types


def test_snapshot_captures_all_reflection_kinds_and_plugin_contracts(
    tmp_path: Path,
) -> None:
    before, _ = _roots(tmp_path)
    (before / "Source" / "Test" / "Types.h").write_text(
        """
USTRUCT(BlueprintType)
struct FThingRow { GENERATED_BODY() };
UENUM(BlueprintType)
enum class EThingState : uint8 { Idle };
UINTERFACE(BlueprintType)
class UThingInterface : public UInterface { GENERATED_BODY() };
""",
        encoding="utf-8",
    )
    descriptor = before / "Plugins" / "Demo" / "Demo.uplugin"
    descriptor.parent.mkdir(parents=True)
    descriptor.write_text(
        '{"Modules":[{"Name":"Demo","Type":"Runtime","LoadingPhase":"Default"}],'
        '"Plugins":[{"Name":"GameplayAbilities","Enabled":true}]}',
        encoding="utf-8",
    )

    snapshot = capture_semantic_snapshot(before)
    surface_types = {
        surface["type"]
        for file_entry in snapshot["files"]
        for surface in file_entry["surfaces"]
    }
    assert {
        "reflection_ustruct",
        "reflection_uenum",
        "reflection_uinterface",
        "plugin_module",
        "plugin_dependency",
    } <= surface_types


def test_private_implementation_refactor_passes_with_exact_bound_proofs(
    tmp_path: Path,
) -> None:
    before, after = _roots(tmp_path)
    cpp = after / "Source" / "Test" / "Thing.cpp"
    cpp.write_text(
        '#include "Thing.h"\n'
        "namespace { int32 Normalize(int32 V) { return V; } }\n"
        "int32 UThing::GetValue() const { return Normalize(Value); }\n",
        encoding="utf-8",
    )

    result = _valid_compare(
        before,
        after,
        ["Source/Test/Thing.cpp"],
    )

    assert result["ok"] is True
    assert result["writeGate"]["writesAllowed"] is True
    assert result["semanticDelta"]["breaking"] == []
    assert "not equivalent to exhaustive behavioral" in result["proofBoundary"]


def test_changed_files_and_diff_identity_are_exact(tmp_path: Path) -> None:
    before, after = _roots(tmp_path)
    (after / "Source" / "Test" / "Thing.cpp").write_text(
        '#include "Thing.h"\nint32 UThing::GetValue() const { return Value + 1; }\n',
        encoding="utf-8",
    )

    missing_file = _valid_compare(before, after, ["Source/Test/Thing.h"])
    assert missing_file["ok"] is False
    assert any("changedFiles must exactly equal" in issue for issue in missing_file["issues"])

    valid = _valid_compare(before, after, ["Source/Test/Thing.cpp"])
    bad_diff = compare_semantic_refactor(
        before,
        after,
        changed_files=["Source/Test/Thing.cpp"],
        diff_hash="wrong",
        invariants=[
            {
                "id": "same",
                "description": "same",
                "beforeObserver": {
                    "observer": "o",
                    "artifactHash": "b",
                    "snapshotHash": valid["beforeSnapshot"]["snapshotHash"],
                    "value": 1,
                },
                "afterObserver": {
                    "observer": "o",
                    "artifactHash": "a",
                    "snapshotHash": valid["afterSnapshot"]["snapshotHash"],
                    "value": 1,
                },
            }
        ],
        static_proof={
            "ok": True,
            "artifactHash": "s",
            "diffHash": "wrong",
            "changedFiles": ["Source/Test/Thing.cpp"],
        },
        build_proof={
            "ok": True,
            "artifactHash": "b",
            "diffHash": "wrong",
            "changedFiles": ["Source/Test/Thing.cpp"],
        },
    )
    assert bad_diff["ok"] is False
    assert bad_diff["writeGate"]["exactDiffIdentity"] is False


def test_reflection_public_and_delegate_changes_need_full_contract(
    tmp_path: Path,
) -> None:
    before, after = _roots(tmp_path)
    changed_header = HEADER.replace(
        "int32 GetValue() const;",
        "float GetValue() const;",
    ).replace("FOnThingChanged", "FOnThingUpdated")
    (after / "Source" / "Test" / "Thing.h").write_text(
        changed_header,
        encoding="utf-8",
    )

    blocked = _valid_compare(before, after, ["Source/Test/Thing.h"])
    breaking_types = {
        surface["type"] for surface in blocked["semanticDelta"]["breaking"]
    }
    assert blocked["ok"] is False
    assert {"reflection_ufunction", "delegate", "public_signature"} <= breaking_types

    covered = _valid_compare(
        before,
        after,
        ["Source/Test/Thing.h"],
        migration_contract=_coverage_for(blocked),
    )
    assert covered["ok"] is True


def test_module_and_config_changes_are_breaking_surfaces(tmp_path: Path) -> None:
    before, after = _roots(tmp_path)
    (after / "Source" / "Test" / "Test.Build.cs").write_text(
        BUILD_CS.replace(', "Engine"', ""),
        encoding="utf-8",
    )
    (after / "Config" / "DefaultGame.ini").write_text(
        "[/Script/EngineSettings.GameMapsSettings]\nGameDefaultMap=/Game/Maps/NewMain\n",
        encoding="utf-8",
    )
    changed = [
        "Config/DefaultGame.ini",
        "Source/Test/Test.Build.cs",
    ]

    blocked = _valid_compare(before, after, changed)
    assert {
        surface["type"] for surface in blocked["semanticDelta"]["breaking"]
    } >= {"module_dependency", "config"}
    assert blocked["ok"] is False

    covered = _valid_compare(
        before,
        after,
        changed,
        migration_contract=_coverage_for(blocked),
    )
    assert covered["ok"] is True


def test_runtime_sensitive_invariant_requires_diff_bound_runtime_proof(
    tmp_path: Path,
) -> None:
    before, after = _roots(tmp_path)
    (after / "Source" / "Test" / "Thing.cpp").write_text(
        '#include "Thing.h"\nint32 UThing::GetValue() const { return (Value); }\n',
        encoding="utf-8",
    )
    valid = _valid_compare(
        before,
        after,
        ["Source/Test/Thing.cpp"],
        runtime_sensitive=True,
    )
    assert valid["ok"] is True
    assert valid["runtimeProofRequired"] is True

    probe = _probe(before, after, ["Source/Test/Thing.cpp"])
    diff_hash = probe["diffHash"]
    common = {
        "ok": True,
        "artifactHash": "proof",
        "diffHash": diff_hash,
        "changedFiles": probe["changedFiles"],
    }
    invariant = {
        "id": "runtime",
        "description": "Runtime return value is stable.",
        "runtimeSensitive": True,
        "beforeObserver": {
            "observer": "runtime",
            "artifactHash": "before",
            "snapshotHash": probe["beforeSnapshot"]["snapshotHash"],
            "value": 3,
        },
        "afterObserver": {
            "observer": "runtime",
            "artifactHash": "after",
            "snapshotHash": probe["afterSnapshot"]["snapshotHash"],
            "value": 3,
        },
    }
    blocked = compare_semantic_refactor(
        before,
        after,
        changed_files=probe["changedFiles"],
        diff_hash=diff_hash,
        invariants=[invariant],
        static_proof=common,
        build_proof=common,
    )
    assert blocked["ok"] is False
    assert any("runtimeProof" in issue for issue in blocked["issues"])


def test_observer_identity_values_and_snapshot_binding_fail_closed(
    tmp_path: Path,
) -> None:
    before, after = _roots(tmp_path)
    (after / "Source" / "Test" / "Thing.cpp").write_text(
        '#include "Thing.h"\nint32 UThing::GetValue() const { return Value; }\n\n',
        encoding="utf-8",
    )
    probe = _probe(before, after, ["Source/Test/Thing.cpp"])
    diff_hash = probe["diffHash"]
    proof = {
        "ok": True,
        "artifactHash": "proof",
        "diffHash": diff_hash,
        "changedFiles": probe["changedFiles"],
    }
    result = compare_semantic_refactor(
        before,
        after,
        changed_files=probe["changedFiles"],
        diff_hash=diff_hash,
        invariants=[
            {
                "id": "bad",
                "description": "bad observer pair",
                "beforeObserver": {
                    "observer": "before-name",
                    "artifactHash": "before",
                    "snapshotHash": "wrong",
                    "value": 1,
                },
                "afterObserver": {
                    "observer": "after-name",
                    "artifactHash": "after",
                    "snapshotHash": probe["afterSnapshot"]["snapshotHash"],
                    "value": 2,
                },
            }
        ],
        static_proof=proof,
        build_proof=proof,
    )
    assert result["ok"] is False
    assert result["writeGate"]["observerEvidencePaired"] is False


def test_roots_must_be_isolated_and_paths_restricted(tmp_path: Path) -> None:
    before, after = _roots(tmp_path)
    same_root = compare_semantic_refactor(
        before,
        before,
        changed_files=["Source/Test/Thing.cpp"],
        diff_hash="x",
        invariants=[],
        static_proof={},
        build_proof={},
    )
    assert same_root["ok"] is False
    assert any("distinct" in issue for issue in same_root["issues"])

    restricted = capture_semantic_snapshot(
        after,
        files=["../outside", "Saved/generated.cpp"],
    )
    assert restricted["ok"] is False
    assert len(restricted["issues"]) == 2


def test_generated_plugin_outputs_do_not_pollute_semantic_diff(
    tmp_path: Path,
) -> None:
    before, after = _roots(tmp_path)
    for root, value in ((before, b"old"), (after, b"new")):
        generated = root / "Plugins" / "Demo" / "Binaries" / "module.bin"
        generated.parent.mkdir(parents=True)
        generated.write_bytes(value)
    (after / "Source" / "Test" / "Thing.cpp").write_text(
        '#include "Thing.h"\nint32 UThing::GetValue() const { return (Value); }\n',
        encoding="utf-8",
    )

    result = _valid_compare(
        before,
        after,
        ["Source/Test/Thing.cpp"],
    )
    assert result["ok"] is True
    assert result["changedFiles"] == ["Source/Test/Thing.cpp"]
