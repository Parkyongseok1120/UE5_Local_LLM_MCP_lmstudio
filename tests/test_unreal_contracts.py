"""Unreal contract data: strict schema, single source, and metadata transport."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
import unreal_contracts as contracts  # noqa: E402
import unreal_api_denylist  # noqa: E402
import unreal_static_lifecycle as lifecycle  # noqa: E402

SHIPPED = json.loads((ROOT / "config" / "unreal_contracts.json").read_text(encoding="utf-8"))


def test_shipped_contract_file_is_valid_and_versioned():
    data = contracts.load_contracts()
    assert contracts.contract_revision(data) == f"unreal_contracts@{SHIPPED['revision']}"
    assert contracts.engine_version_validated(data, "5.8.2")
    assert not contracts.engine_version_validated(data, "5.7.4")
    facts = [c for c in data["contracts"] if c["kind"] == "engine_fact"]
    assert facts and all(c["evidenceRefs"] for c in facts)


def test_lifecycle_and_denylist_tables_come_from_the_contract_source():
    data = contracts.load_contracts()["lifecycle"]
    assert lifecycle.UNREAL_ENGINE_LIFECYCLE_PARENTS == data["parents"]
    assert lifecycle.UNREAL_LIFECYCLE_OVERRIDE_ALLOWLIST == {k: set(v) for k, v in data["overrides"].items()}
    assert unreal_api_denylist.INVALID_LIFECYCLE_OVERRIDES == data["invalidOverrideNames"]
    assert lifecycle.LIFECYCLE_CONTRACT_STATUS == "available"


@pytest.mark.parametrize("mutate, message", [
    (lambda d: d["contracts"].append(copy.deepcopy(d["contracts"][0])), "duplicate contract id"),
    (lambda d: d["contracts"][0].update(evidenceRefs=[]), "needs engine source evidence"),
    (lambda d: d["contracts"][0]["applicability"].update(engineVersions=["5.7"]), "unvalidated version"),
    (lambda d: d["lifecycle"]["parents"].update(UObject="AActor"), "cycle"),
    (lambda d: d["contracts"][0]["evidenceRefs"][0].update(path="../outside.h"), "engine-root relative"),
    (lambda d: d["contracts"][0].update(kind="guess"), "unknown kind"),
    (lambda d: d["contracts"][0].update(code="exec()"), "unexpected keys"),
    (lambda d: d.update(schemaVersion=2), "schemaVersion"),
])
def test_malformed_contract_data_is_rejected(mutate, message):
    data = copy.deepcopy(SHIPPED)
    mutate(data)
    with pytest.raises(contracts.ContractError, match=message):
        contracts.validate_contracts(data)


def test_unreadable_contract_file_is_unavailable_not_empty(tmp_path):
    broken = tmp_path / "unreal_contracts.json"
    broken.write_text("{not json", encoding="utf-8")
    assert contracts.load_contracts_or_none(broken) is None
    with pytest.raises(contracts.ContractError):
        contracts.load_contracts(broken)


def _project_with_invalid_override(tmp_path: Path) -> Path:
    project = tmp_path / "Demo"
    module = project / "Source" / "Demo"
    module.mkdir(parents=True)
    (module / "Demo.Build.cs").write_text(
        'using UnrealBuildTool;\npublic class Demo : ModuleRules { public Demo(ReadOnlyTargetRules Target) : base(Target) {} }\n',
        encoding="utf-8")
    (module / "MySubsystem.h").write_text(
        '#pragma once\n#include "CoreMinimal.h"\n#include "Subsystems/WorldSubsystem.h"\n#include "MySubsystem.generated.h"\n\n'
        'UCLASS()\nclass DEMO_API UMySubsystem : public UTickableWorldSubsystem\n{\n\tGENERATED_BODY()\npublic:\n'
        '\tvirtual void BeginDestroy() override;\n\tvirtual void OnWorldDestroyed() override;\n};\n', encoding="utf-8")
    return project


def test_cli_json_keeps_finding_metadata_and_analysis_coverage(tmp_path):
    project = _project_with_invalid_override(tmp_path)
    result = subprocess.run([sys.executable, str(SCRIPTS / "validate_project_sources.py"), "--project-root",
                             str(project), "--json"], cwd=str(ROOT), capture_output=True, text=True,
                            encoding="utf-8", check=False)
    payload = json.loads(result.stdout)
    revision = contracts.contract_revision(contracts.load_contracts())
    assert payload["analysis"]["contractRevision"] == revision
    assert payload["analysis"]["analysisCoverage"]["contracts"] == "available"
    assert payload["analysis"]["analysisCoverage"]["engineSource"] == "not_used"
    lifecycle_findings = [f for f in payload["findings"] if f["code"] == "INVALID_UNREAL_LIFECYCLE_OVERRIDE"]
    assert len(lifecycle_findings) == 1 and "OnWorldDestroyed" in lifecycle_findings[0]["message"]
    metadata = lifecycle_findings[0]["metadata"]
    assert metadata["contractRevision"] == revision
    assert metadata["contractIds"] == ["ue.subsystem.lifecycle"]
    assert metadata["analysisKind"] == "lexical" and metadata["confidence"] == "high"
    assert metadata["evidenceRefs"] == [{"inheritancePath": ["UTickableWorldSubsystem", "UWorldSubsystem", "USubsystem", "UObject"]}]
    assert all("metadata" not in f or f["metadata"] for f in payload["findings"])


def test_node_adapter_preserves_metadata_and_analysis(tmp_path):
    project = _project_with_invalid_override(tmp_path)
    script = ("const {runStaticValidation}=require('./src/direct-static-validation.js');"
              "runStaticValidation(process.argv[1],{envRoot:process.argv[2]}).then(r=>"
              "process.stdout.write(JSON.stringify({analysis:r.analysis,findings:r.findings})))")
    result = subprocess.run(["node", "-e", script, str(project), str(ROOT)], cwd=ROOT / "lmstudio-unreal-agent-mcp",
                            capture_output=True, text=True, encoding="utf-8", check=True, timeout=180)
    payload = json.loads(result.stdout)
    revision = contracts.contract_revision(contracts.load_contracts())
    assert payload["analysis"]["contractRevision"] == revision
    finding = next(f for f in payload["findings"] if f["code"] == "INVALID_UNREAL_LIFECYCLE_OVERRIDE")
    assert finding["metadata"]["contractRevision"] == revision
