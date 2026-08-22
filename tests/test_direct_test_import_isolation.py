from __future__ import annotations

import ast
from pathlib import Path


TESTS = Path(__file__).resolve().parent

# These modules implement the unsupported Python workflow controller, its task
# and route state, or compatibility projections around it.  Current product
# tests must exercise the dedicated Direct RAG/Node entry points instead.
LEGACY_WORKFLOW_IMPORTS = frozenset(
    {
        "agent_orchestrator",
        "agent_run_report",
        "agent_session_core",
        "analyze_failure_attempts",
        "architecture_claim_validate",
        "architecture_reasoning",
        "architecture_decision",
        "architecture_map",
        "architecture_portfolio",
        "architecture_proposal_store",
        "architecture_state",
        "asset_graph_lookup",
        "asset_hint_resolver",
        "bench_mcp",
        "bootstrap_local_holdout",
        "build_symbol_graph",
        "change_impact_contract",
        "clangd_helper",
        "code_generation_contract",
        "code_sketch_claim_validate",
        "code_sketch_pipeline",
        "control_protocol_spec",
        "control_runtime_identity",
        "control_state_registry",
        "control_transition_bridge",
        "direct_model_mode",
        "domain_planner",
        "eval_agent_harness",
        "eval_domain_contract",
        "eval_e2e_compile",
        "eval_pass_at_k",
        "eval_project_review",
        "eval_reasoning",
        "eval_soulslike_live",
        "evaluate_refactor_plans",
        "evaluate_rag_queries",
        "failure_memory",
        "failure_memory_rerank",
        "feature_intent_contract",
        "feature_intent_fast_path",
        "collect_failure_memory",
        "job_store",
        "knowledge_audit",
        "mcp_boot_instance",
        "mcp_connection",
        "mcp_control_envelope",
        "mcp_public_contract",
        "mcp_tool_registry",
        "mcp_tool_compact",
        "multifile_refactor_autofix",
        "lmstudio_unreal_wrapper",
        "index_staleness",
        "on_active_project_changed",
        "node_plan_validate",
        "phase_tool_router",
        "plan_graph",
        "plan_slice_state",
        "project_switch_invalidate",
        "prompt_history",
        "profile_ab_harness",
        "query_rag",
        "rag_delivery",
        "rag_search",
        "rag_semantic",
        "read_query_history",
        "reject_failure_memory",
        "refactor_plan",
        "review_claim_validate",
        "route_recovery_policy",
        "run_9b_regression_gate",
        "run_eval_harness",
        "run_eval_regression",
        "runtime_debug_session",
        "semantic_refactor_guard",
        "semantic_ambiguity",
        "state_root",
        "synthesis_readiness",
        "task_api",
        "task_autonomy_supervisor",
        "task_continuation_state",
        "task_continuity",
        "task_gate_history",
        "task_phase",
        "token_budget",
        "tool_exposure",
        "tool_policy",
        "unreal_agent_session",
        "unreal_rag_mcp",
        "wrapper_evidence",
        "wrapper_guards",
        "wrapper_job_manager",
        "warm_symbol_cache",
        "write_locks",
    }
)


def imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def test_default_tests_do_not_import_legacy_workflow_controller() -> None:
    violations: dict[str, list[str]] = {}
    for path in sorted(TESTS.glob("test_*.py")):
        forbidden = sorted(imported_roots(path) & LEGACY_WORKFLOW_IMPORTS)
        if forbidden:
            violations[path.name] = forbidden

    assert violations == {}, (
        "Default tests must validate the Direct product surface; move historical "
        f"workflow-controller tests under legacy_eval/tests: {violations}"
    )
