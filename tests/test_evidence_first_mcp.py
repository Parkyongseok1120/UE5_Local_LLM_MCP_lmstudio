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
    assert contract["modeObligations"] == ["invariants", "impactedSurfaces", "validationPlan"]

    result, is_error = module.call_tool("evidence_first_validate", {"packet": {}})
    assert is_error is True
    assert result["ok"] is False


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
