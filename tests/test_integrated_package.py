from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path
from urllib.parse import unquote

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "build_integrated_package.py"


def _legacy_stdout_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "cp1252"
    return env


def _plant_fake_lms(lmstudio_home: Path) -> None:
    """Hermetic LMS stub so package smoke installs do not require a real LM Studio app."""
    bindir = lmstudio_home / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    plugin_dir = (
        lmstudio_home
        / "extensions"
        / "plugins"
        / "codex"
        / "unreal-context-compactor"
    )
    manifest = plugin_dir / "manifest.json"
    bundle = plugin_dir / ".lmstudio" / "production.js"
    current_manifest = json.loads(
        (ROOT / "lmstudio-context-compactor-plugin" / "manifest.json").read_text(encoding="utf-8")
    )
    manifest_json = json.dumps(current_manifest, separators=(",", ":"))
    if os.name == "nt":
        script = bindir / "lms.cmd"
        script.write_text(
            "\r\n".join(
                [
                    "@echo off",
                    'if /I "%~1"=="--version" (echo lms 0.0.0-test & exit /b 0)',
                    f'mkdir "{bundle.parent}" >nul 2>nul',
                    f'echo {manifest_json} > "{manifest}"',
                    f'echo module.exports = {{}}; > "{bundle}"',
                    "exit /b 0",
                    "",
                ]
            ),
            encoding="utf-8",
        )
    else:
        script = bindir / "lms"
        script.write_text(
            "\n".join(
                [
                    "#!/bin/sh",
                    'if [ "$1" = "--version" ]; then echo "lms 0.0.0-test"; exit 0; fi',
                    f'mkdir -p "{bundle.parent}"',
                    f"printf '%s\\n' '{manifest_json}' > \"{manifest}\"",
                    f"printf '%s\\n' 'module.exports = {{}};' > \"{bundle}\"",
                    "exit 0",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _build(tmp_path: Path, name: str, *, legacy_stdout: bool = False) -> tuple[Path, Path]:
    output = tmp_path / name
    archive = tmp_path / f"{name}.zip"
    result = subprocess.run(
        [
            sys.executable,
            str(BUILDER),
            "--output",
            str(output),
            "--zip",
            str(archive),
            "--allow-dirty-source",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=180,
        env=_legacy_stdout_env() if legacy_stdout else None,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return output, archive


def _load_builder_module():
    spec = importlib.util.spec_from_file_location("build_integrated_package", BUILDER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_builder_supports_package_import_used_by_release_ci() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from scripts import build_integrated_package as builder; "
                "assert callable(builder._assert_clean_inventory)"
            ),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout


def _node_relative_require_closure(*entries: Path) -> set[str]:
    pattern = re.compile(r'''require\(["'](\./[^"']+)["']\)''')
    pending = [entry.resolve() for entry in entries]
    seen: set[Path] = set()
    while pending:
        source = pending.pop()
        if source in seen:
            continue
        seen.add(source)
        for match in pattern.finditer(source.read_text(encoding="utf-8")):
            target = source.parent / match.group(1)
            if not target.suffix:
                target = target.with_suffix(".js")
            target = target.resolve()
            if target.is_file() and target.suffix == ".js":
                pending.append(target)
    return {path.relative_to(ROOT).as_posix() for path in seen}


def _broken_local_markdown_links(package_root: Path) -> list[tuple[str, str]]:
    link_pattern = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
    broken: list[tuple[str, str]] = []
    for markdown in sorted(package_root.rglob("*.md")):
        content = markdown.read_text(encoding="utf-8")
        for raw_target in link_pattern.findall(content):
            target = raw_target.strip().strip("<>")
            if not target or target.startswith("#") or "://" in target:
                continue
            if target.startswith(("mailto:", "data:")):
                continue
            local = unquote(target.split("#", 1)[0].split("?", 1)[0])
            resolved = (markdown.parent / local).resolve()
            try:
                resolved.relative_to(package_root.resolve())
            except ValueError:
                broken.append((markdown.relative_to(package_root).as_posix(), raw_target))
                continue
            if not resolved.exists():
                broken.append((markdown.relative_to(package_root).as_posix(), raw_target))
    return broken


def test_portable_node_runtime_uses_direct_default_and_explicit_strict_opt_in() -> None:
    builder = _load_builder_module()
    required = set(builder.REQUIRED_RUNTIME_FILES)
    direct = ROOT / "lmstudio-unreal-agent-mcp" / "src" / "direct-server.js"
    strict = ROOT / "lmstudio-unreal-agent-mcp" / "src" / "strict-server.js"

    assert _node_relative_require_closure(direct, strict) <= required
    assert {
        "lmstudio-unreal-agent-mcp/package.json",
        "lmstudio-unreal-agent-mcp/package-lock.json",
        "lmstudio-unreal-agent-mcp/src/direct-tool-catalog.js",
        "lmstudio-unreal-agent-mcp/src/direct-runtime-context.js",
        "lmstudio-unreal-agent-mcp/src/direct-project-capabilities.js",
        "lmstudio-unreal-agent-mcp/src/direct-read-capabilities.js",
        "lmstudio-unreal-agent-mcp/src/direct-mutation-capabilities.js",
        "lmstudio-unreal-agent-mcp/src/direct-diagnostic-capabilities.js",
        "lmstudio-unreal-agent-mcp/src/direct-response.js",
        "lmstudio-unreal-agent-mcp/src/direct-repeat-cache.js",
        "lmstudio-unreal-agent-mcp/src/strict-lifecycle.js",
        "lmstudio-unreal-agent-mcp/src/strict-session-domain.js",
        "lmstudio-unreal-agent-mcp/src/strict-session-store.js",
        "lmstudio-unreal-agent-mcp/src/strict-project-binding.js",
        "lmstudio-unreal-agent-mcp/src/automation-command-contract.js",
        "lmstudio-unreal-agent-mcp/src/automation-output-parser.js",
        "lmstudio-unreal-agent-mcp/src/automation-process-runner.js",
        "lmstudio-unreal-agent-mcp/src/automation-source-discovery.js",
        "lmstudio-unreal-agent-mcp/src/automation-source-parser.js",
        "lmstudio-unreal-agent-mcp/src/write-lock-reclaim-bridge.py",
    } <= required
    assert {
        "config/control_protocol_spec.json",
        "config/control_state_machine.json",
        "scripts/control_runtime_identity.py",
        "scripts/control_transition_bridge.py",
        "scripts/phase_tool_router.py",
        "scripts/task_api.py",
        "lmstudio-unreal-agent-mcp/src/server.js",
        "lmstudio-unreal-agent-mcp/src/route-watcher.js",
        "lmstudio-unreal-agent-mcp/src/runtime-identity.js",
        "lmstudio-unreal-agent-mcp/src/control-protocol-spec.js",
        "lmstudio-unreal-agent-mcp/src/task-control-transition.js",
    }.isdisjoint(required)


def test_portable_workspace_path_facade_dependencies_are_required() -> None:
    builder = _load_builder_module()
    required = set(builder.REQUIRED_RUNTIME_FILES)
    assert {
        "scripts/workspace_paths.py",
        "scripts/portable_path_identity.py",
        "scripts/workspace_config.py",
        "scripts/workspace_index_paths.py",
        "scripts/unreal_engine_registration.py",
        "scripts/unreal_engine_discovery.py",
        "scripts/unreal_engine_resolution.py",
        "scripts/unreal_engine_runtime_paths.py",
        "scripts/active_project_paths.py",
        "scripts/editor_export_paths.py",
        "scripts/workspace_locator.py",
    } <= required


def test_portable_editor_sync_owners_are_required() -> None:
    builder = _load_builder_module()
    required = set(builder.REQUIRED_RUNTIME_FILES)
    assert {
        "scripts/sync_editor_metadata.py",
        "scripts/editor_sync_context.py",
        "scripts/editor_sync_capture.py",
        "scripts/editor_sync_coordinator.py",
        "scripts/editor_sync_cli.py",
    } <= required


def test_portable_static_validation_owners_are_required() -> None:
    builder = _load_builder_module()
    required = set(builder.REQUIRED_RUNTIME_FILES)
    assert {
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
    } <= required


def test_portable_python_runtime_has_closed_local_import_graph() -> None:
    builder = _load_builder_module()
    required = set(builder.REQUIRED_RUNTIME_FILES)
    missing: dict[str, list[str]] = {}
    for relative in sorted(required):
        source = ROOT / relative
        if not relative.startswith("scripts/") or source.suffix != ".py":
            continue
        tree = ast.parse(source.read_text(encoding="utf-8-sig"), filename=str(source))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".", 1)[0])
        for module in imported:
            dependency = f"scripts/{module}.py"
            if (ROOT / dependency).is_file() and dependency not in required:
                missing.setdefault(dependency, []).append(relative)
    assert missing == {}


def test_cline_and_portable_checks_select_the_direct_entry() -> None:
    template = json.loads(
        (ROOT / "config" / "cline_mcp_settings.template.json").read_text(encoding="utf-8")
    )
    rag = template["mcpServers"]["unreal-rag"]
    agent = template["mcpServers"]["unreal-agent"]
    assert rag["args"] == ["{REPO_ROOT}/scripts/unreal_rag_direct.py"]
    assert agent["args"] == ["{AGENT_MCP_ROOT}/src/direct-server.js"]
    assert "MCP_EXECUTION_MODE" not in rag["env"]
    assert "MCP_EXECUTION_MODE" not in agent["env"]
    for entry in (rag, agent):
        assert "MCP_BRIDGE_PAIR_ID" not in entry["env"]
    assert "MCP_REQUIRE_PLAN_AUTH" not in agent["env"]

    for relative in (
        "scripts/installer_support/Resolve-StackLayout.ps1",
        "scripts/installer_support/Verify-Oss-Ready.ps1",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert r"src\direct-server.js" in source
        assert r"src\server.js" not in source


def test_package_builder_requires_the_direct_compactor_runtime_surface() -> None:
    builder = _load_builder_module()
    required = set(builder.REQUIRED_RUNTIME_FILES)
    assert {
        "lmstudio-context-compactor-plugin/src/index.ts",
        "lmstudio-context-compactor-plugin/src/prediction-loop.ts",
        "lmstudio-context-compactor-plugin/src/round-loop.ts",
        "lmstudio-context-compactor-plugin/src/direct-compaction-core.js",
        "lmstudio-context-compactor-plugin/src/compaction-tool-memory.js",
        "lmstudio-context-compactor-plugin/src/continuity-file-observations.js",
        "lmstudio-context-compactor-plugin/src/continuity-memory.js",
        "lmstudio-context-compactor-plugin/src/continuity-objectives.js",
        "lmstudio-context-compactor-plugin/src/continuity-text.js",
        "lmstudio-context-compactor-plugin/src/durable-memory-sanitizer.js",
        "lmstudio-context-compactor-plugin/src/direct-config.ts",
    } <= required
    assert {
        "lmstudio-context-compactor-plugin/src/generator.ts",
        "lmstudio-context-compactor-plugin/src/compaction-core.js",
        "lmstudio-context-compactor-plugin/src/runtime-identity.js",
        "lmstudio-context-compactor-plugin/src/control-protocol-spec.js",
        "lmstudio-context-compactor-plugin/src/control-state-registry.generated.js",
    }.isdisjoint(required)


def test_package_builder_rejects_tracked_source_drift_before_copying(tmp_path: Path) -> None:
    source = tmp_path / "dirty-source"
    source.mkdir()
    subprocess.run(["git", "init"], cwd=source, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "package-test@example.invalid"],
        cwd=source,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Package Test"],
        cwd=source,
        check=True,
    )
    installer = source / "install.py"
    installer.write_text("# committed\n", encoding="utf-8")
    subprocess.run(["git", "add", "install.py"], cwd=source, check=True)
    subprocess.run(["git", "commit", "-m", "fixture"], cwd=source, check=True, capture_output=True)
    installer.write_text("# dirty\n", encoding="utf-8")

    builder = _load_builder_module()
    with pytest.raises(ValueError, match="tracked source tree differs from HEAD"):
        builder.build(source, tmp_path / "output", None, include_index=False)


def test_include_index_uses_configured_index_path(tmp_path: Path) -> None:
    source = tmp_path / "portable-source"
    index = source / "data" / "unreal510" / "rag.sqlite"
    index.parent.mkdir(parents=True)
    index.write_bytes(b"fixture")
    config_dir = source / "config"
    config_dir.mkdir()
    (config_dir / "workspace.json").write_text(
        json.dumps({"indexNamespace": "unreal510", "indexPath": "data/unreal510/rag.sqlite"}),
        encoding="utf-8",
    )
    builder = _load_builder_module()
    relative = builder._included_index_relative(source)

    assert relative == Path("data/unreal510/rag.sqlite")
    assert builder._include(relative, include_index=True, index_relative=relative) is True
    assert builder._include(
        Path("data/unreal58/rag.sqlite"),
        include_index=True,
        index_relative=relative,
    ) is False


def test_include_index_uses_the_supplied_source_not_another_mcp_workspace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "portable-source"
    other = tmp_path / "running-mcp-workspace"
    for root, namespace in ((source, "unreal510"), (other, "unreal58")):
        config = root / "config"
        config.mkdir(parents=True)
        (config / "workspace.json").write_text(
            json.dumps({"indexPath": f"data/{namespace}/rag.sqlite"}),
            encoding="utf-8",
        )
    monkeypatch.setenv("UNREAL58_ROOT", str(other))

    builder = _load_builder_module()

    assert builder._included_index_relative(source) == Path("data/unreal510/rag.sqlite")


def test_package_has_all_platform_launchers_and_no_local_state(tmp_path: Path) -> None:
    output, archive = _build(tmp_path, "portable 한글 one", legacy_stdout=True)
    builder = _load_builder_module()
    expected_files = {
        "INSTALL.bat",
        "install.sh",
        "install.py",
        "rag.ps1",
        "README.md",
        "skills/evidence-first-code-audit/SKILL.md",
        "skills/evidence-first-code-audit/assets/lmstudio-evidence-first.preset.json",
        "skills/evidence-first-code-audit/scripts/evidence_first_mcp.py",
        "skills/evidence-first-code-audit/scripts/evidence_packet_contract.py",
        "skills/evidence-first-code-audit/scripts/smoke_evidence_first_mcp.py",
        "skills/evidence-first-code-audit/scripts/validate_evidence_packet.py",
        "docs/ARCHITECTURE.md",
        "docs/Integrated_Installer.md",
        "docs/LMStudio_MCP_Tool_Discipline.md",
        "docs/LMStudio_Unreal_Agent_Setup.md",
        "docs/Troubleshooting.md",
        "package-manifest.json",
        *builder.REQUIRED_RUNTIME_FILES,
    }
    for relative in sorted(expected_files):
        assert (output / relative).is_file(), relative
    packaged_guidelines = list((output / "RAG_Project_Guidelines").rglob("*.md"))
    assert packaged_guidelines, "portable lite indexing must include factual guideline input"
    package_manifest = json.loads(
        (output / "package-manifest.json").read_text(encoding="utf-8")
    )
    inventory = {str(row["path"]) for row in package_manifest["inventory"]}
    explicitly_allowed = (
        set(builder.ALLOWED_ROOT_FILES)
        | set(builder.PORTABLE_FILE_ALLOWLIST)
        | {"PORTABLE-INSTALL.md", "README.md", "rag.ps1"}
    )
    unexpected = {
        relative
        for relative in inventory
        if relative not in explicitly_allowed
        and not any(
            relative.startswith(prefix)
            for prefix in builder.PORTABLE_CONTENT_PREFIXES
        )
    }
    assert unexpected == set()
    assert len(inventory) == len(package_manifest["inventory"])
    for language_readme, source_template in (
        ("README.md", "README.portable.md"),
    ):
        packaged_readme = (output / language_readme).read_text(encoding="utf-8")
        assert packaged_readme == (ROOT / source_template).read_text(encoding="utf-8")
        assert "Enable transparent compaction" not in packaged_readme
        assert "단일 스위치" in packaged_readme
        assert "OFF" in packaged_readme
    portable_install = (output / "PORTABLE-INSTALL.md").read_text(encoding="utf-8")
    assert "설치기는 플러그인을 설치하고 목록에 고정하지만 채팅에서 켜지는 않습니다" in portable_install
    assert "LM Studio 플러그인 설치·고정, 언리얼 자동 탐색" in portable_install
    assert "installation/pinning with the chat toggle OFF" not in portable_install
    assert _broken_local_markdown_links(output) == []
    assert not (output / "README.portable.md").exists()
    assert not (output / "README.portable.ko.md").exists()
    assert not (output / "README.ko.md").exists()
    portable_rag = (output / "rag.ps1").read_text(encoding="utf-8")
    assert portable_rag == (ROOT / "scripts" / "portable_rag.ps1").read_text(encoding="utf-8")
    assert "AllowEditorLaunch" in portable_rag
    for forbidden_command in (
        "agent-plan",
        "agent_orchestrator",
        "wrapper",
        "eval-harness",
        "lmstudio-models",
        "route",
    ):
        assert forbidden_command not in portable_rag
    pwsh = shutil.which("pwsh")
    if pwsh:
        portable_status = subprocess.run(
            [
                pwsh,
                "-NoProfile",
                "-File",
                str(output / "rag.ps1"),
                "doctor",
                "-Out",
                str(tmp_path / "missing-portable.sqlite"),
            ],
            cwd=output,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            env={
                **os.environ,
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHON_EXE": sys.executable,
            },
        )
        assert portable_status.returncode == 0, portable_status.stderr
        assert json.loads(portable_status.stdout)["indexStatus"] == "not_ready"
    expected_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert package_manifest["sourceGitCommit"] == expected_commit
    forbidden_legacy_runtime = (
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
        "scripts/control_state_registry.py",
        "scripts/control_transition_bridge.py",
        "scripts/control_protocol_spec.py",
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
    )
    assert not [relative for relative in forbidden_legacy_runtime if (output / relative).exists()]
    assert (output / "scripts" / "unreal_rag_direct.py").is_file()
    for name in (
        "direct_rag_delivery.py",
        "direct_rag_corpus.py",
        "direct_rag_lexical.py",
        "direct_rag_limits.py",
        "direct_rag_request_bounds.py",
        "direct_rag_sql.py",
        "direct_rag_editor_snapshot.py",
        "direct_rag_engine_collection.py",
        "direct_rag_engine_tier.py",
        "direct_rag_freshness_rows.py",
        "direct_rag_generation_boundary.py",
        "direct_rag_generation_identity.py",
        "direct_rag_generation_swap.py",
        "direct_rag_all_refresh.py",
        "direct_rag_build_generation.py",
        "direct_rag_public_build.py",
        "direct_rag_raw_provenance.py",
        "direct_rag_raw_scope.py",
        "direct_rag_project_engine.py",
        "direct_rag_project_generation.py",
        "direct_rag_project_selectors.py",
        "direct_rag_manifest_binding.py",
        "direct_rag_index_registry.py",
        "direct_rag_index_ownership.py",
        "direct_rag_named_index.py",
        "direct_rag_named_candidate.py",
        "direct_rag_request_binding.py",
        "direct_rag_shard_selection.py",
        "direct_rag_unbuilt_shard.py",
        "direct_rag_refresh_target.py",
        "direct_rag_refresh_cli.py",
        "direct_rag_refresh_journal.py",
        "direct_rag_startup_recovery.py",
        "direct_rag_project_collection.py",
        "direct_rag_project_merge.py",
        "direct_rag_project_set.py",
        "editor_export_contract.py",
        "editor_export_runner.py",
        "editor_export_settings.py",
        "editor_export_location.py",
        "editor_export_project.py",
        "editor_export_markers.py",
        "editor_export_process.py",
        "editor_export_mode.py",
        "editor_capture_state.py",
        "editor_metadata_provenance.py",
        "editor_sync_decision.py",
        "direct_rag_atomic_replace.py",
        "direct_rag_backup_restore.py",
        "direct_rag_selection.py",
        "direct_rag_readonly_db.py",
        "direct_rag_symbol_query.py",
        "direct_rag_build_binding.py",
        "rag_build_classification.py",
        "rag_build_input.py",
        "rag_build_metadata.py",
        "rag_build_metadata_projection.py",
        "rag_build_outputs.py",
        "rag_build_schema.py",
        "rag_build_writer.py",
        "editor_metadata_catalog.py",
        "editor_metadata_sources.py",
        "editor_metadata_identity.py",
        "editor_metadata_projection.py",
        "editor_metadata_search_text.py",
        "editor_metadata_jsonl.py",
        "editor_metadata_merge.py",
        "editor_metadata_cli.py",
    ):
        assert (output / "scripts" / name).is_file()
    direct_catalog = subprocess.run(
        [
            sys.executable,
            "-B",
            str(output / "scripts" / "unreal_rag_direct.py"),
            "--index",
            str(tmp_path / "missing.sqlite"),
        ],
        cwd=output,
        input=(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/list",
                    "params": {},
                }
            )
            + "\n"
        ),
        capture_output=True,
        text=True,
        timeout=20,
        env={**os.environ, "MCP_EXECUTION_MODE": "strict"},
    )
    assert direct_catalog.returncode == 0, direct_catalog.stderr
    catalog_payload = json.loads(direct_catalog.stdout)
    assert [tool["name"] for tool in catalog_payload["result"]["tools"]] == [
        "unreal_get_active_project",
        "unreal_set_active_project",
        "unreal_rag_search",
        "unreal_symbol_lookup",
        "unreal_rag_health",
        "unreal_rag_rebuild_status",
        "unreal_rag_refresh",
        "unreal_rag_capabilities",
    ]
    node = shutil.which("node")
    assert node is not None
    node_input = "\n".join(
        [
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "portable-smoke", "version": "1"},
                    },
                }
            ),
            json.dumps(
                {"jsonrpc": "2.0", "method": "notifications/initialized"}
            ),
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/list",
                    "params": {},
                }
            ),
            "",
        ]
    )
    node_catalog = subprocess.run(
        [node, str(output / "lmstudio-unreal-agent-mcp" / "src" / "direct-server.js")],
        cwd=output,
        input=node_input,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        env={
            **os.environ,
            "NODE_PATH": str(ROOT / "lmstudio-unreal-agent-mcp" / "node_modules"),
            "WORKSPACE_ROOT": str(tmp_path),
            "SHARED_UNREAL_CONFIG": str(tmp_path / "shared.json"),
            "AGENT_STATE_ROOT": str(tmp_path / "agent-state"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHON_EXE": sys.executable,
        },
        check=False,
    )
    assert node_catalog.returncode == 0, node_catalog.stderr
    node_messages = [json.loads(line) for line in node_catalog.stdout.splitlines()]
    listed_node_tools = next(message for message in node_messages if message.get("id") == 2)
    stable_manifest = json.loads(
        (output / "config" / "stable_tool_manifest.json").read_text(encoding="utf-8")
    )
    assert {
        tool["name"] for tool in listed_node_tools["result"]["tools"]
    } == set(stable_manifest["agentEssential"])
    public_launchers = {
        path.relative_to(output).as_posix()
        for path in output.rglob("*")
        if path.is_file()
        and (
            path.suffix.lower() in {".bat", ".cmd", ".command"}
            or path.name == "install.sh"
        )
    }
    assert public_launchers == {"INSTALL.bat", "install.sh"}
    assert {path.name for path in (output / "installer").iterdir()} == {
        "README.md",
        "__init__.py",
        "bootstrap_python.ps1",
        "bootstrap_python.sh",
        "bootstrap_runtimes.py",
        "direct_rag_build.py",
        "direct_rag_build_model.py",
        "direct_rag_build_scope.py",
        "direct_rag_build_stage.py",
        "direct_rag_build_steps.py",
        "lmstudio_plugin_install.py",
        "manifest.json",
        "runtime-manifest.json",
        "unreal_engine_binding.py",
    }
    packaged_installer_manifest = json.loads((output / "installer" / "manifest.json").read_text(encoding="utf-8"))
    assert packaged_installer_manifest["productVersion"] == "1.3.3"
    assert packaged_installer_manifest["version"] == "2.1.17"
    assert packaged_installer_manifest["portablePackage"]["releaseReady"] is True
    assert (output / "docs" / "Release_Notes_1_3_3.md").is_file()
    assert (output / "INSTALL.bat").read_bytes() == (ROOT / "INSTALL.bat").read_bytes()
    source_launcher = (ROOT / "install.sh").read_bytes()
    expected_launcher = source_launcher.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    assert (output / "install.sh").read_bytes() == expected_launcher
    assert b"\r" not in (output / "install.sh").read_bytes()
    source_seed = (ROOT / "installer" / "bootstrap_python.sh").read_bytes()
    expected_seed = source_seed.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    assert (output / "installer" / "bootstrap_python.sh").read_bytes() == expected_seed
    assert b"\r" not in (output / "installer" / "bootstrap_python.sh").read_bytes()
    windows_launcher = (output / "INSTALL.bat").read_text(encoding="utf-8")
    posix_launcher = (output / "install.sh").read_text(encoding="utf-8")
    assert "pause >nul" in windows_launcher
    assert "Bootstrapping managed Python 3.12" in windows_launcher
    assert "Bootstrapping managed Python 3.12" in posix_launcher
    assert (output / "installer" / "bootstrap_python.ps1").is_file()
    assert (output / "installer" / "bootstrap_python.sh").is_file()
    assert "python3.10" in posix_launcher
    if os.name != "nt":
        launcher_help = subprocess.run(
            [str(output / "install.sh"), "--help"],
            cwd=output,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert launcher_help.returncode == 0, launcher_help.stderr or launcher_help.stdout
    portable_help = (output / "PORTABLE-INSTALL.md").read_text(encoding="utf-8")
    assert "Ubuntu 22.04/24.04(glibc)" in portable_help
    assert "SHA-256 확인값을 고정" in portable_help
    assert "SHA-256으로 확인한 uv를 자동으로 내려받고" in portable_help
    assert "설치기의 검색 자료 생성은 관리 중인 Python을 직접 사용합니다" in portable_help
    assert "RAG indexing uses the bootstrapped `pwsh`" not in portable_help
    assert "과거 계획·평가·작업 관리 기능은 포함하지 않습니다" in portable_help
    assert "Get-Help ./scripts/portable_rag.ps1 -Detailed" in portable_help
    assert "Get-Help ./rag.ps1 -Detailed" not in portable_help
    forbidden = {".git", ".venv", "node_modules", "tests", "Reports", ".agent"}
    assert not any(forbidden.intersection(path.relative_to(output).parts) for path in output.rglob("*"))
    assert not any(path.suffix in {".sqlite", ".db"} for path in output.rglob("*"))
    assert str(Path.home()) not in "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in output.rglob("*")
        if path.is_file() and path.stat().st_size < 4 * 1024 * 1024
    )
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
    assert any(name.endswith("/install.sh") for name in names)
    assert not any(name.endswith("/INSTALL.command") for name in names)
    assert not any("node_modules" in name or "/.git/" in name for name in names)
    lmstudio_home = tmp_path / "isolated lmstudio"
    lmstudio_home.mkdir(parents=True, exist_ok=True)
    _plant_fake_lms(lmstudio_home)
    installed = subprocess.run(
        [
            sys.executable,
            str(output / "install.py"),
            "--profile",
            "safe",
            "--yes",
            "--skip-deps",
            "--skip-runtime-bootstrap",
            "--codex-home",
            str(tmp_path / "isolated codex"),
            "--lmstudio-home",
            str(lmstudio_home),
            "--state-home",
            str(tmp_path / "isolated state"),
        ],
        cwd=str(output),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert installed.returncode == 0, installed.stderr or installed.stdout
    assert json.loads(installed.stdout)["mcpSmoke"]["ok"] is True


def test_packaged_unreal_skip_deps_fails_before_writing_mcp_config(tmp_path: Path) -> None:
    output, _archive = _build(tmp_path, "missing-agent-dependency")
    lmstudio_home = tmp_path / "isolated-lmstudio"
    state_home = tmp_path / "isolated-state"
    result = subprocess.run(
        [
            sys.executable,
            str(output / "install.py"),
            "--profile",
            "standard",
            "--yes",
            "--skip-deps",
            "--skip-runtime-bootstrap",
            "--codex-home",
            str(tmp_path / "isolated-codex"),
            "--lmstudio-home",
            str(lmstudio_home),
            "--state-home",
            str(state_home),
            "--workspace-root",
            str(tmp_path / "projects"),
        ],
        cwd=str(output),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert "UNREAL_AGENT_DEPENDENCY_MISSING" in payload["error"]
    assert "without --skip-deps" in payload["error"]
    assert not (lmstudio_home / "mcp.json").exists()
    assert not state_home.exists()


def test_packaged_direct_refresh_builds_and_searches_fixture_project(
    tmp_path: Path,
) -> None:
    output, _archive = _build(tmp_path, "direct-refresh-e2e")
    project_root = tmp_path / "FixtureGame"
    source_root = project_root / "Source" / "FixtureGame"
    public_root = source_root / "Public"
    private_root = source_root / "Private"
    public_root.mkdir(parents=True)
    private_root.mkdir(parents=True)
    project_file = project_root / "FixtureGame.uproject"
    project_file.write_text(
        json.dumps(
            {
                "FileVersion": 3,
                "EngineAssociation": "5.8",
                "Modules": [
                    {"Name": "FixtureGame", "Type": "Runtime", "LoadingPhase": "Default"}
                ],
            }
        ),
        encoding="utf-8",
    )
    (source_root / "FixtureGame.Build.cs").write_text(
        """using UnrealBuildTool;
public class FixtureGame : ModuleRules
{
    public FixtureGame(ReadOnlyTargetRules Target) : base(Target)
    {
        PublicDependencyModuleNames.AddRange(new string[] { "Core", "CoreUObject", "Engine" });
    }
}
""",
        encoding="utf-8",
    )
    (public_root / "PackagedRefreshProbe.h").write_text(
        """#pragma once
#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "PackagedRefreshProbe.generated.h"

UCLASS()
class FIXTUREGAME_API APackagedRefreshProbe : public AActor
{
    GENERATED_BODY()
public:
    void VerifyPackagedDirectRefresh();
};
""",
        encoding="utf-8",
    )
    (private_root / "PackagedRefreshProbe.cpp").write_text(
        """#include "PackagedRefreshProbe.h"
void APackagedRefreshProbe::VerifyPackagedDirectRefresh() {}
""",
        encoding="utf-8",
    )

    index = tmp_path / "rag-data" / "rag.sqlite"
    shared_config = tmp_path / "shared-unreal.json"
    shared_config.write_text(
        json.dumps({"indexingTier": "lite"}),
        encoding="utf-8",
    )
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "unreal_set_active_project",
                "arguments": {"projectPath": str(project_file)},
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "unreal_rag_refresh",
                "arguments": {"scope": "project_source"},
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "unreal_rag_search",
                "arguments": {
                    "query": "VerifyPackagedDirectRefresh APackagedRefreshProbe",
                    "project": "FixtureGame",
                    "top_k": 8,
                },
            },
        },
    ]
    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            str(output / "scripts" / "unreal_rag_direct.py"),
            "--index",
            str(index),
        ],
        cwd=output,
        input="".join(json.dumps(request) + "\n" for request in requests),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        env={
            **os.environ,
            "DIRECT_RAG_STATE_ROOT": str(tmp_path / "direct-state"),
            "MCP_EXECUTION_MODE": "strict",
            "PYTHONDONTWRITEBYTECODE": "1",
            "SHARED_UNREAL_CONFIG": str(shared_config),
            "UNREAL58_ROOT": str(output),
            "UNREAL_RAG_INDEX_PATH": str(index),
        },
    )

    assert completed.returncode == 0, completed.stderr
    responses = {
        message["id"]: message["result"]["structuredContent"]
        for line in completed.stdout.splitlines()
        if (message := json.loads(line)).get("id") in {1, 2, 3}
    }
    assert responses[1]["ok"] is True
    refresh = responses[2]
    assert refresh["ok"] is True, refresh
    assert refresh["scope"] == "project_source"
    assert refresh["editorLaunchAllowed"] is False
    assert refresh["projectSourceSync"]["ok"] is True
    assert all(step["ok"] is True for step in refresh["projectSourceSync"]["steps"])
    assert index.is_file()
    search = responses[3]
    assert search["ok"] is True, search
    assert search["matchCount"] >= 1
    serialized = json.dumps(search, ensure_ascii=False)
    assert "APackagedRefreshProbe" in serialized
    assert "continuationToken" not in serialized


def test_manifest_inventory_and_zip_are_reproducible(tmp_path: Path) -> None:
    first, first_zip = _build(tmp_path, "first")
    second, second_zip = _build(tmp_path, "second")
    first_manifest = json.loads((first / "package-manifest.json").read_text(encoding="utf-8"))
    second_manifest = json.loads((second / "package-manifest.json").read_text(encoding="utf-8"))
    assert first_manifest == second_manifest
    assert hashlib.sha256(first_zip.read_bytes()).hexdigest() == hashlib.sha256(second_zip.read_bytes()).hexdigest()


def test_builder_rejects_source_or_nested_destinations(tmp_path: Path) -> None:
    for destination in (ROOT, ROOT / "dist" / "unsafe"):
        result = subprocess.run(
            [sys.executable, str(BUILDER), "--output", str(destination)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 1
        assert "disjoint" in result.stdout


def test_builder_error_json_is_safe_with_legacy_stdout_encoding(tmp_path: Path) -> None:
    missing_source = tmp_path / "없는 source"
    result = subprocess.run(
        [
            sys.executable,
            str(BUILDER),
            "--source",
            str(missing_source),
            "--output",
            str(tmp_path / "output"),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=30,
        env=_legacy_stdout_env(),
    )
    assert result.returncode == 1
    assert str(missing_source) in json.loads(result.stdout)["error"]
