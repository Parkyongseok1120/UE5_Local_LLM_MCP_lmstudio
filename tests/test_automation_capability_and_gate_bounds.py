from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from task_api import (
    task_require_automation_after_build,
    task_root,
    task_start,
)
from unreal_capability_detection import detect_unreal_capabilities


def test_portable_source_extensions_have_python_js_discovery_parity(
    tmp_path: Path,
) -> None:
    project = tmp_path / "PortableSources" / "PortableSources.uproject"
    project.parent.mkdir()
    project.write_text(json.dumps({"FileVersion": 3}), encoding="utf-8")
    declarations = {
        "Source/Portable/Private/MacTests.mm": 'TEST(MacFixture, "Portable.CQ") {}',
        "Source/Portable/Public/HeaderTests.hpp": (
            'IMPLEMENT_SIMPLE_AUTOMATION_TEST(FHeader, "Portable.Header.Hpp", Flags)'
        ),
        "Source/Portable/Public/InlineTests.inl": (
            'DEFINE_SPEC(FInline, "Portable.Header.Inline", Flags)'
        ),
        "Source/Portable/Notes.txt": (
            'IMPLEMENT_SIMPLE_AUTOMATION_TEST('
            'FText, "Portable.Text.MustNotRegister", Flags)'
        ),
    }
    for relative_path, content in declarations.items():
        target = project.parent / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    python_result = detect_unreal_capabilities(project, host_platform="linux")
    python_names = {
        row["name"] for row in python_result["execution"]["automationTests"]
    }
    expected = {
        "Portable.CQ.MacFixture",
        "Portable.Header.Hpp",
        "Portable.Header.Inline",
    }
    assert python_names == expected

    node = shutil.which("node")
    if node is None:
        raise AssertionError("Node.js is required for the JS/Python parity test")
    module_path = ROOT / "lmstudio-unreal-agent-mcp" / "src" / "automation-executor.js"
    script = (
        "const { discoverAutomationTests } = require(process.argv[1]);"
        "process.stdout.write(JSON.stringify(discoverAutomationTests(process.argv[2]).names));"
    )
    completed = subprocess.run(
        [node, "-e", script, str(module_path), str(project.parent)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert set(json.loads(completed.stdout)) == python_names


def test_capability_detection_recognizes_define_spec_variants(tmp_path: Path) -> None:
    project = tmp_path / "Portable" / "Portable.uproject"
    project.parent.mkdir()
    project.write_text(json.dumps({"FileVersion": 3}), encoding="utf-8")
    source = project.parent / "Source" / "Portable" / "Private"
    source.mkdir(parents=True)
    (source / "PortableSpecs.cpp").write_text(
        'DEFINE_SPEC(FDefineSpec, TEXT("Portable.Spec.Define"), Flags)\n'
        'BEGIN_DEFINE_SPEC(FBeginSpec, TEXT ( "Portable.Spec.Begin" ), Flags)\n'
        'IMPLEMENT_CUSTOM_SIMPLE_AUTOMATION_TEST('
        'FCustom, FAutomationTestBase, TEXT("Portable.Custom.Simple"), Flags)\n'
        'IMPLEMENT_NETWORKED_AUTOMATION_TEST('
        'FNetworked, "Portable.Networked", Flags)',
        encoding="utf-8",
    )

    result = detect_unreal_capabilities(project, host_platform="linux")

    assert result["execution"]["automationDeclared"] is True
    assert {
        row["name"] for row in result["execution"]["automationTests"]
    } == {
        "Portable.Custom.Simple",
        "Portable.Networked",
        "Portable.Spec.Begin",
        "Portable.Spec.Define",
    }


def test_capability_detection_composes_official_cqtest_directory_and_name(
    tmp_path: Path,
) -> None:
    project = tmp_path / "CQPortable" / "CQPortable.uproject"
    project.parent.mkdir()
    project.write_text(json.dumps({"FileVersion": 3}), encoding="utf-8")
    source = project.parent / "Source" / "CQPortable" / "Private"
    source.mkdir(parents=True)
    (source / "CQTests.cpp").write_text(
        '#include "CQTest.h"\n'
        'TEST(MinimalTest, "Game.MyGame") {}\n'
        'TEST_CLASS(MyFixture, TEXT("Game.MyGame")) {\n'
        '  TEST_METHOD(ChildMethod) {}\n'
        '};\n'
        'TEST_CLASS_WITH_ASSERTS(AssertFixture, "Game.Assert", FAsserter) {};\n'
        'TEST_CLASS_WITH_BASE(BaseFixture, "Game.Base", FBase) {};\n'
        'TEST_CLASS_WITH_FLAGS(FlagsFixture, "Game.Flags", Flags) {};\n'
        'TEST_CLASS_WITH_BASE_AND_FLAGS(BothFixture, "Game.Both", FBase, Flags) {};\n'
        'TEST_METHOD(StandaloneMethodMustNotRegister) {}\n'
        'TEST_CLASS_IMPL(InternalMacroMustNotRegister, "Game.Internal", Extra) {};\n'
        'TEST_CLASS_WITH_NOT_REAL(FakeVariant, "Game.Fake", Extra) {};\n'
        '// TEST(CommentOnly, "Game.Comment") {}\n'
        '/* TEST_CLASS(BlockComment, "Game.Comment") {} */\n'
        'const char* Example = "TEST(StringOnly, \\"Game.String\\")";\n'
        'const char* Raw = R"cq(TEST(RawOnly, "Game.String"))cq";\n'
        '#define WRAPPED TEST(DefinitionOnly, "Game.Definition")\n'
        + '#define WRAPPED_MULTILINE '
        + "\\"
        + '\n  TEST(DefinitionContinuationOnly, "Game.Definition")',
        encoding="utf-8",
    )

    result = detect_unreal_capabilities(project, host_platform="linux")

    assert result["execution"]["automationDeclared"] is True
    assert {
        row["name"] for row in result["execution"]["automationTests"]
    } == {
        "Game.Assert.AssertFixture",
        "Game.Base.BaseFixture",
        "Game.Both.BothFixture",
        "Game.Flags.FlagsFixture",
        "Game.MyGame.MinimalTest",
        "Game.MyGame.MyFixture",
    }


def test_automation_filter_persistence_batches_total_limit_and_rejects_overflow(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="Verify a bounded project-independent slice",
        mode="agent_edit",
        plan_payload={
            "taskKind": "codegen",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 1},
            "orchestration": {"requiredBeforeWrite": []},
            "executablePlanSlices": [
                {"sliceId": "runtime", "files": ["Source/Runtime/Feature.cpp"]}
            ],
        },
    )
    bounded = [f"Portable.Filter{index:04d}" for index in range(4096)]
    state_path = task_root(tmp_path, started["taskSessionId"]) / "state.json"
    post_static = json.loads(state_path.read_text(encoding="utf-8"))
    post_static["controlState"] = {
        "version": 2,
        "authoritative": True,
        "activeSliceId": "runtime",
        "mutationGeneration": 0,
        "requiredTool": {"name": "build_unreal_project", "args": {}},
    }
    post_static.setdefault("continuity", {})["checkpoint"] = {
        "activeSliceId": "runtime",
        "mutationGeneration": 0,
        "validation": {"status": "passed"},
    }
    state_path.write_text(json.dumps(post_static), encoding="utf-8")

    overflow = task_require_automation_after_build(
        tmp_path,
        task_authorization=started["taskAuthorization"],
        mutation_generation=0,
        build_log_path=".agent/logs/overflow-build.log",
        test_filter="",
        test_filters=[*bounded, "Portable.FilterOverflow"],
        declared_tests=[],
    )

    assert overflow["ok"] is False
    assert overflow["errorCode"] == "AUTOMATION_FILTER_SET_TOO_LARGE"
    assert overflow["filterCount"] == 4097
    assert overflow["maxFilters"] == 4096
    after_overflow = json.loads(state_path.read_text(encoding="utf-8"))
    assert "buildVerification" not in after_overflow

    accepted = task_require_automation_after_build(
        tmp_path,
        task_authorization=started["taskAuthorization"],
        mutation_generation=0,
        build_log_path=".agent/logs/latest-build.log",
        test_filter="",
        test_filters=bounded,
        declared_tests=[f"{name}.Case" for name in bounded],
    )

    assert accepted["ok"] is True
    assert accepted["testFilters"] == bounded[:256]
    assert accepted["filterBatchCount"] == 16
    before_overflow = json.loads(state_path.read_text(encoding="utf-8"))
    assert before_overflow["buildVerification"]["testFilters"] == bounded[:256]
    assert before_overflow["buildVerification"]["allFilterCount"] == 4096
    assert len(before_overflow["buildVerification"]["remainingFilterBatches"]) == 15
