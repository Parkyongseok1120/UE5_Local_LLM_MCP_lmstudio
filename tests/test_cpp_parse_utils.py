from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from cpp_parse_utils import (  # noqa: E402
    mask_comments_and_strings,
    offset_in_regions,
    preprocessor_editor_safe_regions,
)


def test_raw_string_with_parens_in_delimiter_is_masked() -> None:
    text = 'const char* s = R"foo(content(with)parens)foo"; int x = 1;'
    masked = mask_comments_and_strings(text)
    assert "content(with)parens" not in masked
    assert masked.rstrip().endswith("1;")


def test_ifndef_with_editor_is_not_safe_region() -> None:
    text = "#ifndef WITH_EDITOR\nGEditor;\n#endif\n"
    regions = preprocessor_editor_safe_regions(text)
    assert not any(offset_in_regions(text.index("GEditor"), regions) for _ in [0])


def test_if_with_editor_else_branch_is_not_safe() -> None:
    text = "#if WITH_EDITOR\nGEditor;\n#else\nGEditor;\n#endif\n"
    regions = preprocessor_editor_safe_regions(text)
    else_offset = text.index("GEditor", text.index("#else"))
    assert not offset_in_regions(else_offset, regions)
    true_offset = text.index("GEditor")
    assert offset_in_regions(true_offset, regions)


def test_nested_if_with_editor_tracks_inner_branch() -> None:
    text = (
        "#if WITH_EDITOR\n"
        "#if 1\n"
        "GEditor;\n"
        "#else\n"
        "GEditor;\n"
        "#endif\n"
        "#endif\n"
    )
    regions = preprocessor_editor_safe_regions(text)
    inner_true = text.index("GEditor")
    inner_false = text.index("GEditor", inner_true + 1)
    assert offset_in_regions(inner_true, regions)
    assert not offset_in_regions(inner_false, regions)
