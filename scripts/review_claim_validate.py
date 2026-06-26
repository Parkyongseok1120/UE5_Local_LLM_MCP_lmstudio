#!/usr/bin/env python
"""Grep-based claim validation for grounded project review (anti-hallucination)."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from workspace_paths import load_shared_config

MISSING_CLAIM_RE = re.compile(
    r"(없음|없다|누락|미사용|missing|not\s+used|unused|does\s+not\s+exist|no\s+such)",
    re.IGNORECASE,
)
SUBSYSTEM_SPLIT_RE = re.compile(
    r"(subsystem|서브시스템).*(분리|추가|새로|new|create|introduce)",
    re.IGNORECASE,
)
DATA_ASSET_MISSING_RE = re.compile(
    r"(data\s*asset|dataasset|데이터\s*에셋).*(없|누락|missing|없음|미사용)",
    re.IGNORECASE,
)
SYMBOL_EXTRACT_RE = re.compile(
    r"\b(U[A-Z][A-Za-z0-9_]*|[A-Z][A-Za-z0-9_]*(?:Component|Subsystem|DataAsset|Montage))\b"
)

SKIP_DIRS = {".git", ".vs", "Binaries", "DerivedDataCache", "Intermediate", "Saved"}
SOURCE_EXTENSIONS = {".h", ".hpp", ".cpp", ".cs", ".ini"}


def resolve_project_root(project_arg: str | None) -> Path:
    if project_arg:
        candidate = Path(project_arg).resolve()
        if candidate.is_file() and candidate.suffix.lower() == ".uproject":
            return candidate.parent
        if candidate.is_dir():
            return candidate
        raise ValueError(f"Invalid project path: {project_arg}")

    config = load_shared_config()
    active = str(config.get("activeProject") or "").strip()
    if not active:
        raise ValueError("No activeProject. Run pick-project or pass projectRoot.")
    active_path = Path(active).resolve()
    if active_path.suffix.lower() == ".uproject":
        return active_path.parent
    return active_path


def load_pab(project_root: Path, pab_path: Path | None = None) -> dict[str, Any]:
    candidates = [
        pab_path,
        project_root / "project_architecture.json",
        Path("data/unreal58/project_architecture.json"),
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            return json.loads(candidate.read_text(encoding="utf-8-sig"))
    return {}


def grep_project(project_root: Path, pattern: str) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    regex = re.compile(pattern, re.IGNORECASE)
    source = project_root / "Source"
    if not source.is_dir():
        return hits
    for path in source.rglob("*"):
        if not path.is_file() or any(p in SKIP_DIRS for p in path.parts):
            continue
        if path.suffix.lower() not in SOURCE_EXTENSIONS:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                hits.append(
                    {
                        "path": path.relative_to(project_root).as_posix(),
                        "line": line_no,
                        "text": line.strip()[:160],
                    }
                )
    return hits


def pab_symbol_names(pab: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for key in ("classes", "subsystems", "components", "interfaces", "dataAssets"):
        for item in pab.get(key) or []:
            name = str(item.get("name") or "")
            if name:
                names.add(name)
    return names


def validate_claim(
    claim_text: str,
    project_root: Path,
    pab: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pab = pab or load_pab(project_root)
    pab_names = pab_symbol_names(pab)
    issues: list[str] = []
    warnings: list[str] = []
    evidence: list[dict[str, Any]] = []
    ok = True

    symbols = SYMBOL_EXTRACT_RE.findall(claim_text)
    unique_symbols = list(dict.fromkeys(symbols))

    if MISSING_CLAIM_RE.search(claim_text):
        for symbol in unique_symbols:
            hits = grep_project(project_root, re.escape(symbol))
            if hits:
                issues.append(f"Claim suggests missing/unused but '{symbol}' found in project source.")
                evidence.extend(hits[:3])
                ok = False

    if SUBSYSTEM_SPLIT_RE.search(claim_text):
        for symbol in unique_symbols:
            if "Subsystem" in symbol and symbol in pab_names:
                issues.append(f"Subsystem split suggested but '{symbol}' already exists in PAB.")
                ok = False
        for sub in pab.get("subsystems") or []:
            sub_name = str(sub.get("name") or "")
            role_words = re.findall(r"[A-Za-z]{4,}", claim_text)
            for word in role_words:
                if word.lower() in sub_name.lower() and len(word) > 4:
                    issues.append(f"Similar subsystem already exists: {sub_name}")
                    ok = False

    if DATA_ASSET_MISSING_RE.search(claim_text):
        da_hits = grep_project(project_root, r"DataAsset|UDataAsset")
        if da_hits or pab.get("dataAssets"):
            issues.append("Claim suggests missing DataAsset but project has DataAsset types.")
            evidence.extend(da_hits[:3])
            ok = False

    return {
        "ok": ok,
        "issues": issues,
        "warnings": warnings,
        "evidence": evidence[:8],
        "symbolsChecked": unique_symbols,
        "pabLoaded": bool(pab),
    }


def validate_claims(
    claims: list[str],
    project_root: str | Path | None = None,
    pab_path: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_project_root(str(project_root) if project_root else None)
    pab = load_pab(root, Path(pab_path) if pab_path else None)
    results = []
    for claim in claims:
        if not str(claim).strip():
            continue
        row = validate_claim(claim, root, pab)
        row["claim"] = claim[:500]
        results.append(row)

    fail_count = sum(1 for r in results if not r.get("ok"))
    return {
        "ok": fail_count == 0,
        "projectRoot": str(root),
        "claimCount": len(results),
        "failCount": fail_count,
        "results": results,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate review claims against project source.")
    parser.add_argument("--claim", action="append", default=[], help="Single claim text")
    parser.add_argument("--claims-file", default="", help="JSON file with claims array or findings")
    parser.add_argument("--project-root", default="", help="Project root or .uproject")
    parser.add_argument("--pab", default="", help="Path to project_architecture.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    claims = list(args.claim or [])
    if args.claims_file:
        data = json.loads(Path(args.claims_file).read_text(encoding="utf-8-sig"))
        if isinstance(data, list):
            claims.extend([str(c) for c in data])
        elif isinstance(data, dict):
            for key in ("claims", "findings"):
                if isinstance(data.get(key), list):
                    for item in data[key]:
                        if isinstance(item, str):
                            claims.append(item)
                        elif isinstance(item, dict):
                            claims.append(str(item.get("text") or item.get("claim") or ""))

    if not claims:
        print("No claims provided. Use --claim or --claims-file.", file=__import__("sys").stderr)
        return 2

    payload = validate_claims(
        claims,
        args.project_root or None,
        args.pab or None,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
