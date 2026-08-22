"""Unit tests for portable-package forbidden path markers and inventory filters."""

from __future__ import annotations

import importlib.util
import json
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


def test_include_uses_the_explicit_direct_runtime_allowlist(builder) -> None:
    assert builder._include(Path("scripts/build_integrated_package.py"), include_index=False) is False
    assert builder._include(Path("docs/Integrated_Installer.md"), include_index=False) is True
    assert builder._include(Path("scripts/unreal_rag_direct.py"), include_index=False) is True
    assert builder._include(Path("scripts/scratch.bak"), include_index=False) is False
    assert builder._include(Path("scripts/phase_tool_router.py"), include_index=False) is False
    assert builder._include(Path("rag.ps1"), include_index=False) is False
    assert builder._include(Path("scripts/portable_rag.ps1"), include_index=False) is True
    for legacy_node in (
        "edit-bundle.js",
        "mutation-generation.js",
        "resolve-recovery-journal-cli.js",
        "state-root.js",
        "transaction-journal.js",
        "validate-write.js",
        "validation-dirty.js",
    ):
        assert builder._include(
            Path("lmstudio-unreal-agent-mcp/src") / legacy_node,
            include_index=False,
        ) is False
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


@pytest.mark.parametrize("nested", (False, True))
def test_private_path_scan_rejects_json_escaped_windows_home(
    builder, tmp_path: Path, nested: bool
) -> None:
    private_home = "C:" + "\\" + "Users" + "\\" + "private-user" + "\\Project"
    payload: object = {"path": private_home}
    if nested:
        payload = {"toolResult": json.dumps(payload)}
    (tmp_path / "fixture.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="private home path leaked into package: fixture.json"):
        builder._scan_private_paths(tmp_path)


def test_private_path_scan_allows_json_escaped_public_profile(builder, tmp_path: Path) -> None:
    public_path = "C:" + "\\" + "Users" + "\\" + "Public" + "\\Project"
    (tmp_path / "fixture.json").write_text(
        json.dumps({"path": public_path}),
        encoding="utf-8",
    )

    builder._scan_private_paths(tmp_path)


def test_private_path_scan_rejects_source_literal_windows_home(builder, tmp_path: Path) -> None:
    escaped_separator = "\\" * 2
    source = (
        'const fixturePath = "C:'
        f"{escaped_separator}Users{escaped_separator}private-user"
        f'{escaped_separator}Project";\n'
    )
    (tmp_path / "fixture.js").write_text(source, encoding="utf-8")

    with pytest.raises(ValueError, match="private home path leaked into package: fixture.js"):
        builder._scan_private_paths(tmp_path)
