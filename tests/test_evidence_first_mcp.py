from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import shutil
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


def test_contract_lookup_is_optional_and_never_sequences_project_tools() -> None:
    module = _load_mcp()
    tools = {tool["name"]: tool for tool in module.tool_definitions()}
    description = tools["evidence_first_contract"]["description"]
    preset = json.loads(
        (ROOT / "skills" / "evidence-first-code-audit" / "assets" / "lmstudio-evidence-first.preset.json")
        .read_text(encoding="utf-8")
    )["operation"]["fields"][0]["value"]

    assert "Optionally load" in description
    assert "grants no authority" in description
    assert "never sequences RAG, read, write, or build tools" in description
    assert "Call FIRST" not in description
    assert "optional exact-schema lookup" in preset
    assert "never as a routine preflight" in preset
    assert "call evidence_first_contract first" not in preset
    assert "Call evidence_first_validate" in preset


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
    for schema in (claim["properties"]["claim"], evidence["properties"]["location"], behavior_path["properties"]["symbol"]):
        assert schema["minLength"] == 1
        assert "pattern" not in schema
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
    assert result["validationScope"] == "packet_structure_and_record_consistency"
    assert result["factualEvidenceVerified"] is False
    assert result["wholeTaskVerified"] is False
    assert result["summary"] == "structure_valid; facts_not_verified"
    rejected, _ = module.call_tool("evidence_first_validate", {"packet": {}})
    assert rejected["summary"] == "structure_invalid; facts_not_verified"


def test_small_packet_policy_is_shared_and_does_not_certify_audit_completion() -> None:
    module = _load_mcp()
    from evidence_packet_contract import VALIDATION_POLICY, model_guidance_prompt

    preset = json.loads((SCRIPTS.parent / "assets/lmstudio-evidence-first.preset.json").read_text(encoding="utf-8"))
    assert preset["operation"]["fields"][0]["value"] == model_guidance_prompt()
    description = next(t for t in module.tool_definitions() if t["name"] == "evidence_first_validate")["description"]
    assert VALIDATION_POLICY in description
    assert VALIDATION_POLICY in module.contract_payload("audit")["nextAction"]
    # Separate valid packets remain independent; no accumulated session verdict.
    for _ in range(2):
        result, is_error = module.call_tool("evidence_first_validate", {"packet": _neutral_architecture_packet()})
        assert not is_error and result["claimCount"] == 1
        assert result["wholeTaskVerified"] is False
    for mode in ("invalid", "", None, 42):
        result, is_error = module.call_tool("evidence_first_contract", {"mode": mode})
        assert is_error and result["ok"] is False


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


def test_grouped_diagnostics_keep_two_exact_nested_paths_and_count_the_rest() -> None:
    module = _load_mcp()
    values = [
        f"claims[{index}].evidence[3].location must be a non-empty string"
        for index in (1, 3, 8, 12)
    ]
    grouped, shapes, omitted = module._bounded_issues(
        values, max_shapes=16, max_chars=3600,
    )
    assert (shapes, omitted) == (1, 0)
    assert "claims[1].evidence[3].location" in grouped[0]
    assert "claims[3].evidence[3].location" in grouped[0]
    assert "+2 more locations" in grouped[0]
    assert "claims[8]" not in grouped[0]
    assert grouped[0].endswith("(4 occurrences)")


def test_single_claim_error_retains_its_actual_index_without_echoing_packet() -> None:
    module = _load_mcp()
    packet = _neutral_architecture_packet()
    packet["claims"] *= 4
    packet = json.loads(json.dumps(packet))
    packet["claims"][3]["evidence"][0]["location"] = ""
    packet["claims"][3]["claim"] = "PRIVATE_CLAIM_BODY"
    packet["claims"][3]["evidence"][0]["observation"] = "PRIVATE_SOURCE_BODY"
    result, is_error = module.call_tool("evidence_first_validate", {"packet": packet})
    assert is_error and result["errorCount"] == 1
    assert "claims[3].evidence[0].location" in result["errors"][0]
    assert "PRIVATE_" not in json.dumps(result)
    assert result["factualEvidenceVerified"] is False
    assert result["wholeTaskVerified"] is False


def test_behavioral_diagnostic_identifies_one_repair_without_changing_facts() -> None:
    module = _load_mcp()
    for claim_type in ("behavior", "wiring", "state_transition", "data_flow"):
        packet = _neutral_architecture_packet()
        claim = json.loads(json.dumps(packet["claims"][0]))
        claim.update({"claimType": claim_type, "verdict": "Ambiguous", "severity": "P2",
                      "unknowns": ["The final effect has not been observed."],
                      "behaviorPath": [
                          {"stage": stage, "stageStatus": "present", "symbol": "StateStore",
                           "location": "src/state.py:20"}
                          for stage in ("entry", "decision", "dispatch")
                      ]})
        packet["claims"].append(claim)
        before = json.loads(json.dumps(packet))
        result, is_error = module.call_tool("evidence_first_validate", {"packet": packet})
        assert is_error
        assert any("claims[1].behaviorPath" in error and "final effect" in error
                   for error in result["errors"])
        assert all("claims[0]" not in error for error in result["errors"])
        # Record the still-unknown stage; do not invent a successful observation.
        claim["behaviorPath"].append({"stage": "side_effect", "stageStatus": "unknown",
                                      "symbol": "StateStore result", "location": "src/state.py:20"})
        repaired, is_error = module.call_tool("evidence_first_validate", {"packet": packet})
        assert not is_error and repaired["ok"]
        assert repaired["factualEvidenceVerified"] is False
        assert repaired["wholeTaskVerified"] is False
        assert claim["behaviorPath"][-1]["stageStatus"] == "unknown"
        for key in before["claims"][1]:
            if key != "behaviorPath":
                assert claim[key] == before["claims"][1][key]
        assert packet["claims"][0] == before["claims"][0]


def test_large_diagnostic_sets_preserve_limits_and_omission_counts() -> None:
    module = _load_mcp()
    errors = [f"claims[{index}].evidence[0].location must be a non-empty string"
              for index in range(1000)]
    errors += [f"claims[999].field{index} must be an array" for index in range(40)]
    result = module.bounded_validation_result({"ok": False, "errors": errors,
                                              "warnings": errors, "factualEvidenceVerified": False})
    for kind, max_shapes, max_chars in (
        ("error", module.MAX_ERROR_SHAPES, module.MAX_ERROR_DIAGNOSTIC_CHARS),
        ("warning", module.MAX_WARNING_SHAPES, module.MAX_WARNING_DIAGNOSTIC_CHARS),
    ):
        values = result[kind + "s"]
        assert len(values) <= max_shapes
        assert sum(map(len, values)) <= max_chars
        assert all(len(value) <= module.MAX_DIAGNOSTIC_ITEM_CHARS for value in values)
        assert result[kind + "Count"] == 1040
        assert result[kind + "ShapeCount"] == 41
        assert result["omitted" + kind.title() + "ShapeCount"] == 41 - len(values)
        assert "+998 more locations" in values[0]
    assert result["factualEvidenceVerified"] is False


def test_duplicate_paths_and_nonindexed_errors_do_not_invent_locations() -> None:
    module = _load_mcp()
    errors = ["claims[7].unknowns must be an array"] * 3 + ["mode must be audit, architecture, or codegen"]
    grouped, shapes, omitted = module._bounded_issues(errors, max_shapes=16, max_chars=3600)
    assert (shapes, omitted) == (2, 0)
    assert grouped[0].count("claims[7].unknowns") == 1
    assert "more locations" not in grouped[0]
    assert grouped[1] == errors[-1]
    assert module._bounded_issues(errors, max_shapes=0, max_chars=0) == ([], 2, 2)


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
    assert payload["contractCompatible"] is True


def test_smoke_rejects_installed_contract_revision_drift(tmp_path: Path) -> None:
    target = tmp_path / "skill" / "scripts"
    shutil.copytree(SCRIPTS.parent, target.parent, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    contract = target / "evidence_packet_contract.py"
    source = contract.read_text(encoding="utf-8")
    import re
    source, count = re.subn(r'SCHEMA_VERSION = "[^"]+"', 'SCHEMA_VERSION = "0.0.0"', source, count=1)
    assert count == 1
    contract.write_text(source, encoding="utf-8")
    completed = subprocess.run([sys.executable, str(SMOKE), "--server", str(target / "evidence_first_mcp.py")],
                               capture_output=True, text=True, encoding="utf-8", timeout=30)
    payload = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert payload["contractCompatible"] is False
    assert payload["observedSchemaVersion"] == "0.0.0"


def test_generic_mcp_stdio_smoke_with_legacy_windows_code_page() -> None:
    completed = subprocess.run(
        [sys.executable, str(SMOKE)],
        cwd=str(ROOT),
        env={**os.environ, "PYTHONIOENCODING": "cp949"},
        capture_output=True,
        text=True,
        encoding="cp949",
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert json.loads(completed.stdout)["ok"] is True


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
