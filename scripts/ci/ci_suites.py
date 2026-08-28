"""Single owner for pytest file membership used by local and remote CI."""

from __future__ import annotations


SUITES: dict[str, tuple[str, ...]] = {
    "portable_direct": (
        "tests/test_python_direct_rag_server.py",
        "tests/test_direct_rag_response_budget.py",
        "tests/test_tool_manifest_contract.py",
        "tests/test_direct_mcp_subprocess_e2e.py",
        "tests/test_cross_language_tool_contract.py",
        "tests/test_project_controller.py",
        "tests/test_project_context.py",
        "tests/test_project_routing.py",
        "tests/test_index_path_resolver.py",
        "tests/test_index_inputs.py",
        "tests/test_build_rag_index_atomic.py",
        "tests/test_build_rag_index_compact.py",
        "tests/test_rag_refresh.py",
        "tests/test_rag_smoke.py",
        "tests/test_direct_rag_project_isolation.py",
        "tests/test_dynamic_rag_cli_defaults.py",
        "tests/test_direct_installer_docs.py",
        "tests/test_rag_doctor_repo_only.py",
        "tests/test_active_project_read_resolver.py",
        "tests/test_collect_unreal_projects.py",
        "tests/test_editor_export_runner.py",
        "tests/test_unreal_static_validate.py",
        "tests/test_validate_project_sources.py",
        "tests/test_engine_registration_portability.py",
        "tests/test_project_name_resolver.py",
        "tests/test_target_resolver.py",
        "tests/test_ue_export_compatibility.py",
        "tests/test_public_path_hygiene.py",
        "tests/test_no_project_hardcode.py",
    ),
    "portable_release": (
        "tests/test_integrated_installer.py",
        "tests/test_bootstrap_runtimes.py",
        "tests/test_integrated_package.py",
        "tests/test_package_forbidden_filters.py",
        "tests/test_install_sh_python_launcher.py",
        "tests/test_python_seed_bootstrap.py",
        "tests/test_patch_mcp_config.py",
        "tests/test_verify_release.py",
        "tests/test_evidence_first_mcp.py",
        "tests/test_evidence_packet_validator.py",
        "tests/test_evidence_first_benchmark.py",
        "tests/test_ci_suite_runner.py",
    ),
    "windows_direct": (
        "tests/test_agent_write_guards.py",
        "tests/test_asset_taxonomy.py",
        "tests/test_atomic_io.py",
        "tests/test_cline_direct_contract.py",
        "tests/test_direct_source_import_isolation.py",
        "tests/test_direct_test_import_isolation.py",
        "tests/test_editor_metadata_material.py",
        "tests/test_editor_metadata_provenance.py",
        "tests/test_plugin_project_context.py",
    ),
    "windows_release": (
        "tests/test_installer_gates.py",
        "tests/test_ci_release_readiness.py",
    ),
    # Intentional overlap: these deterministic, flaky-prone paths retain the
    # pre-consolidation three-pass repetition gate in addition to normal CI.
    "direct_repetition": (
        "tests/test_python_direct_rag_server.py",
        "tests/test_direct_mcp_subprocess_e2e.py",
        "tests/test_cross_language_tool_contract.py",
        "tests/test_build_rag_index_atomic.py",
        "tests/test_atomic_io.py",
    ),
}

PRIMARY_SUITE_NAMES: tuple[str, ...] = (
    "portable_direct",
    "portable_release",
    "windows_direct",
    "windows_release",
)

# The historical planner/eval stack remains inspectable but is never part of a
# Direct product suite. Its tests must be selected explicitly outside this CI.
QUARANTINED_TEST_ROOTS: tuple[str, ...] = ("legacy_eval",)
