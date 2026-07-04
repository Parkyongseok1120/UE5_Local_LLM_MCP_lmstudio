from __future__ import annotations

import sys
from pathlib import Path

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
