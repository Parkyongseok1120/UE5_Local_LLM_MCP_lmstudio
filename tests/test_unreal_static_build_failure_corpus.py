"""Regression corpus: real Unreal build failures the static validator should catch before UBT.

``tests/fixtures/ue_build_failures/cases.json`` lists 39 minimal reproductions of the 51 failed
``build_unreal_project`` results recorded in local LM Studio sessions (UE 5.7.4). Every case is a small
hand-written snippet; ``engine_stub`` holds the few engine declarations the engine-backed rules read,
at their real UE 5.7 header paths. A case is caught when ``validate_unreal_readiness`` reports one of
its expected codes at the expected file and line. Each case's ``fix`` is the change that made the real
build pass, and the expected code must disappear after it.

Catch rate, weighted by failed builds (51). "Blocking" counts findings with severity ``error``. The
numbers are identical against ``engine_stub`` and the installed UE 5.7.4.

| Failure class (failed builds)               | Before (blocking) | After | After, blocking |
|---------------------------------------------|------------------:|------:|----------------:|
| Engine type/member does not exist (13)      |             0 (0) |    13 |              13 |
| Engine include path not found (11)          |             0 (0) |    11 |               5 |
| Engine type used without its header (11)    |             5 (0) |    11 |               2 |
| UnrealHeaderTool error (7)                  |             0 (0) |     7 |               7 |
| Other C++ semantics (6)                     |             0 (0) |     3 |               3 |
| Override signature differs from engine (3)  |             0 (0) |     3 |               3 |
| **Total (51)**                              |  **5 (10%), 0 blocking** | **48 (94%)** | **33 (65%)** |

As a check on real code (not committed): replaying the reconstructed full source of one session behind
32 of these failures against UE 5.7.4, a finding within one line of the compiler's error location appeared
for 30 of them (19 errors), versus 5 (0 errors) before. The project's current, building source produces
no findings from the new codes.

Not blocking by design: includes whose file name no public engine header has (third-party and
private headers are not catalogued) and missing complete-type includes (engine headers may supply
them transitively) stay warnings.

Three failures (a missing project struct member, an ambiguous ``TObjectPtr`` ternary and an undeclared
project member) have no target rule. ``python tests/test_unreal_static_build_failure_corpus.py``
prints the current table; set ``UE_CORPUS_ENGINE_ROOT`` to measure against an installed engine too.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from engine_identity import describe_unreal_engine_identity  # noqa: E402
from unreal_static_runner import validate_unreal_readiness  # noqa: E402

CORPUS = ROOT / "tests" / "fixtures" / "ue_build_failures"
MANIFEST = json.loads((CORPUS / "cases.json").read_text(encoding="utf-8"))
CASES = MANIFEST["cases"]
TARGETED = [case for case in CASES if case["expect"]]
ENGINE_STUB = CORPUS / MANIFEST["engineStub"]
REAL_ENGINE = Path(os.environ.get("UE_CORPUS_ENGINE_ROOT", "C:/Program Files/Epic Games/UE_5.7"))
# Engine-backed rules; without an engine they must stay silent, not fail.
ENGINE_CODES = frozenset({
    "ENGINE_INCLUDE_NOT_FOUND", "ENGINE_MEMBER_NOT_FOUND", "ENGINE_OVERRIDE_SIGNATURE_MISMATCH",
    "UPROPERTY_SHADOWS_BASE_MEMBER", "UFUNCTION_OVERRIDE_REDECLARED", "REPLICATED_USING_FUNCTION_MISSING",
    "PARAMETER_SHADOWS_ENGINE_MEMBER",
})


def _identity(engine_root: Path) -> dict:
    return describe_unreal_engine_identity(engine_root)


def _findings(project: Path, identity: dict):
    return validate_unreal_readiness(project, engine_identity=identity)


def _matches(case: dict, findings) -> list:
    expect = case["expect"]
    return [f for f in findings
            if f.code in expect["codes"]
            and str(f.path).replace("\\", "/") == expect["path"]
            and f.line == expect["line"]]


def measure(identity: dict) -> dict[str, dict[str, int]]:
    """Failed builds per class: total, caught (any severity) and caught by an error."""
    table: dict[str, dict[str, int]] = {}
    for case in CASES:
        row = table.setdefault(case["class"], {"failedBuilds": 0, "caught": 0, "blocking": 0})
        row["failedBuilds"] += case["failedBuilds"]
        if not case["expect"]:
            continue
        hits = _matches(case, _findings(CORPUS / "cases" / case["id"], identity))
        if hits:
            row["caught"] += case["failedBuilds"]
        if any(f.severity == "error" for f in hits):
            row["blocking"] += case["failedBuilds"]
    return table


def _apply_fix(case: dict, destination: Path) -> Path:
    project = destination / case["id"]
    shutil.copytree(CORPUS / "cases" / case["id"], project)
    for change in case["fix"]:
        path = project / change["path"]
        text = path.read_text(encoding="utf-8")
        assert change["old"] in text
        path.write_text(text.replace(change["old"], change["new"], 1), encoding="utf-8", newline="\n")
    return project


def test_manifest_accounts_for_every_failed_build():
    assert sum(case["failedBuilds"] for case in CASES) == MANIFEST["failedBuilds"] == 51
    assert len({case["id"] for case in CASES}) == len(CASES)
    for case in CASES:
        assert (CORPUS / "cases" / case["id"] / "Source" / "Demo" / "Demo.Build.cs").is_file()


@pytest.mark.parametrize("case", TARGETED, ids=[case["id"] for case in TARGETED])
def test_case_is_caught_and_cleared_by_the_real_fix(case, tmp_path):
    identity = _identity(ENGINE_STUB)
    findings = _findings(CORPUS / "cases" / case["id"], identity)
    hits = _matches(case, findings)
    assert hits, [(f.code, f.path, f.line) for f in findings]
    for hit in hits:
        assert hit.message
        if hit.code in ENGINE_CODES:
            assert hit.metadata["analysisKind"] == "engine_header_lexical"
            assert hit.metadata["confidence"] in {"high", "medium"}
    fixed = _findings(_apply_fix(case, tmp_path), identity)
    expect = case["expect"]
    leftover = [f for f in fixed if f.code in expect["codes"]
                and str(f.path).replace("\\", "/") == expect["path"]]
    assert not leftover, [(f.code, f.line, f.message) for f in leftover]


@pytest.mark.parametrize("case", [c for c in TARGETED if c["requiresEngine"]],
                         ids=[c["id"] for c in TARGETED if c["requiresEngine"]])
def test_engine_rules_degrade_to_no_finding_without_an_engine(case, tmp_path):
    unavailable = {"status": "unknown", "engine": "unreal", "reason": "no engine"}
    missing_root = {**_identity(ENGINE_STUB), "engineRoot": str(tmp_path / "missing")}
    for identity in (unavailable, missing_root):
        findings = _findings(CORPUS / "cases" / case["id"], identity)
        assert not [f for f in findings if f.code in ENGINE_CODES]
        assert not [f for f in findings if f.code == "DOMAIN_VALIDATOR_INTERNAL_ERROR"]


def test_catch_rate_does_not_regress():
    table = measure(_identity(ENGINE_STUB))
    caught = sum(row["caught"] for row in table.values())
    blocking = sum(row["blocking"] for row in table.values())
    assert caught >= 48
    assert blocking >= 33


@pytest.mark.slow
@pytest.mark.skipif(not (REAL_ENGINE / "Engine" / "Build" / "Build.version").is_file(),
                    reason="installed engine not available")
@pytest.mark.parametrize("case", [c for c in TARGETED if c["requiresEngine"]],
                         ids=[c["id"] for c in TARGETED if c["requiresEngine"]])
def test_engine_backed_cases_against_the_installed_engine(case, tmp_path):
    identity = _identity(REAL_ENGINE)
    findings = _findings(CORPUS / "cases" / case["id"], identity)
    assert _matches(case, findings), [(f.code, f.path, f.line) for f in findings]
    fixed = _findings(_apply_fix(case, tmp_path), identity)
    assert not [f for f in fixed if f.code in case["expect"]["codes"]
                and str(f.path).replace("\\", "/") == case["expect"]["path"]]


def _print_table(label: str, table: dict[str, dict[str, int]]) -> None:
    print(f"\n{label}\n| Class | Failed builds | Caught | Blocking |\n|---|---:|---:|---:|")
    for name, row in sorted(table.items(), key=lambda item: -item[1]["failedBuilds"]):
        print(f"| {name} | {row['failedBuilds']} | {row['caught']} | {row['blocking']} |")
    total = sum(row["failedBuilds"] for row in table.values())
    caught = sum(row["caught"] for row in table.values())
    blocking = sum(row["blocking"] for row in table.values())
    print(f"| total | {total} | {caught} ({caught / total:.0%}) | {blocking} ({blocking / total:.0%}) |")


if __name__ == "__main__":
    _print_table(f"engine stub ({ENGINE_STUB.name})", measure(_identity(ENGINE_STUB)))
    if (REAL_ENGINE / "Engine" / "Build" / "Build.version").is_file():
        _print_table(f"installed engine ({REAL_ENGINE})", measure(_identity(REAL_ENGINE)))
