from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"

LEGACY_RUNTIME_MODULES = frozenset(
    {
        "agent_orchestrator",
        "agent_run_report",
        "agent_session_core",
        "analyze_failure_attempts",
        "approve_feature_intent",
        "architecture_claim_validate",
        "architecture_decision",
        "architecture_map",
        "architecture_portfolio",
        "architecture_proposal_store",
        "architecture_reasoning",
        "architecture_state",
        "asset_graph_lookup",
        "asset_hint_resolver",
        "bench_token_budget",
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
        "domain_eval_normalize",
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
        "lmstudio_unreal_wrapper",
        "mcp_boot_instance",
        "mcp_connection",
        "mcp_control_envelope",
        "mcp_public_contract",
        "mcp_tool_registry",
        "mcp_tool_compact",
        "migrate_jobs_to_sqlite",
        "mutation_generation",
        "multifile_refactor_autofix",
        "node_plan_validate",
        "index_staleness",
        "on_active_project_changed",
        "phase_tool_router",
        "plan_consistency",
        "plan_graph",
        "plan_slice_state",
        "project_switch_invalidate",
        "prompt_history",
        "profile_ab_harness",
        "query_rag",
        "rag_delivery",
        "rag_search",
        "rag_semantic",
        "refactor_plan",
        "review_claim_validate",
        "read_query_history",
        "reject_failure_memory",
        "rag_context",
        "reconcile_jobs",
        "route_recovery_policy",
        "runtime_debug_session",
        "run_9b_regression_gate",
        "run_eval_harness",
        "run_eval_regression",
        "smoke_cinematic_analysis",
        "synthesis_readiness",
        "semantic_refactor_guard",
        "semantic_ambiguity",
        "state_root",
        "task_api",
        "task_autonomy_supervisor",
        "task_continuation_state",
        "task_continuity",
        "task_gate_history",
        "task_phase",
        "test_unreal_readiness_fixture",
        "test_validate_on_write",
        "token_budget",
        "tool_discovery",
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

LEGACY_CONFIGS = frozenset(
    {
        "control_protocol_spec.json",
        "control_state_machine.json",
        "synthesis_readiness_policy.json",
        "task_route_recovery_policy.json",
        "tool_orchestration.json",
        "continue_continuerc.json",
        "rag_eval_agent_harness_cases.json",
        "rag_eval_reasoning_cases.example.json",
        "rag_eval_reasoning_cases.json",
        "rag_eval_architecture_cases.json",
        "rag_eval_e2e_compile_cases.json",
    }
)

ARCHIVED_REPO_ONLY_PATHS = (
    "config/rag_eval_e2e_compile_cases.json",
    "docs/Evaluation_Claim_Guardrail.md",
    "docs/Evaluation_Risk_Register.md",
    "docs/Live_Test_Improvement_Plan.md",
    "docs/Mac_Remote_Setup.md",
    "scripts/asset_graph_lookup.py",
    "scripts/asset_hint_resolver.py",
    "scripts/bootstrap_local_holdout.py",
    "scripts/profile_ab_harness.py",
    "scripts/run_dryrun_holdout.ps1",
    "scripts/run_index_pipeline.ps1",
    "scripts/run_live_holdout.ps1",
    "tests/test_asset_hint_resolver.py",
    "tests/test_bootstrap_local_holdout.py",
    "tests/test_filter_project_strict.py",
)


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def test_legacy_controller_sources_are_not_production_scripts() -> None:
    remaining = sorted(
        module for module in LEGACY_RUNTIME_MODULES if (SCRIPTS / f"{module}.py").exists()
    )
    assert remaining == []


def test_production_scripts_do_not_import_archived_controller_modules() -> None:
    violations: dict[str, list[str]] = {}
    for path in sorted(SCRIPTS.rglob("*.py")):
        forbidden = sorted(_imported_roots(path) & LEGACY_RUNTIME_MODULES)
        if forbidden:
            violations[path.relative_to(ROOT).as_posix()] = forbidden
    assert violations == {}


def test_controller_configs_are_outside_the_current_config_surface() -> None:
    remaining = sorted(name for name in LEGACY_CONFIGS if (ROOT / "config" / name).exists())
    assert remaining == []


def test_repo_only_legacy_runners_and_docs_are_outside_current_surface() -> None:
    assert [relative for relative in ARCHIVED_REPO_ONLY_PATHS if (ROOT / relative).exists()] == []
    assert [
        relative
        for relative in ARCHIVED_REPO_ONLY_PATHS
        if not (ROOT / "legacy_eval" / relative).is_file()
    ] == []


def test_direct_composition_owners_stay_bounded() -> None:
    limits = {
        "project_controller.py": 220,
        "rag_refresh.py": 180,
        "active_project_sync.py": 100,
        "direct_rag_editor_stage.py": 180,
        "direct_rag_engine_collection.py": 190,
        "direct_rag_engine_tier.py": 50,
        "direct_rag_backup_restore.py": 80,
        "direct_rag_project_refresh.py": 180,
        "direct_rag_project_collection.py": 180,
        "direct_rag_project_set.py": 100,
        "direct_rag_project_merge.py": 140,
        "direct_rag_all_refresh.py": 160,
        "direct_rag_build_generation.py": 120,
        "direct_rag_public_build.py": 140,
        "direct_rag_raw_provenance.py": 160,
        "direct_rag_raw_scope.py": 60,
        "direct_rag_corpus.py": 70,
        "direct_rag_freshness.py": 170,
        "direct_rag_freshness_rows.py": 100,
        "direct_rag_generation_boundary.py": 50,
        "direct_rag_generation_identity.py": 130,
        "direct_rag_project_engine.py": 160,
        "direct_rag_project_generation.py": 60,
        "direct_rag_project_selectors.py": 110,
        "direct_rag_manifest_binding.py": 120,
        "direct_rag_index_registry.py": 190,
        "direct_rag_index_ownership.py": 100,
        "direct_rag_named_index.py": 120,
        "direct_rag_named_candidate.py": 70,
        "direct_rag_request_binding.py": 80,
        "direct_rag_shard_selection.py": 160,
        "direct_rag_unbuilt_shard.py": 110,
        "direct_rag_refresh_target.py": 60,
        "direct_rag_refresh_facts.py": 100,
        "direct_rag_refresh_lock.py": 130,
        "direct_rag_refresh_journal.py": 100,
        "direct_rag_refresh_recovery.py": 160,
        "direct_rag_refresh_transaction.py": 200,
        "direct_rag_refresh_cli.py": 60,
        "direct_rag_startup_recovery.py": 100,
        "direct_rag_server.py": 225,
        "direct_rag_build_binding.py": 70,
        "workspace_paths.py": 220,
        "portable_path_identity.py": 360,
        "workspace_config.py": 360,
        "workspace_index_paths.py": 360,
        "unreal_engine_registration.py": 360,
        "unreal_engine_discovery.py": 360,
        "unreal_engine_resolution.py": 360,
        "unreal_engine_runtime_paths.py": 360,
        "active_project_paths.py": 360,
        "editor_export_paths.py": 360,
        "editor_export_contract.py": 150,
        "editor_export_runner.py": 210,
        "editor_export_settings.py": 60,
        "editor_export_location.py": 65,
        "editor_export_project.py": 80,
        "editor_export_markers.py": 130,
        "editor_export_process.py": 165,
        "editor_export_mode.py": 100,
        "editor_capture_state.py": 130,
        "editor_metadata_catalog.py": 90,
        "editor_metadata_provenance.py": 140,
        "editor_metadata_sources.py": 120,
        "editor_metadata_identity.py": 110,
        "editor_metadata_projection.py": 120,
        "editor_metadata_search_text.py": 95,
        "editor_metadata_jsonl.py": 100,
        "editor_metadata_merge.py": 80,
        "editor_metadata_cli.py": 100,
        "sync_editor_metadata.py": 100,
        "editor_sync_context.py": 140,
        "editor_sync_capture.py": 130,
        "editor_sync_coordinator.py": 150,
        "editor_sync_cli.py": 70,
        "rag_build_classification.py": 140,
        "rag_build_input.py": 90,
        "rag_build_metadata.py": 60,
        "rag_build_metadata_projection.py": 100,
        "rag_build_outputs.py": 150,
        "rag_build_schema.py": 135,
        "rag_build_writer.py": 180,
        "workspace_locator.py": 360,
        "build_rag_index.py": 190,
        "unreal_static_validate.py": 100,
        "unreal_static_model.py": 100,
        "unreal_static_scan.py": 550,
        "unreal_static_reflection.py": 650,
        "unreal_static_delegate.py": 380,
        "unreal_static_lifecycle.py": 680,
        "unreal_static_build.py": 430,
        "unreal_static_include.py": 430,
        "unreal_static_network.py": 320,
        "unreal_static_crossfile.py": 480,
        "unreal_static_safety.py": 410,
        "unreal_static_registry.py": 330,
        "unreal_static_runner.py": 500,
    }
    counts = {
        name: len((SCRIPTS / name).read_text(encoding="utf-8").splitlines())
        for name in limits
    }
    assert {name: count for name, count in counts.items() if count > limits[name]} == {}


def test_installer_direct_rag_build_owners_stay_bounded() -> None:
    installer = ROOT / "installer"
    limits = {
        "direct_rag_build.py": 100,
        "direct_rag_build_model.py": 90,
        "direct_rag_build_scope.py": 170,
        "direct_rag_build_stage.py": 90,
        "direct_rag_build_steps.py": 140,
    }
    counts = {
        name: len((installer / name).read_text(encoding="utf-8").splitlines())
        for name in limits
    }
    assert {name: count for name, count in counts.items() if count > limits[name]} == {}


def test_workspace_path_owners_do_not_import_the_compatibility_facade() -> None:
    owners = {
        "portable_path_identity.py",
        "workspace_config.py",
        "workspace_index_paths.py",
        "unreal_engine_registration.py",
        "unreal_engine_discovery.py",
        "unreal_engine_resolution.py",
        "unreal_engine_runtime_paths.py",
        "active_project_paths.py",
        "editor_export_paths.py",
        "workspace_locator.py",
    }
    assert {
        name: sorted(_imported_roots(SCRIPTS / name) & {"workspace_paths"})
        for name in owners
        if "workspace_paths" in _imported_roots(SCRIPTS / name)
    } == {}


def test_legacy_compaction_god_object_is_not_a_production_script() -> None:
    assert not (SCRIPTS / "mcp_tool_compact.py").exists()
