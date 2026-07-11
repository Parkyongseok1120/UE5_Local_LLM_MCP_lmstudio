from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from domain_validation_context import clear_domain_validation_cache, get_cached_domain_context  # noqa: E402


def test_context_cache_hit(tmp_path: Path) -> None:
    root = tmp_path / "Demo"
    module = root / "Source" / "Demo" / "Private"
    module.mkdir(parents=True)
    path = module / "Demo.cpp"
    path.write_text("void X() {}\n", encoding="utf-8")
    clear_domain_validation_cache(root)
    first = get_cached_domain_context(root, paths=[path])
    second = get_cached_domain_context(root, paths=[path])
    assert len(first.paths) == len(second.paths)


def test_invalidate_after_write_clears_cache(tmp_path: Path) -> None:
    from domain_validation_context import (
        get_context_cache_metrics,
        invalidate_domain_validation_cache_for_paths,
    )

    root = tmp_path / "Demo"
    module = root / "Source" / "Demo" / "Private"
    module.mkdir(parents=True)
    path = module / "Demo.cpp"
    path.write_text("void X() {}\n", encoding="utf-8")
    clear_domain_validation_cache(root)
    get_cached_domain_context(root, paths=[path])
    invalidate_domain_validation_cache_for_paths(root, [path])
    metrics = get_context_cache_metrics()
    assert int(metrics.get("invalidations") or 0) >= 1
