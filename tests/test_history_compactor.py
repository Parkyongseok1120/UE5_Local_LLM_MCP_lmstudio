import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from lmstudio_unreal_wrapper import (
    COMPACT_SUMMARY_PREFIX,
    cap_message_history,
    prepare_messages_for_attempt,
    summarize_compacted_messages,
    write_token_usage,
)


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


def test_prepare_messages_for_attempt_uses_new_chat_slice(monkeypatch):
    monkeypatch.setattr(
        "lmstudio_unreal_wrapper.token_budget.mode_budget",
        lambda mode: {
            "historySummaryMaxChars": 500,
            "session": {"newChatPerSlice": True},
        },
    )
    large_prompt = "large prompt " * 1000
    messages = [
        {"role": "system", "content": "system rules"},
        {"role": "user", "content": large_prompt},
        {"role": "assistant", "content": '{"answer":"patched","patches":[{"path":"Source/Game/Foo.cpp"}]}'},
    ]

    prepared = prepare_messages_for_attempt(messages, "compile_fix", attempt=2)

    assert len(prepared) == 2
    assert prepared[0]["role"] == "system"
    assert prepared[1]["content"].startswith(COMPACT_SUMMARY_PREFIX)
    assert "Source/Game/Foo.cpp" in prepared[1]["content"]
    assert large_prompt not in prepared[1]["content"]


def test_write_token_usage_does_not_double_count_prompt_parts(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "lmstudio_unreal_wrapper.token_budget.mode_budget",
        lambda mode: {
            "maxOutputTokens": 100,
            "feedbackTailChars": 50,
            "maxHistoryMessages": 2,
            "historySummaryMaxChars": 80,
            "projectSummaryMaxFiles": 1,
            "projectSummaryMaxChars": 120,
            "session": {"newChatPerSlice": True},
        },
    )
    path = tmp_path / "usage.json"
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "prompt-rag-state"}]

    write_token_usage(
        path,
        attempt=1,
        messages=messages,
        prompt="prompt",
        rag_context="rag",
        project_state="state",
        mode="compile_fix",
        preset={"maxTokens": 42},
    )

    data = __import__("json").loads(path.read_text(encoding="utf-8"))
    assert data["inputChars"] == len("sys") + len("prompt-rag-state")
    assert data["currentPromptChars"] == len("prompt")
    assert data["ragContextChars"] == len("rag")
    assert data["projectStateChars"] == len("state")
