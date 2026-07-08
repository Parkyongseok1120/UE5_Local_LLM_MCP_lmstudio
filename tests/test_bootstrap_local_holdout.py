from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bootstrap_local_holdout  # noqa: E402


EXAMPLE = ROOT / "config" / "rag_eval_real_project_holdout_cases.local.example.json"


def test_bootstrap_creates_local_config_from_example(tmp_path):
    output = tmp_path / "rag_eval_real_project_holdout_cases.local.json"

    data = bootstrap_local_holdout.write_local_config(example_path=EXAMPLE, output_path=output)

    assert output.is_file()
    loaded = json.loads(output.read_text(encoding="utf-8"))
    assert loaded["suite"] == "real-project-holdout-local-v0"
    assert len(loaded["cases"]) == 5
    assert data == loaded
    assert loaded["engineVersion"] == "5.8"
    assert all(case["engineVersion"] == "5.8" for case in loaded["cases"])


def test_bootstrap_suite_12_adds_expected_case_ids(tmp_path):
    output = tmp_path / "rag_eval_real_project_holdout_cases.local.json"

    data = bootstrap_local_holdout.write_local_config(
        example_path=EXAMPLE,
        output_path=output,
        fixture_root="data/local_holdout_fixtures",
        project_file="HoldoutFixture.uproject",
        suite="12",
    )

    ids = {case["id"] for case in data["cases"]}
    assert len(ids) == 12
    assert {
        "local_gameplaytags_missing_module",
        "local_enhanced_input_missing_module",
        "local_generated_h_not_last",
        "local_header_cpp_signature_mismatch",
        "local_lnk2019_missing_cpp_definition",
        "local_umg_missing_module",
        "local_niagara_missing_module",
        "local_aimodule_missing_module",
        "local_navigation_system_missing_module",
        "local_levelsequence_missing_module",
        "local_blueprint_native_event_missing_implementation",
        "local_editor_only_runtime_boundary",
    } == ids
    assert all(case["projectFile"] == "HoldoutFixture.uproject" for case in data["cases"])
    assert all(case["target"] == "HoldoutFixtureEditor Win64 Development" for case in data["cases"])
    assert data["engineVersion"] == "5.8"
    assert all(case["engineVersion"] == "5.8" for case in data["cases"])
    assert all(str(case["fixtureDir"]).startswith("data/local_holdout_fixtures/") for case in data["cases"])
    assert all(case.get("evalTier") for case in data["cases"])


def test_bootstrap_suite_12_writes_expansion_fixture_skeletons(tmp_path):
    fixture_root = tmp_path / "local_holdout_fixtures"

    created = bootstrap_local_holdout.write_fixture_cases(
        [case["id"] for case in bootstrap_local_holdout.EXPANSION_CASES_12],
        fixture_root,
    )

    assert len(created) == 7
    for case in bootstrap_local_holdout.EXPANSION_CASES_12:
        fixture_dir = fixture_root / case["id"]
        assert (fixture_dir / "HoldoutFixture.uproject").is_file()
        assert (fixture_dir / "Source").is_dir()
        assert (fixture_dir / "request.txt").is_file()
    assert "UUserWidget" in (
        fixture_root
        / "local_umg_missing_module"
        / "Source"
        / "HoldoutFixture"
        / "Public"
        / "HoldoutWidgetHostComponent.h"
    ).read_text(encoding="utf-8")


def test_bootstrap_fixture_targets_are_ue58_editor_compatible(tmp_path):
    fixture_root = tmp_path / "local_holdout_fixtures"
    bootstrap_local_holdout.write_fixture_cases(["local_gameplaytags_missing_module"], fixture_root)
    editor_target = (
        fixture_root
        / "local_gameplaytags_missing_module"
        / "Source"
        / "HoldoutFixtureEditor.Target.cs"
    ).read_text(encoding="utf-8")
    game_target = (
        fixture_root
        / "local_gameplaytags_missing_module"
        / "Source"
        / "HoldoutFixture.Target.cs"
    ).read_text(encoding="utf-8")

    assert "IncludeOrderVersion = EngineIncludeOrderVersion.Unreal5_8" in editor_target
    assert "bOverrideBuildEnvironment = true" in editor_target
    assert "bOverrideBuildEnvironment = true" in game_target
    assert "IncludeOrderVersion = EngineIncludeOrderVersion.Unreal5_8" in game_target


def test_bootstrap_generated_h_fixture_writes_golden_header(tmp_path):
    fixture_root = tmp_path / "local_holdout_fixtures"
    bootstrap_local_holdout.write_fixture_cases(["local_generated_h_not_last"], fixture_root)
    golden_header = (
        fixture_root
        / "local_generated_h_not_last"
        / "golden"
        / "Source"
        / "HoldoutFixture"
        / "Public"
        / "HoldoutGeneratedOrderComponent.h"
    )
    broken_header = (
        fixture_root
        / "local_generated_h_not_last"
        / "Source"
        / "HoldoutFixture"
        / "Public"
        / "HoldoutGeneratedOrderComponent.h"
    ).read_text(encoding="utf-8")
    golden_text = golden_header.read_text(encoding="utf-8")

    assert golden_header.is_file()
    assert ".generated.h" in broken_header
    assert broken_header.index(".generated.h") < broken_header.index("Components/ActorComponent.h")
    assert golden_text.index("Components/ActorComponent.h") < golden_text.index(".generated.h")


def test_bootstrap_module_fix_fixture_writes_golden_build_cs(tmp_path):
    fixture_root = tmp_path / "local_holdout_fixtures"
    bootstrap_local_holdout.write_fixture_cases(
        ["local_gameplaytags_missing_module", "local_enhanced_input_missing_module"],
        fixture_root,
    )
    gameplay_golden = (
        fixture_root
        / "local_gameplaytags_missing_module"
        / "golden"
        / "Source"
        / "HoldoutFixture"
        / "HoldoutFixture.Build.cs"
    )
    enhanced_golden = (
        fixture_root
        / "local_enhanced_input_missing_module"
        / "golden"
        / "Source"
        / "HoldoutFixture"
        / "HoldoutFixture.Build.cs"
    )
    assert gameplay_golden.is_file()
    assert enhanced_golden.is_file()
    assert '"GameplayTags"' in gameplay_golden.read_text(encoding="utf-8")
    assert '"InputCore"' in enhanced_golden.read_text(encoding="utf-8")
    assert '"EnhancedInput"' in enhanced_golden.read_text(encoding="utf-8")


def test_bootstrap_does_not_overwrite_without_force(tmp_path):
    output = tmp_path / "local.json"
    output.write_text('{"sentinel": true}', encoding="utf-8")

    try:
        bootstrap_local_holdout.write_local_config(example_path=EXAMPLE, output_path=output)
    except FileExistsError:
        pass
    else:
        raise AssertionError("expected FileExistsError")

    assert json.loads(output.read_text(encoding="utf-8")) == {"sentinel": True}


def test_bootstrap_fills_project_file_and_fixture_root(tmp_path):
    output = tmp_path / "local.json"

    data = bootstrap_local_holdout.write_local_config(
        example_path=EXAMPLE,
        output_path=output,
        project_file="<PATH_TO_PROJECT>.uproject",
        fixture_root="<PATH_TO_FIXTURE_ROOT>",
    )

    for case in data["cases"]:
        assert case["projectFile"] == "<PATH_TO_PROJECT>.uproject"
        assert case["fixtureDir"] == f"<PATH_TO_FIXTURE_ROOT>/{case['id']}"


def test_next_step_text_includes_required_commands_and_model():
    text = bootstrap_local_holdout.next_step_text(Path("config/rag_eval_real_project_holdout_cases.local.json"))

    assert "validate_holdout_cases.py" in text
    assert "build_symbol_graph.py" in text
    assert "eval_pass_at_k.py --metrics-only" in text
    assert "eval_pass_at_k.py --live --require-live" in text
    assert "qwen3.6-27b-heretic-uncensored-finetune-neo-code-di-imatrix-max" in text


def test_bootstrap_cli_writes_temp_config_and_prints_next_steps(tmp_path):
    output = tmp_path / "local.json"

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "bootstrap_local_holdout.py"),
            "--example-config",
            str(EXAMPLE),
            "--output-config",
            str(output),
            "--project-file",
            "<PATH_TO_PROJECT>.uproject",
            "--fixture-root",
            "<PATH_TO_FIXTURE_ROOT>",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=20,
    )

    assert proc.returncode == 0, proc.stderr
    assert output.is_file()
    assert "validate_holdout_cases.py" in proc.stdout
    assert "eval_pass_at_k.py --live --require-live" in proc.stdout
    assert "qwen3.6-27b-heretic-uncensored-finetune-neo-code-di-imatrix-max" in proc.stdout


def test_bootstrap_cli_suite_12_writes_temp_config_and_fixtures(tmp_path):
    output = tmp_path / "local.json"
    fixture_root = tmp_path / "fixtures"

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "bootstrap_local_holdout.py"),
            "--suite",
            "12",
            "--example-config",
            str(EXAMPLE),
            "--output-config",
            str(output),
            "--fixture-root",
            str(fixture_root),
            "--force",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=20,
    )

    assert proc.returncode == 0, proc.stderr
    assert "Prepared 12 local fixture directories" in proc.stdout
    data = json.loads(output.read_text(encoding="utf-8"))
    assert len(data["cases"]) == 12
    assert (fixture_root / "local_gameplaytags_missing_module" / "HoldoutFixture.uproject").is_file()
    assert (fixture_root / "local_umg_missing_module" / "HoldoutFixture.uproject").is_file()


def test_bootstrap_cli_suite_24_writes_expanded_config_and_fixtures(tmp_path):
    output = tmp_path / "local.json"
    fixture_root = tmp_path / "fixtures"

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "bootstrap_local_holdout.py"),
            "--suite",
            "24",
            "--example-config",
            str(EXAMPLE),
            "--output-config",
            str(output),
            "--fixture-root",
            str(fixture_root),
            "--force",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=20,
    )

    assert proc.returncode == 0, proc.stderr
    assert "Prepared 24 local fixture directories" in proc.stdout
    data = json.loads(output.read_text(encoding="utf-8"))
    ids = {case["id"] for case in data["cases"]}
    assert len(ids) == 24
    assert "local_multifile_interface_signature_update" in ids
    assert "local_plugin_projects_missing_module" in ids
    assert (fixture_root / "local_multifile_interface_signature_update" / "request.txt").is_file()
    assert (fixture_root / "local_plugin_projects_missing_module" / "HoldoutFixture.uproject").is_file()


def test_bootstrap_suite_36_adds_multifile_refactor_tier_cases(tmp_path):
    output = tmp_path / "local.json"

    data = bootstrap_local_holdout.write_local_config(
        example_path=EXAMPLE,
        output_path=output,
        fixture_root="data/local_holdout_fixtures",
        project_file="HoldoutFixture.uproject",
        suite="36",
    )

    ids = {case["id"] for case in data["cases"]}
    tiers = [case.get("evalTier") for case in data["cases"]]
    assert len(ids) == 36
    assert "local_multifile_delegate_param_type_change" in ids
    assert "local_multifile_method_split_callsite_update" in ids
    assert "local_reflection_blueprint_event_rename" in ids
    assert tiers.count("multifile_refactor") == 12
    assert all(tiers)


def test_bootstrap_cli_suite_36_writes_expanded_config_and_fixtures(tmp_path):
    output = tmp_path / "local.json"
    fixture_root = tmp_path / "fixtures"

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "bootstrap_local_holdout.py"),
            "--suite",
            "36",
            "--example-config",
            str(EXAMPLE),
            "--output-config",
            str(output),
            "--fixture-root",
            str(fixture_root),
            "--force",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=20,
    )

    assert proc.returncode == 0, proc.stderr
    assert "Prepared 36 local fixture directories" in proc.stdout
    data = json.loads(output.read_text(encoding="utf-8"))
    assert len(data["cases"]) == 36
    assert (fixture_root / "local_multifile_delegate_param_type_change" / "request.txt").is_file()
    assert (fixture_root / "local_include_owner_forward_decl_mixup" / "HoldoutFixture.uproject").is_file()


def test_tracked_bootstrap_files_do_not_contain_user_paths():
    paths = [
        ROOT / "scripts" / "bootstrap_local_holdout.py",
        ROOT / "config" / "rag_eval_real_project_holdout_cases.local.example.json",
    ]

    text = "\n".join(path.read_text(encoding="utf-8", errors="replace") for path in paths)
    assert ("C:" + "/Users/") not in text
    assert ("C:" + "\\Users\\") not in text
    assert "/Users/" not in text
    assert "/home/" not in text


def test_bootstrap_detects_only_ue58_ubt_candidates():
    text = (ROOT / "scripts" / "bootstrap_local_holdout.py").read_text(encoding="utf-8")

    assert "UE_5.8" in text
    assert "UE_5.7" not in text
    assert "UE_5.6" not in text


def test_local_holdout_paths_are_ignored_by_gitignore():
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "config/rag_eval_real_project_holdout_cases.local.json" in text
    assert "data/local_holdout_fixtures/" in text
    assert "data/baseline/*" in text
    assert "!data/baseline/live_holdout/.gitkeep" not in text


def test_live_holdout_milestone_has_claim_guardrail():
    text = (ROOT / "docs" / "Live_Holdout_Milestone_20260705.md").read_text(encoding="utf-8")

    assert "UE 5.8 local live holdout" in text
    assert "data/baseline/live_holdout/20260705-040303" in text
    assert "not a public benchmark" in text
    assert "does not show Sonnet 4.5 equivalence" in text
    assert "12-case UE 5.8 local live holdout" in text
