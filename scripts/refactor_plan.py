#!/usr/bin/env python
"""Validate refactor stage plans (R0-R4) for agent and MCP tools."""

from __future__ import annotations

import re
from typing import Any

VALID_STAGES = {"R0", "R1", "R2", "R3", "R4", "r0", "r1", "r2", "r3", "r4"}

FORBIDDEN_CODE_MARKERS = (
    "#include",
    "UCLASS(",
    "USTRUCT(",
    "GENERATED_BODY()",
    "void ",
    "bool ",
    "int32 ",
)

SSOT_MARKERS = ("ssot", "owner", "소유", "단일 원본", "single source")
RISK_MARKERS = ("risk", "위험", "impact", "영향")
FILE_MARKERS = ("file", "path", "파일", ".h", ".cpp", "build.cs")


def normalize_stage(stage: str) -> str:
    value = str(stage or "R0").strip().upper()
    if value not in {"R0", "R1", "R2", "R3", "R4"}:
        return "R0"
    return value


def validate_refactor_plan(stage: str, plan_text: str) -> dict[str, Any]:
    stage = normalize_stage(stage)
    text = str(plan_text or "").strip()
    lowered = text.lower()
    issues: list[str] = []
    warnings: list[str] = []
    passed: list[str] = []

    if not text:
        return {
            "ok": False,
            "stage": stage,
            "issues": ["Plan text is empty."],
            "warnings": [],
            "passed": [],
        }

    if stage == "R0":
        if any(marker.lower() in lowered for marker in FORBIDDEN_CODE_MARKERS):
            issues.append("R0 must not include code snippets or UCLASS/GENERATED_BODY blocks.")
        else:
            passed.append("R0 has no obvious code blocks.")
        if any(marker in lowered for marker in SSOT_MARKERS):
            passed.append("SSOT/ownership language present.")
        else:
            issues.append("R0 should name state owners (SSOT table or ownership section).")
        if any(marker in lowered for marker in FILE_MARKERS):
            passed.append("Impact file list or path references present.")
        else:
            warnings.append("R0 should list impacted files or paths.")
        if any(marker in lowered for marker in RISK_MARKERS):
            passed.append("Risk/impact notes present.")
        else:
            warnings.append("Consider adding risks or migration hazards.")

    if stage == "R1":
        if "interface" not in lowered and "boundary" not in lowered and "경계" not in lowered:
            warnings.append("R1 should describe API/header boundaries.")
        if re.search(r"\bdelete\b|\bremove all\b|전면 삭제", lowered):
            warnings.append("R1 should avoid mass deletion; defer removal to R4.")

    if stage in {"R2", "R3", "R4"}:
        file_hits = len(re.findall(r"\.(?:h|cpp)\b", lowered, flags=re.IGNORECASE))
        if file_hits > 5:
            warnings.append(f"{stage} mentions many files ({file_hits}). Prefer ≤3 files per turn.")
        if "build" not in lowered and "ubt" not in lowered and "compile" not in lowered:
            warnings.append(f"{stage} should state how UBT/build verification will run.")

    if "lyra" in lowered and "example" not in lowered and "project-specific" not in lowered:
        warnings.append("Lyra names should be labeled project-specific, not universal rules.")

    ok = len(issues) == 0
    return {
        "ok": ok,
        "stage": stage,
        "issues": issues,
        "warnings": warnings,
        "passed": passed,
    }


def scan_symbol_impact(project_root: str, symbol: str, *, max_files: int = 40) -> dict[str, Any]:
    from pathlib import Path

    root = Path(project_root)
    if not root.exists():
        return {"ok": False, "error": f"Project root not found: {root}", "matches": []}

    query = str(symbol or "").strip()
    if len(query) < 2:
        return {"ok": False, "error": "symbol must be at least 2 characters", "matches": []}

    skip = {"Binaries", "Intermediate", "Saved", "DerivedDataCache", ".git"}
    suffixes = {".h", ".hpp", ".cpp", ".c", ".cc", ".cs", ".Build.cs"}
    matches: list[dict[str, Any]] = []
    pattern = re.compile(re.escape(query))

    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in suffixes and path.name.endswith(".Build.cs") is False:
            if path.suffix.lower() not in suffixes:
                continue
        if any(part in skip for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if not pattern.search(text):
            continue
        line_numbers = [index + 1 for index, line in enumerate(text.splitlines()) if pattern.search(line)]
        matches.append(
            {
                "path": str(path),
                "relativePath": str(path.relative_to(root)) if str(path).startswith(str(root)) else str(path),
                "lineNumbers": line_numbers[:8],
                "hitCount": len(line_numbers),
            }
        )
        if len(matches) >= max_files:
            break

    return {
        "ok": True,
        "symbol": query,
        "projectRoot": str(root),
        "matchCount": len(matches),
        "matches": matches,
        "truncated": len(matches) >= max_files,
    }
