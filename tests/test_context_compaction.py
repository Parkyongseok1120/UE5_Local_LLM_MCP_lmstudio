from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from context_compaction import budget_decision, build_checkpoint, compact_messages


def test_budget_gate_reserves_build_output() -> None:
    decision = budget_decision(
        context_length=32_000,
        input_tokens=8_000,
        next_tool_name="build_unreal_project",
        tool_schema_tokens=2_000,
    )
    assert decision.action == "soft_compact"
    assert decision.remaining_tokens == 9_904


def test_checkpoint_keeps_recovery_contract() -> None:
    checkpoint = build_checkpoint([
        {"role": "user", "content": "fix compile"},
        {"role": "tool", "content": '{"requiredNextTool":"unreal_symbol_lookup","requiredNextToolArgs":{"query":"LoadStreamLevel"},"signatureContract":{"parameterCount":5},"path":"project://Source/Game/Foo.cpp"}'},
    ])
    assert checkpoint["requiredNextTool"]["name"] == "unreal_symbol_lookup"
    assert checkpoint["requiredNextTool"]["args"]["query"] == "LoadStreamLevel"
    assert checkpoint["exactSignatureContracts"][0]["parameterCount"] == 5
    assert checkpoint["modifiedFiles"] == ["project://Source/Game/Foo.cpp"]


def test_compaction_retains_system_objective_and_recent_tail() -> None:
    messages = [{"role": "system", "content": "rules"}, {"role": "user", "content": "objective"}]
    messages.extend({"role": role, "content": f"turn-{index}"} for index in range(10) for role in ("assistant", "user"))
    checkpoint = build_checkpoint(messages)
    compacted = compact_messages(messages, checkpoint, recent_messages=4)
    assert compacted[0]["role"] == "system"
    assert compacted[1]["role"] == "user"
    assert any("context_checkpoint" in str(message.get("content")) for message in compacted)
    assert compacted[-1]["content"] == "turn-9"


def test_hard_compaction_removes_a_single_huge_old_message() -> None:
    huge = "x" * 100_000
    messages = [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "objective"},
        {"role": "assistant", "content": huge},
        {"role": "user", "content": "latest"},
    ]
    compacted = compact_messages(messages, build_checkpoint(messages), recent_messages=0)
    assert all(message.get("content") != huge for message in compacted)
    assert compacted[-1]["content"] == "latest"
