from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import lmstudio_unreal_wrapper as wrapper  # noqa: E402


def test_build_retry_state_payload_marks_repeated_error():
    records = [
        {
            "text": "Message: fatal error C1083: Cannot open include file: 'UserWidget.h'",
            "metadata": {"error_code": "C1083", "error_subkind": "C1083_MISSING_INCLUDE"},
        }
    ]
    first = wrapper.build_retry_state_payload(
        previous_record=None,
        attempt=1,
        passed=False,
        records=records,
        changed_paths=["Source/Demo/Demo.Build.cs"],
        build_log_path="ubt.log",
        fallback_message="",
    )
    second = wrapper.build_retry_state_payload(
        previous_record=first["current"],
        attempt=2,
        passed=False,
        records=records,
        changed_paths=["Source/Demo/Demo.Build.cs"],
        build_log_path="ubt.log",
        fallback_message="",
    )

    assert second["recommendation"]["sameErrorRepeated"] is True
    assert second["current"]["errorRoute"]["broadMode"] == "module_fix"
    assert "Do not repeat the same patch" in wrapper.retry_feedback_block(second["recommendation"])


def test_build_error_record_priority_prefers_real_error_over_toolchain_warning():
    warning = {
        "text": "Message:\nVisual Studio compiler 14.51.36248 is not a preferred version",
        "metadata": {"severity": "warning", "error_code": "", "error_subkind": "COMPILE_GENERIC"},
    }
    c2511 = {
        "text": "Message:\n'void UDemo::DoWork(void)': overloaded member function not found",
        "metadata": {"severity": "error", "error_code": "C2511", "error_subkind": "HEADER_CPP_SIGNATURE_MISMATCH"},
    }

    records = wrapper.prioritize_build_error_records([warning, c2511])

    assert records[0] is c2511


def test_retry_payload_preserves_specific_route_when_new_parse_is_generic():
    records = [
        {
            "text": "Message: Visual Studio compiler 14.51.36248 is not a preferred version",
            "metadata": {"error_code": "", "error_subkind": "COMPILE_GENERIC"},
        }
    ]
    previous_route = {
        "errorSubkind": "LNK_MISSING_CPP_DEFINITION",
        "broadMode": "compile_fix",
        "buildCsFirstWarning": "Build.cs-first fix is not supported by current evidence.",
        "softSteering": [],
    }

    payload = wrapper.build_retry_state_payload(
        previous_record=None,
        previous_route=previous_route,
        attempt=2,
        passed=False,
        records=records,
        changed_paths=["Source/Demo/Demo.Build.cs"],
        build_log_path="ubt.log",
        fallback_message="",
    )

    assert payload["current"]["errorRoute"]["errorSubkind"] == "LNK_MISSING_CPP_DEFINITION"
    assert payload["current"]["routePreservedFromInitial"] is True


def test_optional_symbol_graph_context_missing_graph_is_empty(monkeypatch):
    monkeypatch.setattr(wrapper, "load_symbol_graph", lambda: {"version": 1, "symbols": []})

    assert wrapper.optional_symbol_graph_context("ADemoActor C1083") == ""


def test_rag_telemetry_summary_counts_sidecars(tmp_path):
    rows = [
        {"source": "project_guideline", "layer": "compile_fix", "resolved_mode": "module_fix"},
        {
            "source": "rag_sidecar",
            "sidecarType": "module_resolver",
            "items": [{"module": "UMG"}],
            "resolved_mode": "module_fix",
        },
        {
            "source": "rag_sidecar",
            "sidecarType": "error_route",
            "items": [{"broadMode": "module_fix", "errorSubkind": "C1083_MISSING_INCLUDE"}],
            "resolved_mode": "module_fix",
        },
    ]

    telemetry = wrapper.summarize_rag_telemetry(
        query="C1083 UserWidget.h",
        requested_mode="module_fix",
        selected_mode="module_fix",
        rows=rows,
        context="abc",
    )
    wrapper.write_rag_telemetry(tmp_path, telemetry)

    assert telemetry["normalRowCount"] == 1
    assert telemetry["sidecarRowCount"] == 2
    assert telemetry["sidecarCountsByType"] == {"module_resolver": 1, "error_route": 1}
    assert telemetry["suspectedModules"] == ["UMG"]
    assert (tmp_path / "rag_telemetry.jsonl").is_file()


def test_wrapper_rag_project_filters_prefers_explicit_project_file(tmp_path):
    project_dir = tmp_path / "DemoProject"
    args = SimpleNamespace(project_file=str(project_dir / "DemoProject.uproject"))

    assert wrapper.wrapper_rag_project_filters(args) == ["DemoProject"]


def test_collect_rag_context_passes_project_filter_for_live_refactor(tmp_path, monkeypatch):
    index = tmp_path / "rag.sqlite"
    index.write_text("", encoding="utf-8")
    project_dir = tmp_path / "AdventureGame"
    captured = {}

    def fake_search(index_path, query, top_k, options):
        captured["projects"] = options.projects
        captured["mode"] = options.mode
        return [
            {
                "source": "project_source",
                "layer": "project_text",
                "resolved_mode": "refactor_r2",
            }
        ]

    monkeypatch.setattr(wrapper, "search_index", fake_search)
    monkeypatch.setattr(wrapper, "assemble_context", lambda rows, request, mode: "ctx")
    monkeypatch.setattr(wrapper, "write_rag_telemetry", lambda run_dir, record: None)

    args = SimpleNamespace(
        index=str(index),
        mode="refactor_r2",
        top_k=8,
        project_file=str(project_dir / "AdventureGame.uproject"),
    )

    assert wrapper.collect_rag_context(args, "refactor cinematic logging") == "ctx"
    assert captured["mode"] == "refactor_r2"
    assert captured["projects"] == ["AdventureGame"]


def test_rag_telemetry_summary_includes_route_hint_fields():
    rows = [
        {
            "source": "rag_sidecar",
            "sidecarType": "error_route",
            "items": [
                {
                    "broadMode": "compile_fix",
                    "errorSubkind": "LNK_MISSING_CPP_DEFINITION",
                    "requiredReads": ["header declaration", "matching cpp definition"],
                    "allowedPatchTargets": ["matching cpp/header"],
                    "forbiddenActions": ["Build.cs-first fix without module evidence"],
                    "buildCsFirstWarning": "Build.cs-first fix is not supported by current evidence.",
                    "routePriorityApplied": "lnk_missing_definition_before_signature_mismatch",
                }
            ],
            "resolved_mode": "compile_fix",
        }
    ]

    telemetry = wrapper.summarize_rag_telemetry(
        query="LNK2019 unresolved external",
        requested_mode="compile_fix",
        selected_mode="compile_fix",
        rows=rows,
        context="abc",
    )

    assert telemetry["requiredReadHints"] == ["header declaration", "matching cpp definition"]
    assert telemetry["allowedPatchTargetHints"] == ["matching cpp/header"]
    assert telemetry["forbiddenActionHints"] == ["Build.cs-first fix without module evidence"]
    assert telemetry["buildCsFirstWarningEmitted"] is True
    assert telemetry["buildCsUnsupportedForRouteWarning"] is True
    assert telemetry["routePriorityApplied"] == "lnk_missing_definition_before_signature_mismatch"
    assert telemetry["staticValidationRetryHint"] is False


def test_soft_route_feedback_warns_without_hard_blocking():
    route = {
        "softSteering": ["This looks like a header/cpp signature mismatch."],
        "buildCsFirstWarning": "Build.cs-first fix is not supported by current evidence.",
    }

    feedback = wrapper.soft_route_feedback(route, module_evidence=False)
    warning = wrapper.build_cs_first_soft_warning(route, ["Source/Demo/Demo.Build.cs"], module_evidence=False)

    assert "warning only" in feedback
    assert "header/cpp signature mismatch" in feedback
    assert "Build.cs-first fix is not supported" in warning


def test_build_cs_unsupported_warning_for_signature_route():
    route = {"errorSubkind": "HEADER_CPP_SIGNATURE_MISMATCH"}

    warning = wrapper.build_cs_unsupported_for_route_warning(
        route,
        ["Source/Demo/Demo.Build.cs"],
        module_evidence=False,
    )

    assert "Build.cs edit is not supported by the current route" in warning
    assert "matching header/cpp file" in warning


def test_build_cs_unsupported_warning_for_lnk_route():
    route = {"errorSubkind": "LNK_MISSING_CPP_DEFINITION"}

    warning = wrapper.build_cs_unsupported_for_route_warning(
        route,
        ["Source/Demo/Demo.Build.cs"],
        module_evidence=False,
    )

    assert "missing implementation related" in warning


def test_build_cs_unsupported_warning_not_emitted_for_module_evidence():
    route = {"errorSubkind": "LNK_MISSING_CPP_DEFINITION"}

    warning = wrapper.build_cs_unsupported_for_route_warning(
        route,
        ["Source/Demo/Demo.Build.cs"],
        module_evidence=True,
    )

    assert warning == ""


def test_unsupported_build_cs_soft_replan_for_signature_route():
    route = {"errorSubkind": "HEADER_CPP_SIGNATURE_MISMATCH"}

    feedback = wrapper.unsupported_build_cs_soft_replan_feedback(
        route,
        ["Source/Demo/Demo.Build.cs"],
        module_evidence=False,
    )

    assert "Soft replan" in feedback
    assert "matching header/cpp pair only" in feedback


def test_static_validation_retry_feedback_for_signature_mismatch():
    finding = wrapper.Finding(
        "warning",
        "Source/Demo/Private/DemoComponent.cpp",
        3,
        "CPP_FUNCTION_SIGNATURE_MISMATCH",
        "signature mismatch",
    )

    feedback = wrapper.static_validation_retry_feedback(
        [finding],
        {"errorSubkind": "HEADER_CPP_SIGNATURE_MISMATCH"},
    )

    assert "Static validation still reports CPP_FUNCTION_SIGNATURE_MISMATCH" in feedback
    assert "Do not edit Build.cs unless module evidence exists" in feedback


def test_static_validation_retry_feedback_for_lnk_remaining():
    feedback = wrapper.static_validation_retry_feedback(
        [],
        {"errorSubkind": "LNK_MISSING_CPP_DEFINITION"},
        build_output="error LNK2019: unresolved external symbol UDemoComponent::StartDash",
    )

    assert "unresolved external / missing cpp definition remains" in feedback


def test_first_attempt_patch_preset_is_limited_to_decl_definition_routes():
    assert wrapper.should_use_patch_preset_on_first_attempt(
        {"errorSubkind": "HEADER_CPP_SIGNATURE_MISMATCH"},
        "compile_fix",
    )
    assert wrapper.should_use_patch_preset_on_first_attempt(
        {"errorSubkind": "LNK_MISSING_CPP_DEFINITION"},
        "compile_fix",
    )
    assert not wrapper.should_use_patch_preset_on_first_attempt(
        {"errorSubkind": "C1083_MISSING_INCLUDE"},
        "compile_fix",
    )
    assert not wrapper.should_use_patch_preset_on_first_attempt(
        {"errorSubkind": "LNK_MISSING_CPP_DEFINITION"},
        "analyze",
    )
    assert wrapper.should_use_patch_preset_on_first_attempt(
        {"errorSubkind": "HEADER_CPP_SIGNATURE_MISMATCH"},
        "multifile_refactor",
    )


def test_multifile_refactor_mode_directive_names_hidden_file_surfaces():
    directive = wrapper.mode_directive("multifile_refactor")

    assert "declaration" in directive
    assert "definition" in directive
    assert "callsite" in directive
    assert "binding" in directive
    assert "override" in directive
    assert "cpp-only" in directive


def test_refactor_r0_directive_requires_scope_and_approval_gates():
    directive = wrapper.mode_directive("refactor_r0")

    assert "classify scope" in directive
    assert "approval gates" in directive
    assert "No code edits" in directive


def test_refactor_r2_directive_limits_execution_to_approved_cluster():
    directive = wrapper.mode_directive("refactor_r2")

    assert "approved implementation cluster" in directive
    assert "Do not combine API migration" in directive
    assert "UBT must pass" in directive


def test_missing_definition_full_file_merge_preserves_existing_call_site(tmp_path: Path):
    source = tmp_path / "Source" / "HoldoutFixture" / "Private"
    source.mkdir(parents=True)
    target = source / "HoldoutMissingDefinitionComponent.cpp"
    target.write_text(
        "#include \"HoldoutMissingDefinitionComponent.h\"\n\n"
        "void UHoldoutMissingDefinitionComponent::BeginPlay()\n"
        "{\n"
        "\tSuper::BeginPlay();\n"
        "\tStartDash();\n"
        "}\n",
        encoding="utf-8",
    )
    bundle = {
        "answer": "Added missing definition.",
        "files": [
            {
                "path": "Source/HoldoutFixture/Private/HoldoutMissingDefinitionComponent.cpp",
                "content": (
                    "#include \"HoldoutMissingDefinitionComponent.h\"\n\n"
                    "void UHoldoutMissingDefinitionComponent::BeginPlay()\n"
                    "{\n"
                    "\tSuper::BeginPlay();\n"
                    "}\n\n"
                    "void UHoldoutMissingDefinitionComponent::StartDash()\n"
                    "{\n"
                    "\t// TODO: Implement StartDash logic here.\n"
                    "}\n"
                ),
            }
        ],
        "patches": [],
        "notes": [],
    }

    merged = wrapper.merge_missing_definition_full_file_edits(
        tmp_path,
        bundle,
        {"errorSubkind": "LNK_MISSING_CPP_DEFINITION"},
    )

    content = merged["files"][0]["content"]
    assert "\tStartDash();\n" in content
    assert content.count("UHoldoutMissingDefinitionComponent::StartDash()") == 1
    assert "append-only cpp preservation" in merged["notes"][0]


def test_missing_definition_patch_merge_handles_stale_old_text(tmp_path: Path):
    source = tmp_path / "Source" / "HoldoutFixture" / "Private"
    source.mkdir(parents=True)
    target = source / "HoldoutMissingDefinitionComponent.cpp"
    target.write_text(
        "#include \"HoldoutMissingDefinitionComponent.h\"\n\n"
        "void UHoldoutMissingDefinitionComponent::BeginPlay()\n"
        "{\n"
        "\tSuper::BeginPlay();\n"
        "\tStartDash();\n"
        "}\n",
        encoding="utf-8",
    )
    bundle = {
        "answer": "Added missing definition.",
        "files": [],
        "patches": [
            {
                "path": "Source/HoldoutFixture/Private/HoldoutMissingDefinitionComponent.cpp",
                "oldText": (
                    "#include \"HoldoutMissingDefinitionComponent.h\"\n\n"
                    "void UHoldoutMissingDefinitionComponent::BeginPlay()\n"
                    "{\n"
                    "\tSuper::BeginPlay();\n"
                    "}"
                ),
                "newText": (
                    "#include \"HoldoutMissingDefinitionComponent.h\"\n\n"
                    "void UHoldoutMissingDefinitionComponent::StartDash()\n"
                    "{\n"
                    "\t// TODO: Implement dash logic.\n"
                    "}\n\n"
                    "void UHoldoutMissingDefinitionComponent::BeginPlay()\n"
                    "{\n"
                    "\tSuper::BeginPlay();\n"
                    "}"
                ),
                "expectedOccurrences": 1,
            }
        ],
        "notes": [],
    }

    merged = wrapper.merge_missing_definition_full_file_edits(
        tmp_path,
        bundle,
        {"errorSubkind": "LNK_MISSING_CPP_DEFINITION"},
    )

    assert merged["patches"] == []
    assert len(merged["files"]) == 1
    content = merged["files"][0]["content"]
    assert "\tStartDash();\n" in content
    assert content.count("UHoldoutMissingDefinitionComponent::StartDash()") == 1


def test_build_retry_state_payload_carries_static_validation_hint():
    records = [
        {
            "text": "Message: CPP_FUNCTION_SIGNATURE_MISMATCH Source/Demo/Private/DemoComponent.cpp",
            "metadata": {"error_code": "", "error_subkind": "HEADER_CPP_SIGNATURE_MISMATCH"},
        }
    ]
    finding = wrapper.Finding(
        "warning",
        "Source/Demo/Private/DemoComponent.cpp",
        3,
        "CPP_FUNCTION_SIGNATURE_MISMATCH",
        "signature mismatch",
    )

    payload = wrapper.build_retry_state_payload(
        previous_record=None,
        attempt=1,
        passed=False,
        records=records,
        changed_paths=["Source/Demo/Demo.Build.cs"],
        build_log_path="ubt.log",
        fallback_message="",
        static_findings=[finding],
    )

    assert "CPP_FUNCTION_SIGNATURE_MISMATCH" in payload["current"]["staticValidationRetryHint"]


def test_write_rag_telemetry_without_run_dir_is_noop():
    wrapper.write_rag_telemetry(None, {"query": "noop"})
