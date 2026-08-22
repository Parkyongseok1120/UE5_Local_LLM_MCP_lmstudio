#!/usr/bin/env python
"""Test write validation catches bad include paths."""

from __future__ import annotations

import tempfile
from pathlib import Path

from lmstudio_unreal_wrapper import has_static_errors, validate_unreal_readiness


def main() -> int:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        source = root / "Source" / "Demo"
        source.mkdir(parents=True)
        (source / "Demo.Build.cs").write_text(
            'using UnrealBuildTool;\npublic class Demo : ModuleRules { public Demo(ReadOnlyTargetRules Target) : base(Target) { PublicDependencyModuleNames.AddRange(new string[] { "Core", "Engine" }); } }\n',
            encoding="utf-8",
        )
        bad_cpp = source / "Private" / "BadCharacter.cpp"
        bad_cpp.parent.mkdir(parents=True)
        bad_cpp.write_text(
            '#include "Game/Framework/Character.h"\nvoid Foo() {}\n',
            encoding="utf-8",
        )
        findings = validate_unreal_readiness(root)
        codes = {f.code for f in findings}
        if "BAD_INCLUDE_PATH" not in codes:
            print("[FAIL] BAD_INCLUDE_PATH not detected")
            return 1
        if not has_static_errors(findings):
            print("[FAIL] expected static errors for bad include")
            return 1
        print("[PASS] BAD_INCLUDE_PATH detected as error")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
