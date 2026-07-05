#!/usr/bin/env python
"""Validate structured architecture claims against architecture_map.json."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def type_index(arch: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row.get("name") or ""): row for row in arch.get("types") or [] if row.get("name")}


def resolve_subject(subject: str, arch: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    index = type_index(arch)
    if subject in index:
        return index[subject], subject
    candidates: list[tuple[str, dict[str, Any]]] = []
    for name, row in index.items():
        stripped = name[1:] if name[:1] in {"A", "F", "I", "S", "T", "U"} else name
        if subject == stripped:
            candidates.append((name, row))
    if len(candidates) == 1:
        return candidates[0][1], candidates[0][0]
    return None, subject


def _has_reflected_property(row: dict[str, Any]) -> bool:
    return bool((row.get("reflectedSurface") or {}).get("properties"))


def _has_reflected_function(row: dict[str, Any]) -> bool:
    return bool((row.get("reflectedSurface") or {}).get("functions"))


def _has_member_variable(row: dict[str, Any]) -> bool:
    return bool((row.get("memberEvidence") or {}).get("variables"))


def _has_member_method(row: dict[str, Any]) -> bool:
    return bool((row.get("memberEvidence") or {}).get("methods"))


def _has_function_specifier(row: dict[str, Any], specifier: str) -> bool:
    for fn in (row.get("reflectedSurface") or {}).get("functions") or []:
        if specifier in (fn.get("specifiers") or []):
            return True
    return False


def evidence_supported(row: dict[str, Any], evidence: str) -> bool:
    raw = str(evidence or "").strip().lower()
    if raw in {"reflected property", "uproperty"}:
        return _has_reflected_property(row)
    if raw in {"property", "member", "member variable", "field", "variable"}:
        return _has_reflected_property(row) or _has_member_variable(row)
    if raw in {"reflected function", "ufunction"}:
        return _has_reflected_function(row)
    if raw in {"function", "method", "member function", "regular function"}:
        return _has_reflected_function(row) or _has_member_method(row)
    if raw in {"cpp pair", "header/cpp pair", "cpp definition"}:
        return bool(row.get("header") and row.get("cpp"))
    if raw in {"module ownership", "module"}:
        return bool(row.get("module"))
    if raw in {"uprop/ufunc surface", "uproperty/ufunction surface", "reflected surface"}:
        return _has_reflected_property(row) or _has_reflected_function(row)
    if raw in {"asset reference hint", "asset reference"}:
        return "possible_asset_reference" in row.get("riskFlags", [])
    if raw in {"blueprint-facing risk", "blueprint surface"}:
        return "blueprint_facing_surface" in row.get("riskFlags", [])
    if raw in {"blueprintnativeevent", "blueprint native event"}:
        return _has_function_specifier(row, "BlueprintNativeEvent") or "blueprint_native_event_surface" in row.get("riskFlags", [])
    if raw in {"blueprintimplementableevent", "blueprint implementable event"}:
        return (
            _has_function_specifier(row, "BlueprintImplementableEvent")
            or "blueprint_implementable_event_surface" in row.get("riskFlags", [])
        )
    if raw in {"blueprint event", "blueprint event surface"}:
        return "blueprint_event_surface" in row.get("riskFlags", [])
    return False


def validate_claim(claim: dict[str, Any], arch: dict[str, Any]) -> dict[str, Any]:
    subject = str(claim.get("subject") or "").strip()
    claim_type = str(claim.get("type") or "").strip().lower()
    required = [str(item) for item in claim.get("requiredEvidence") or []]
    change_type = str(claim.get("changeType") or "").strip().lower()
    risk_text = " ".join(str(item) for item in claim.get("riskIfChanged") or []).lower()
    row, resolved_subject = resolve_subject(subject, arch)

    issues: list[str] = []
    warnings: list[str] = []
    evidence: list[str] = []

    if not subject:
        issues.append("missing subject")
    if subject and not row:
        issues.append(f"missing subject: {subject}")
        return {
            "claim": str(claim.get("claim") or ""),
            "subject": subject,
            "ok": False,
            "issues": issues,
            "warnings": warnings,
            "evidence": evidence,
            "confidence": "none",
        }

    if row:
        for item in required:
            if evidence_supported(row, item):
                evidence.append(item)
            else:
                issues.append(f"missing evidence: {item}")

        risk_flags = set(row.get("riskFlags") or [])
        if claim_type == "ownership" and not (
            _has_reflected_property(row) or _has_reflected_function(row) or _has_member_variable(row) or _has_member_method(row)
        ):
            issues.append("unsupported ownership claim: no member/function evidence")
        if change_type == "rename" or "rename" in risk_text:
            if _has_reflected_property(row) or _has_reflected_function(row):
                warnings.append("reflected rename risk: migration and Blueprint validation required")
            if "blueprint_facing_surface" in risk_flags:
                warnings.append("Blueprint-facing change risk: validate Blueprint references before changing names or signatures")
        if "possible_asset_reference" in risk_flags:
            warnings.append("asset/reference validation required before claiming unused or safe-to-remove")
        if "runtime_editor_boundary_risk" in risk_flags:
            warnings.append("editor/runtime boundary validation required")
        if "blueprint_event_surface" in risk_flags:
            warnings.append("Blueprint event implementation/override validation required")
        if required and not evidence:
            warnings.append("low-confidence claim: no requested evidence was found")

    return {
        "claim": str(claim.get("claim") or ""),
        "subject": subject,
        "resolvedSubject": resolved_subject if resolved_subject != subject else "",
        "ok": not issues,
        "issues": issues,
        "warnings": warnings,
        "evidence": evidence,
        "confidence": "medium" if evidence and not issues else ("low" if not issues else "none"),
    }


def validate_claims_payload(arch: dict[str, Any], claims_payload: dict[str, Any] | list[Any]) -> dict[str, Any]:
    raw_claims = claims_payload if isinstance(claims_payload, list) else claims_payload.get("claims", [])
    claims = [item for item in raw_claims if isinstance(item, dict)]
    results = [validate_claim(claim, arch) for claim in claims]
    fail_count = sum(1 for row in results if not row.get("ok"))
    return {
        "ok": fail_count == 0,
        "claimCount": len(results),
        "failCount": fail_count,
        "results": results,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate structured architecture claims.")
    parser.add_argument("--architecture", type=Path, required=True, help="Path to architecture_map.json.")
    parser.add_argument("--claims", type=Path, required=True, help="JSON file containing claims[].")
    parser.add_argument("--out", type=Path, default=None, help="Optional output JSON path. Defaults to stdout.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    arch = load_json(args.architecture)
    claims = load_json(args.claims)
    payload = validate_claims_payload(arch, claims)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"Wrote {args.out}")
    else:
        print(text, end="")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
