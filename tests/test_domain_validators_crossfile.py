from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from unreal_static_validate import validate_unreal_readiness  # noqa: E402


def _project(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "Demo"
    module = root / "Source" / "Demo"
    (module / "Public").mkdir(parents=True)
    (module / "Private").mkdir(parents=True)
    (root / "Demo.uproject").write_text(
        '{"Modules":[{"Name":"Demo","Type":"Runtime"}]}', encoding="utf-8"
    )
    return root, module


def _codes(root: Path, *paths: Path) -> set[str]:
    findings = validate_unreal_readiness(
        root, scope_paths=list(paths), skip_include_path_checks=True
    )
    return {finding.code for finding in findings}


def test_component_header_cpp_split_is_normal(tmp_path: Path) -> None:
    root, module = _project(tmp_path)
    header = module / "Public" / "DemoActor.h"
    cpp = module / "Private" / "DemoActor.cpp"
    header.write_text(
        "class ADemoActor : public AActor {\n"
        "UPROPERTY() TObjectPtr<UTargetingComponent> TargetingComponent;\n};\n",
        encoding="utf-8",
    )
    cpp.write_text(
        "ADemoActor::ADemoActor()\n{\n TargetingComponent =\n"
        " CreateDefaultSubobject<UTargetingComponent>(TEXT(\"Targeting\"));\n}\n",
        encoding="utf-8",
    )
    codes = _codes(root, header, cpp)
    assert "COMPONENT_CREATE_DEFAULT_SUBOBJECT_WRONG_LOCATION" not in codes
    assert "COMPONENT_MEMBER_DECLARATION_MISSING" not in codes


def test_component_wrong_function_warns(tmp_path: Path) -> None:
    root, module = _project(tmp_path)
    header = module / "Public" / "DemoActor.h"
    cpp = module / "Private" / "DemoActor.cpp"
    header.write_text("class ADemoActor : public AActor {};\n", encoding="utf-8")
    cpp.write_text(
        "void ADemoActor::BeginPlay()\n{\n CreateDefaultSubobject<UTargetingComponent>(TEXT(\"X\"));\n}\n",
        encoding="utf-8",
    )
    assert "COMPONENT_CREATE_DEFAULT_SUBOBJECT_WRONG_LOCATION" in _codes(root, header, cpp)


def test_server_rpc_does_not_require_redundant_authority_check(tmp_path: Path) -> None:
    root, module = _project(tmp_path)
    header = module / "Public" / "NetActor.h"
    cpp = module / "Private" / "NetActor.cpp"
    header.write_text(
        "class ANetActor : public AActor {\n"
        "UFUNCTION(Server, Reliable) void ServerUse();\n};\n",
        encoding="utf-8",
    )
    cpp.write_text("void ANetActor::ServerUse_Implementation() {}\n", encoding="utf-8")
    assert "REPLICATION_RPC_AUTHORITY_CHECK_MISSING" not in _codes(root, header, cpp)


def test_replicated_using_requires_exact_handler(tmp_path: Path) -> None:
    root, module = _project(tmp_path)
    header = module / "Public" / "NetActor.h"
    cpp = module / "Private" / "NetActor.cpp"
    header.write_text(
        "class ANetActor : public AActor {\n"
        "UPROPERTY(ReplicatedUsing=OnRep_Health) float Health;\n"
        "void OnRep_Other();\n};\n",
        encoding="utf-8",
    )
    cpp.write_text("void ANetActor::OnRep_Other() {}\n", encoding="utf-8")
    assert "REPLICATION_ONREP_HANDLER_MISSING" in _codes(root, header, cpp)


def test_gas_init_and_grant_may_be_in_different_functions(tmp_path: Path) -> None:
    root, module = _project(tmp_path)
    header = module / "Public" / "GasActor.h"
    cpp = module / "Private" / "GasActor.cpp"
    header.write_text("class AGasActor : public AActor {};\n", encoding="utf-8")
    cpp.write_text(
        "void AGasActor::InitASC() { ASC->InitAbilityActorInfo(this, this); }\n"
        "void AGasActor::Grant() { ASC->GiveAbility(Spec); }\n",
        encoding="utf-8",
    )
    assert "GAS_ABILITY_BEFORE_ASC_INIT" not in _codes(root, header, cpp)


def test_nonreplicated_attribute_set_is_allowed(tmp_path: Path) -> None:
    root, module = _project(tmp_path)
    header = module / "Public" / "Stats.h"
    cpp = module / "Private" / "Stats.cpp"
    header.write_text("class UStats : public UAttributeSet { float LocalValue; };\n", encoding="utf-8")
    cpp.write_text("void UStats::ResetLocal() { LocalValue = 0; }\n", encoding="utf-8")
    assert "GAS_ATTRIBUTE_REPLICATION_MISSING" not in _codes(root, header, cpp)


def test_anim_notify_does_not_force_specific_engine_callback(tmp_path: Path) -> None:
    root, module = _project(tmp_path)
    header = module / "Public" / "FootstepNotify.h"
    cpp = module / "Private" / "FootstepNotify.cpp"
    header.write_text("class UFootstepNotify : public UAnimNotify {};\n", encoding="utf-8")
    cpp.write_text("void UFootstepNotify::Helper() {}\n", encoding="utf-8")
    codes = _codes(root, header, cpp)
    assert "ANIM_NOTIFY_RECEIVED_MISSING" not in codes
    assert "ANIM_NOTIFYSTATE_LIFECYCLE_INCOMPLETE" not in codes
