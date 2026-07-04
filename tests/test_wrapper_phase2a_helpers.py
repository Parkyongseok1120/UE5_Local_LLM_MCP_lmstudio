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


def test_write_rag_telemetry_without_run_dir_is_noop():
    wrapper.write_rag_telemetry(None, {"query": "noop"})
