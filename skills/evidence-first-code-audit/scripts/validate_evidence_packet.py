#!/usr/bin/env python3
"""Validate portable evidence-first audit packets without external dependencies."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

VERDICTS = {"Bug", "ByDesign", "Ambiguous", "NeedsRuntimeProof"}
SEVERITIES = {"P0", "P1", "P2", "P3"}
PROOF_LEVELS = {
    "Proposed",
    "SourceVerified",
    "StaticVerified",
    "BuildVerified",
    "TestVerified",
    "RuntimeVerified",
}
EVIDENCE_KINDS = {
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
BEHAVIORAL_STAGES = {"entry", "decision", "dispatch", "mutation", "side_effect", "observer"}
PATH_STATUSES = {"present", "expected_missing", "unknown"}
CLAIM_TYPES = {
    "existence",
    "behavior",
    "framework_semantics",
    "wiring",
    "state_transition",
    "data_flow",
    "architecture",
    "codegen",
}
BEHAVIORAL_CLAIM_TYPES = {"behavior", "wiring", "state_transition", "data_flow"}
PROOF_EVIDENCE_REQUIREMENTS = {
    "SourceVerified": {"project_source", "framework_source", "official_docs"},
    "StaticVerified": {"static_analysis"},
    "BuildVerified": {"build"},
    "TestVerified": {"test"},
    "RuntimeVerified": {"runtime"},
}


def _nonempty_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value)


def _validate_evidence(entries: Any, path: str, errors: list[str]) -> set[str]:
    kinds: set[str] = set()
    if not isinstance(entries, list):
        errors.append(f"{path} must be an array")
        return kinds
    for index, entry in enumerate(entries):
        item_path = f"{path}[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{item_path} must be an object")
            continue
        kind = str(entry.get("kind") or "")
        if kind not in EVIDENCE_KINDS:
            errors.append(f"{item_path}.kind must be one of {sorted(EVIDENCE_KINDS)}")
        else:
            kinds.add(kind)
        if not str(entry.get("location") or "").strip():
            errors.append(f"{item_path}.location is required")
        if not str(entry.get("observation") or "").strip():
            errors.append(f"{item_path}.observation is required")
    return kinds


def _validate_behavior_path(entries: Any, path: str, errors: list[str]) -> set[str]:
    stages: set[str] = set()
    if not isinstance(entries, list):
        errors.append(f"{path} must be an array")
        return stages
    for index, entry in enumerate(entries):
        item_path = f"{path}[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{item_path} must be an object")
            continue
        stage = str(entry.get("stage") or "")
        if stage not in BEHAVIORAL_STAGES:
            errors.append(f"{item_path}.stage must be one of {sorted(BEHAVIORAL_STAGES)}")
        else:
            stages.add(stage)
        stage_status = str(entry.get("stageStatus") or "")
        if stage_status not in PATH_STATUSES:
            errors.append(f"{item_path}.stageStatus must be one of {sorted(PATH_STATUSES)}")
        if not str(entry.get("location") or "").strip():
            errors.append(f"{item_path}.location is required")
        if not str(entry.get("symbol") or "").strip():
            errors.append(f"{item_path}.symbol is required")
    return stages


def _has_ordered_flow(entries: Any, final_stages: set[str]) -> bool:
    if not isinstance(entries, list):
        return False
    stages = [
        str(entry.get("stage") or "")
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


def validate_packet(packet: Any) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(packet, dict):
        return {"ok": False, "errors": ["packet must be a JSON object"], "warnings": []}

    mode = str(packet.get("mode") or "")
    if mode not in {"audit", "architecture", "codegen"}:
        errors.append("mode must be audit, architecture, or codegen")

    claims = packet.get("claims")
    if not _nonempty_list(claims):
        errors.append("claims must be a non-empty array")
        claims = []

    for index, claim in enumerate(claims):
        path = f"claims[{index}]"
        if not isinstance(claim, dict):
            errors.append(f"{path} must be an object")
            continue
        if not str(claim.get("claim") or "").strip():
            errors.append(f"{path}.claim is required")
        verdict = str(claim.get("verdict") or "")
        severity = str(claim.get("severity") or "")
        proof = str(claim.get("proofLevel") or "")
        claim_type = str(claim.get("claimType") or "")
        if verdict not in VERDICTS:
            errors.append(f"{path}.verdict must be one of {sorted(VERDICTS)}")
        if severity not in SEVERITIES:
            errors.append(f"{path}.severity must be one of {sorted(SEVERITIES)}")
        if proof not in PROOF_LEVELS:
            errors.append(f"{path}.proofLevel must be one of {sorted(PROOF_LEVELS)}")
        if claim_type not in CLAIM_TYPES:
            errors.append(f"{path}.claimType must be one of {sorted(CLAIM_TYPES)}")
        unknowns = claim.get("unknowns")
        if not isinstance(unknowns, list):
            errors.append(f"{path}.unknowns must be an array")
        else:
            for unknown_index, unknown in enumerate(unknowns):
                if not isinstance(unknown, str) or not unknown.strip():
                    errors.append(f"{path}.unknowns[{unknown_index}] must be a non-empty string")

        evidence = claim.get("evidence")
        evidence_kinds = _validate_evidence(evidence, f"{path}.evidence", errors)
        counter = claim.get("counterEvidence")
        _validate_evidence(counter, f"{path}.counterEvidence", errors)
        behavior = claim.get("behaviorPath")
        stages = _validate_behavior_path(behavior, f"{path}.behaviorPath", errors)
        path_statuses = {
            str(entry.get("stageStatus") or "")
            for entry in behavior
            if isinstance(behavior, list) and isinstance(entry, dict)
        } if isinstance(behavior, list) else set()

        critical = severity in {"P0", "P1"}
        if not evidence:
            errors.append(
                f"{path} needs requirement, source, static, build, test, or runtime evidence"
            )
        if critical and not counter:
            errors.append(f"{path} needs counterEvidence for a P0/P1 claim")
        if claim_type == "framework_semantics" and not evidence_kinds.intersection(
            {"framework_source", "official_docs"}
        ):
            errors.append(
                f"{path} framework_semantics claim needs framework_source or official_docs evidence"
            )

        required_kinds = PROOF_EVIDENCE_REQUIREMENTS.get(proof)
        if required_kinds and not evidence_kinds.intersection(required_kinds):
            errors.append(
                f"{path} {proof} needs evidence kind from {sorted(required_kinds)}"
            )

        behavioral = claim_type in BEHAVIORAL_CLAIM_TYPES
        if behavioral and (not isinstance(behavior, list) or len(behavior) < 3):
            errors.append(f"{path} behavioral/wiring claim needs at least three behaviorPath stages")
        if behavioral and "entry" not in stages:
            errors.append(f"{path} behavioral claim needs an entry stage")
        if behavioral and not stages.intersection({"decision", "dispatch"}):
            errors.append(f"{path} behavioral claim needs a decision or dispatch stage")
        if behavioral and not stages.intersection({"mutation", "side_effect", "observer"}):
            errors.append(f"{path} behavioral claim needs a final effect or observer stage")
        ordered_final_stages = (
            {"mutation", "side_effect"}
            if claim_type == "wiring"
            else {"mutation", "side_effect", "observer"}
        )
        if behavioral and not _has_ordered_flow(behavior, ordered_final_stages):
            errors.append(
                f"{path} behaviorPath must order entry before decision/dispatch before final effect"
            )
        if claim_type == "wiring" and not stages.intersection({"mutation", "side_effect"}):
            errors.append(
                f"{path} wiring claim must identify a mutation or side_effect stage and its status"
            )
        if "unknown" in path_statuses and verdict not in {"Ambiguous", "NeedsRuntimeProof"}:
            errors.append(
                f"{path} unknown behaviorPath stages require Ambiguous or NeedsRuntimeProof verdict"
            )
        if critical and proof == "Proposed":
            errors.append(f"{path} P0/P1 claim cannot remain Proposed")
        if verdict in {"Ambiguous", "NeedsRuntimeProof"} and not _nonempty_list(claim.get("unknowns")):
            warnings.append(f"{path} should record unknowns for {verdict}")

    if mode == "architecture":
        for field in ("existing", "proposed", "doNotDuplicate"):
            if not _nonempty_list(packet.get(field)):
                errors.append(f"architecture packet needs non-empty {field}")
    if mode == "codegen":
        for field in ("invariants", "impactedSurfaces", "validationPlan"):
            if not _nonempty_list(packet.get(field)):
                errors.append(f"codegen packet needs non-empty {field}")

    return {
        "ok": not errors,
        "mode": mode,
        "claimCount": len(claims),
        "errors": errors,
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packet", nargs="?", help="JSON packet path; omit to read stdin")
    args = parser.parse_args()
    if args.packet:
        payload = json.loads(Path(args.packet).read_text(encoding="utf-8-sig"))
    else:
        payload = json.load(sys.stdin)
    result = validate_packet(payload)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
