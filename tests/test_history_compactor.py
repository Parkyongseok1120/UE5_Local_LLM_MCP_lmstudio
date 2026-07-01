import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from lmstudio_unreal_wrapper import COMPACT_SUMMARY_PREFIX, cap_message_history, summarize_compacted_messages


def test_cap_message_history_preserves_summary_and_tail():
    messages = [
        {"role": "system", "content": "system rules"},
        {"role": "user", "content": "User request:\nAdd component"},
        {"role": "assistant", "content": '{"answer":"planned edit","files":[{"path":"Source/Game/Foo.h"}]}'},
        {"role": "user", "content": "Compile loop attempt 2/4. Previous validation/build feedback: C1083 missing include"},
        {"role": "assistant", "content": '{"answer":"patched include","patches":[{"path":"Source/Game/Foo.cpp"}]}'},
        {"role": "user", "content": "latest user prompt"},
        {"role": "assistant", "content": "latest assistant response"},
    ]

    compacted = cap_message_history(messages, "api_lookup")

    assert compacted[0]["role"] == "system"
    assert compacted[1]["content"].startswith(COMPACT_SUMMARY_PREFIX)
    assert "Source/Game/Foo.h" in compacted[1]["content"]
    assert compacted[-2]["content"] == "latest user prompt"
    assert compacted[-1]["content"] == "latest assistant response"


def test_compact_summary_truncation_preserves_prefix():
    messages = [
        {
            "role": "assistant",
            "content": '{"answer":"' + ("very long answer " * 80) + '","files":[{"path":"Source/Game/Foo.cpp"}]}',
        }
    ]

    summary = summarize_compacted_messages(messages, 240)

    assert summary.startswith(COMPACT_SUMMARY_PREFIX)
    assert len(summary) <= 240
    assert "compact summary truncated" in summary
