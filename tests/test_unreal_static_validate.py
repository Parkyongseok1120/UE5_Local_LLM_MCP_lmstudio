from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from unreal_static_validate import (  # noqa: E402
    validate_duplicate_source_basenames,
    validate_include_paths_exist,
    build_source_include_index,
    has_static_errors,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_duplicate_source_basename_detected(tmp_path: Path) -> None:
    project = tmp_path / "Demo"
    _write(
        project / "Source" / "Demo" / "Public" / "Foo" / "HealthComponent.h",
        '#include "HealthComponent.generated.h"\n',
    )
    _write(
        project / "Source" / "Demo" / "Public" / "Bar" / "HealthComponent.h",
        '#include "HealthComponent.generated.h"\n',
    )

    findings = validate_duplicate_source_basenames(project)
    codes = {item.code for item in findings}

    assert "DUPLICATE_SOURCE_BASENAME" in codes
    assert has_static_errors(findings)


def test_include_path_not_found_detected(tmp_path: Path) -> None:
    project = tmp_path / "Demo"
    cpp = project / "Source" / "Demo" / "Private" / "Enemy.cpp"
    _write(
        project / "Source" / "Demo" / "Public" / "SharedComponent" / "HealthComponent.h",
        '#include "HealthComponent.generated.h"\n',
    )
    _write(cpp, '#include "Character/Player/Component/HealthComponent.h"\n')

    include_index = build_source_include_index(project)
    findings = validate_include_paths_exist(cpp, cpp.read_text(encoding="utf-8"), project, include_index)

    assert any(item.code == "INCLUDE_PATH_NOT_FOUND" for item in findings)
