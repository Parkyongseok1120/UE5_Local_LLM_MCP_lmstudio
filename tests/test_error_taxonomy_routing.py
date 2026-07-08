from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import error_taxonomy  # noqa: E402


def test_route_generated_h_error_to_reflection_fix():
    route = error_taxonomy.route_error_action("BadComponent.generated.h must be the last include")

    assert route["errorSubkind"] == "GENERATED_H_NOT_LAST"
    assert route["broadMode"] == "reflection_fix"
    assert route["preferredRagModes"] == ["reflection_fix", "compile_fix"]
    assert "failing header" in route["allowedPatchTargets"]
    assert "broad refactor" in route["forbiddenActions"]


def test_route_missing_include_to_module_fix():
    route = error_taxonomy.route_error_action(
        "fatal error C1083: Cannot open include file: 'GameplayTagContainer.h': No such file or directory"
    )

    assert route["broadMode"] == "module_fix"
    assert "owner Build.cs" in route["requiredReads"]
    assert "owner Build.cs" in route["allowedPatchTargets"]
    assert "explaining dependency without Build.cs patch" in route["forbiddenActions"]


def test_route_link_error_avoids_build_cs_first():
    route = error_taxonomy.route_error_action(
        "error LNK2019: unresolved external symbol Foo plus declaration definition mismatch"
    )

    assert route["errorSubkind"] == "LNK_MISSING_CPP_DEFINITION"
    assert route["broadMode"] == "compile_fix"
    assert route["routePriorityApplied"] == "lnk_missing_definition_before_signature_mismatch"
    assert "Build.cs-first fix without module evidence" in route["forbiddenActions"]
    assert any("missing cpp definition" in item for item in route["softSteering"])
    assert "Build.cs-first fix is not supported" in route["buildCsFirstWarning"]


def test_route_unresolved_external_wins_over_signature_mismatch():
    route = error_taxonomy.route_error_action(
        'unresolved external symbol "public: void __cdecl UDemoComponent::StartDash(void)" '
        "while checking declaration definition"
    )

    assert route["errorSubkind"] == "LNK_MISSING_CPP_DEFINITION"
    assert route["broadMode"] == "compile_fix"


def test_route_missing_implementation_wins_over_signature_mismatch():
    route = error_taxonomy.route_error_action(
        "missing implementation for declared function; declaration and definition text appears in request"
    )

    assert route["errorSubkind"] == "LNK_MISSING_CPP_DEFINITION"


def test_route_declared_but_not_defined_wins_over_signature_mismatch():
    route = error_taxonomy.route_error_action(
        "UDemoComponent::StartDash was declared but not defined; declaration definition mismatch suspected"
    )

    assert route["errorSubkind"] == "LNK_MISSING_CPP_DEFINITION"


def test_route_header_cpp_signature_mismatch_soft_steering():
    route = error_taxonomy.route_error_action(
        "CPP_FUNCTION_SIGNATURE_MISMATCH Source/HoldoutFixture/Private/HoldoutDashComponent.cpp"
    )

    assert route["errorSubkind"] == "HEADER_CPP_SIGNATURE_MISMATCH"
    assert route["broadMode"] == "compile_fix"
    assert route["routePriorityApplied"] == "signature_mismatch_without_lnk_evidence"
    assert "header declaration" in route["requiredReads"]
    assert "matching cpp/header" in route["allowedPatchTargets"]
    assert any("header/cpp signature mismatch" in item for item in route["softSteering"])
    assert "Build.cs-first fix is not supported" in route["buildCsFirstWarning"]


def test_module_fix_routes_still_allow_build_cs_dependency_hints():
    route = error_taxonomy.route_error_action(
        "fatal error C1083: Cannot open include file: 'EnhancedInputComponent.h': No such file or directory"
    )

    assert route["broadMode"] == "module_fix"
    assert "owner Build.cs" in route["allowedPatchTargets"]
    assert not route["buildCsFirstWarning"]


def test_known_module_fix_routes_still_allow_dependency_flow():
    samples = {
        "GameplayTags": "fatal error C1083: Cannot open include file: 'GameplayTagContainer.h'",
        "EnhancedInput": "fatal error C1083: Cannot open include file: 'EnhancedInputComponent.h'",
        "UMG": "fatal error C1083: Cannot open include file: 'Blueprint/UserWidget.h'",
        "Niagara": "fatal error C1083: Cannot open include file: 'NiagaraComponent.h'",
        "AIModule": "fatal error C1083: Cannot open include file: 'BehaviorTree/BlackboardComponent.h'",
        "NavigationSystem": "fatal error C1083: Cannot open include file: 'NavigationSystem.h'",
        "LevelSequence": "fatal error C1083: Cannot open include file: 'LevelSequenceActor.h'",
    }

    for text in samples.values():
        route = error_taxonomy.route_error_action(text)
        assert route["broadMode"] == "module_fix"
        assert "owner Build.cs" in route["allowedPatchTargets"]
        assert not route["buildCsFirstWarning"]


def test_route_sequencer_binding_confusion_log_first():
    route = error_taxonomy.route_error_action("LevelSequence binding actor mismatch during playback")

    assert route["errorSubkind"] == "SEQUENCER_BINDING_CONFUSION"
    assert route["routePriorityApplied"] == "sequencer_binding_log_first"
    assert "sequencer asset" in route["requiredReads"]


def test_route_tick_order_suspect_log_first():
    route = error_taxonomy.route_error_action("PrimaryActorTick order suspect before TG_PrePhysics")

    assert route["errorSubkind"] == "TICK_ORDER_SUSPECT"
    assert route["routePriorityApplied"] == "tick_order_log_first"


def test_route_api_version_mismatch_log_first():
    route = error_taxonomy.route_error_action("API_VERSION mismatch for deprecated UE macro")

    assert route["errorSubkind"] == "API_VERSION_MISMATCH"
    assert route["routePriorityApplied"] == "api_version_log_first"


def test_route_callback_function_pointer_mismatch():
    route = error_taxonomy.route_error_action("CALLBACK_FUNCTION_POINTER_MISMATCH handler params drift")

    assert route["errorSubkind"] == "CALLBACK_FUNCTION_POINTER_MISMATCH"
    assert route["broadMode"] == "compile_fix"
    assert "registration site" in route["allowedPatchTargets"]


def test_route_interface_implementer_signature_mismatch():
    route = error_taxonomy.route_error_action("INTERFACE_IMPLEMENTER_SIGNATURE_MISMATCH implementer signature does not match")

    assert route["errorSubkind"] == "INTERFACE_IMPLEMENTER_SIGNATURE_MISMATCH"
    assert "implementer header" in route["allowedPatchTargets"]


def test_route_cpp_not_declared_in_header():
    route = error_taxonomy.route_error_action("CPP_FUNCTION_NOT_DECLARED_IN_HEADER missing declaration")

    assert route["errorSubkind"] == "CPP_FUNCTION_NOT_DECLARED_IN_HEADER"
    assert "matching header" in route["allowedPatchTargets"]

