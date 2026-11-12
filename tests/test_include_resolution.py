"""Include existence follows compiler lookup; only provable misses are errors.

Conversation 1794469194476: a file-relative include and one with "//" in a
file that had just compiled were reported as definite errors and skipped UBT.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from unreal_static_include import (  # noqa: E402
    module_include_roots,
    normalize_include_path,
    validate_include_paths_exist,
)
from unreal_static_scan import build_source_include_index  # noqa: E402
from unreal_static_visibility import _resolve_project_include  # noqa: E402

BUILD_CS = "public class Game : ModuleRules { public Game(ReadOnlyTargetRules T) : base(T) {\n{body}\n} }\n"


def _project(tmp_path: Path, build_body: str = "PublicIncludePaths.Add(ModuleDirectory);") -> Path:
    project = tmp_path / "Game"
    files = {
        "Source/Game/Game.Build.cs": BUILD_CS.replace("{body}", build_body),
        "Source/Game/Enemy/AI/Controller.h": "#pragma once\n",
        "Source/Game/Sound/Data.h": "#pragma once\n",
        "Source/Game/Item/Orb.h": "#pragma once\n",
        "Source/Game/Extra/Tool.h": "#pragma once\n",
    }
    for relative, text in files.items():
        (project / relative).parent.mkdir(parents=True, exist_ok=True)
        (project / relative).write_text(text, encoding="utf-8")
    return project


def _check(project: Path, text: str) -> list[tuple[int, str, str]]:
    target = project / "Source/Game/Enemy/Enemy.cpp"
    target.write_text(text, encoding="utf-8")
    index = build_source_include_index(project)
    return [(item.line, item.severity, item.code) for item in validate_include_paths_exist(target, text, project, index)]


def test_compiling_forms_resolve_and_a_wrong_path_is_an_error(tmp_path: Path) -> None:
    project = _project(tmp_path)
    text = ('#include "AI/Controller.h"\n#include "Sound//Data.h"\n#include "./AI/Controller.h"\n'
            '#include "Item/Orb.h"\n#include "Enemy/Item/Orb.h"\n')
    assert _check(project, text) == [(5, "error", "INCLUDE_PATH_NOT_FOUND")]


def test_conditional_commented_and_escaping_includes_are_not_provable(tmp_path: Path) -> None:
    project = _project(tmp_path)
    text = ('#if WITH_EDITOR\n#include "Enemy/Item/Orb.h"\n#endif\n'
            '/*\n#include "Old/Orb.h"\n*/\n'
            '#include "../../../Elsewhere/Orb.h"\n')
    assert _check(project, text) == [(2, "warning", "INCLUDE_PATH_UNVERIFIED"), (7, "warning", "INCLUDE_PATH_UNVERIFIED")]


def test_a_header_guard_is_not_a_conditional(tmp_path: Path) -> None:
    project = _project(tmp_path)
    text = '#ifndef ENEMY_H\n#define ENEMY_H\n#include "Enemy/Item/Orb.h"\n#endif\n'
    assert _check(project, text) == [(3, "error", "INCLUDE_PATH_NOT_FOUND")]


def test_build_cs_roots_literal_and_dynamic(tmp_path: Path) -> None:
    literal = _project(tmp_path / "a", 'PublicIncludePaths.Add(Path.Combine(ModuleDirectory, "Extra"));')
    assert _check(literal, '#include "Tool.h"\n#include "Wrong/Orb.h"\n') == [(2, "error", "INCLUDE_PATH_NOT_FOUND")]
    roots, complete = module_include_roots(literal / "Source/Game/Enemy/Enemy.cpp", literal)
    assert complete and literal.resolve() / "Source/Game/Extra" in [root.resolve() for root in roots]
    dynamic = _project(tmp_path / "b", 'PublicIncludePaths.Add(Path.Combine(PluginDirectory, "Shared"));')
    assert _check(dynamic, '#include "Wrong/Orb.h"\n') == [(1, "warning", "INCLUDE_PATH_UNVERIFIED")]


@pytest.mark.skipif(sys.platform != "win32", reason="case-insensitive file system only")
def test_case_only_match_is_a_warning_on_windows(tmp_path: Path) -> None:
    project = _project(tmp_path)
    assert _check(project, '#include "ai/controller.h"\n') == [(1, "warning", "INCLUDE_PATH_CASE_MISMATCH")]


def test_normalization_and_visibility_share_the_lookup(tmp_path: Path) -> None:
    assert normalize_include_path("a\\\\b//./c/../d.h") == "a/b/d.h"
    project = _project(tmp_path)
    index = build_source_include_index(project)
    cpp = project / "Source/Game/Enemy/Enemy.cpp"
    assert [p.name for p in _resolve_project_include("Sound//Data.h", cpp, index)] == ["Data.h"]
    assert [p.name for p in _resolve_project_include("AI/Controller.h", cpp, index)] == ["Controller.h"]
