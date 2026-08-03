"""Unit tests for portable-package forbidden path markers and inventory filters."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUILDER_PATH = ROOT / "scripts" / "build_integrated_package.py"


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_integrated_package", BUILDER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def builder():
    return _load_builder()


# Filenames that must match markers/inventory exactly (basename or under a dir).
EXACT_FORBIDDEN_NAMES = (
    "campaign.out.log",
    "driver.runner.log",
    "probe.shell.log",
    "agent_session.json",
    "local_ai_prompt_runner.py",
    "local_ai_scratch.md",
    "omock_harness.py",
    "omock_replay.json",
)


@pytest.mark.parametrize("name", EXACT_FORBIDDEN_NAMES)
def test_forbidden_markers_match_exact_filenames(builder, name: str) -> None:
    for relative in (name, f"scripts/{name}", f"docs/nested/{name}"):
        assert builder.FORBIDDEN_PACKAGE_MARKERS.search(relative), relative
        assert builder.FORBIDDEN_INVENTORY_RE.search(relative), relative


@pytest.mark.parametrize(
    "name",
    (
        "campaign.out.log",
        "driver.runner.log",
        "probe.shell.log",
        "agent_session.json",
        "local_ai_prompt_runner.py",
        "omock_harness.py",
    ),
)
def test_scripts_name_deny_matches_exact_basenames(builder, name: str) -> None:
    assert builder.SCRIPTS_NAME_DENY.match(name), name


def test_double_escaped_dot_log_patterns_are_absent(builder) -> None:
    """Regression: r'|\\.out\\.log$' matched backslash+anychar, not literal '.out.log'."""
    for pattern in (builder.FORBIDDEN_PACKAGE_MARKERS, builder.FORBIDDEN_INVENTORY_RE):
        source = pattern.pattern
        assert r"\\.out\\.log" not in source
        assert r"\\.runner\\.log" not in source
        assert r"\\.shell\\.log" not in source
        assert r"\.out\.log$" in source
        assert r"\.runner\.log$" in source
        assert r"\.shell\.log$" in source


def test_include_rejects_forbidden_names_outside_scripts(builder, tmp_path: Path) -> None:
    """Allowlisted top-level dirs still drop forbidden basenames via markers (not scripts-only)."""
    for name in EXACT_FORBIDDEN_NAMES:
        relative = Path("docs") / name
        assert builder._include(relative, include_index=False) is False, name
        relative = Path("scripts") / name
        assert builder._include(relative, include_index=False) is False, name


def test_include_keeps_benign_neighbors(builder) -> None:
    assert builder._include(Path("scripts/build_integrated_package.py"), include_index=False) is True
    assert builder._include(Path("docs/Integrated_Installer.md"), include_index=False) is True
    assert builder._include(Path("scripts/scratch.bak"), include_index=False) is False
    assert builder._include(Path("scripts/phase_tool_router.py"), include_index=False) is True
    assert builder.FORBIDDEN_PACKAGE_MARKERS.search("scripts/local_notes.py") is None
    assert builder.FORBIDDEN_INVENTORY_RE.search("config/workspace.example.json") is None


def test_assert_clean_inventory_counts_forbidden_zero_for_clean_set(builder) -> None:
    manifest = {
        "inventory": [
            {"path": "install.py"},
            {"path": "scripts/build_integrated_package.py"},
            {"path": "docs/README.md"},
        ]
    }
    paths = builder._assert_clean_inventory(manifest)
    assert paths == ["install.py", "scripts/build_integrated_package.py", "docs/README.md"]


@pytest.mark.parametrize("name", EXACT_FORBIDDEN_NAMES)
def test_assert_clean_inventory_rejects_forbidden_rows(builder, name: str) -> None:
    with pytest.raises(ValueError, match="forbidden files"):
        builder._assert_clean_inventory({"inventory": [{"path": f"scripts/{name}"}]})
