from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import error_taxonomy  # noqa: E402


def test_route_generated_h_error_to_reflection_fix():
    route = error_taxonomy.route_error_action("BadComponent.generated.h must be the last include")

    assert route["errorSubkind"] == "GENERATED_H_NOT_LAST"
    assert route["broadMode"] == "reflection_fix"
    assert route["preferredRagModes"] == ["reflection_fix", "compile_fix"]
    assert "failing header" in route["allowedPatchTargets"]
    assert "broad refactor" in route["forbiddenActions"]


def test_route_missing_include_to_module_fix():
    route = error_taxonomy.route_error_action(
        "fatal error C1083: Cannot open include file: 'GameplayTagContainer.h': No such file or directory"
    )

    assert route["broadMode"] == "module_fix"
    assert "owner Build.cs" in route["requiredReads"]
    assert "owner Build.cs" in route["allowedPatchTargets"]
    assert "explaining dependency without Build.cs patch" in route["forbiddenActions"]


def test_route_link_error_avoids_build_cs_first():
    route = error_taxonomy.route_error_action("error LNK2019: unresolved external symbol Foo")

    assert route["errorSubkind"] == "LNK_MISSING_CPP_DEFINITION"
    assert route["broadMode"] == "compile_fix"
    assert "Build.cs-first fix without module evidence" in route["forbiddenActions"]
