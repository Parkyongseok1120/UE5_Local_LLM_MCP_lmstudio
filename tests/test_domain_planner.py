from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from agent_orchestrator import build_agent_plan  # noqa: E402
from domain_planner import (  # noqa: E402
    architecture_ambiguity_gate,
    build_fix_evidence,
    detect_domain_kind,
    select_subsystem_lifetime,
)
from error_taxonomy import route_error_action  # noqa: E402
from retry_state import (  # noqa: E402
    count_consecutive_include_fingerprint_rejections,
    make_include_missing_rejection_record,
    recommend_retry_action,
)


def test_component_registration_error_route():
    route = route_error_action(
        "error C2027: use of undefined type 'UBoxComponent' while creating a default subobject"
    )
    assert route["errorSubkind"] == "COMPONENT_REGISTRATION_MISSING_INCLUDE"
    assert route["broadMode"] == "compile_fix"


def test_fix_evidence_for_component_include():
    route = route_error_action(
        "error C2027: use of undefined type 'UBoxComponent' while creating a default subobject"
    )
    evidence = build_fix_evidence("Fix UBoxComponent registration", route)
    assert evidence is not None
    assert evidence["errorSubkind"] == "COMPONENT_REGISTRATION_MISSING_INCLUDE"
    assert "BoxComponent.h" in (evidence.get("patchTemplate") or evidence.get("patch_template") or "")


def test_component_domain_plan_slices():
    plan = build_agent_plan("Implement UHealthComponent on ADemoActor", "prototype_component")
    payload = plan.to_dict()
    assert payload["domainKind"] == "component"
    assert len(payload.get("planSlices") or []) >= 2
    assert payload["planSlices"][0]["slice_id"] == "component_scaffold"


def test_subsystem_lifetime_selector():
    payload = select_subsystem_lifetime("Add session-wide inventory UGameInstanceSubsystem")
    assert payload["requestedLifetime"] == "game_instance"
    assert payload["recommendedBase"] == "UGameInstanceSubsystem"


def test_architecture_ambiguity_gate_plan_only():
    gate = architecture_ambiguity_gate("Maybe refactor ownership across multiple modules whole project")
    assert gate["recommendedAction"] in {"plan_only", "ask_user_once"}


def test_include_fingerprint_retry_escalation():
    record = make_include_missing_rejection_record(
        attempt=1,
        feedback="missing include",
        symbol="UBoxComponent",
        patch_target="Source/HoldoutFixture/Private/HoldoutBoxActor.cpp",
        required_include="Components/BoxComponent.h",
    )
    repeat = count_consecutive_include_fingerprint_rejections([record], record["includeFingerprint"])
    assert repeat == 1
    recommendation = recommend_retry_action(
        record,
        record,
        attempts=[record],
        rejection_kind="component_include_missing",
    )
    assert recommendation["action"] == "inject_include_template"
