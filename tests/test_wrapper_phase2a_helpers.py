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
