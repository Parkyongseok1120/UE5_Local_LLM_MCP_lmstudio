from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import prompt_history  # noqa: E402


def test_prepare_messages_for_attempt_compacts_on_retry():
    messages = [
        {"role": "system", "content": "static rules"},
        {"role": "user", "content": "first attempt"},
        {"role": "assistant", "content": '{"answer":"patched file"}'},
        {"role": "user", "content": "build failed with fatal error C1083"},
    ]
    compacted, metrics = prompt_history.prepare_messages_for_attempt(messages, "compile_fix", attempt=2)
    assert compacted[0]["role"] == "system"
    assert len(compacted) == 2
    assert compacted[1]["content"].startswith(prompt_history.COMPACT_SUMMARY_PREFIX)
    assert metrics["compactedMessageCount"] == 3
    assert metrics["summaryCharsAfter"] > 0


def test_cap_message_history_respects_history_turns():
    messages = [{"role": "system", "content": "rules"}]
    for idx in range(8):
        messages.append({"role": "user", "content": f"turn {idx}"})
        messages.append({"role": "assistant", "content": f"reply {idx}"})
    capped = prompt_history.cap_message_history(messages, "execute", history_turns=2)
    assert len(capped) < len(messages)
    assert capped[0]["role"] == "system"
