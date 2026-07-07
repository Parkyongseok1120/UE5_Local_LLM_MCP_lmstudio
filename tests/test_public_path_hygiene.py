#!/usr/bin/env python
"""Public release guard for machine-specific paths."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = (
    ROOT / "scripts",
    ROOT / "installer",
    ROOT / "config",
    ROOT / ".github",
    ROOT / "docs",
    ROOT / "prompts",
    ROOT / "tests",
    ROOT / "lmstudio-unreal-agent-mcp",
    ROOT / "RAG_Project_Guidelines",
)
SCAN_ROOT_MARKDOWN = True
TEXT_SUFFIXES = {".bat", ".json", ".ps1", ".py", ".txt", ".yml", ".yaml", ".md"}
SKIP_FILES = {
    ROOT / "installer" / "Verify-Oss-Ready.ps1",
    ROOT / "CONTRIBUTING.md",
    ROOT / "SECURITY.md",
    ROOT / "tests" / "test_public_path_hygiene.py",
}


def _text_files():
    seen: set[Path] = set()
    for base in SCAN_DIRS:
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path in SKIP_FILES or path in seen:
                continue
            if path.suffix.lower() in TEXT_SUFFIXES:
                seen.add(path)
                yield path
    if SCAN_ROOT_MARKDOWN:
        for path in ROOT.glob("*.md"):
            if path.is_file() and path not in SKIP_FILES and path not in seen:
                seen.add(path)
                yield path


def test_no_personal_windows_user_paths_in_release_files():
    forbidden = ("C:" + "\\Users\\", "C:/Users/")
    violations: list[str] = []
    for path in _text_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for marker in forbidden:
            if marker in text:
                violations.append(str(path.relative_to(ROOT)))
    assert not violations, "Personal Windows paths found:\n" + "\n".join(sorted(violations))


def test_no_versioned_unreal_install_path_defaults_in_code_or_config():
    forbidden = (
        "C:" + "\\Program Files\\Epic Games\\UE_",
        "C:/Program Files/Epic Games/UE_",
    )
    violations: list[str] = []
    for path in _text_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for marker in forbidden:
            if marker in text:
                violations.append(str(path.relative_to(ROOT)))
    assert not violations, "Hardcoded Unreal install paths found:\n" + "\n".join(sorted(violations))


def test_tracked_workspace_config_is_sanitized():
    path = ROOT / "config" / "workspace.json"
    if not path.is_file():
        path = ROOT / "config" / "workspace.example.json"
    data = json.loads(path.read_text(encoding="utf-8-sig"))

    assert not data.get("rootPath")
    assert not data.get("defaultEngineRoot")
