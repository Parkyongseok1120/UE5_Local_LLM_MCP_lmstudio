from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "evidence-first-code-audit" / "scripts"
MCP = SCRIPTS / "evidence_first_mcp.py"
SMOKE = SCRIPTS / "smoke_evidence_first_mcp.py"


def _load_mcp():
    spec = importlib.util.spec_from_file_location("evidence_first_mcp", MCP)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(SCRIPTS))
    return module


def test_generic_mcp_is_read_only_and_project_neutral() -> None:
    module = _load_mcp()
    names = {tool["name"] for tool in module.tool_definitions()}
    assert names == {
        "evidence_first_contract",
        "evidence_first_validate",
        "evidence_first_status",
    }
    serialized = json.dumps(module.tool_definitions(), ensure_ascii=False)
    assert "write_file" not in serialized
    assert "Unreal" not in serialized
    assert all(tool["annotations"]["readOnlyHint"] is True for tool in module.tool_definitions())
    assert all(tool["annotations"]["destructiveHint"] is False for tool in module.tool_definitions())


def test_protocol_version_negotiation_and_invalid_arguments() -> None:
    module = _load_mcp()
    sent = []
    server = module.McpServer()
    server.send = sent.append
    server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2099-01-01"},
        }
    )
    assert sent[-1]["result"]["protocolVersion"] == module.SUPPORTED_PROTOCOL_VERSIONS[0]
    server.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "evidence_first_status", "arguments": []},
        }
    )
    assert sent[-1]["error"]["code"] == -32602


def test_generic_mcp_contract_and_validator() -> None:
    module = _load_mcp()
    contract, contract_error = module.call_tool("evidence_first_contract", {"mode": "codegen"})
    assert contract_error is False
    assert contract["schemaVersion"] == module.SERVER_VERSION
    assert contract["modeObligations"] == ["invariants", "impactedSurfaces", "validationPlan"]
    assert contract["verdicts"] == sorted(contract["verdicts"])
    assert "Confirmed" in contract["verdicts"]
    assert "Info" in contract["severities"]
    assert contract["requiredEvidenceFields"] == ["kind", "location", "observation"]
    assert contract["requiredBehaviorPathFields"] == [
        "stage",
        "stageStatus",
        "location",
        "symbol",
    ]
    assert "P0/P1" in contract["nextAction"]
    assert "multi-file implementation plan" in contract["nextAction"]
    assert "before the final answer" not in contract["nextAction"]

    result, is_error = module.call_tool("evidence_first_validate", {"packet": {}})
    assert is_error is True
    assert result["ok"] is False
    assert result["errorCount"] >= len(result["errors"])


def test_validator_tool_advertises_the_exact_nested_packet_shape() -> None:
    module = _load_mcp()
    tools = {tool["name"]: tool for tool in module.tool_definitions()}
    packet = tools["evidence_first_validate"]["inputSchema"]["properties"]["packet"]
    claim = packet["properties"]["claims"]["items"]
    evidence = claim["properties"]["evidence"]["items"]
    behavior_path = claim["properties"]["behaviorPath"]["items"]

    assert packet["required"] == ["mode", "claims"]
    assert set(claim["required"]) == {
        "claim",
        "claimType",
        "verdict",
        "severity",
        "proofLevel",
        "evidence",
        "behaviorPath",
        "counterEvidence",
        "unknowns",
    }
    assert evidence["required"] == ["kind", "location", "observation"]
    assert behavior_path["required"] == ["stage", "stageStatus", "location", "symbol"]
    assert claim["properties"]["claim"]["pattern"] == r"\S"
    assert evidence["properties"]["location"]["pattern"] == r"\S"
    assert behavior_path["properties"]["symbol"]["pattern"] == r"\S"
    assert set(claim["properties"]["verdict"]["enum"]) == set(module.contract_payload("audit")["verdicts"])
    assert set(claim["properties"]["severity"]["enum"]) == set(module.contract_payload("audit")["severities"])


def _neutral_architecture_packet() -> dict:
    return {
        "mode": "architecture",
        "claims": [
            {
                "claim": "The existing state owner is source verified without inferring intent.",
                "claimType": "architecture",
                "verdict": "Confirmed",
                "severity": "Info",
                "proofLevel": "SourceVerified",
                "evidence": [
                    {
                        "kind": "project_source",
                        "location": "src/state.py:20",
                        "observation": "StateStore owns the mutation API.",
                    }
                ],
                "behaviorPath": [],
                "counterEvidence": [],
                "unknowns": [],
            }
        ],
        "existing": ["StateStore owns state mutation."],
        "proposed": [],
        "doNotDuplicate": [],
    }


def test_contract_shaped_neutral_architecture_packet_validates_on_first_call() -> None:
    module = _load_mcp()
    result, is_error = module.call_tool(
        "evidence_first_validate",
        {"packet": _neutral_architecture_packet()},
    )

    assert is_error is False
    assert result["ok"] is True
    assert result["schemaVersion"] == module.SERVER_VERSION
    assert result["errorCount"] == 0
    assert result["errors"] == []


def test_mcp_validation_groups_repeated_error_paths_and_bounds_diagnostics() -> None:
    module = _load_mcp()
    malformed_claim = {
        "claim": "A malformed repeated claim.",
        "claimType": "behavior",
        "verdict": "confirmed",
        "severity": "info",
        "proofLevel": "SourceVerified",
        "evidence": [
            {"kind": "project_source", "locator": "source:a", "detail": "observed"}
        ],
        "behaviorPath": [
            {"stage": "entry", "status": "present", "detail": "request"},
            {"stage": "decision", "status": "present", "detail": "branch"},
            {"stage": "observer", "status": "present", "detail": "result"},
        ],
        "counterEvidence": [
            {"kind": "project_source", "locator": "counter:a", "detail": "checked"}
        ],
        "unknowns": [],
    }
    packet = {
        "mode": "architecture",
        "claims": [dict(malformed_claim) for _ in range(7)],
        "existing": {},
        "proposed": {},
        "doNotDuplicate": {},
    }

    result, is_error = module.call_tool("evidence_first_validate", {"packet": packet})
    serialized_errors = json.dumps(result["errors"], ensure_ascii=False)

    assert is_error is True
    assert result["ok"] is False
    assert result["errorCount"] > len(result["errors"])
    assert len(result["errors"]) <= module.MAX_ERROR_SHAPES
    assert result["errorShapeCount"] == (
        len(result["errors"]) + result["omittedErrorShapeCount"]
    )
    assert len(serialized_errors) <= module.MAX_ERROR_DIAGNOSTIC_CHARS + 512
    assert "claims[]" in serialized_errors
    assert "occurrences" in serialized_errors
    assert "verdict" in serialized_errors
    assert "evidence[]" in serialized_errors
    assert "location" in serialized_errors
    assert "observation" in serialized_errors
    assert "stageStatus" in serialized_errors
    assert "symbol" in serialized_errors
    assert "non-empty existing" in serialized_errors
    assert "proposed array" in serialized_errors


def test_grouped_diagnostic_suffix_stays_inside_the_per_item_bound() -> None:
    module = _load_mcp()
    values = [f"claims[{index}].{'x' * 500}" for index in range(12)]

    grouped, shape_count, omitted = module._bounded_issues(
        values,
        max_shapes=module.MAX_ERROR_SHAPES,
        max_chars=module.MAX_ERROR_DIAGNOSTIC_CHARS,
    )

    assert shape_count == 1
    assert omitted == 0
    assert len(grouped) == 1
    assert grouped[0].endswith("(12 occurrences)")
    assert len(grouped[0]) <= module.MAX_DIAGNOSTIC_ITEM_CHARS


def test_generic_mcp_stdio_smoke() -> None:
    completed = subprocess.run(
        [sys.executable, str(SMOKE)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["status"]["readOnly"] is True


def test_lmstudio_preset_contains_contract_and_low_temperature() -> None:
    preset = json.loads(
        (
            ROOT
            / "skills"
            / "evidence-first-code-audit"
            / "assets"
            / "lmstudio-evidence-first.preset.json"
        ).read_text(encoding="utf-8")
    )
    fields = {entry["key"]: entry["value"] for entry in preset["operation"]["fields"]}
    assert "evidence_first_contract" in fields["llm.prediction.systemPrompt"]
    assert "evidence_first_validate" in fields["llm.prediction.systemPrompt"]
    assert fields["llm.prediction.temperature"] <= 0.2
