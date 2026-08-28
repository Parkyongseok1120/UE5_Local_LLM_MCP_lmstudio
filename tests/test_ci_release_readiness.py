from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.ci.ci_suites import PRIMARY_SUITE_NAMES, QUARANTINED_TEST_ROOTS, SUITES


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _all_suite_paths() -> tuple[str, ...]:
    return tuple(path for name in PRIMARY_SUITE_NAMES for path in SUITES[name])


def test_dev_requirements_include_pytest_for_github_actions() -> None:
    assert "pytest" in _read("requirements-dev.txt").lower()


def test_only_current_direct_workflow_is_active() -> None:
    workflows = ROOT / ".github" / "workflows"
    names = {path.name for path in workflows.glob("*.yml")}
    ci = _read(".github/workflows/ci.yml")

    assert "eval-regression.yml" not in names
    assert names == {"ci.yml"}
    assert "portable-cross-platform:" in ci
    assert "windows-full:" in ci
    assert "node-mcp:" in ci
    assert "context-compactor:" in ci
    assert "lint-static:" in ci
    assert "release-package:" in ci


def test_workflow_invokes_only_existing_named_pytest_suites() -> None:
    ci = _read(".github/workflows/ci.yml")
    selected = {name for name in SUITES if name in ci}

    assert selected == set(SUITES)
    assert "tests/test_" not in ci
    assert all(SUITES[name] for name in selected)
    assert "python scripts/run_repetition_gate.py direct_repetition --repeat 3" in ci
    assert "tests/test_" not in _read("scripts/run_repetition_gate.py")


def test_suite_manifest_preserves_pre_consolidation_unique_coverage() -> None:
    assert {name: len(SUITES[name]) for name in PRIMARY_SUITE_NAMES} == {
        "portable_direct": 28,
        "portable_release": 12,
        "windows_direct": 9,
        "windows_release": 2,
    }
    paths = _all_suite_paths()
    normalized = [path.casefold() for path in paths]

    assert len(paths) == 51
    assert len(normalized) == len(set(normalized))
    assert all((ROOT / path).is_file() for path in paths)
    assert "tests/test_public_path_hygiene.py" in paths
    assert "tests/test_direct_source_import_isolation.py" in paths
    assert "tests/test_ci_release_readiness.py" in paths
    assert "tests/test_integrated_installer.py" in paths
    assert "tests/test_integrated_package.py" in paths
    assert "tests/test_evidence_packet_validator.py" in paths
    assert "tests/test_package_forbidden_filters.py" in paths
    assert "tests/test_python_seed_bootstrap.py" in paths
    assert "tests/test_ci_suite_runner.py" in paths


def test_repetition_suite_preserves_the_pre_consolidation_three_pass_gate() -> None:
    ci = _read(".github/workflows/ci.yml")
    repetition = SUITES["direct_repetition"]
    primary = set(_all_suite_paths())

    assert len(repetition) == 5
    assert len(set(repetition)) == 5
    assert set(repetition) <= primary
    assert "Direct deterministic repetition gate (three passes)" in ci
    assert "direct_repetition --repeat 3" in ci


def test_windows_full_job_directly_runs_all_primary_suites() -> None:
    ci = _read(".github/workflows/ci.yml")
    windows = ci.split("  windows-full:", 1)[1].split("  node-mcp:", 1)[0]

    assert "python scripts/ci/run_ci_suite.py" in windows
    assert all(name in windows for name in PRIMARY_SUITE_NAMES)


def test_direct_suites_do_not_import_quarantined_legacy_eval() -> None:
    assert QUARANTINED_TEST_ROOTS == ("legacy_eval",)
    for suite_name, members in SUITES.items():
        assert not any(Path(path).parts[0] in QUARANTINED_TEST_ROOTS for path in members)
        if "direct" not in suite_name:
            continue
        for relative in members:
            tree = ast.parse(_read(relative), filename=relative)
            imported: list[str] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.append(node.module)
            assert not [name for name in imported if name == "legacy_eval" or name.startswith("legacy_eval.")]


def test_context_compactor_semantic_preservation_tests_are_mandatory() -> None:
    ci = _read(".github/workflows/ci.yml")
    package = json.loads(_read("lmstudio-context-compactor-plugin/package.json"))
    test_command = package["scripts"]["test"]

    assert "Context Compactor build and full semantic test suite" in ci
    assert "working-directory: lmstudio-context-compactor-plugin" in ci
    assert "run: npm test" in ci
    assert "test/durable-memory-sanitizer.test.js" in test_command
    assert "test/direct-compaction-core.test.js" in test_command
    assert "test/prediction-loop.test.cjs" in test_command
    assert "test/validation-repair-memory.test.cjs" in test_command


def test_component_package_version_sources_are_synchronized() -> None:
    package = json.loads(_read("lmstudio-context-compactor-plugin/package.json"))
    lock = json.loads(_read("lmstudio-context-compactor-plugin/package-lock.json"))
    plugin_manifest = json.loads(_read("lmstudio-context-compactor-plugin/manifest.json"))
    installer_manifest = json.loads(_read("installer/manifest.json"))

    assert package["version"] == lock["version"] == lock["packages"][""]["version"]
    assert plugin_manifest["name"] == package["name"]
    assert isinstance(plugin_manifest["revision"], int) and plugin_manifest["revision"] > 0
    assert installer_manifest["safety"]["contextCompactionEnabledByDefault"] is False


def test_workflow_has_supported_triggers_permissions_cancellation_timeouts_and_caches() -> None:
    ci = _read(".github/workflows/ci.yml")

    assert ci.count('branches: ["main", "Develop"]') == 2
    assert "master" not in ci
    assert "permissions:\n  contents: read" in ci
    assert "group: ci-${{ github.workflow }}-${{ github.ref }}" in ci
    assert "cancel-in-progress: true" in ci
    assert ci.count("timeout-minutes:") == 6
    assert "actions/checkout@v7" in ci
    assert "actions/setup-python@v7" in ci
    assert "actions/setup-node@v7" in ci
    assert "cache: pip" in ci
    assert "cache: npm" in ci
    assert "continue-on-error" not in ci


def test_workflow_has_no_stale_or_machine_local_paths() -> None:
    inspected = "\n".join(
        (
            _read(".github/workflows/ci.yml"),
            _read("scripts/ci/ci_suites.py"),
            _read("scripts/ci/run_ci_suite.py"),
        )
    )
    personal_path_patterns = (
        r"(?i)[A-Z]:[\\/](?:Users|Documents)[\\/]",
        r"(?i)/(?:Users|home)/[^/<$\s]+/",
    )

    assert "scripts/unreal_rag_mcp.py" not in inspected.replace("\\", "/")
    assert not any(re.search(pattern, inspected) for pattern in personal_path_patterns)


def test_release_job_closes_clean_package_and_compactor_only_dry_run() -> None:
    ci = _read(".github/workflows/ci.yml")
    release = ci.split("  release-package:", 1)[1]

    assert "scripts/build_integrated_package.py" in release
    assert "--allow-dirty-source" not in release
    assert 'manifest["sourceTreeClean"] is True' in release
    assert 'manifest["sourceGitCommit"] == expected_commit' in release
    assert "package_builder._assert_clean_inventory(manifest)" in release
    assert 'digest == row["sha256"]' in release
    assert "forbidden inventory count: 0" in release
    assert "--profile custom --components context_compactor" in release
    assert "--dry-run" in release


def test_oss_release_scan_excludes_the_quarantined_legacy_archive() -> None:
    checker = _read("scripts/installer_support/Verify-Oss-Ready.ps1")

    assert "$relPosix -match '(?i)^legacy_eval/'" in checker
    assert "must not influence the release-path hygiene scan" in checker


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
