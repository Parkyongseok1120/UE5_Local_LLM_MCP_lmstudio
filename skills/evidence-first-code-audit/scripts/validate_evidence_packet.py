#!/usr/bin/env python3
"""Validate portable evidence-first audit packets without external dependencies."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from evidence_packet_contract import (
    BEHAVIORAL_CLAIM_TYPES,
    BEHAVIORAL_STAGES,
    CLAIM_TYPES,
    EVIDENCE_KINDS,
    MODE_OBLIGATIONS,
    MODES,
    NEUTRAL_EXCLUDED_CLAIM_TYPES,
    NEUTRAL_MODE,
    NEUTRAL_SEVERITY,
    NEUTRAL_VERDICT,
    PATH_STATUSES,
    PROOF_EVIDENCE_REQUIREMENTS,
    PROOF_LEVELS,
    SEVERITIES,
    VERDICTS,
    VERIFIED_PROOF_LEVELS,
)


def _nonempty_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value)


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


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
        if not _nonempty_string(entry.get("location")):
            errors.append(f"{item_path}.location must be a non-empty string")
        if not _nonempty_string(entry.get("observation")):
            errors.append(f"{item_path}.observation must be a non-empty string")
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
        if not _nonempty_string(entry.get("location")):
            errors.append(f"{item_path}.location must be a non-empty string")
        if not _nonempty_string(entry.get("symbol")):
            errors.append(f"{item_path}.symbol must be a non-empty string")
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
    if mode not in MODES:
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
        if not _nonempty_string(claim.get("claim")):
            errors.append(f"{path}.claim must be a non-empty string")
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
        neutral_verdict = verdict == NEUTRAL_VERDICT
        neutral_severity = severity == NEUTRAL_SEVERITY
        if neutral_verdict != neutral_severity:
            errors.append(
                f"{path} {NEUTRAL_VERDICT} verdict and {NEUTRAL_SEVERITY} severity "
                "must be used together"
            )
        if neutral_verdict and (
            mode != NEUTRAL_MODE or claim_type in NEUTRAL_EXCLUDED_CLAIM_TYPES
        ):
            errors.append(
                f"{path} {NEUTRAL_VERDICT}/{NEUTRAL_SEVERITY} is limited to verified "
                f"non-codegen facts in {NEUTRAL_MODE} mode"
            )
        if neutral_verdict and proof not in VERIFIED_PROOF_LEVELS:
            errors.append(
                f"{path} {NEUTRAL_VERDICT}/{NEUTRAL_SEVERITY} requires a verified "
                "proofLevel, not Proposed"
            )
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
        if not _nonempty_list(packet.get("existing")):
            errors.append("architecture packet needs non-empty existing")
        for field in MODE_OBLIGATIONS["architecture"][1:]:
            if not isinstance(packet.get(field), list):
                errors.append(f"architecture packet needs {field} array; an empty array is allowed")
    if mode == "codegen":
        for field in MODE_OBLIGATIONS["codegen"]:
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
