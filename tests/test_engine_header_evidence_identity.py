from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import engine_header_evidence  # noqa: E402
from engine_header_evidence import (  # noqa: E402
    _identity,
    clear_engine_header_catalog_cache,
    resolve_engine_include_path,
)


def _synthetic_header_root(tmp_path: Path) -> tuple[Path, Path]:
    engine_root = tmp_path / "EngineRoot"
    header_root = engine_root / "Engine" / "Source" / "Runtime" / "Demo" / "Public"
    header_root.mkdir(parents=True)
    return engine_root, header_root


def test_engine_header_catalog_uses_injected_host_case_rules(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine_root, header_root = _synthetic_header_root(tmp_path)
    header = header_root / "Inventory.h"
    header.write_text("struct FInventory {};\n", encoding="utf-8")
    monkeypatch.setattr(engine_header_evidence.shutil, "which", lambda _name: None)

    clear_engine_header_catalog_cache()
    posix = resolve_engine_include_path(
        engine_root,
        "Runtime/Demo/Public/inventory.h",
        host_platform="linux",
    )
    clear_engine_header_catalog_cache()
    windows = resolve_engine_include_path(
        engine_root,
        "Runtime/Demo/Public/inventory.h",
        host_platform="win32",
    )

    assert posix["ok"] is False
    assert windows["matches"] == [str(header)]


def test_engine_header_catalog_does_not_merge_unicode_casefold_aliases(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine_root, header_root = _synthetic_header_root(tmp_path)
    composed_name = "\u0130nventory.h"
    decomposed_name = "I\u0307nventory.h"
    composed = header_root / composed_name
    decomposed = header_root / decomposed_name
    composed.write_text("struct FComposedInventory {};\n", encoding="utf-8")
    decomposed.write_text("struct FDecomposedInventory {};\n", encoding="utf-8")
    assert composed_name.casefold() == decomposed_name.casefold()
    monkeypatch.setattr(engine_header_evidence.shutil, "which", lambda _name: None)

    include = f"Runtime/Demo/Public/{composed_name}"
    for host_platform in ("linux", "win32"):
        clear_engine_header_catalog_cache()
        result = resolve_engine_include_path(
            engine_root,
            include,
            host_platform=host_platform,
        )
        assert result["matches"] == [str(composed)]
        assert result["ambiguous"] is False


def test_engine_root_identity_preserves_unicode_and_injects_ascii_case_rules(
    tmp_path: Path,
) -> None:
    upper = tmp_path / "UpperEngine"
    lower = tmp_path / "upperengine"
    assert _identity(upper, host_platform="linux") != _identity(
        lower,
        host_platform="linux",
    )
    assert _identity(upper, host_platform="win32") == _identity(
        lower,
        host_platform="win32",
    )

    composed = tmp_path / "\u0130Engine"
    decomposed = tmp_path / "I\u0307Engine"
    for host_platform in ("linux", "win32"):
        assert _identity(composed, host_platform=host_platform) != _identity(
            decomposed,
            host_platform=host_platform,
        )
