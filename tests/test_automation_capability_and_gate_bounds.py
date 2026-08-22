from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

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
