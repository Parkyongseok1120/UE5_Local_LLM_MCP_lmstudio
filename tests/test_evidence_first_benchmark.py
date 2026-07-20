from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "eval_evidence_first_benchmark.py"


def _load():
    spec = importlib.util.spec_from_file_location("eval_evidence_first_benchmark", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_benchmark_covers_audit_architecture_and_codegen_domains() -> None:
    module = _load()
    cases = module.load_cases()
    domains = {case["domain"] for case in cases}
    assert {
        "causal_bug",
        "framework_semantics",
        "wiring_data_flow",
        "state_transition",
        "architecture",
        "codegen",
    }.issubset(domains)
    assert len(cases) >= 8


def test_benchmark_good_fixtures_pass_and_bad_are_rejected() -> None:
    module = _load()
    cases = module.load_cases()
    result = module.evaluate_fixtures(cases)
    assert result["ok"] is True
    assert result["goodPassed"] == len(cases)
    assert result["badRejected"] == len(cases)


def test_summary_reports_absolute_relative_and_exploratory_confidence() -> None:
    module = _load()
    rows = [
        {"domain": "codegen", "condition": "off", "score": 40.0},
        {"domain": "codegen", "condition": "on", "score": 80.0},
    ]
    result = module.summarize(rows)
    assert result["absoluteImprovementPoints"] == 40.0
    assert result["relativeImprovementPercent"] == 100.0
    assert result["domains"]["codegen"]["confidence"] == "exploratory"


def test_prompts_do_not_leak_rubric_or_fixture_answers() -> None:
    module = _load()
    case = module.load_cases()[0]
    prompt = module.build_user_prompt(case)
    assert case["goodAnswerFixture"] not in prompt
    assert case["badAnswerFixture"] not in prompt
    assert "requiredPatterns" not in prompt
    assert "forbiddenPatterns" not in prompt
    assert "structurePatterns" not in prompt


def test_native_mcp_provenance_is_required_and_fallback_is_forbidden(monkeypatch) -> None:
    module = _load()
    response = {
        "output": [
            {
                "type": "tool_call",
                "tool": "evidence_first_contract",
                "provider_info": {"plugin_id": "mcp/evidence-first"},
            },
            {"type": "message", "content": "ClaimType audit. ProofLevel SourceVerified."},
        ]
    }
    monkeypatch.setattr(module, "_post_json", lambda *args, **kwargs: response)
    answer, api, provenance = module.chat(
        base_url="http://localhost:1234",
        model="test-model",
        system_prompt="system",
        user_prompt="user",
        enable_mcp=True,
        timeout=1,
        require_mcp=True,
    )
    assert answer
    assert api == "rest-v1"
    assert module._native_mcp_verified(provenance) is True


def test_native_mcp_failure_never_falls_back_to_openai(monkeypatch) -> None:
    module = _load()
    calls = []

    def fake_post(url, payload, timeout):
        calls.append(url)
        raise module.urllib.error.URLError("native unavailable")

    monkeypatch.setattr(module, "_post_json", fake_post)
    try:
        module.chat(
            base_url="http://localhost:1234",
            model="test-model",
            system_prompt="system",
            user_prompt="user",
            enable_mcp=True,
            timeout=1,
            require_mcp=False,
        )
    except RuntimeError as exc:
        assert "native LM Studio MCP request failed" in str(exc)
    else:
        raise AssertionError("native MCP failure should not fall back")
    assert calls == ["http://localhost:1234/api/v1/chat"]


def test_summary_keeps_runtime_failures_as_zero_score() -> None:
    module = _load()
    result = module.summarize(
        [
            {"id": "a", "domain": "architecture", "condition": "off", "score": 80.0},
            {"id": "a", "domain": "architecture", "condition": "on", "score": 0.0, "error": "empty"},
        ]
    )
    assert result["skillOffScore"] == 80.0
    assert result["skillOnScore"] == 0.0
