"""Unit tests for apply_patch."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from apply_patch import apply_patch, apply_patches, is_allowed_path  # noqa: E402


def test_apply_dry_run_does_not_write(tmp_path: Path) -> None:
    target = tmp_path / "Source" / "Game" / "Foo.cpp"
    target.parent.mkdir(parents=True)
    original = "int a = 1;\n"
    target.write_text(original, encoding="utf-8")
    ok, msg, updated = apply_patch(target, "int a = 1;", "int a = 99;", dry_run=True)
    assert ok, msg
    assert "99" in updated
    assert target.read_text(encoding="utf-8") == original


def test_apply_single_occurrence(tmp_path: Path) -> None:
    target = tmp_path / "Source" / "Game" / "Foo.cpp"
    target.parent.mkdir(parents=True)
    target.write_text("int a = 1;\nint b = 2;\n", encoding="utf-8")
    ok, msg, updated = apply_patch(target, "int a = 1;", "int a = 42;")
    assert ok, msg
    assert "42" in updated
    assert "int b = 2" in updated


def test_apply_normalizes_leading_whitespace_for_unique_multiline_block(tmp_path: Path) -> None:
    target = tmp_path / "Source" / "Game" / "DashComponent.cpp"
    target.parent.mkdir(parents=True)
    target.write_text(
        "#include \"DashComponent.h\"\n\n"
        "void UDashComponent::ApplyDash(int32 Strength)\n"
        "{\n"
        "\tCachedStrength = Strength;\n"
        "}\n",
        encoding="utf-8",
    )

    old_text = (
        "\tvoid UDashComponent::ApplyDash(int32 Strength)\n"
        "\t{\n"
        "\t\tCachedStrength = Strength;\n"
        "\t}"
    )
    new_text = (
        "\tvoid UDashComponent::ApplyDash(float Strength)\n"
        "\t{\n"
        "\t\tCachedStrength = FMath::RoundToInt(Strength);\n"
        "\t}"
    )

    ok, msg, updated = apply_patch(target, old_text, new_text)

    assert ok, msg
    assert msg == "ok (leading whitespace normalized)"
    assert "\nvoid UDashComponent::ApplyDash(float Strength)\n" in updated
    assert "\n\tvoid UDashComponent::ApplyDash(float Strength)\n" not in updated
    assert "FMath::RoundToInt" in updated


def test_apply_normalized_fallback_requires_unique_block(tmp_path: Path) -> None:
    target = tmp_path / "Source" / "Game" / "DashComponent.cpp"
    target.parent.mkdir(parents=True)
    target.write_text(
        "void PatchMe()\n"
        "{\n"
        "\tValue = 1;\n"
        "}\n"
        "\tvoid PatchMe()\n"
        "\t{\n"
        "\t\tValue = 1;\n"
        "\t}\n",
        encoding="utf-8",
    )

    old_text = (
        "    void PatchMe()\n"
        "    {\n"
        "        Value = 1;\n"
        "    }"
    )

    ok, msg, updated = apply_patch(target, old_text, "void PatchMe() {}")

    assert not ok
    assert "leading whitespace normalized candidates=2" in msg
    assert updated == target.read_text(encoding="utf-8")


def test_apply_wrong_occurrence_count(tmp_path: Path) -> None:
    target = tmp_path / "Foo.cpp"
    target.write_text("x\nx\n", encoding="utf-8")
    ok, msg, _ = apply_patch(target, "x", "y", expected_occurrences=1)
    assert not ok
    assert "found 2" in msg


def test_apply_patches_batch(tmp_path: Path) -> None:
    a = tmp_path / "A.h"
    b = tmp_path / "B.cpp"
    a.write_text("#pragma once\n", encoding="utf-8")
    b.write_text("void F();\n", encoding="utf-8")
    written, errors = apply_patches(
        tmp_path,
        [
            {"path": "A.h", "oldText": "#pragma once", "newText": "#pragma once\n// patched"},
            {"path": "B.cpp", "oldText": "void F();", "newText": "void F() {}"},
        ],
    )
    assert not errors
    assert len(written) == 2
    assert "// patched" in a.read_text(encoding="utf-8")


def test_path_not_allowed_outside_workspace(tmp_path: Path) -> None:
    outside = Path("C:/Windows/not_allowed.cpp")
    assert not is_allowed_path(outside, tmp_path)
