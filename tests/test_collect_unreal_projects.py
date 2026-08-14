from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from collect_unreal_projects import _unique_project_files  # noqa: E402


def test_project_collection_dedupe_uses_host_path_identity(tmp_path: Path) -> None:
    upper = tmp_path / "Upper" / "Demo.uproject"
    lower = tmp_path / "upper" / "Demo.uproject"

    assert len(_unique_project_files([upper, lower], host_platform="linux")) == 2
    assert len(_unique_project_files([upper, lower], host_platform="win32")) == 1


def test_project_collection_does_not_merge_unicode_casefold_aliases(
    tmp_path: Path,
) -> None:
    composed = tmp_path / "\u0130Project" / "Demo.uproject"
    decomposed = tmp_path / "I\u0307Project" / "Demo.uproject"
    assert str(composed).casefold() == str(decomposed).casefold()

    for host_platform in ("linux", "win32"):
        unique = _unique_project_files(
            [composed, decomposed],
            host_platform=host_platform,
        )
        assert len(unique) == 2
