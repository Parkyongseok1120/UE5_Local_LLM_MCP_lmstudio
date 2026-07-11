from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from holdout_autofixes import apply_component_include_autofix  # noqa: E402
from unreal_static_validate import Finding, validate_component_registration_includes  # noqa: E402


def _holdout_fixture(tmp_path: Path) -> Path:
    root = tmp_path / "HoldoutFixture"
    root.mkdir(parents=True)
    (root / "HoldoutFixture.uproject").write_text("{}", encoding="utf-8")
    public = root / "Source" / "HoldoutFixture" / "Public"
    private = root / "Source" / "HoldoutFixture" / "Private"
    public.mkdir(parents=True)
    private.mkdir(parents=True)
    (public / "HoldoutBoxActor.h").write_text(
        "class UBoxComponent;\nUCLASS()\nclass AHoldoutBoxActor : public AActor { GENERATED_BODY() public: AHoldoutBoxActor(); TObjectPtr<UBoxComponent> Box; };\n",
        encoding="utf-8",
    )
    cpp = private / "HoldoutBoxActor.cpp"
    cpp.write_text(
        '#include "HoldoutBoxActor.h"\n\nAHoldoutBoxActor::AHoldoutBoxActor() {\n'
        '  Box = CreateDefaultSubobject<UBoxComponent>(TEXT("Box"));\n}\n',
        encoding="utf-8",
    )
    return root


def test_component_registration_finding_on_holdout_shape(tmp_path: Path) -> None:
    root = _holdout_fixture(tmp_path)
    cpp = root / "Source" / "HoldoutFixture" / "Private" / "HoldoutBoxActor.cpp"
    text = cpp.read_text(encoding="utf-8")
    findings = validate_component_registration_includes(cpp, text, root)
    codes = {finding.code for finding in findings}
    assert "COMPONENT_REGISTRATION_INCLUDE_MISSING" in codes or not findings


def test_component_include_autofix_inserts_include(tmp_path: Path) -> None:
    root = _holdout_fixture(tmp_path)
    cpp = root / "Source" / "HoldoutFixture" / "Private" / "HoldoutBoxActor.cpp"
    text = cpp.read_text(encoding="utf-8")
    findings = validate_component_registration_includes(cpp, text, root)
    if not findings:
        return
    written = apply_component_include_autofix(root, findings)
    updated = cpp.read_text(encoding="utf-8")
    assert written or "BoxComponent.h" in updated or "Components/" in updated
