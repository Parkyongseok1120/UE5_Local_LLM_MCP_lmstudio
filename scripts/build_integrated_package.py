#!/usr/bin/env python3
"""Build a relocatable, cross-platform integrated installer package (allowlist)."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_ROOT = "Evidence-First-Integrated"

# Only these top-level directories may enter a portable package.
ALLOWED_TOP_LEVEL_DIRS = frozenset(
    {
        "config",
        "docs",
        "Game_Design_Docs",
        "installer",
        "lmstudio-context-compactor-plugin",
        "lmstudio-unreal-agent-mcp",
        "mcp-tools",
        "prompts",
        "RAG_Project_Guidelines",
        "scripts",
        "skills",
        "tools",
    }
)

# Only these root files may enter a portable package.
ALLOWED_ROOT_FILES = frozenset(
    {
        "CONTRIBUTING.md",
        "EPIC_NOTICE.md",
        "INSTALL.bat",
        "LICENSE",
        ".clinerules",
        "README.portable.ko.md",
        "README.portable.md",
        "SECURITY.md",
        "install.py",
        "install.sh",
        "requirements.txt",
    }
)

ANY_DIR_EXCLUDES = frozenset({".agent", "__pycache__", "node_modules", "dist", "release_evidence"})
LOCAL_CONFIG_NAMES = frozenset(
    {
        "agent-mcp.json",
        "cline-workspace.json",
        "lmstudio-mcp-unreal-agent.json",
        "lmstudio_mcp_unreal_rag.json",
        "unreal-workspace.json",
        "workspace.json",
        "workspace.local.json",
    }
)

PORTABLE_LEGACY_RUNTIME_FILES = frozenset(
    {
        "config/control_protocol_spec.json",
        "config/control_state_machine.json",
        "config/rag_eval_architecture_cases.json",
        "config/rag_eval_e2e_compile_cases.json",
        "config/strict_tool_manifest.json",
        "config/synthesis_readiness_policy.json",
        "config/task_route_recovery_policy.json",
        "config/tool_orchestration.json",
        "scripts/agent_orchestrator.py",
        "scripts/agent_run_report.py",
        "scripts/architecture_claim_validate.py",
        "scripts/architecture_decision.py",
        "scripts/architecture_map.py",
        "scripts/architecture_portfolio.py",
        "scripts/architecture_proposal_store.py",
        "scripts/architecture_reasoning.py",
        "scripts/architecture_state.py",
        "scripts/asset_graph_lookup.py",
        "scripts/asset_hint_resolver.py",
        "scripts/bootstrap_local_holdout.py",
        "scripts/build_symbol_graph.py",
        "scripts/change_impact_contract.py",
        "scripts/clangd_helper.py",
        "scripts/code_generation_contract.py",
        "scripts/code_sketch_claim_validate.py",
        "scripts/code_sketch_pipeline.py",
        "scripts/control_runtime_identity.py",
        "scripts/control_protocol_spec.py",
        "scripts/control_state_registry.py",
        "scripts/control_transition_bridge.py",
        "scripts/direct_model_mode.py",
        "scripts/domain_planner.py",
        "scripts/evaluate_refactor_plans.py",
        "scripts/feature_intent_contract.py",
        "scripts/feature_intent_fast_path.py",
        "scripts/job_store.py",
        "scripts/mcp_control_envelope.py",
        "scripts/multifile_refactor_autofix.py",
        "scripts/node_plan_validate.py",
        "scripts/phase_tool_router.py",
        "scripts/plan_graph.py",
        "scripts/plan_slice_state.py",
        "scripts/profile_ab_harness.py",
        "scripts/rag_delivery.py",
        "scripts/rag_search.py",
        "scripts/rag_semantic.py",
        "scripts/read_query_history.py",
        "scripts/refactor_plan.py",
        "scripts/route_recovery_policy.py",
        "scripts/runtime_debug_session.py",
        "scripts/run_dryrun_holdout.ps1",
        "scripts/run_index_pipeline.ps1",
        "scripts/run_live_holdout.ps1",
        "scripts/semantic_refactor_guard.py",
        "scripts/state_root.py",
        "scripts/synthesis_readiness.py",
        "scripts/task_api.py",
        "scripts/task_autonomy_supervisor.py",
        "scripts/task_continuation_state.py",
        "scripts/task_continuity.py",
        "scripts/task_gate_history.py",
        "scripts/task_phase.py",
        "scripts/token_budget.py",
        "scripts/unreal_rag_mcp.py",
        "scripts/wrapper_job_manager.py",
        "lmstudio-unreal-agent-mcp/src/edit-bundle.js",
        "lmstudio-unreal-agent-mcp/src/mutation-generation.js",
        "lmstudio-unreal-agent-mcp/src/resolve-recovery-journal-cli.js",
        "lmstudio-unreal-agent-mcp/src/state-root.js",
        "lmstudio-unreal-agent-mcp/src/transaction-journal.js",
        "lmstudio-unreal-agent-mcp/src/validate-write.js",
        "lmstudio-unreal-agent-mcp/src/validation-dirty.js",
    }
)

# Development marathon / personal campaign / debug runners excluded even under scripts/.
SCRIPTS_NAME_DENY = re.compile(
    r"(?ix)^("
    r"local_ai_.*"
    r"|omock_.*"
    r"|run_omock_.*"
    r"|supervisor_local_ai_.*"
    r"|lmstudio_e2e_.*"
    r"|lmstudio_marathon_.*"
    r"|stage_campaign_marathon.*"
    r"|stage_campaign_(report|state)\.json$"
    r"|mcp_.*_(report|audit|aggregate)\.json$"
    r"|mcp_stale_task_quarantine_report\.json$"
    r"|.*_session\.json$"
    r"|.*\.out\.log$"
    r"|.*\.runner\.log$"
    r"|.*\.shell\.log$"
    r"|MIDPOINT_.*"
    r"|STAGE3_7_.*"
    r"|INFRA_STALE_.*"
    r"|_tmp_.*"
    r")$"
)

# Path-wide denylist (posix relative paths). Use single-backslash escapes in raw
# strings so "\.out\.log" matches a literal ".out.log" suffix — not "\\." (backslash + any char).
FORBIDDEN_PACKAGE_MARKERS = re.compile(
    r"(?ix)("
    r"(^|/)local_ai_"
    r"|(^|/)omock_"
    r"|_session\.json$"
    r"|\.out\.log$"
    r"|\.runner\.log$"
    r"|\.shell\.log$"
    r"|stage_campaign_marathon"
    r"|marathon17"
    r")"
)

REQUIRED_RUNTIME_FILES = (
    "config/retrieval_profiles.json",
    "config/stable_tool_manifest.json",
    "installer/bootstrap_python.ps1",
    "installer/bootstrap_python.sh",
        "prompts/lmstudio_direct_model_system.md",
        "prompts/cline_unreal_agent_system.md",
    "scripts/unreal_rag_direct.py",
    "scripts/direct_rag_status.py",
    "scripts/direct_rag_evidence.py",
    "scripts/direct_rag_freshness.py",
    "scripts/direct_rag_freshness_rows.py",
    "scripts/direct_rag_generation_boundary.py",
    "scripts/direct_rag_generation_identity.py",
    "scripts/direct_rag_generation_swap.py",
    "scripts/direct_rag_all_refresh.py",
    "scripts/direct_rag_build_generation.py",
    "scripts/direct_rag_public_build.py",
    "scripts/direct_rag_raw_provenance.py",
    "scripts/direct_rag_raw_scope.py",
    "scripts/direct_rag_project_engine.py",
    "scripts/direct_rag_project_generation.py",
    "scripts/direct_rag_project_selectors.py",
    "scripts/direct_rag_manifest_binding.py",
    "scripts/direct_rag_index_registry.py",
    "scripts/direct_rag_index_ownership.py",
    "scripts/direct_rag_named_index.py",
    "scripts/direct_rag_named_candidate.py",
    "scripts/direct_rag_request_binding.py",
    "scripts/direct_rag_shard_selection.py",
    "scripts/direct_rag_unbuilt_shard.py",
    "scripts/direct_rag_refresh_target.py",
    "scripts/direct_rag_refresh_facts.py",
    "scripts/direct_rag_contract.py",
    "scripts/direct_rag_corpus.py",
    "scripts/direct_rag_delivery.py",
    "scripts/direct_rag_atomic_replace.py",
    "scripts/direct_rag_backup_restore.py",
    "scripts/direct_rag_history.py",
    "scripts/direct_rag_index.py",
    "scripts/direct_rag_lexical.py",
    "scripts/direct_rag_limits.py",
    "scripts/direct_rag_projects.py",
    "scripts/direct_rag_project_cache.py",
    "scripts/direct_rag_probe.py",
    "scripts/direct_rag_result.py",
    "scripts/direct_rag_retrieval.py",
    "scripts/direct_rag_selection.py",
    "scripts/direct_rag_runtime.py",
    "scripts/direct_rag_search.py",
    "scripts/direct_rag_server.py",
    "scripts/direct_rag_sql.py",
    "scripts/direct_rag_readonly_db.py",
    "scripts/direct_rag_symbol.py",
    "scripts/direct_rag_symbol_query.py",
    "scripts/mcp_stdio.py",
    "scripts/active_project_sync.py",
    "scripts/direct_rag_editor_stage.py",
    "scripts/direct_rag_editor_snapshot.py",
    "scripts/direct_rag_engine_collection.py",
    "scripts/direct_rag_engine_tier.py",
    "scripts/direct_rag_project_refresh.py",
    "scripts/direct_rag_project_collection.py",
    "scripts/direct_rag_project_merge.py",
    "scripts/direct_rag_editor_legacy.py",
    "scripts/direct_rag_symbol_legacy.py",
    "scripts/direct_rag_project_set.py",
    "scripts/direct_rag_refresh_lock.py",
    "scripts/direct_rag_refresh_journal.py",
    "scripts/direct_rag_refresh_recovery.py",
    "scripts/direct_rag_refresh_transaction.py",
    "scripts/direct_rag_refresh_cli.py",
    "scripts/direct_rag_startup_recovery.py",
    "scripts/atomic_io.py",
    "scripts/asset_taxonomy.py",
    "scripts/blueprint_graph_format.py",
    "scripts/build_rag_index.py",
    "scripts/rag_build_classification.py",
    "scripts/rag_build_input.py",
    "scripts/rag_build_metadata.py",
    "scripts/rag_build_metadata_projection.py",
    "scripts/rag_build_outputs.py",
    "scripts/rag_build_schema.py",
    "scripts/rag_build_writer.py",
    "scripts/collect_editor_metadata.py",
    "scripts/editor_metadata_identity.py",
    "scripts/editor_metadata_projection.py",
    "scripts/editor_metadata_search_text.py",
    "scripts/editor_metadata_jsonl.py",
    "scripts/editor_metadata_merge.py",
    "scripts/editor_metadata_cli.py",
    "scripts/collect_game_design_docs.py",
    "scripts/collect_project_guidelines.py",
    "scripts/collect_project_architecture.py",
    "scripts/collect_unreal_project_profile.py",
    "scripts/collect_unreal_projects.py",
    "scripts/collect_unreal_source.py",
    "scripts/collect_unreal_symbols.py",
    "scripts/editor_export_runner.py",
    "scripts/editor_export_settings.py",
    "scripts/editor_export_location.py",
    "scripts/editor_export_project.py",
    "scripts/editor_export_markers.py",
    "scripts/editor_export_process.py",
    "scripts/editor_export_mode.py",
    "scripts/editor_export_contract.py",
    "scripts/editor_capture_state.py",
    "scripts/editor_metadata_catalog.py",
    "scripts/editor_metadata_provenance.py",
    "scripts/editor_metadata_status.py",
    "scripts/editor_metadata_sources.py",
    "scripts/domain_validation_context.py",
    "scripts/domain_validators.py",
    "scripts/include_resolver.py",
    "scripts/incremental_build.py",
    "scripts/direct_rag_build_binding.py",
    "scripts/index_inputs.py",
    "scripts/ingest_editor_exports.py",
    "scripts/material_graph_format.py",
    "scripts/project_controller.py",
    "scripts/portable_rag.ps1",
    "scripts/project_context.py",
    "scripts/plugin_project_context.py",
    "scripts/project_routing.py",
    "scripts/rag_embeddings.py",
    "scripts/rag_index_ops.py",
    "scripts/rag_modes.py",
    "scripts/rag_refresh.py",
    "scripts/rag_types.py",
    "scripts/retrieval_profiles.py",
    "scripts/structured_metadata_format.py",
    "scripts/symbol_graph.py",
    "scripts/sync_editor_metadata.py",
    "scripts/editor_sync_context.py",
    "scripts/editor_sync_capture.py",
    "scripts/editor_sync_coordinator.py",
    "scripts/editor_sync_cli.py",
    "scripts/target_resolver.py",
    "scripts/active_project_paths.py",
    "scripts/editor_export_paths.py",
    "scripts/editor_sync_decision.py",
    "scripts/portable_path_identity.py",
    "scripts/unreal_engine_discovery.py",
    "scripts/unreal_engine_registration.py",
    "scripts/unreal_engine_resolution.py",
    "scripts/unreal_engine_runtime_paths.py",
    "scripts/workspace_config.py",
    "scripts/workspace_index_paths.py",
    "scripts/workspace_locator.py",
    "scripts/workspace_paths.py",
    "scripts/runtime_config_checklist.py",
    "scripts/validate_project_sources.py",
    "scripts/unreal_static_validate.py",
    "scripts/unreal_static_model.py",
    "scripts/unreal_static_scan.py",
    "scripts/unreal_static_reflection.py",
    "scripts/unreal_static_delegate.py",
    "scripts/unreal_static_lifecycle.py",
    "scripts/unreal_static_build.py",
    "scripts/unreal_static_include.py",
    "scripts/unreal_static_network.py",
    "scripts/unreal_static_crossfile.py",
    "scripts/unreal_static_safety.py",
    "scripts/unreal_static_registry.py",
    "scripts/unreal_static_runner.py",
    "scripts/cpp_parse_utils.py",
    "scripts/parse_build_cs.py",
    "scripts/ue_cpp_signatures.py",
    "scripts/mutation_semantic_guard.py",
    "scripts/unreal_api_denylist.py",
    "scripts/unreal_source_extensions.py",
    "installer/direct_rag_build.py",
    "installer/direct_rag_build_model.py",
    "installer/direct_rag_build_scope.py",
    "installer/direct_rag_build_stage.py",
    "installer/direct_rag_build_steps.py",
    "installer/lmstudio_plugin_install.py",
    "installer/unreal_engine_binding.py",
    "scripts/installer_support/Install-PathHelpers.ps1",
    "lmstudio-unreal-agent-mcp/package.json",
    "lmstudio-unreal-agent-mcp/package-lock.json",
    "lmstudio-unreal-agent-mcp/src/direct-server.js",
    "lmstudio-unreal-agent-mcp/src/direct-tool-catalog.js",
    "lmstudio-unreal-agent-mcp/src/direct-transaction-recovery.js",
    "lmstudio-unreal-agent-mcp/src/direct-transaction-store.js",
    "lmstudio-unreal-agent-mcp/src/direct-runtime-shared.js",
    "lmstudio-unreal-agent-mcp/src/direct-runtime-context.js",
    "lmstudio-unreal-agent-mcp/src/direct-static-validation.js",
    "lmstudio-unreal-agent-mcp/src/direct-bundle-capability.js",
    "lmstudio-unreal-agent-mcp/src/direct-delete-capabilities.js",
    "lmstudio-unreal-agent-mcp/src/direct-edit-bundle.js",
    "lmstudio-unreal-agent-mcp/src/direct-edit-bundle-commit.js",
    "lmstudio-unreal-agent-mcp/src/direct-edit-bundle-plan.js",
    "lmstudio-unreal-agent-mcp/src/direct-edit-bundle-preflight.js",
    "lmstudio-unreal-agent-mcp/src/direct-file-version-policy.js",
    "lmstudio-unreal-agent-mcp/src/direct-read-snapshot.js",
    "lmstudio-unreal-agent-mcp/src/file-snapshot-registry.js",
    "lmstudio-unreal-agent-mcp/src/direct-file-mutation-capabilities.js",
    "lmstudio-unreal-agent-mcp/src/direct-file-snapshot.js",
    "lmstudio-unreal-agent-mcp/src/direct-project-capabilities.js",
    "lmstudio-unreal-agent-mcp/src/direct-read-capabilities.js",
    "lmstudio-unreal-agent-mcp/src/direct-log-capabilities.js",
    "lmstudio-unreal-agent-mcp/src/direct-mutation-capabilities.js",
    "lmstudio-unreal-agent-mcp/src/direct-mutation-limits.js",
    "lmstudio-unreal-agent-mcp/src/direct-mutation-scope.js",
    "lmstudio-unreal-agent-mcp/src/direct-diagnostic-capabilities.js",
    "lmstudio-unreal-agent-mcp/src/direct-response.js",
    "lmstudio-unreal-agent-mcp/src/direct-repeat-cache.js",
    "lmstudio-unreal-agent-mcp/src/strict-server.js",
    "lmstudio-unreal-agent-mcp/src/strict-lifecycle.js",
    "lmstudio-unreal-agent-mcp/src/strict-session-domain.js",
    "lmstudio-unreal-agent-mcp/src/strict-session-store.js",
    "lmstudio-unreal-agent-mcp/src/strict-project-binding.js",
    "lmstudio-unreal-agent-mcp/src/atomic-io.js",
    "lmstudio-unreal-agent-mcp/src/automation-executor.js",
    "lmstudio-unreal-agent-mcp/src/automation-command-contract.js",
    "lmstudio-unreal-agent-mcp/src/automation-output-parser.js",
    "lmstudio-unreal-agent-mcp/src/automation-process-runner.js",
    "lmstudio-unreal-agent-mcp/src/automation-source-discovery.js",
    "lmstudio-unreal-agent-mcp/src/automation-source-parser.js",
    "lmstudio-unreal-agent-mcp/src/bounded-read.js",
    "lmstudio-unreal-agent-mcp/src/build-executor.js",
    "lmstudio-unreal-agent-mcp/src/bounded-process-runner.js",
    "lmstudio-unreal-agent-mcp/src/process-output-decoder.js",
    "lmstudio-unreal-agent-mcp/src/process-tree-termination.js",
    "lmstudio-unreal-agent-mcp/src/build-proof.js",
    "lmstudio-unreal-agent-mcp/src/filesystem-path-identity.js",
    "lmstudio-unreal-agent-mcp/src/command-policy.js",
    "lmstudio-unreal-agent-mcp/src/direct-build-response.js",
    "lmstudio-unreal-agent-mcp/src/python-executable.js",
    "lmstudio-unreal-agent-mcp/src/mutation-semantic-guard.js",
    "lmstudio-unreal-agent-mcp/src/read-path-resolver.js",
    "lmstudio-unreal-agent-mcp/src/runtime-state-root.js",
    "lmstudio-unreal-agent-mcp/src/safe-write.js",
    "lmstudio-unreal-agent-mcp/src/unreal-active-project.js",
    "lmstudio-unreal-agent-mcp/src/unreal-browse-metadata.js",
    "lmstudio-unreal-agent-mcp/src/unreal-build-plan.js",
    "lmstudio-unreal-agent-mcp/src/unreal-config.js",
    "lmstudio-unreal-agent-mcp/src/unreal-detect.js",
    "lmstudio-unreal-agent-mcp/src/unreal-engine-core.js",
    "lmstudio-unreal-agent-mcp/src/unreal-engine-registry.js",
    "lmstudio-unreal-agent-mcp/src/unreal-engine-resolution.js",
    "lmstudio-unreal-agent-mcp/src/unreal-project-core.js",
    "lmstudio-unreal-agent-mcp/src/unreal-project-discovery.js",
    "lmstudio-unreal-agent-mcp/src/unreal-project-name-selection.js",
    "lmstudio-unreal-agent-mcp/src/unreal-project-selection.js",
    "lmstudio-unreal-agent-mcp/src/write-guards.js",
    "lmstudio-unreal-agent-mcp/src/write-locks.js",
    "lmstudio-unreal-agent-mcp/src/write-lock-reclaim-bridge.py",
    "lmstudio-context-compactor-plugin/src/index.ts",
    "lmstudio-context-compactor-plugin/src/prediction-loop.ts",
    "lmstudio-context-compactor-plugin/src/direct-compaction-core.js",
    "lmstudio-context-compactor-plugin/src/compaction-tool-memory.js",
    "lmstudio-context-compactor-plugin/src/continuity-file-observations.js",
    "lmstudio-context-compactor-plugin/src/continuity-memory.js",
    "lmstudio-context-compactor-plugin/src/continuity-objectives.js",
    "lmstudio-context-compactor-plugin/src/continuity-text.js",
    "lmstudio-context-compactor-plugin/src/durable-memory-sanitizer.js",
    "lmstudio-context-compactor-plugin/src/direct-config.ts",
    "lmstudio-context-compactor-plugin/.lmstudio/entry.ts",
    "lmstudio-context-compactor-plugin/manifest.json",
    "lmstudio-context-compactor-plugin/package-lock.json",
    "lmstudio-context-compactor-plugin/package.json",
    "lmstudio-context-compactor-plugin/scripts/clean-dist.cjs",
    "lmstudio-context-compactor-plugin/test/direct-compaction-core.test.js",
    "lmstudio-context-compactor-plugin/test/durable-memory-sanitizer.test.js",
    "lmstudio-context-compactor-plugin/test/fixtures/qwen-direct-e2e-continuity.json",
    "lmstudio-context-compactor-plugin/test/fixtures/qwen-receipt-path-confusion.json",
    "lmstudio-context-compactor-plugin/test/prediction-loop.test.cjs",
    "lmstudio-context-compactor-plugin/test/status.test.cjs",
    "lmstudio-context-compactor-plugin/tsconfig.json",
)

# Portable builds are an explicitly reviewed product surface, not a mirror of
# the development repository.  Runtime code belongs in REQUIRED_RUNTIME_FILES;
# this set adds only installer metadata and current Direct user documentation.
PORTABLE_CONTENT_FILES = frozenset(
    {
        "config/cline_mcp_settings.template.json",
        "config/unreal_asset_taxonomy.json",
        "config/workspace.example.json",
        "config/workspace.json.template",
        "docs/ARCHITECTURE.md",
        "docs/Blueprint_Metadata.md",
        "docs/Build_Cs_Parser.md",
        "docs/Cline_Rider_Unreal_Agent_Setup.md",
        "docs/Editor_Metadata_Export.md",
        "docs/Indexing_Tiers.md",
        "docs/Integrated_Installer.md",
        "docs/LMStudio_MCP_Tool_Discipline.md",
        "docs/LMStudio_Unreal_Agent_Setup.md",
        "docs/Project_Routing.md",
        "docs/RAG_Setup.md",
        "docs/Release_Notes_1_3_1.md",
        "docs/Rider_Cline_Smoke_Checklist.md",
        "docs/Safe_Agent_Mode.md",
        "docs/Troubleshooting.md",
        "docs/VERSIONING.md",
        "installer/README.md",
        "installer/__init__.py",
        "installer/bootstrap_runtimes.py",
        "installer/manifest.json",
        "installer/runtime-manifest.json",
        "lmstudio-context-compactor-plugin/README.md",
        "lmstudio-context-compactor-plugin/scripts/status.cjs",
        "lmstudio-unreal-agent-mcp/README.md",
        "lmstudio-unreal-agent-mcp/config/agent-mcp.json.template",
        "lmstudio-unreal-agent-mcp/config/lmstudio-mcp-unreal-agent.json.template",
        "lmstudio-unreal-agent-mcp/lmstudio-mcp-config-example.json",
    }
)

# These directories are data/assets consumed by the explicitly allowed Direct
# runtime or the installable evidence-first skill.  No controller/eval source
# directory is admitted by prefix.
PORTABLE_CONTENT_PREFIXES = (
    "RAG_Project_Guidelines/",
    "skills/evidence-first-code-audit/",
    "tools/ue_export/",
    "tools/ue_plugins/LmStudioGraphExporter/",
)

PORTABLE_FILE_ALLOWLIST = frozenset(REQUIRED_RUNTIME_FILES) | PORTABLE_CONTENT_FILES

# The portable RAG entry points dot-source this cross-platform path/index
# resolver. Keep the rest of installer_support out of the release bundle.
PORTABLE_INSTALLER_SUPPORT_FILES = frozenset(
    {"scripts/installer_support/Install-PathHelpers.ps1"}
)

# Absolute home-path shapes across Windows / macOS / Linux.
_WIN_USERS_BS = "C:" + "\\" + "Users" + "\\"
_WIN_USERS_FS = "C:" + "/" + "Users" + "/"
_UNIX_USERS = "/" + "Users" + "/"
_UNIX_HOME = "/" + "home" + "/"
PRIVATE_PATH_RE = re.compile(
    rf"(?ix)("
    rf"{re.escape(_WIN_USERS_BS)}(?!Public\\)[A-Za-z][^\\\s\"'`<>]*"
    rf"|{re.escape(_WIN_USERS_FS)}(?!Public/)[A-Za-z][^/\s\"'`<>]*"
    rf"|{re.escape(_UNIX_USERS)}(?!Shared(?:/|\b))[A-Za-z][^/\s\"'`<>]*"
    rf"|{re.escape(_UNIX_HOME)}[A-Za-z][^/\s\"'`<>]*"
    rf")"
)

FORBIDDEN_INVENTORY_RE = re.compile(
    r"(?ix)("
    r"(^|/)local_ai_"
    r"|(^|/)omock_"
    r"|_session\.json$"
    r"|\.out\.log$"
    r"|\.runner\.log$"
    r"|\.shell\.log$"
    r"|stage_campaign_marathon"
    r"|supervisor_local_ai"
    r"|lmstudio_e2e_driver"
    r"|(^|/)MIDPOINT_AUDIT_"
    r"|(^|/)STAGE3_7_"
    r")"
)


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _validate_destination(path: Path, source: Path) -> Path:
    resolved = path.expanduser().resolve()
    source = source.resolve()
    if resolved == source or _within(resolved, source) or _within(source, resolved):
        raise ValueError(f"package destination must be disjoint from source: {resolved}")
    anchor = Path(resolved.anchor)
    if resolved == anchor:
        raise ValueError(f"refusing to use a filesystem root: {resolved}")
    return resolved


def _included_index_relative(source: Path) -> Path:
    """Return the configured source-local index path for --include-index."""

    scripts_dir = str(Path(__file__).resolve().parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    from workspace_paths import resolve_index_path_in_workspace

    index = resolve_index_path_in_workspace(source)
    try:
        return index.relative_to(source.resolve())
    except ValueError as exc:
        raise ValueError(
            "--include-index requires workspace indexPath to remain under the package source"
        ) from exc


def _include(
    relative: Path,
    *,
    include_index: bool,
    index_relative: Path | None = None,
) -> bool:
    parts = relative.parts
    if not parts:
        return False

    if len(parts) == 1:
        return relative.name in ALLOWED_ROOT_FILES

    if parts[0] not in ALLOWED_TOP_LEVEL_DIRS:
        if include_index and index_relative is not None and relative == index_relative:
            return True
        return False

    if any(part in ANY_DIR_EXCLUDES for part in parts):
        return False
    if relative.as_posix() in PORTABLE_LEGACY_RUNTIME_FILES:
        return False
    if relative.name in LOCAL_CONFIG_NAMES:
        return False

    lower = relative.name.lower()
    if lower.endswith((".pyc", ".pyo", ".log", ".tmp", ".bak")) or ".bak-" in lower:
        return False
    if lower.endswith((".sqlite", ".sqlite3", ".db")) and not (
        include_index and index_relative is not None and relative == index_relative
    ):
        return False

    if parts[0] == "scripts" and SCRIPTS_NAME_DENY.match(relative.name):
        return False
    if FORBIDDEN_PACKAGE_MARKERS.search(relative.as_posix()):
        return False

    # Keep only the reviewed installer path/config merge helper.
    if parts[:2] == ("scripts", "installer_support"):
        return relative.as_posix() in PORTABLE_INSTALLER_SUPPORT_FILES

    portable_path = relative.as_posix()
    if portable_path in PORTABLE_FILE_ALLOWLIST:
        return True
    return any(portable_path.startswith(prefix) for prefix in PORTABLE_CONTENT_PREFIXES)


def _source_files(source: Path, *, include_index: bool) -> Iterable[tuple[Path, Path]]:
    """Prefer git-tracked files so ignored local overlays never enter the ZIP."""
    index_relative = _included_index_relative(source) if include_index else None
    selected: list[tuple[Path, Path]] = []
    tracked: list[str] = []
    try:
        tracked = subprocess.check_output(
            ["git", "-C", str(source), "ls-files", "-z"],
            text=False,
        ).split(b"\0")
        tracked_paths = [Path(item.decode("utf-8")) for item in tracked if item]
    except (OSError, subprocess.CalledProcessError, UnicodeDecodeError):
        tracked_paths = []

    if tracked_paths:
        selected_relatives: set[str] = set()
        for relative in sorted(tracked_paths, key=lambda item: item.as_posix().lower()):
            if not _include(relative, include_index=include_index, index_relative=index_relative):
                continue
            path = source / relative
            if not path.is_file():
                continue
            if path.is_symlink():
                raise ValueError(f"symlinks are not allowed in portable packages: {relative}")
            selected.append((path, relative))
            selected_relatives.add(relative.as_posix())
        # Explicit release-critical files may be new in the current release
        # candidate before the eventual commit is created. Never widen this to
        # arbitrary untracked files.
        for required in REQUIRED_RUNTIME_FILES:
            if required in selected_relatives:
                continue
            relative = Path(required)
            path = source / relative
            if path.is_file() and _include(
                relative,
                include_index=include_index,
                index_relative=index_relative,
            ):
                if path.is_symlink():
                    raise ValueError(
                        f"symlinks are not allowed in portable packages: {relative}"
                    )
                selected.append((path, relative))
                selected_relatives.add(required)
        if include_index:
            assert index_relative is not None
            index_path = source / index_relative
            if index_path.is_file() and _include(
                index_relative,
                include_index=True,
                index_relative=index_relative,
            ):
                selected.append((index_path, index_relative))
        yield from sorted(selected, key=lambda item: item[1].as_posix().lower())
        return

    for directory, dirnames, filenames in os.walk(source, topdown=True, followlinks=False):
        directory_path = Path(directory)
        relative_directory = directory_path.relative_to(source)
        kept_dirs: list[str] = []
        for name in dirnames:
            candidate = relative_directory / name
            parts = candidate.parts
            if not parts:
                continue
            if parts[0] not in ALLOWED_TOP_LEVEL_DIRS and not (
                include_index and index_relative is not None and parts[0] == index_relative.parts[0]
            ):
                continue
            if name in ANY_DIR_EXCLUDES:
                continue
            if parts[:2] == ("scripts", "installer_support") and not any(
                required.startswith(candidate.as_posix() + "/")
                for required in PORTABLE_INSTALLER_SUPPORT_FILES
            ):
                continue
            path = directory_path / name
            if path.is_symlink():
                raise ValueError(f"symlinks are not allowed in portable packages: {candidate}")
            kept_dirs.append(name)
        dirnames[:] = sorted(kept_dirs, key=str.lower)
        for name in sorted(filenames, key=str.lower):
            path = directory_path / name
            relative = path.relative_to(source)
            if not _include(relative, include_index=include_index, index_relative=index_relative):
                continue
            if path.is_symlink():
                raise ValueError(f"symlinks are not allowed in portable packages: {relative}")
            selected.append((path, relative))
    yield from sorted(selected, key=lambda item: item[1].as_posix().lower())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_launchers(staging: Path) -> None:
    shutil.copy2(ROOT / "INSTALL.bat", staging / "INSTALL.bat")
    shutil.copy2(ROOT / "scripts" / "portable_rag.ps1", staging / "rag.ps1")
    for source_name, target_name in (
        ("README.portable.md", "README.md"),
        ("README.portable.ko.md", "README.ko.md"),
    ):
        shutil.copy2(ROOT / source_name, staging / target_name)
        staged_template = staging / source_name
        if staged_template.exists():
            staged_template.unlink()
    # A Windows checkout may materialize tracked shell files with CRLF.
    # Normalize both POSIX bootstrap stages before the ZIP moves to Linux or
    # macOS, regardless of the packaging host.
    for relative in ("install.sh", "installer/bootstrap_python.sh"):
        target = staging / relative
        source = (ROOT / relative).read_bytes()
        target.write_bytes(source.replace(b"\r\n", b"\n").replace(b"\r", b"\n"))
        target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    (staging / "PORTABLE-INSTALL.md").write_text(
        "# Integrated portable installer\n\n"
        "## Prerequisites\n\n"
        "- `INSTALL.bat` / `install.sh` use Python 3.10+ when available. On a clean supported "
        "host they automatically download SHA-256-verified uv, install managed Python 3.12 in "
        "the selected user state-home, and continue without a system-wide Python install. "
        "Direct `python3 install.py` use still requires host Python 3.10+.\n"
        "- Node.js 20+/npm is downloaded only for Unreal/context components. PowerShell 7 "
        "(`pwsh`) is only for optional manual `rag.ps1` maintenance. Runtime archives are pinned by SHA-256 and safely "
        "extracted for the host CPU architecture (arm64/x64).\n"
        "- Context-compactor installation requires the LM Studio `lms` CLI. The plugin is installed "
        "for availability but never chat-activated by the installer; verify the host-owned toggle is OFF.\n\n"
        "## Host support\n\n"
        "- **Windows**: supported for LM Studio and Unreal-integrated profiles.\n"
        "- **Ubuntu 22.04/24.04 with glibc**: supported; musl/Alpine is not.\n"
        "- **Apple Silicon macOS**: physical FULL install verified on darwin-arm64 "
        "(runtimes, context compactor, LM Studio plugin installation/pinning, Unreal auto-detect, "
        "full RAG, evidence-first MCP smoke). Signing/notarization is not claimed; "
        "see docs/Release_Notes_1_3_1.md for the release boundary.\n"
        "- **Intel macOS (x86_64)**: LM Studio is not supported by LM Studio upstream. "
        "LM Studio / Unreal / context-compactor installs abort early. "
        "Custom Codex / portable-rule / Cline-only installs remain allowed.\n"
        "- **Windows**: automated fixture/CI installer and Direct MCP paths are exercised. "
        "A prior native LM Studio GUI session observed RAG/MCP tool use and a real UBT "
        "invocation. A clean-machine physical installer lifecycle and universal "
        "project/engine/plugin coverage are not claimed.\n\n"
        "## Launch\n\n"
        "- Windows: `INSTALL.bat`\n"
        "- Ubuntu Linux and Apple Silicon macOS: `./install.sh`\n\n"
        "The installer asks for SAFE, STANDARD, FULL, or CUSTOM. All profiles remain "
        "read-only unless agent mode and its separate risk acknowledgement are both supplied.\n"
        "Run `python3 install.py --help` for automation flags. Generated indexes and machine "
        "configuration are not bundled by default. Installer RAG indexing uses managed Python directly; "
        "custom Unreal installs can be supplied with `--engine-root` or `UNREAL_ENGINE_ROOT`.\n\n"
        "## Portable RAG maintenance\n\n"
        "The packaged `rag.ps1` is intentionally limited to factual collection, index build, "
        "Direct project selection, synchronous refresh, and health commands. It contains no "
        "planner, wrapper, evaluation, task, or route controller. `refresh` defaults to "
        "`project_source`; Editor launch requires both an Editor scope and the explicit "
        "`-AllowEditorLaunch` switch. Run "
        "`Get-Help ./scripts/portable_rag.ps1 -Detailed` for parameters.\n",
        encoding="utf-8",
    )


def _source_git_commit(source: Path) -> str:
    explicit = str(os.environ.get("CONTROL_RUNTIME_GIT_COMMIT") or "").strip()
    if explicit:
        return explicit[:80]
    try:
        git_root = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=source,
            capture_output=True,
            text=True,
            check=False,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        git_root = None
    if (
        git_root is not None
        and git_root.returncode == 0
        and Path(git_root.stdout.strip()).resolve() == source.resolve()
    ):
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=source,
            capture_output=True,
            text=True,
            check=False,
            timeout=3,
        )
        if completed.returncode == 0:
            return completed.stdout.strip()[:80]
    try:
        packaged = json.loads((source / "package-manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(packaged.get("sourceGitCommit") or "").strip()[:80]


def _assert_source_tree_matches_head(source: Path) -> str:
    try:
        inside = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=source,
            capture_output=True,
            text=True,
            check=False,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        inside = None
    if (
        inside is not None
        and inside.returncode == 0
        and Path(inside.stdout.strip()).resolve() == source.resolve()
    ):
        try:
            diff = subprocess.run(
                ["git", "diff", "--quiet", "HEAD", "--"],
                cwd=source,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError(f"unable to verify tracked source tree: {exc}") from exc
        if diff.returncode == 1:
            raise ValueError("tracked source tree differs from HEAD; commit before packaging")
        if diff.returncode != 0:
            detail = (diff.stderr or diff.stdout or "git diff failed").strip()
            raise ValueError(f"unable to verify tracked source tree: {detail}")
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=source,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if untracked.returncode != 0:
            detail = (untracked.stderr or untracked.stdout or "git ls-files failed").strip()
            raise ValueError(f"unable to verify untracked source files: {detail}")
        if untracked.stdout.strip():
            raise ValueError("untracked source files exist; add and commit or remove them before packaging")
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=source,
            capture_output=True,
            text=True,
            check=False,
            timeout=3,
        )
        commit = completed.stdout.strip()[:80] if completed.returncode == 0 else ""
        if commit:
            return commit
        raise ValueError("clean checkout HEAD is unavailable")
    try:
        packaged = json.loads((source / "package-manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        packaged = {}
    commit = str(packaged.get("sourceGitCommit") or "").strip()[:80]
    if commit:
        return commit
    raise ValueError("source commit is not sealed in this package")


def _manifest(
    staging: Path,
    *,
    include_index: bool,
    source_git_commit: str,
    source_tree_clean: bool,
) -> dict[str, object]:
    inventory = []
    for path in sorted(staging.rglob("*"), key=lambda item: item.as_posix().lower()):
        if path.is_file() and path.name != "package-manifest.json":
            inventory.append(
                {
                    "path": path.relative_to(staging).as_posix(),
                    "size": path.stat().st_size,
                    "sha256": _sha256(path),
                }
            )
    return {
        "schemaVersion": 1,
        "name": "evidence-first-integrated-coding",
        "sourceGitCommit": str(source_git_commit or ""),
        "sourceTreeClean": bool(source_tree_clean),
        "portable": True,
        "supportedHosts": ["windows", "linux", "macos-apple-silicon"],
        "hostNotes": {
            "macos-apple-silicon": "Physical FULL install PASS on darwin-arm64; signing/notarization not claimed; Python-free seed path is automated but not part of that historical physical run",
            "macos-intel": "LM Studio configuration unsupported; custom/Cline-only allowed",
            "windows": "Automated CI/fixture paths and a prior native LM Studio GUI/RAG/UBT workflow are evidenced; clean-machine installer lifecycle and universal compatibility are not claimed",
        },
        "defaultProfile": "safe",
        "indexIncluded": include_index,
        "inventory": inventory,
    }


def _scan_private_paths(staging: Path) -> None:
    for path in staging.rglob("*"):
        if not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        candidates = [text]
        # JSON serialization and source string literals double Windows
        # separators. Nested strings can double them repeatedly, so inspect
        # each bounded collapsed form for every textual package member.
        normalized = text
        while "\\\\" in normalized:
            normalized = normalized.replace("\\\\", "\\")
            candidates.append(normalized)
        for candidate in candidates:
            for match in PRIVATE_PATH_RE.finditer(candidate):
                snippet = match.group(0)
                # Ignore documentation placeholders such as <name> / YOUR_NAME.
                if "<" in snippet or "YOUR_NAME" in snippet.upper() or "USERNAME" in snippet.upper():
                    continue
                raise ValueError(
                    f"private home path leaked into package: {path.relative_to(staging)}"
                )


def _assert_clean_inventory(manifest: dict[str, object]) -> list[str]:
    inventory = manifest.get("inventory")
    if not isinstance(inventory, list):
        raise ValueError("package manifest inventory missing")
    forbidden: list[str] = []
    for row in inventory:
        if not isinstance(row, dict):
            continue
        rel = str(row.get("path") or "")
        if FORBIDDEN_INVENTORY_RE.search(rel):
            forbidden.append(rel)
    if forbidden:
        raise ValueError(
            "forbidden files present in portable inventory ("
            f"{len(forbidden)}): " + ", ".join(forbidden[:20])
        )
    return [str(row.get("path") or "") for row in inventory if isinstance(row, dict)]


def _write_deterministic_zip(staging: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    if temporary.exists():
        temporary.unlink()
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path in sorted(staging.rglob("*"), key=lambda item: item.as_posix().lower()):
                if not path.is_file():
                    continue
                relative = Path(ARCHIVE_ROOT) / path.relative_to(staging)
                info = zipfile.ZipInfo(relative.as_posix(), date_time=(2026, 1, 1, 0, 0, 0))
                mode = 0o755 if os.access(path, os.X_OK) else 0o644
                info.external_attr = (mode & 0xFFFF) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, path.read_bytes())
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def build(
    source: Path,
    output: Path,
    zip_path: Path | None,
    *,
    include_index: bool,
    require_clean_source: bool = True,
) -> dict[str, object]:
    source = source.expanduser().resolve()
    output = _validate_destination(output, source)
    if zip_path is not None:
        zip_path = _validate_destination(zip_path, source)
        if _within(zip_path, output):
            raise ValueError("zip path must not be inside the staging directory")
    if not (source / "install.py").is_file():
        raise FileNotFoundError(f"integrated installer not found under source: {source}")
    source_git_commit = (
        _assert_source_tree_matches_head(source)
        if require_clean_source
        else _source_git_commit(source)
    )
    if not source_git_commit:
        raise ValueError("source commit is unavailable")
    missing_required = [
        relative for relative in REQUIRED_RUNTIME_FILES
        if not (source / relative).is_file()
    ]
    if missing_required:
        raise FileNotFoundError(
            "required integrated runtime files are missing: "
            + ", ".join(missing_required)
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}-staging-", dir=output.parent))
    try:
        for path, relative in _source_files(source, include_index=include_index):
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        _write_launchers(staging)
        missing_staged = [
            relative for relative in REQUIRED_RUNTIME_FILES
            if not (staging / relative).is_file()
        ]
        if missing_staged:
            raise FileNotFoundError(
                "required runtime files were not packaged: "
                + ", ".join(missing_staged)
            )
        manifest = _manifest(
            staging,
            include_index=include_index,
            source_git_commit=source_git_commit,
            source_tree_clean=require_clean_source,
        )
        inventory_paths = _assert_clean_inventory(manifest)
        (staging / "package-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        _scan_private_paths(staging)
        if output.exists():
            shutil.rmtree(output) if output.is_dir() else output.unlink()
        staging.replace(output)
        if zip_path is not None:
            _write_deterministic_zip(output, zip_path)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise

    return {
        "ok": True,
        "output": str(output),
        "zip": str(zip_path or ""),
        "files": len(manifest["inventory"]),
        "indexIncluded": include_index,
        "forbiddenInventoryCount": 0,
        "inventorySample": inventory_paths[:40],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--zip", dest="zip_path", type=Path)
    parser.add_argument("--include-index", action="store_true")
    parser.add_argument(
        "--allow-dirty-source",
        action="store_true",
        help=(
            "Build a development snapshot from the current working tree. "
            "The package manifest records sourceTreeClean=false; release builds "
            "remain clean-tree-only by default."
        ),
    )
    parser.add_argument(
        "--print-inventory",
        action="store_true",
        help="Print the full packaged inventory paths to stdout after a successful build.",
    )
    args = parser.parse_args()
    try:
        result = build(
            args.source,
            args.output,
            args.zip_path,
            include_index=args.include_index,
            require_clean_source=not args.allow_dirty_source,
        )
    except (OSError, ValueError, FileNotFoundError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=True, indent=2))
        return 1
    if args.print_inventory:
        manifest = json.loads(Path(result["output"], "package-manifest.json").read_text(encoding="utf-8"))
        for row in manifest["inventory"]:
            print(row["path"])
        print(
            json.dumps(
                {
                    "ok": True,
                    "files": result["files"],
                    "forbiddenInventoryCount": 0,
                },
                ensure_ascii=True,
            ),
            file=sys.stderr,
        )
    else:
        print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
