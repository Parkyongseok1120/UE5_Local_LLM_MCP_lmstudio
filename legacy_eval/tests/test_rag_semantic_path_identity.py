# Archived tests for the removed semantic embedding layer.
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from rag_semantic import _embedding_row_matches_projects  # noqa: E402


def test_embedding_project_filter_uses_injected_host_case_rules() -> None:
    row = {
        "project": "DemoProject",
        "locator": "C:/Projects/DemoProject/Source/Worker.cpp:1",
    }

    assert not _embedding_row_matches_projects(
        row,
        ["demoproject"],
        host_platform="linux",
    )
    assert _embedding_row_matches_projects(
        row,
        ["demoproject"],
        host_platform="win32",
    )


def test_embedding_project_filter_rejects_unicode_casefold_alias() -> None:
    composed = "\u0130Project"
    decomposed = "I\u0307Project"
    assert composed.casefold() == decomposed.casefold()
    row = {
        "project": composed,
        "locator": f"/Projects/{composed}/Source/Worker.cpp:1",
    }

    for host_platform in ("linux", "win32"):
        assert not _embedding_row_matches_projects(
            row,
            [decomposed],
            host_platform=host_platform,
        )


def test_embedding_foreign_project_cannot_be_overridden_by_locator_substring() -> None:
    foreign = {
        "project": "Other",
        "locator": "/Projects/MyGameBackup/Source/Worker.cpp:1",
    }
    metadata_absent_false_positive = {
        "project": "",
        "locator": "/Projects/MyGameBackup/Source/Worker.cpp:1",
    }
    metadata_absent_match = {
        "project": "",
        "locator": "/Projects/Game/Source/Worker.cpp:1",
    }

    for host_platform in ("linux", "win32"):
        assert not _embedding_row_matches_projects(
            foreign,
            ["Game"],
            host_platform=host_platform,
        )
        assert not _embedding_row_matches_projects(
            metadata_absent_false_positive,
            ["Game"],
            host_platform=host_platform,
        )
        assert _embedding_row_matches_projects(
            metadata_absent_match,
            ["Game"],
            host_platform=host_platform,
        )
