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
LOGIC_MISSING_CLAIM_RE = re.compile(
    r"("
    r"누락|로직\s*없음|처리되지\s*않|무시됨|빠져\s*있|"
    r"missing\s+logic|does\s+nothing|not\s+handled|unhandled|"
    r"should\s+call\s+SetActorTransform|SetActorTransform.*(?:없|누락|missing|never)|"
    r"early\s+return.*(?:bug|버그|누락)|(?:bug|버그).*early\s+return"
    r")",
    re.IGNORECASE,
)
BY_DESIGN_PHRASE_RE = re.compile(
    r"("
    r"그대로\s*사용|에셋에\s*저장된|authored\s+world|as\s+authored|"
    r"leave\s+(?:the\s+)?(?:asset|authored)|do\s+not\s+override|"
    r"동적\s*원점을?\s*(?:계산하지|덮어쓰지)|"
    r"Level\s+Sequence\s+에셋에\s+저장된"
    r")",
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
MODE_TOKEN_RE = re.compile(
    r"\b("
    r"AuthoredWorld|ExplicitTransform|InstigatorToSubject|InstigatorActor|SubjectActor|"
    r"AnchorMode|RotationSource|TargetSocketName|AnchorSocketName|ExplicitWorldTransform|"
    r"ApplyDynamicTransform|ResolveDynamicAnchorTransform|ResolveRotation"
    r")\b"
)
CPP_CITATION_RE = re.compile(r"([\w./\\-]+\.cpp)(?::\d+)?", re.IGNORECASE)
H_CITATION_RE = re.compile(r"([\w./\\-]+\.(?:h|hpp))(?::\d+)?", re.IGNORECASE)
BUG_CLAIM_RE = re.compile(r"(버그|bug|logical\s+error|논리적\s+오류)", re.IGNORECASE)

SKIP_DIRS = {".git", ".vs", "Binaries", "DerivedDataCache", "Intermediate", "Saved"}
SOURCE_EXTENSIONS = {".h", ".hpp", ".cpp", ".cs", ".ini"}
HEADER_EXTENSIONS = {".h", ".hpp"}


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


def _read_source_file(project_root: Path, rel_path: str) -> str | None:
    candidate = (project_root / rel_path).resolve()
    try:
        candidate.relative_to(project_root.resolve())
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    try:
        return candidate.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _sibling_header_paths(cpp_rel: str) -> list[str]:
    normalized = cpp_rel.replace("\\", "/")
    stem = Path(normalized).stem
    parent = Path(normalized).parent.as_posix()
    candidates = [
        f"{parent}/{stem}.h",
        f"{parent}/{stem}.hpp",
    ]
    # Common Unreal Public/Private split
    if "/Private/" in normalized:
        public_parent = parent.replace("/Private", "/Public")
        candidates.extend(
            [
                f"{public_parent}/{stem}.h",
                f"{public_parent}/{stem}.hpp",
            ]
        )
    return candidates


def find_by_design_contract_hits(
    project_root: Path,
    tokens: list[str],
) -> list[dict[str, Any]]:
    """Return header hits where a mode/token sits near intentional no-op language."""
    hits: list[dict[str, Any]] = []
    source = project_root / "Source"
    if not source.is_dir() or not tokens:
        return hits
    token_re = re.compile("|".join(re.escape(t) for t in tokens), re.IGNORECASE)
    for path in source.rglob("*"):
        if not path.is_file() or any(p in SKIP_DIRS for p in path.parts):
            continue
        if path.suffix.lower() not in HEADER_EXTENSIONS:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = text.splitlines()
        for line_no, line in enumerate(lines, start=1):
            if not token_re.search(line):
                continue
            window_start = max(0, line_no - 6)
            window_end = min(len(lines), line_no + 5)
            window = "\n".join(lines[window_start:window_end])
            if BY_DESIGN_PHRASE_RE.search(window):
                hits.append(
                    {
                        "path": path.relative_to(project_root).as_posix(),
                        "line": line_no,
                        "text": line.strip()[:160],
                    }
                )
    return hits


def check_logic_missing_guards(
    claim_text: str,
    project_root: Path,
) -> tuple[bool, list[str], list[dict[str, Any]], list[str]]:
    """Return (ok, issues, evidence, reasons) for logic-missing false positives."""
    if not LOGIC_MISSING_CLAIM_RE.search(claim_text) and not (
        BUG_CLAIM_RE.search(claim_text) and MODE_TOKEN_RE.search(claim_text)
    ):
        return True, [], [], []

    issues: list[str] = []
    evidence: list[dict[str, Any]] = []
    reasons: list[str] = []
    ok = True

    mode_tokens = list(dict.fromkeys(MODE_TOKEN_RE.findall(claim_text)))
    if mode_tokens:
        contract_hits = find_by_design_contract_hits(project_root, mode_tokens)
        if contract_hits:
            ok = False
            reasons.append("by_design_contract")
            issues.append(
                "Claim treats intentional mode/no-op as missing logic, but header contract "
                f"documents by-design behavior near: {', '.join(mode_tokens[:4])}."
            )
            evidence.extend(contract_hits[:4])

    cpp_citations = [m.group(1).replace("\\", "/") for m in CPP_CITATION_RE.finditer(claim_text)]
    h_citations = [m.group(1).replace("\\", "/") for m in H_CITATION_RE.finditer(claim_text)]
    if cpp_citations and not h_citations:
        header_hints: list[str] = []
        for cpp_rel in cpp_citations[:3]:
            for hdr in _sibling_header_paths(cpp_rel):
                text = _read_source_file(project_root, hdr)
                if text is None:
                    continue
                if mode_tokens and any(tok in text for tok in mode_tokens):
                    header_hints.append(hdr)
                elif BY_DESIGN_PHRASE_RE.search(text) and (
                    MODE_TOKEN_RE.search(text) or "UENUM" in text
                ):
                    header_hints.append(hdr)
        if header_hints:
            ok = False
            reasons.append("header_contract_unread")
            unique_hints = list(dict.fromkeys(header_hints))
            issues.append(
                "Logic/bug claim cites .cpp only; read sibling header contract first: "
                + ", ".join(unique_hints[:3])
            )
            for hint in unique_hints[:3]:
                evidence.append({"path": hint, "line": 1, "text": "sibling header with contract docs"})

    return ok, issues, evidence, reasons


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
    reasons: list[str] = []
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
                reasons.append("symbol_present")

    if SUBSYSTEM_SPLIT_RE.search(claim_text):
        for symbol in unique_symbols:
            if "Subsystem" in symbol and symbol in pab_names:
                issues.append(f"Subsystem split suggested but '{symbol}' already exists in PAB.")
                ok = False
                reasons.append("subsystem_exists")
        for sub in pab.get("subsystems") or []:
            sub_name = str(sub.get("name") or "")
            role_words = re.findall(r"[A-Za-z]{4,}", claim_text)
            for word in role_words:
                if word.lower() in sub_name.lower() and len(word) > 4:
                    issues.append(f"Similar subsystem already exists: {sub_name}")
                    ok = False
                    reasons.append("subsystem_exists")

    if DATA_ASSET_MISSING_RE.search(claim_text):
        da_hits = grep_project(project_root, r"DataAsset|UDataAsset")
        if da_hits or pab.get("dataAssets"):
            issues.append("Claim suggests missing DataAsset but project has DataAsset types.")
            evidence.extend(da_hits[:3])
            ok = False
            reasons.append("data_asset_present")

    logic_ok, logic_issues, logic_evidence, logic_reasons = check_logic_missing_guards(
        claim_text, project_root
    )
    if not logic_ok:
        ok = False
        issues.extend(logic_issues)
        evidence.extend(logic_evidence)
        reasons.extend(logic_reasons)

    return {
        "ok": ok,
        "issues": issues,
        "warnings": warnings,
        "evidence": evidence[:8],
        "symbolsChecked": unique_symbols,
        "reasons": list(dict.fromkeys(reasons)),
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
