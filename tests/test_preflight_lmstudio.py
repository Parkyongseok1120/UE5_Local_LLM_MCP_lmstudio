#!/usr/bin/env python

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import preflight_lmstudio as preflight  # noqa: E402


def test_preflight_reports_loaded_context_and_parallel_warning(monkeypatch):
    monkeypatch.setattr(preflight, "fetch_lmstudio_model_ids", lambda *_args, **_kwargs: ["qwen-flash"])
    monkeypatch.setattr(
        preflight,
        "fetch_lmstudio_models_v0",
        lambda *_args, **_kwargs: [{
            "id": "qwen-flash",
            "state": "loaded",
            "loaded_context_length": 140032,
            "max_context_length": 262144,
        }],
    )
    monkeypatch.setattr(
        preflight,
        "fetch_lms_loaded_models",
        lambda *_args, **_kwargs: [{"identifier": "qwen-flash", "parallel": 4}],
    )

    report = preflight.check_lmstudio("http://localhost:1234/v1")

    assert report["ok"] is True
    assert report["loadedContextLength"] == 140032
    assert report["maxContextLength"] == 262144
    assert report["contextConfigured"] is True
    assert report["contextHeadroom"] == 122112
    assert report["parallelRequests"] == 4
    assert report["recommendedParallelRequests"] == 1
    assert "Parallel=1" in report["longContextSingleAgentWarning"]


def test_preflight_has_no_parallel_warning_for_single_slot(monkeypatch):
    monkeypatch.setattr(preflight, "fetch_lmstudio_model_ids", lambda *_args, **_kwargs: ["qwen-flash"])
    monkeypatch.setattr(
        preflight,
        "fetch_lmstudio_models_v0",
        lambda *_args, **_kwargs: [{
            "id": "qwen-flash",
            "state": "loaded",
            "loaded_context_length": 140032,
            "max_context_length": 262144,
        }],
    )
    monkeypatch.setattr(
        preflight,
        "fetch_lms_loaded_models",
        lambda *_args, **_kwargs: [{"identifier": "qwen-flash", "parallel": 1}],
    )

    report = preflight.check_lmstudio("http://localhost:1234/v1")

    assert report["parallelRequests"] == 1
    assert "longContextSingleAgentWarning" not in report
