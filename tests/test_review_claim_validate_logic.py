from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from review_claim_validate import validate_claim  # noqa: E402


def _write_authored_world_fixture(project_root: Path) -> None:
    public = project_root / "Source" / "DemoGame" / "Public" / "Cinematic"
    private = project_root / "Source" / "DemoGame" / "Private" / "Cinematic"
    public.mkdir(parents=True)
    private.mkdir(parents=True)
    (public / "CinematicTypes.h").write_text(
        "\n".join(
            [
                "#pragma once",
                "UENUM(BlueprintType)",
                "enum class ECinematicAnchorMode : uint8",
                "{",
                "\t// Level Sequence 에셋에 저장된 위치/회전을 그대로 사용.",
                "\tAuthoredWorld UMETA(DisplayName = \"Authored World\"),",
                "\tInstigatorActor,",
                "};",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (private / "CinematicDirectorSubsystem.cpp").write_text(
        "\n".join(
            [
                '#include "Cinematic/CinematicDirectorSubsystem.h"',
                "void UCinematicDirectorSubsystem::ApplyDynamicTransform() const",
                "{",
                "\tif (Request.AnchorMode == ECinematicAnchorMode::AuthoredWorld)",
                "\t{",
                "\t\treturn;",
                "\t}",
                "\tActiveSequenceActor->SetActorTransform(OutAnchorWorldTransform);",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (public / "CinematicDirectorSubsystem.h").write_text(
        "\n".join(
            [
                "#pragma once",
                "class UCinematicDirectorSubsystem",
                "{",
                "\tvoid ApplyDynamicTransform() const;",
                "};",
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_authored_world_missing_logic_claim_fails_by_design(tmp_path: Path) -> None:
    project = tmp_path / "DemoGame"
    project.mkdir()
    _write_authored_world_fixture(project)

    result = validate_claim(
        "AuthoredWorld 로직 누락 — SetActorTransform을 호출하지 않아 버그",
        project,
        pab={},
    )
    assert result["ok"] is False
    assert "by_design_contract" in result["reasons"]
    assert any("by-design" in issue.lower() or "intentional" in issue.lower() for issue in result["issues"])


def test_cpp_only_bug_claim_fails_header_unread(tmp_path: Path) -> None:
    project = tmp_path / "DemoGame"
    project.mkdir()
    _write_authored_world_fixture(project)

    result = validate_claim(
        "Bug in Source/DemoGame/Private/Cinematic/CinematicDirectorSubsystem.cpp: "
        "AuthoredWorld early return is missing logic",
        project,
        pab={},
    )
    assert result["ok"] is False
    assert "header_contract_unread" in result["reasons"] or "by_design_contract" in result["reasons"]


def test_eval_case_bad_answer_fails_claim_validate(tmp_path: Path) -> None:
    import json

    cases = json.loads(
        (ROOT / "config" / "rag_eval_project_review_cases.json").read_text(encoding="utf-8-sig")
    )
    case = next(c for c in cases["cases"] if c["id"] == "project_example_authored_world_by_design")
    project = tmp_path / "DemoGame"
    project.mkdir()
    for snippet in case["snippets"]:
        path = project / snippet["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(snippet["content"], encoding="utf-8")

    result = validate_claim(case["badAnswerFixture"], project, pab={})
    assert result["ok"] is False
    assert "by_design_contract" in result["reasons"]


def test_core_review_cases_include_authored_world_false_positive() -> None:
    import json

    cases = json.loads(
        (ROOT / "config" / "rag_eval_project_review_cases.json").read_text(encoding="utf-8-sig")
    )
    ids = {c["id"] for c in cases["cases"]}
    assert "project_example_authored_world_by_design" in ids
