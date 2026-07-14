from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from agent_orchestrator import build_agent_plan, classify_task, choose_edit_strategy  # noqa: E402
from domain_planner import build_domain_profile, build_domain_slice_dag, select_subsystem_lifetime  # noqa: E402
from plan_slice_state import init_slice_state, mark_slice_complete, proof_level_from_build_output  # noqa: E402
from plugin_project_context import paired_header_for_cpp  # noqa: E402
from include_resolver import _module_name_from_path  # noqa: E402
from architecture_decision import (  # noqa: E402
    approval_is_valid,
    build_architecture_decision,
    persist_approval,
)


def test_cpp_analysis_is_source_first_and_fail_closed() -> None:
    assert classify_task("현재 프로젝트 시네마틱 시스템 분석", "auto") == "cpp_analysis"
    payload = build_agent_plan("현재 프로젝트 시네마틱 시스템 분석", "auto").to_dict()
    assert payload["taskKind"] == "cpp_analysis"
    assert payload["editStrategy"] == "no_edit"
    assert payload["sourceEvidence"]["required"] is True
    assert payload["sourceEvidence"]["sourceReadSucceeded"] is False
    assert payload["suggestedToolCalls"][1]["tool"] == "search_files"
    assert payload["suggestedToolCalls"][1]["args"]["path"] == "project://Source"
    tools = [call["tool"] for call in payload["suggestedToolCalls"]]
    assert tools.index("unreal_rag_search") < tools.index("unreal_review_claim_validate")
    assert tools[-1] == "unreal_review_claim_validate"


def test_generic_component_example_does_not_claim_project_evidence() -> None:
    payload = build_agent_plan("UActorComponent 예제 코드 보여줘", "code_sketch").to_dict()
    assert payload["sourceEvidence"]["required"] is False
    assert payload["sourceEvidence"]["claimPolicy"] == "generic_example_allowed"


def test_existing_single_file_edit_uses_exact_patch() -> None:
    assert choose_edit_strategy("edit", "fix existing file", file_count_hint=1) == "exact_patch"


def test_mixed_domain_profile_serializes_dag() -> None:
    request = "Replicate a GAS ability component and trigger it from AnimNotify"
    profile = build_domain_profile(request)
    assert profile.mixed is True
    assert "gas" in {profile.primary, *profile.secondary_domains}
    assert "replication" in {profile.primary, *profile.secondary_domains}
    slices = build_domain_slice_dag(profile, request)
    assert slices[0].slice_id == "ownership_decision"
    assert all(len(item.files) <= 2 for item in slices)
    assert any(item.domain == "gas" for item in slices)
    assert any(item.domain == "replication" for item in slices)
    payload = build_agent_plan(request, "auto").to_dict()
    assert payload["domainProfile"]["mixed"] is True
    assert payload["domainProfile"]["secondaryDomains"]


def test_ambiguous_subsystem_requires_lifetime_decision() -> None:
    lifetime = select_subsystem_lifetime("Create a cinematic state subsystem")
    assert lifetime["requestedLifetime"] == "unknown"
    assert lifetime["recommendedBase"] is None
    payload = build_agent_plan("Create a cinematic state subsystem", "prototype_subsystem").to_dict()
    assert payload["writeGate"]["writesAllowed"] is False
    assert payload["ambiguityGate"]["recommendedAction"] == "ask_user_once"


def test_nested_plugin_pair_and_module_name(tmp_path: Path) -> None:
    root = tmp_path / "Demo"
    plugin = root / "Plugins" / "Local" / "Source" / "Local"
    header = plugin / "Public" / "Cinematic" / "Thing.h"
    cpp = plugin / "Private" / "Cinematic" / "Thing.cpp"
    header.parent.mkdir(parents=True)
    cpp.parent.mkdir(parents=True)
    (root / "Demo.uproject").write_text("{}", encoding="utf-8")
    (root / "Plugins" / "Local" / "Local.uplugin").write_text(
        '{"Installed":false,"Modules":[{"Name":"Local","Type":"Runtime"}]}', encoding="utf-8"
    )
    header.write_text("class UThing {};", encoding="utf-8")
    cpp.write_text('#include "Cinematic/Thing.h"', encoding="utf-8")
    assert paired_header_for_cpp(cpp, root) == header
    assert _module_name_from_path(cpp, root) == "Local"


def test_slice_requires_exact_built_proof(tmp_path: Path) -> None:
    target = tmp_path / "Thing.cpp"
    target.write_text("void X() {}", encoding="utf-8")
    plan = [{"slice_id": "s1", "slice_kind": "compile"}]
    stale = mark_slice_complete(
        init_slice_state(plan), project_root=tmp_path, written_paths=[target],
        plan_slices=plan, proof_level="BuiltStale",
    )
    assert stale["slices"][0]["status"] != "complete"
    built = mark_slice_complete(
        init_slice_state(plan), project_root=tmp_path, written_paths=[target],
        plan_slices=plan, proof_level="Built",
    )
    assert built["slices"][0]["status"] == "complete"
    assert proof_level_from_build_output(True, "Target is up to date") == "BuiltStale"
    assert proof_level_from_build_output(True, "Building 4 actions with 4 processes") == "BuiltUnverified"
    assert proof_level_from_build_output(True, "[1/3] Compile Demo.cpp") == "Built"


def test_architecture_approval_invalidates_on_authority_or_scope(tmp_path: Path) -> None:
    store = tmp_path / "approval.json"
    gate = {"ambiguityScore": 0.8, "recommendedAction": "human_approval", "clarificationQuestions": []}
    decision = build_architecture_decision(
        ambiguity_gate=gate, ownership="World", lifetime="Map", authority="Server",
        project_path="Demo.uproject", scope_hash="abc", affected_file_hash="def", plan_revision="1",
    )
    persist_approval(store, decision)
    assert approval_is_valid(store, decision)
    changed = build_architecture_decision(
        ambiguity_gate=gate, ownership="World", lifetime="Map", authority="Client",
        project_path="Demo.uproject", scope_hash="abc", affected_file_hash="def", plan_revision="1",
    )
    assert not approval_is_valid(store, changed)
