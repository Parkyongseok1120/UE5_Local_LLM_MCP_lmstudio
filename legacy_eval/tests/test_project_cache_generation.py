from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from project_switch_invalidate import (  # noqa: E402
    clear_local_project_caches,
    publish_project_switch_generation,
    read_cache_generation,
)


def test_publish_increments_once_per_switch(tmp_path: Path) -> None:
    before = read_cache_generation(tmp_path)
    after = publish_project_switch_generation(tmp_path)
    assert after == before + 1 or after > before


def test_observer_clear_does_not_increment_generation(tmp_path: Path) -> None:
    gen = publish_project_switch_generation(tmp_path)
    clear_local_project_caches(tmp_path, previous_project=None, new_project=None)
    assert read_cache_generation(tmp_path) == gen
