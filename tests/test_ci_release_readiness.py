from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_dev_requirements_include_pytest_for_github_actions() -> None:
    assert "pytest" in _read("requirements-dev.txt").lower()


def test_only_current_direct_workflow_is_active() -> None:
    workflows = ROOT / ".github" / "workflows"
    names = {path.name for path in workflows.glob("*.yml")}
    ci = _read(".github/workflows/ci.yml")

    assert "eval-regression.yml" not in names
    assert "Direct production regression suite" in ci
    assert "Agent Direct and Node Strict safety suite" in ci
    assert "Context compactor full build and test suite" in ci
    assert "python -m pip install -r requirements-dev.txt" in ci
    assert "python -m pip install ruff" in ci
    assert "npm.cmd ci --no-fund --no-audit" in ci
    assert "Node syntax check (all src JS)" in ci


def test_oss_release_scan_excludes_the_quarantined_legacy_archive() -> None:
    checker = _read("scripts/installer_support/Verify-Oss-Ready.ps1")

    assert "$relPosix -match '(?i)^legacy_eval/'" in checker
    assert "must not influence the release-path hygiene scan" in checker


def test_ci_release_gates_are_direct_only_and_explicit() -> None:
    ci = _read(".github/workflows/ci.yml")
    production = ci.split("      - name: Direct production regression suite", 1)[1].split(
        "        working-directory:", 1
    )[0]

    required = (
        "tests/test_python_direct_rag_server.py",
        "tests/test_tool_manifest_contract.py",
        "tests/test_direct_mcp_subprocess_e2e.py",
        "tests/test_cross_language_tool_contract.py",
        "tests/test_integrated_installer.py",
        "tests/test_bootstrap_runtimes.py",
        "tests/test_integrated_package.py",
        "tests/test_package_forbidden_filters.py",
        "tests/test_install_sh_python_launcher.py",
        "tests/test_installer_gates.py",
        "tests/test_patch_mcp_config.py",
        "tests/test_verify_release.py",
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
        "tests/test_editor_metadata_material.py",
        "tests/test_editor_metadata_provenance.py",
        "tests/test_asset_taxonomy.py",
        "tests/test_plugin_project_context.py",
        "tests/test_agent_write_guards.py",
        "tests/test_atomic_io.py",
        "tests/test_unreal_static_validate.py",
        "tests/test_validate_project_sources.py",
        "tests/test_editor_export_runner.py",
        "tests/test_engine_registration_portability.py",
        "tests/test_project_name_resolver.py",
        "tests/test_target_resolver.py",
        "tests/test_ue_export_compatibility.py",
        "tests/test_public_path_hygiene.py",
        "tests/test_no_project_hardcode.py",
        "tests/test_cline_direct_contract.py",
        "tests/test_direct_test_import_isolation.py",
        "tests/test_direct_source_import_isolation.py",
        "tests/test_ci_release_readiness.py",
    )
    legacy_names = (
        "unreal_rag_mcp.py",
        "test_agent_orchestrator.py",
        "test_task_api_integration.py",
        "test_phase_tool_router.py",
        "test_conversation_ownership.py",
        "eval_domain_contract.py",
        "run_eval_regression.py",
    )

    assert all(path in production for path in required)
    assert all(name not in ci for name in legacy_names)
    assert "pytest --tb=short -q" not in ci
    assert "--suite tests/test_python_direct_rag_server.py" in ci
    assert "--suite tests/test_direct_mcp_subprocess_e2e.py" in ci
    assert "--suite tests/test_build_rag_index_atomic.py" in ci


def test_node_install_command_available_via_cmd_on_windows() -> None:
    if sys.platform != "win32":
        pytest.skip("cmd.exe/npm.cmd contract is Windows-only")
    proc = subprocess.run(
        ["cmd", "/c", "npm.cmd", "--version"],
        cwd=ROOT / "lmstudio-unreal-agent-mcp",
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip()


def test_direct_agent_delete_file_requires_scoped_approval_and_current_hash() -> None:
    catalog_js = _read("lmstudio-unreal-agent-mcp/src/direct-tool-catalog.js")
    delete_js = _read("lmstudio-unreal-agent-mcp/src/direct-delete-capabilities.js")
    version_policy_js = _read("lmstudio-unreal-agent-mcp/src/direct-file-version-policy.js")

    assert 'name: "propose_file_deletions"' in catalog_js
    assert "userApproved=true" in catalog_js
    assert "deletesNothing: true" in delete_js
    assert 'envFlag(env, "ALLOW_SOURCE_DELETE", false)' in delete_js
    assert 'failure("APPROVAL_SCOPE_MISMATCH"' in delete_js
    assert "versionConflict(version" in delete_js
    assert 'failure(\n    "FILE_VERSION_CONFLICT"' in version_policy_js
    assert "trashPath = path.join(" in delete_js
    assert '".agent-trash"' in delete_js
    assert "nearestExistingAncestorRealPath(trashParent)" in delete_js
    assert "fsp.rename(refreshed.absolutePath, trashPath)" in delete_js
