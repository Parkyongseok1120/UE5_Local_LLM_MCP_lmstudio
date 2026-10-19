"""Unreal contract data: strict schema, single source, and metadata transport."""
from __future__ import annotations

import copy
import json
import os
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
    full = contracts.load_contracts()
    assert unreal_api_denylist.INVALID_LIFECYCLE_OVERRIDES == {
        name: contracts.render_contract_text(full, message) for name, message in data["invalidOverrideNames"].items()}
    assert "{validatedEngineVersions}" not in " ".join(unreal_api_denylist.INVALID_LIFECYCLE_OVERRIDES.values())
    assert ", ".join(full["validatedEngineVersions"]) in unreal_api_denylist.INVALID_LIFECYCLE_OVERRIDES["onworlddestroyed"]
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
    (lambda d: d.update(engine="unity"), "engine must be"),
    (lambda d: d.pop("engine"), "unexpected top-level keys"),
    (lambda d: d.update(extra=1), "unexpected top-level keys"),
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


def _fake_engine(tmp_path: Path, build_version: dict | None, name: str = "FakeEngine") -> Path:
    engine = tmp_path / name
    (engine / "Engine" / "Source").mkdir(parents=True)
    if build_version is not None:
        (engine / "Engine" / "Build").mkdir(parents=True)
        (engine / "Engine" / "Build" / "Build.version").write_text(json.dumps(build_version), encoding="utf-8")
    return engine


def _run_cli(project: Path, engine: Path) -> dict:
    (project / "Demo.uproject").write_text(json.dumps({"FileVersion": 3, "EngineAssociation": ""}), encoding="utf-8")
    env = {**os.environ, "UNREAL_ENGINE_ROOT": str(engine)}
    result = subprocess.run([sys.executable, str(SCRIPTS / "validate_project_sources.py"), "--project-root",
                             str(project), "--json"], cwd=str(ROOT), capture_output=True, text=True,
                            encoding="utf-8", check=False, env=env)
    return json.loads(result.stdout)


def test_unverifiable_engine_identity_is_reported_and_lowers_version_dependent_findings(tmp_path):
    project = _project_with_invalid_override(tmp_path)
    payload = _run_cli(project, _fake_engine(tmp_path, None))
    identity = payload["analysis"]["engineIdentity"]
    assert identity["status"] == "unknown" and identity["reason"]
    finding = next(f for f in payload["findings"] if f["code"] == "INVALID_UNREAL_LIFECYCLE_OVERRIDE")
    assert finding["severity"] == "warning" and not finding["blocking"]
    assert finding["metadata"]["confidence"] == "medium"
    assert any("override table validated on" in item for item in finding["metadata"]["limitations"])


def test_cli_json_keeps_finding_metadata_and_analysis_coverage(tmp_path):
    project = _project_with_invalid_override(tmp_path)
    validated = contracts.load_contracts()["validatedEngineVersions"][0].split(".")
    engine = _fake_engine(tmp_path, {"MajorVersion": int(validated[0]), "MinorVersion": int(validated[1]),
                                     "PatchVersion": 0})
    payload = _run_cli(project, engine)
    revision = contracts.contract_revision(contracts.load_contracts())
    assert payload["analysis"]["contractRevision"] == revision
    assert payload["analysis"]["analysisCoverage"]["contracts"] == "available"
    assert payload["analysis"]["analysisCoverage"]["engineSource"] == "not_used"
    identity = payload["analysis"]["engineIdentity"]
    assert identity["status"] == "observed" and identity["source"] == "build_version_file"
    assert identity["version"] == f"{validated[0]}.{validated[1]}.0"
    lifecycle_findings = [f for f in payload["findings"] if f["code"] == "INVALID_UNREAL_LIFECYCLE_OVERRIDE"]
    assert len(lifecycle_findings) == 1 and "OnWorldDestroyed" in lifecycle_findings[0]["message"]
    assert lifecycle_findings[0]["severity"] == "error"
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
