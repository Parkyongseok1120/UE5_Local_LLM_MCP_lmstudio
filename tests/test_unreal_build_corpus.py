"""Minimized compiler-error regressions and conservative engine coverage."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from engine_header_evidence import clear_engine_header_catalog_cache
from engine_identity import describe_unreal_engine_identity
from unreal_static_compile_rules import CompileEvidence, _signature
from unreal_static_runner import validate_unreal_readiness
from unreal_static_reflection import validate_generated_h

CORPUS = json.loads((ROOT / "tests/fixtures/ue_build_failures/corpus.json").read_text(encoding="utf-8"))


def write(root, files):
    for name, text in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


@pytest.fixture
def engine_root(tmp_path):
    root = tmp_path / "engine"
    # Synthetic API declarations for deterministic dispatch tests. The checked-in
    # measurement report separately uses installed UE 5.7.4, not this stub.
    write(root, {
        "Engine/Build/Build.version": '{"MajorVersion":5,"MinorVersion":7,"PatchVersion":4}',
        "Engine/Source/Runtime/Core/Public/CoreMinimal.h": "#pragma once\n",
        "Engine/Source/Runtime/CoreUObject/Public/UObject/Object.h": "class UObject {};\n",
        "Engine/Source/Runtime/Engine/Classes/GameFramework/Actor.h": "class AActor : public UObject\n{\npublic:\nvirtual float TakeDamage(float Damage, struct FDamageEvent const& Event, AController* Controller, AActor* Causer);\n};\n",
        "Engine/Source/Runtime/Engine/Classes/GameFramework/Pawn.h": "class APawn : public AActor {};\n",
        "Engine/Source/Runtime/Engine/Classes/GameFramework/PlayerState.h": "class APlayerState : public AActor\n{\npublic:\nUFUNCTION()\nvirtual void OnRep_Score();\n};\n",
        "Engine/Source/Runtime/Engine/Classes/GameFramework/GameModeBase.h": "class AGameModeBase : public AActor\n{\npublic:\nUPROPERTY()\nTSubclassOf<AActor> PlayerStateClass;\nUPROPERTY()\nTSubclassOf<AActor> GameStateClass;\nUFUNCTION(BlueprintNativeEvent)\nAActor* ChoosePlayerStart(AController* Player);\n};\n",
        "Engine/Source/Runtime/Engine/Classes/GameFramework/PlayerController.h": "class APlayerController : public AActor {};\n",
        "Engine/Source/Runtime/InputCore/Classes/InputCoreTypes.h": "struct FKey {};\nstruct EKeys\n{\nstatic INPUTCORE_API const FKey W;\n};\n",
        "Engine/Source/Runtime/Engine/Classes/Engine/DamageEvents.h": "struct FDamageEvent {};\n",
        "Engine/Source/Runtime/Engine/Classes/Sound/SoundBase.h": "class USoundBase : public UObject {};\n",
        "Engine/Source/Runtime/Engine/Public/Net/UnrealNetwork.h": "#pragma once\n",
        "Engine/Plugins/EnhancedInput/Source/EnhancedInput/Public/EnhancedInputComponent.h": "class UEnhancedInputComponent : public UObject {};\n",
        "Engine/Plugins/EnhancedInput/Source/EnhancedInput/Public/InputMappingContext.h": "class UInputMappingContext : public UObject {};\n",
        "Engine/Plugins/EnhancedInput/Source/EnhancedInput/Public/InputActionValue.h": "struct FInputActionValue {};\n",
        "Engine/Plugins/EnhancedInput/Source/EnhancedInput/Public/EnhancedInputSubsystems.h": "class UEnhancedInputLocalPlayerSubsystem : public UObject\n{\npublic:\nvoid AddMappingContext(UInputMappingContext* Context, int Priority);\n};\n",
    })
    clear_engine_header_catalog_cache()
    return root


@pytest.mark.parametrize("case", [c for c in CORPUS["cases"] if c["expectedCodes"]], ids=lambda c: c["id"])
def test_recorded_failure_has_targeted_diagnostic_and_fix_removes_it(tmp_path, engine_root, case):
    identity = describe_unreal_engine_identity(engine_root)
    expected = set(case["expectedCodes"])
    for variant in ("broken", "fixed"):
        root = tmp_path / variant
        write(root, case[variant])
        findings = validate_unreal_readiness(root, engine_identity=identity)
        codes = {f.code for f in findings} & expected
        assert bool(codes) == (variant == "broken"), (case["id"], variant, codes)
        new_findings = [f for f in findings if f.code in expected and f.code != "MISSING_CPP_SYMBOL_INCLUDE"]
        assert all(f.severity == "warning" for f in new_findings)


def test_corpus_accounts_for_all_failed_builds_without_duplicate_observations():
    observed = [(o["conversation"], o["buildIndex"]) for c in CORPUS["cases"] for o in c["observations"]]
    assert len(observed) == len(set(observed)) == CORPUS["failedBuilds"] == 51
    assert CORPUS["buildsObserved"] == 57
    assert any(not c["expectedCodes"] for c in CORPUS["cases"])


def test_generated_header_comments_and_strings_never_block_build(tmp_path):
    path = tmp_path / "Plain.h"
    text = '// Use UCLASS() only for reflected types.\nconst char* Example = "USTRUCT()";\n'
    assert not validate_generated_h(path, text, tmp_path)
    text = '// UCLASS examples below\n/*\n#include "Wrong.generated.h"\n*/\n#include "CoreMinimal.h"\n#include "Plain.generated.h"\nUCLASS()\nclass APlain {};\n'
    assert not validate_generated_h(path, text, tmp_path)
    assert "GENERATED_H_MISSING" in {f.code for f in validate_generated_h(path, 'UCLASS()\nclass APlain {};', tmp_path)}


def test_valid_inherited_repnotify_and_unknown_parent_are_not_missing(tmp_path, engine_root):
    for parent in ("APlayerState", "AUnknownExternalBase"):
        root = tmp_path / parent
        write(root, {"Source/Game/Score.h": '#include "Score.generated.h"\nUCLASS()\nclass AScore : public '+parent+'\n{\nGENERATED_BODY()\nUPROPERTY(ReplicatedUsing=OnRep_Score)\nfloat Score;\n};\n'})
        findings = validate_unreal_readiness(root, engine_identity=describe_unreal_engine_identity(engine_root))
        assert "REPNOTIFY_DECLARATION_NOT_FOUND" not in {f.code for f in findings}


def test_unknown_and_exhausted_engine_lookup_do_not_assert_absence(engine_root):
    evidence = CompileEvidence(describe_unreal_engine_identity(engine_root))
    assert evidence.member("ANeverDeclared", "Anything") == ([], False)
    evidence.lookups = 24
    assert evidence.member("AActor", "NeverDeclared") == ([], False)


def test_signature_normalization_preserves_reference_const_and_elaborated_types():
    a = _signature('virtual float Hit(struct FDamageEvent const& Event, AActor* Target);', 'Hit')
    b = _signature('float Hit(const FDamageEvent& Other, AActor* OtherTarget);', 'Hit')
    assert a == b
    assert a != _signature('float Hit(FDamageEvent& Event, AActor* Target);', 'Hit')


def test_scoped_precheck_runs_the_same_new_rules(tmp_path):
    write(tmp_path, {"Game.uproject": '{"Modules":[{"Name":"Game","Type":"Runtime"}]}',
                     "Source/Game/Game.Build.cs": 'public class Game : ModuleRules {}',
                     "Source/Game/Demo.h": '#pragma once\n', "Source/Game/Demo.cpp": '#include "CoreMinimal.h"\n#include "Demo.h"\n'})
    findings = validate_unreal_readiness(tmp_path, scope_paths=[tmp_path / "Source/Game/Demo.cpp"], engine_identity={"status": "unknown"})
    assert "CPP_FIRST_INCLUDE_MISMATCH" in {f.code for f in findings}


def test_receiver_types_do_not_leak_across_functions(tmp_path, engine_root):
    write(tmp_path, {"Source/Game/Input.cpp": '''
void First(UEnhancedInputComponent* Input) {}
void Second(UEnhancedInputLocalPlayerSubsystem* Input) {
    Input->AddMappingContext(nullptr, 0);
}
'''})
    findings = validate_unreal_readiness(tmp_path, engine_identity=describe_unreal_engine_identity(engine_root))
    assert "ENGINE_MEMBER_NOT_OBSERVED" not in {f.code for f in findings}


def test_valid_overloads_and_third_party_includes_are_not_engine_mismatches(tmp_path, engine_root):
    write(tmp_path, {"Source/Game/Demo.h": '''
class ABase {
public:
virtual void Hit(int Value);

virtual void Hit(float Value);
};
class ADerived : public ABase {
virtual void Hit(float Value) override;
};
''', "Source/Game/External.cpp": '#include <boost/asio.hpp>\n'})
    findings = validate_unreal_readiness(tmp_path, engine_identity=describe_unreal_engine_identity(engine_root))
    assert not ({"ENGINE_OVERRIDE_SIGNATURE_MISMATCH", "ENGINE_INCLUDE_UNRESOLVED"} & {f.code for f in findings})
    assert _signature('void Hit(const int Value);', 'Hit') == _signature('void Hit(int Other);', 'Hit')
    assert _signature('void Hit(unsigned int);', 'Hit') == _signature('void Hit(unsigned int Other);', 'Hit')
