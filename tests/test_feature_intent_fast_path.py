from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from feature_intent_fast_path import (
    _scope_owner,
    discover_project_test_convention,
    evaluate_bounded_local_fast_path,
)


def test_fast_path_accepts_existing_two_file_local_slice() -> None:
    targets = ["Source/Demo/Thing.h", "Source/Demo/Thing.cpp"]
    decision = evaluate_bounded_local_fast_path(
        "Add a null guard in Source/Demo/Thing.cpp and preserve existing behavior.",
        target_files=targets,
        target_snapshots=[
            {"path": path, "exists": True, "fileHash": f"hash-{index}"}
            for index, path in enumerate(targets)
        ],
    )

    assert decision["eligible"] is True
    assert decision["selectedIntentId"] == "bounded_local"
    assert decision["serverOwnedPhases"] == [
        "SelectIntent",
        "ResolveSlice",
        "CaptureSnapshot",
        "BindIntent",
    ]


def test_fast_path_rejects_new_or_cross_authority_work() -> None:
    decision = evaluate_bounded_local_fast_path(
        "Create a replicated server subsystem for clients.",
        target_files=["Source/Demo/NewService.cpp"],
        target_snapshots=[
            {"path": "Source/Demo/NewService.cpp", "exists": False, "fileHash": ""}
        ],
    )

    assert decision["eligible"] is False
    assert decision["selectedIntentId"] == ""
    assert any("cannot create" in reason for reason in decision["reasons"])
    assert any("authority" in reason for reason in decision["reasons"])


def test_fast_path_accepts_one_new_test_source_under_existing_module_tests() -> None:
    target = "Source/O_Mock/Tests/GomokuStage1CoreRules.spec.cpp"
    decision = evaluate_bounded_local_fast_path(
        "Complete the local-play rule and add the required automated test.",
        target_files=[target],
        target_snapshots=[
            {
                "path": target,
                "exists": False,
                "parentExists": True,
                "fileHash": "",
                "projectConventionEvidence": {
                    "conforms": True,
                    "owner": _scope_owner(target),
                    "targetStyle": "dot_spec",
                    "matchingSiblingCount": 1,
                },
            }
        ],
    )

    assert decision["eligible"] is True
    assert decision["selectedIntentId"] == "bounded_local"
    assert decision["newAutomationTestFiles"] == [target]


@pytest.mark.parametrize(
    ("sibling_name", "target_name", "expected_style"),
    [
        ("Existing.spec.cpp", "NewRule.spec.cpp", "dot_spec"),
        ("ExistingTests.cpp", "NewRuleTests.cpp", "plural_tests"),
        ("ExistingTest.cpp", "NewRuleTest.cpp", "singular_test"),
        ("Existing.Automation.cpp", "NewRule.Automation.cpp", "dot_automation"),
    ],
)
def test_project_convention_discovery_is_name_and_module_driven(
    tmp_path: Path,
    sibling_name: str,
    target_name: str,
    expected_style: str,
) -> None:
    project = tmp_path / "ArbitraryProject"
    directory = project / "Source" / "ArbitraryRuntime" / "Private" / "Specs"
    directory.mkdir(parents=True)
    (directory / sibling_name).write_text("// existing convention\n", encoding="utf-8")
    relative = f"Source/ArbitraryRuntime/Private/Specs/{target_name}"

    evidence = discover_project_test_convention(project, relative)

    assert evidence["conforms"] is True
    assert evidence["owner"] == _scope_owner(relative)
    assert evidence["targetStyle"] == expected_style


def test_new_test_fast_path_rejects_directory_name_without_convention_evidence() -> None:
    target = "Plugins/Feature/Source/FeatureRuntime/Private/Tests/NewRule.spec.cpp"
    decision = evaluate_bounded_local_fast_path(
        "Add an automated test for the local rule.",
        target_files=[target],
        target_snapshots=[{
            "path": target,
            "exists": False,
            "parentExists": True,
            "fileHash": "",
            "projectConventionEvidence": {
                "conforms": False,
                "owner": _scope_owner(target),
            },
        }],
    )

    assert decision["eligible"] is False
    assert any("cannot create" in reason for reason in decision["reasons"])


def test_fast_path_module_grouping_uses_injected_host_case_rules() -> None:
    targets = ["Source/Demo/One.cpp", "Source/demo/Two.cpp"]
    snapshots = [{"path": path, "exists": True} for path in targets]

    posix = evaluate_bounded_local_fast_path(
        "Add local guards to both files.",
        target_files=targets,
        target_snapshots=snapshots,
        host_platform="linux",
    )
    windows = evaluate_bounded_local_fast_path(
        "Add local guards to both files.",
        target_files=targets,
        target_snapshots=snapshots,
        host_platform="win32",
    )

    assert posix["eligible"] is False
    assert any("one existing Source module owner" in reason for reason in posix["reasons"])
    assert windows["eligible"] is True


def test_fast_path_does_not_merge_unicode_casefold_module_owners() -> None:
    composed = "Source/\u0130Module/One.cpp"
    decomposed = "Source/I\u0307Module/Two.cpp"
    assert composed.casefold().split("/")[1] == decomposed.casefold().split("/")[1]
    targets = [composed, decomposed]
    snapshots = [{"path": path, "exists": True} for path in targets]

    for host_platform in ("linux", "win32"):
        decision = evaluate_bounded_local_fast_path(
            "Add local guards to both files.",
            target_files=targets,
            target_snapshots=snapshots,
            host_platform=host_platform,
        )
        assert decision["eligible"] is False
        assert any(
            "one existing Source module owner" in reason
            for reason in decision["reasons"]
        )
