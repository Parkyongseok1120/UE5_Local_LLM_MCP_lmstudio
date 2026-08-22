from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from task_api import _normalize_task_scope_target  # noqa: E402


def _scope_key(path: str, host_platform: str) -> str:
    display, key, issue = _normalize_task_scope_target(
        {},
        path,
        host_platform=host_platform,
    )
    assert display
    assert issue == ""
    return key


def test_code_sketch_scope_key_uses_injected_host_case_rules() -> None:
    upper = "Source/Demo/Worker.cpp"
    lower = "source/demo/worker.cpp"

    assert _scope_key(upper, "linux") != _scope_key(lower, "linux")
    assert _scope_key(upper, "win32") == _scope_key(lower, "win32")


def test_code_sketch_scope_key_rejects_unicode_casefold_alias() -> None:
    composed = "Source/Demo/\u0130mplementation.cpp"
    decomposed = "Source/Demo/I\u0307mplementation.cpp"
    assert composed.casefold() == decomposed.casefold()

    for host_platform in ("linux", "win32"):
        assert _scope_key(composed, host_platform) != _scope_key(
            decomposed,
            host_platform,
        )


def test_code_sketch_scope_key_does_not_use_unicode_normcase() -> None:
    upper = "Source/Demo/\u00c4ctor.cpp"
    lower = "Source/Demo/\u00e4ctor.cpp"
    assert upper.lower() == lower.lower()

    for host_platform in ("linux", "win32"):
        assert _scope_key(upper, host_platform) != _scope_key(
            lower,
            host_platform,
        )
