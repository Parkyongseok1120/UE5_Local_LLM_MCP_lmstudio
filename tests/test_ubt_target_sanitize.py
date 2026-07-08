from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from ubt_utils import sanitize_ubt_target  # noqa: E402


def test_sanitize_ubt_target_rejects_header_path() -> None:
    sanitized, reason = sanitize_ubt_target(
        r"C:\Temp\HoldoutFixture\Source\HoldoutFixture\Public\HoldoutGlobalSubsystem.h"
    )
    assert sanitized == "HoldoutFixtureEditor"
    assert reason is not None


def test_sanitize_ubt_target_keeps_editor_target() -> None:
    sanitized, reason = sanitize_ubt_target("HoldoutFixtureEditor")
    assert sanitized == "HoldoutFixtureEditor"
    assert reason is None
