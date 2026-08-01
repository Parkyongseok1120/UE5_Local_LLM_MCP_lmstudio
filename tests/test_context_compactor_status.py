from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from context_compactor_status import recent_context_compactor_status  # noqa: E402


def _write_event(root: Path, event: dict) -> None:
    session = root / "session"
    session.mkdir(parents=True)
    (session / "events.jsonl").write_text(
        json.dumps(event) + "\n{partial",
        encoding="utf-8",
    )


def _measurement(at: str) -> dict:
    return {
        "type": "context_measurement",
        "at": at,
        "proxyActive": True,
        "targetModel": "qwen",
        "inputTokens": 100,
        "contextLength": 55_040,
        "decision": {"action": "normal"},
    }


def test_recent_context_compactor_measurement_is_active(tmp_path: Path) -> None:
    _write_event(
        tmp_path,
        _measurement("2026-08-02T00:59:00Z"),
    )
    result = recent_context_compactor_status(
        state_root=tmp_path,
        max_age_seconds=300,
        now=datetime(2026, 8, 2, 1, 0, tzinfo=timezone.utc),
    )
    assert result["active"] is True
    assert result["ageSeconds"] == 60


def test_stale_context_compactor_measurement_is_rejected(tmp_path: Path) -> None:
    _write_event(
        tmp_path,
        _measurement("2026-08-02T00:00:00Z"),
    )
    result = recent_context_compactor_status(
        state_root=tmp_path,
        max_age_seconds=300,
        now=datetime(2026, 8, 2, 1, 0, tzinfo=timezone.utc),
    )
    assert result["active"] is False
    assert result["reason"] == "stale_proxy_measurement"


def test_future_context_compactor_measurement_is_rejected(tmp_path: Path) -> None:
    _write_event(
        tmp_path,
        _measurement("2026-08-02T02:00:00Z"),
    )
    result = recent_context_compactor_status(
        state_root=tmp_path,
        max_age_seconds=300,
        now=datetime(2026, 8, 2, 1, 0, tzinfo=timezone.utc),
    )
    assert result["active"] is False
    assert result["reason"] == "future_proxy_measurement"


def test_large_event_file_reads_only_recent_tail(tmp_path: Path) -> None:
    session = tmp_path / "session"
    session.mkdir(parents=True)
    padding = '{"type":"noise"}\n' * 70_000
    measurement = json.dumps(_measurement("2026-08-02T00:59:00Z"))
    (session / "events.jsonl").write_text(
        padding + measurement + "\n{partial",
        encoding="utf-8",
    )
    result = recent_context_compactor_status(
        state_root=tmp_path,
        max_age_seconds=300,
        now=datetime(2026, 8, 2, 1, 0, tzinfo=timezone.utc),
    )
    assert result["active"] is True


def test_incomplete_measurement_cannot_authorize_proxy(tmp_path: Path) -> None:
    _write_event(
        tmp_path,
        {
            "type": "context_measurement",
            "at": "2026-08-02T00:59:00Z",
            "proxyActive": True,
        },
    )
    result = recent_context_compactor_status(
        state_root=tmp_path,
        max_age_seconds=300,
        now=datetime(2026, 8, 2, 1, 0, tzinfo=timezone.utc),
    )
    assert result["active"] is False
    assert result["reason"] == "no_proxy_measurement"
