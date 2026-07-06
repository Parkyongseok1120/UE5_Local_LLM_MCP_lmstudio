#!/usr/bin/env python
"""Guard against fixed-project hardcoding in scripts/prompts."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = (ROOT / "scripts", ROOT / "prompts")
LEGACY_PROJECT_NAME = "_".join(("Project", "MJS"))
LEGACY_PROJECT_PATH = rf"Git[/\\]{LEGACY_PROJECT_NAME}"

FORBIDDEN = (
    re.compile(re.escape(LEGACY_PROJECT_NAME)),
    re.compile(LEGACY_PROJECT_PATH),
    re.compile(r"/Game/01_Character"),
)
ALLOWLIST = (
    "test_no_project_hardcode.py",
)


def _line_is_anti_hardcode_instruction(line: str) -> bool:
    lowered = line.lower()
    markers = (
        "never hardcode",
        "do not hardcode",
        "hardcoding forbidden",
        "하드코딩 금지",
        "placeholder",
        "{projectname}",
        "{sourcebrowsepath}",
    )
    return any(marker in lowered for marker in markers)


def test_no_project_hardcode_in_scripts_and_prompts():
    violations: list[str] = []
    for base in SCAN_DIRS:
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if path.name in ALLOWLIST:
                continue
            if path.suffix.lower() not in {".py", ".md", ".json", ".ps1"}:
                continue
            for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
                if _line_is_anti_hardcode_instruction(line):
                    continue
                for pattern in FORBIDDEN:
                    if pattern.search(line):
                        violations.append(f"{path.relative_to(ROOT)}:{line_no}: {pattern.pattern}")
    assert not violations, "Hardcoded project paths found:\n" + "\n".join(violations)
