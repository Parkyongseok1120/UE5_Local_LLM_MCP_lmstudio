"""Engine-version policy for the lexical Unreal validators: validated / same-major / unknown."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import unreal_contracts as contracts  # noqa: E402
from unreal_static_build import validate_enhanced_input  # noqa: E402
from unreal_static_engine import apply_engine_version_policy  # noqa: E402
from unreal_static_model import Finding  # noqa: E402
from unreal_static_runner import validate_unreal_readiness_report  # noqa: E402

DATA = contracts.load_contracts()
VALIDATED = DATA["validatedEngineVersions"][0]
MAJOR = VALIDATED.split(".")[0]


def _identity(version: str | None) -> dict:
    if version is None:
        return {"status": "unknown", "reason": "not readable"}
    return {"status": "observed", "version": version, "source": "build_version_file"}


def _finding() -> Finding:
    return Finding("error", "A.h", 3, "INVALID_UNREAL_LIFECYCLE_OVERRIDE", "bad",
                   metadata={"confidence": "high", "limitations": ["macros are not expanded"]})


@pytest.mark.parametrize("version, tier, severity", [
    (f"{VALIDATED}.2", "validated", "error"),
    (f"{MAJOR}.0.1", "same_major_unvalidated", "warning"),
    ("4.27.2", "other_major", "warning"),
    (None, "unknown", "warning"),
])
def test_version_dependent_finding_severity_follows_the_tier(version, tier, severity):
    [result] = apply_engine_version_policy([_finding()], _identity(version), DATA)
    assert result.severity == severity
    if tier == "validated":
        assert result.metadata["confidence"] == "high" and "engineVersionTier" not in result.metadata
        return
    assert result.metadata["engineVersionTier"] == tier
    assert result.metadata["confidence"] == "medium"
    assert f"override table validated on {', '.join(DATA['validatedEngineVersions'])}" in result.metadata["limitations"]
    assert "macros are not expanded" in result.metadata["limitations"]


def test_findings_unrelated_to_the_override_table_are_untouched():
    other = Finding("error", "A.h", 1, "CPP_RETURN_TYPE_MISMATCH", "x")
    assert apply_engine_version_policy([other], _identity(None), DATA) == [other]


def test_missing_contract_file_leaves_findings_unchanged():
    finding = _finding()
    assert apply_engine_version_policy([finding], _identity(None), None) == [finding]


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "Demo"
    module = project / "Source" / "Demo"
    module.mkdir(parents=True)
    (module / "Demo.Build.cs").write_text(
        "using UnrealBuildTool;\npublic class Demo : ModuleRules { public Demo(ReadOnlyTargetRules Target) : base(Target) {} }\n",
        encoding="utf-8")
    (module / "MySubsystem.h").write_text(
        '#pragma once\n#include "CoreMinimal.h"\n#include "Subsystems/WorldSubsystem.h"\n#include "MySubsystem.generated.h"\n\n'
        "UCLASS()\nclass DEMO_API UMySubsystem : public UTickableWorldSubsystem\n{\n\tGENERATED_BODY()\npublic:\n"
        "\tvirtual void OnWorldDestroyed() override;\n};\n", encoding="utf-8")
    return project


@pytest.mark.parametrize("version, severity", [(VALIDATED, "error"), (f"{MAJOR}.0", "warning"), (None, "warning")])
def test_report_applies_the_injected_engine_identity(tmp_path, version, severity):
    report = validate_unreal_readiness_report(_project(tmp_path), engine_identity=_identity(version))
    [finding] = [f for f in report.findings if f.code == "INVALID_UNREAL_LIFECYCLE_OVERRIDE"]
    assert finding.severity == severity
    assert report.engineIdentity == _identity(version)


@pytest.mark.parametrize("version, severity", [(VALIDATED, "error"), (f"{MAJOR}.0", "warning"), (None, "warning")])
def test_plain_list_api_uses_the_same_version_policy(tmp_path, version, severity):
    from unreal_static_runner import validate_unreal_readiness
    findings = validate_unreal_readiness(_project(tmp_path), engine_identity=_identity(version))
    [finding] = [f for f in findings if f.code == "INVALID_UNREAL_LIFECYCLE_OVERRIDE"]
    assert finding.severity == severity


def test_legacy_bind_action_is_a_low_confidence_review_warning_not_an_error(tmp_path):
    source = tmp_path / "Source" / "Pawn.cpp"
    source.parent.mkdir(parents=True)
    text = "void APawnX::SetupPlayerInputComponent(UInputComponent* PlayerInputComponent)\n{\n\tPlayerInputComponent->BindAction(\"Jump\", IE_Pressed, this, &APawnX::Jump);\n}\n"
    source.write_text(text, encoding="utf-8")
    [finding] = [f for f in validate_enhanced_input(source, text, tmp_path, "") if f.code == "DIRECT_BIND_ACTION"]
    assert finding.severity == "warning" and finding.metadata["confidence"] == "low"
    assert "valid in UE 5.x" in finding.message and "Do not call" not in finding.message
