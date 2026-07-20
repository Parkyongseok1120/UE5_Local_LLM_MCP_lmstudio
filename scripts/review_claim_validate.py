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
EXISTS_CLAIM_RE = re.compile(
    r"(존재|있습니다|구현되어|이미\s*있|exists|is\s+implemented|already\s+(?:has|exists)|present\s+in\s+(?:the\s+)?project)",
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
FRAMEWORK_SYMBOL_RE = re.compile(
    r"(Super::[A-Za-z_][A-Za-z0-9_]*|AActor::TakeDamage|UGameplayStatics::ApplyDamage|"
    r"FDamageEvent|base\s+class|부모\s*(?:클래스|구현)|프레임워크\s*(?:기본|구현))",
    re.IGNORECASE,
)
SEMANTIC_ASSERTION_RE = re.compile(
    r"(때문|원인|감소|증가|호출|발생|반환|처리|"
    r"cause|because|reduce|decrease|increase|call|emit|return|handle|does\s+not|is\s+not|아니)",
    re.IGNORECASE,
)
FRAMEWORK_EVIDENCE_RE = re.compile(
    r"(framework_source|official_docs|Engine[/\\]Source|Unreal\s+Engine\s+source|"
    r"Epic\s+(?:documentation|docs)|공식\s*문서|엔진\s*소스)",
    re.IGNORECASE,
)
WIRING_ASSERTION_RE = re.compile(
    r"(연결|연동|통합|작동|처리한다|사용\s*중|구현\s*(?:완료|됨)|"
    r"wired|connected|integrated|functional|works|handles|is\s+used|fully\s+implemented)",
    re.IGNORECASE,
)
BEHAVIOR_PATH_MARKER_RE = re.compile(
    r"(BehaviorPath\s*:|행동\s*경로\s*:|동작\s*경로\s*:|→|->)",
    re.IGNORECASE,
)
CODE_CITATION_RE = re.compile(r"[A-Za-z0-9_./\\-]+\.(?:h|hpp|cpp|cs|py|js|ts):\d+", re.IGNORECASE)

PACKET_VERDICTS = {"Bug", "ByDesign", "Ambiguous", "NeedsRuntimeProof"}
PACKET_SEVERITIES = {"P0", "P1", "P2", "P3"}
PACKET_PROOF_LEVELS = {
    "Proposed",
    "SourceVerified",
    "StaticVerified",
    "BuildVerified",
    "TestVerified",
    "RuntimeVerified",
}
PACKET_EVIDENCE_KINDS = {
    "requirement",
    "project_source",
    "framework_source",
    "official_docs",
    "static_analysis",
    "build",
    "test",
    "runtime",
    "generated_metadata",
}
PACKET_PATH_STAGES = {"entry", "decision", "dispatch", "mutation", "side_effect", "observer"}
PACKET_PATH_STATUSES = {"present", "expected_missing", "unknown"}
PACKET_CLAIM_TYPES = {
    "existence",
    "behavior",
    "framework_semantics",
    "wiring",
    "state_transition",
    "data_flow",
    "architecture",
    "codegen",
}
PACKET_BEHAVIORAL_CLAIM_TYPES = {"behavior", "wiring", "state_transition", "data_flow"}
PACKET_PROOF_EVIDENCE_REQUIREMENTS = {
    "SourceVerified": {"project_source", "framework_source", "official_docs"},
    "StaticVerified": {"static_analysis"},
    "BuildVerified": {"build"},
    "TestVerified": {"test"},
    "RuntimeVerified": {"runtime"},
}

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


def _packet_evidence_kinds(entries: Any) -> set[str]:
    kinds: set[str] = set()
    if not isinstance(entries, list):
        return kinds
    for entry in entries:
        if isinstance(entry, dict):
            kind = str(entry.get("kind") or "").strip()
            if kind:
                kinds.add(kind)
        elif isinstance(entry, str):
            prefix = entry.partition(":")[0].strip()
            if prefix:
                kinds.add(prefix)
    return kinds


def _packet_path_stages(entries: Any) -> set[str]:
    if not isinstance(entries, list):
        return set()
    return {
        str(entry.get("stage") or "").strip()
        for entry in entries
        if isinstance(entry, dict) and str(entry.get("stage") or "").strip()
    }


def _packet_has_ordered_flow(entries: Any, final_stages: set[str]) -> bool:
    if not isinstance(entries, list):
        return False
    stages = [
        str(entry.get("stage") or "").strip()
        for entry in entries
        if isinstance(entry, dict)
    ]
    try:
        entry_index = stages.index("entry")
        decision_index = next(
            index
            for index in range(entry_index + 1, len(stages))
            if stages[index] in {"decision", "dispatch"}
        )
        next(
            index
            for index in range(decision_index + 1, len(stages))
            if stages[index] in final_stages
        )
    except (StopIteration, ValueError):
        return False
    return True


def _packet_shape_issues(packet: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    for field in ("evidence", "counterEvidence"):
        entries = packet.get(field)
        if not isinstance(entries, list):
            issues.append(f"Structured claim {field} must be an array.")
            continue
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                issues.append(f"Structured claim {field}[{index}] must be an object.")
                continue
            if str(entry.get("kind") or "") not in PACKET_EVIDENCE_KINDS:
                issues.append(
                    f"Structured claim {field}[{index}].kind must be one of "
                    f"{sorted(PACKET_EVIDENCE_KINDS)}."
                )
            for required in ("location", "observation"):
                if not str(entry.get(required) or "").strip():
                    issues.append(f"Structured claim {field}[{index}].{required} is required.")

    behavior_path = packet.get("behaviorPath")
    if not isinstance(behavior_path, list):
        issues.append("Structured claim behaviorPath must be an array.")
    else:
        for index, entry in enumerate(behavior_path):
            if not isinstance(entry, dict):
                issues.append(f"Structured claim behaviorPath[{index}] must be an object.")
                continue
            if str(entry.get("stage") or "") not in PACKET_PATH_STAGES:
                issues.append(
                    f"Structured claim behaviorPath[{index}].stage must be one of "
                    f"{sorted(PACKET_PATH_STAGES)}."
                )
            if str(entry.get("stageStatus") or "") not in PACKET_PATH_STATUSES:
                issues.append(
                    f"Structured claim behaviorPath[{index}].stageStatus must be one of "
                    f"{sorted(PACKET_PATH_STATUSES)}."
                )
            for required in ("location", "symbol"):
                if not str(entry.get(required) or "").strip():
                    issues.append(
                        f"Structured claim behaviorPath[{index}].{required} is required."
                    )
    unknowns = packet.get("unknowns")
    if isinstance(unknowns, list):
        for index, unknown in enumerate(unknowns):
            if not isinstance(unknown, str) or not unknown.strip():
                issues.append(
                    f"Structured claim unknowns[{index}] must be a non-empty string."
                )
    return issues


def check_evidence_packet_guards(
    claim_text: str,
    packet: dict[str, Any] | None,
) -> tuple[bool, list[str], list[str], list[str]]:
    """Validate optional structured evidence without breaking legacy string claims."""
    ok = True
    issues: list[str] = []
    warnings: list[str] = []
    reasons: list[str] = []

    framework_semantics = bool(
        FRAMEWORK_SYMBOL_RE.search(claim_text) and SEMANTIC_ASSERTION_RE.search(claim_text)
    )
    if packet is None:
        if framework_semantics and not FRAMEWORK_EVIDENCE_RE.search(claim_text):
            ok = False
            reasons.append("framework_semantics_unverified")
            issues.append(
                "Framework/base-class behavior is used as a causal claim without a direct "
                "framework-source or authoritative-documentation citation."
            )
        existence_plus_wiring = bool(
            EXISTS_CLAIM_RE.search(claim_text) and WIRING_ASSERTION_RE.search(claim_text)
        )
        if existence_plus_wiring and not BEHAVIOR_PATH_MARKER_RE.search(claim_text):
            if len(CODE_CITATION_RE.findall(claim_text)) < 2:
                ok = False
                reasons.append("presence_not_wiring")
                issues.append(
                    "Claim promotes symbol presence/implementation to runtime wiring without an "
                    "entry-to-mutation BehaviorPath or multiple source citations."
                )
        return ok, issues, warnings, reasons

    verdict = str(packet.get("verdict") or "").strip()
    severity = str(packet.get("severity") or "").strip()
    proof_level = str(packet.get("proofLevel") or "").strip()
    claim_type = str(packet.get("claimType") or "").strip()
    evidence_entries = packet.get("evidence") or []
    counter_entries = packet.get("counterEvidence") or []
    behavior_path = packet.get("behaviorPath") or []
    evidence_kinds = _packet_evidence_kinds(evidence_entries)
    stages = _packet_path_stages(behavior_path)
    path_statuses = {
        str(entry.get("stageStatus") or "").strip()
        for entry in behavior_path
        if isinstance(entry, dict)
    } if isinstance(behavior_path, list) else set()

    if verdict not in PACKET_VERDICTS:
        ok = False
        reasons.append("verdict_missing")
        issues.append(f"Structured claim verdict must be one of {sorted(PACKET_VERDICTS)}.")
    if severity not in PACKET_SEVERITIES:
        ok = False
        reasons.append("severity_missing")
        issues.append(f"Structured claim severity must be one of {sorted(PACKET_SEVERITIES)}.")
    if proof_level not in PACKET_PROOF_LEVELS:
        ok = False
        reasons.append("proof_level_missing")
        issues.append(f"Structured claim proofLevel must be one of {sorted(PACKET_PROOF_LEVELS)}.")
    if claim_type not in PACKET_CLAIM_TYPES:
        ok = False
        reasons.append("claim_type_missing")
        issues.append(f"Structured claim claimType must be one of {sorted(PACKET_CLAIM_TYPES)}.")
    if not isinstance(packet.get("unknowns"), list):
        ok = False
        reasons.append("unknowns_missing")
        issues.append("Structured claim unknowns must be an array, even when empty.")

    shape_issues = _packet_shape_issues(packet)
    if shape_issues:
        ok = False
        reasons.append("evidence_packet_invalid")
        issues.extend(shape_issues)

    critical = severity in {"P0", "P1"}
    if not evidence_entries:
        ok = False
        reasons.append("evidence_missing")
        issues.append(
            "Structured claims require requirement, source, static, build, test, or runtime evidence."
        )
    if critical and not counter_entries:
        ok = False
        reasons.append("counterevidence_missing")
        issues.append("P0/P1 claims require counterEvidence or an explicitly checked alternative path.")

    framework_claim = (
        claim_type == "framework_semantics"
        or bool(packet.get("frameworkClaim"))
        or framework_semantics
    )
    if framework_claim and not evidence_kinds.intersection({"framework_source", "official_docs"}):
        ok = False
        reasons.append("framework_semantics_unverified")
        issues.append(
            "Framework semantic claim requires evidence kind framework_source or official_docs."
        )

    required_proof_evidence = PACKET_PROOF_EVIDENCE_REQUIREMENTS.get(proof_level)
    if required_proof_evidence and not evidence_kinds.intersection(required_proof_evidence):
        ok = False
        reasons.append("proof_evidence_mismatch")
        issues.append(
            f"{proof_level} requires evidence kind from {sorted(required_proof_evidence)}."
        )

    behavioral_claim = (
        claim_type in PACKET_BEHAVIORAL_CLAIM_TYPES
        or bool(packet.get("behavioralClaim"))
        or bool(packet.get("wiringClaim"))
    )
    if behavioral_claim and (not isinstance(behavior_path, list) or len(behavior_path) < 3):
        ok = False
        reasons.append("behavior_path_incomplete")
        issues.append("Behavioral/wiring claim requires at least three BehaviorPath stages.")
    if behavioral_claim and "entry" not in stages:
        ok = False
        reasons.append("behavior_path_incomplete")
        issues.append("Behavioral claim requires an entry stage.")
    if behavioral_claim and not stages.intersection({"decision", "dispatch"}):
        ok = False
        reasons.append("behavior_path_incomplete")
        issues.append("Behavioral claim requires a decision or dispatch stage.")
    if behavioral_claim and not stages.intersection({"mutation", "side_effect", "observer"}):
        ok = False
        reasons.append("behavior_path_incomplete")
        issues.append("Behavioral claim requires a final effect or observer stage.")
    wiring_claim = claim_type == "wiring" or bool(packet.get("wiringClaim"))
    ordered_final_stages = (
        {"mutation", "side_effect"}
        if wiring_claim
        else {"mutation", "side_effect", "observer"}
    )
    if behavioral_claim and not _packet_has_ordered_flow(behavior_path, ordered_final_stages):
        ok = False
        reasons.append("behavior_path_incomplete")
        issues.append(
            "BehaviorPath must order entry before decision/dispatch before the final effect."
        )
    if wiring_claim and not stages.intersection({"mutation", "side_effect"}):
        ok = False
        reasons.append("presence_not_wiring")
        issues.append(
            "Wiring claim must identify a mutation or side_effect stage and its stageStatus."
        )
    if "unknown" in path_statuses and verdict not in {"Ambiguous", "NeedsRuntimeProof"}:
        ok = False
        reasons.append("behavior_path_unknown")
        issues.append(
            "Unknown BehaviorPath stages require verdict Ambiguous or NeedsRuntimeProof."
        )
    if critical and proof_level == "Proposed":
        ok = False
        reasons.append("proof_level_too_weak")
        issues.append("P0/P1 findings cannot remain at Proposed proof level.")
    if verdict in {"Ambiguous", "NeedsRuntimeProof"} and not packet.get("unknowns"):
        warnings.append(f"{verdict} claim should list the remaining unknowns.")

    return ok, issues, warnings, reasons


def validate_claim(
    claim_text: str,
    project_root: Path,
    pab: dict[str, Any] | None = None,
    claim_packet: dict[str, Any] | None = None,
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

    if EXISTS_CLAIM_RE.search(claim_text) and not MISSING_CLAIM_RE.search(claim_text):
        if not unique_symbols:
            ok = False
            issues.append(
                "Existence claim has no project symbol; needs_source_read via search_files/read_file "
                "(guideline/RAG alone is not project evidence)."
            )
            reasons.append("needs_source_read")
        else:
            for symbol in unique_symbols:
                hits = grep_project(project_root, re.escape(symbol))
                if not hits:
                    ok = False
                    issues.append(
                        f"Claim says '{symbol}' exists but no Source hit; needs_source_read "
                        "or mark the feature absent (do not cite guideline RAG as proof)."
                    )
                    reasons.append("needs_source_read")
                else:
                    evidence.extend(hits[:3])

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

    packet_ok, packet_issues, packet_warnings, packet_reasons = check_evidence_packet_guards(
        claim_text,
        claim_packet,
    )
    if not packet_ok:
        ok = False
        issues.extend(packet_issues)
        reasons.extend(packet_reasons)
    warnings.extend(packet_warnings)

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
    claims: list[Any],
    project_root: str | Path | None = None,
    pab_path: str | Path | None = None,
) -> dict[str, Any]:
    root = resolve_project_root(str(project_root) if project_root else None)
    pab = load_pab(root, Path(pab_path) if pab_path else None)
    results = []
    for claim in claims:
        packet = claim if isinstance(claim, dict) else None
        claim_text = str(
            (packet or {}).get("claim")
            or (packet or {}).get("text")
            or (claim if isinstance(claim, str) else "")
        ).strip()
        if not claim_text:
            continue
        row = validate_claim(claim_text, root, pab, packet)
        row["claim"] = claim_text[:500]
        row["structured"] = packet is not None
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
    claims: list[Any] = list(args.claim or [])
    if args.claims_file:
        data = json.loads(Path(args.claims_file).read_text(encoding="utf-8-sig"))
        if isinstance(data, list):
            claims.extend(data)
        elif isinstance(data, dict):
            for key in ("claims", "findings"):
                if isinstance(data.get(key), list):
                    for item in data[key]:
                        if isinstance(item, str):
                            claims.append(item)
                        elif isinstance(item, dict):
                            claims.append(item)

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
