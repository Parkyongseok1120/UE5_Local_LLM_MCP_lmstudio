"""Declarative single source for the portable evidence-packet contract."""

from __future__ import annotations

from typing import Any


SCHEMA_VERSION = "1.1.0"
MODES = ("audit", "architecture", "codegen")
NEUTRAL_VERDICT = "Confirmed"
NEUTRAL_SEVERITY = "Info"
NEUTRAL_MODE = "architecture"
NEUTRAL_EXCLUDED_CLAIM_TYPES = frozenset({"codegen"})
VERDICTS = ("Ambiguous", "Bug", "ByDesign", NEUTRAL_VERDICT, "NeedsRuntimeProof")
SEVERITIES = (NEUTRAL_SEVERITY, "P0", "P1", "P2", "P3")
PROOF_LEVELS = (
    "Proposed",
    "SourceVerified",
    "StaticVerified",
    "BuildVerified",
    "TestVerified",
    "RuntimeVerified",
)
EVIDENCE_KINDS = (
    "requirement",
    "project_source",
    "framework_source",
    "official_docs",
    "static_analysis",
    "build",
    "test",
    "runtime",
    "generated_metadata",
)
BEHAVIORAL_STAGES = (
    "entry",
    "decision",
    "dispatch",
    "mutation",
    "side_effect",
    "observer",
)
PATH_STATUSES = ("present", "expected_missing", "unknown")
CLAIM_TYPES = (
    "existence",
    "behavior",
    "framework_semantics",
    "wiring",
    "state_transition",
    "data_flow",
    "architecture",
    "codegen",
)
BEHAVIORAL_CLAIM_TYPES = frozenset({"behavior", "wiring", "state_transition", "data_flow"})
VERIFIED_PROOF_LEVELS = frozenset(
    {"SourceVerified", "StaticVerified", "BuildVerified", "TestVerified", "RuntimeVerified"}
)
PROOF_EVIDENCE_REQUIREMENTS = {
    "SourceVerified": frozenset({"project_source", "framework_source", "official_docs"}),
    "StaticVerified": frozenset({"static_analysis"}),
    "BuildVerified": frozenset({"build"}),
    "TestVerified": frozenset({"test"}),
    "RuntimeVerified": frozenset({"runtime"}),
}
REQUIRED_CLAIM_FIELDS = (
    "claim",
    "claimType",
    "verdict",
    "severity",
    "proofLevel",
    "evidence",
    "behaviorPath",
    "counterEvidence",
    "unknowns",
)
REQUIRED_EVIDENCE_FIELDS = ("kind", "location", "observation")
REQUIRED_BEHAVIOR_PATH_FIELDS = ("stage", "stageStatus", "location", "symbol")
MODE_OBLIGATIONS = {
    "audit": (),
    "architecture": ("existing", "proposed", "doNotDuplicate"),
    "codegen": ("invariants", "impactedSurfaces", "validationPlan"),
}


def _nonempty_string_schema() -> dict[str, Any]:
    return {"type": "string", "minLength": 1, "pattern": r"\S"}


def selected_mode(mode: str) -> str:
    return mode if mode in MODES else "audit"


def _array_schema(
    description: str,
    *,
    items: dict[str, Any] | None = None,
    min_items: int | None = None,
) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "array", "description": description}
    if items is not None:
        schema["items"] = items
    if min_items is not None:
        schema["minItems"] = min_items
    return schema


def _evidence_item_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": list(EVIDENCE_KINDS)},
            "location": _nonempty_string_schema(),
            "observation": _nonempty_string_schema(),
        },
        "required": list(REQUIRED_EVIDENCE_FIELDS),
    }


def _behavior_path_item_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "stage": {"type": "string", "enum": list(BEHAVIORAL_STAGES)},
            "stageStatus": {"type": "string", "enum": list(PATH_STATUSES)},
            "location": _nonempty_string_schema(),
            "symbol": _nonempty_string_schema(),
        },
        "required": list(REQUIRED_BEHAVIOR_PATH_FIELDS),
    }


def _claim_schema() -> dict[str, Any]:
    evidence_item = _evidence_item_schema()
    return {
        "type": "object",
        "properties": {
            "claim": _nonempty_string_schema(),
            "claimType": {"type": "string", "enum": list(CLAIM_TYPES)},
            "verdict": {
                "type": "string",
                "enum": list(VERDICTS),
                "description": (
                    f"{NEUTRAL_VERDICT} is reserved for verified neutral as-built facts in "
                    f"{NEUTRAL_MODE} mode."
                ),
            },
            "severity": {
                "type": "string",
                "enum": list(SEVERITIES),
                "description": (
                    f"{NEUTRAL_SEVERITY} is valid only with the {NEUTRAL_VERDICT} verdict."
                ),
            },
            "proofLevel": {"type": "string", "enum": list(PROOF_LEVELS)},
            "evidence": _array_schema(
                "Typed evidence supporting this claim; at least one item is required.",
                items=evidence_item,
                min_items=1,
            ),
            "behaviorPath": _array_schema(
                "Ordered causal stages; required to contain at least three stages for behavioral claims.",
                items=_behavior_path_item_schema(),
            ),
            "counterEvidence": _array_schema(
                "Counterevidence; at least one item is required for P0/P1 claims.",
                items=evidence_item,
            ),
            "unknowns": _array_schema(
                "Remaining unknowns.",
                items=_nonempty_string_schema(),
            ),
        },
        "required": list(REQUIRED_CLAIM_FIELDS),
    }


def packet_input_schema() -> dict[str, Any]:
    """Return the exact nested packet shape exposed through MCP tools/list."""
    obligation_array = {
        "type": "array",
        "description": "Mode-specific obligation entries; concise strings are recommended.",
    }
    return {
        "type": "object",
        "properties": {
            "mode": {"type": "string", "enum": list(MODES)},
            "claims": {
                "type": "array",
                "minItems": 1,
                "items": _claim_schema(),
            },
            "existing": dict(obligation_array),
            "proposed": dict(obligation_array),
            "doNotDuplicate": dict(obligation_array),
            "invariants": dict(obligation_array),
            "impactedSurfaces": dict(obligation_array),
            "validationPlan": dict(obligation_array),
        },
        "required": ["mode", "claims"],
    }


def contract_metadata(mode: str) -> dict[str, Any]:
    """Return compact model-facing metadata derived only from canonical declarations."""
    normalized_mode = selected_mode(mode)
    obligation_rules: dict[str, dict[str, Any]] = {
        "audit": {"requiredArrays": [], "nonEmptyArrays": []},
        "architecture": {
            "requiredArrays": list(MODE_OBLIGATIONS["architecture"]),
            "nonEmptyArrays": ["existing"],
            "emptyArraysAllowed": ["proposed", "doNotDuplicate"],
        },
        "codegen": {
            "requiredArrays": list(MODE_OBLIGATIONS["codegen"]),
            "nonEmptyArrays": list(MODE_OBLIGATIONS["codegen"]),
        },
    }
    return {
        "schemaVersion": SCHEMA_VERSION,
        "requiredClaimFields": list(REQUIRED_CLAIM_FIELDS),
        "claimTypes": list(CLAIM_TYPES),
        "verdicts": list(VERDICTS),
        "severities": list(SEVERITIES),
        "proofLevels": list(PROOF_LEVELS),
        "evidenceKinds": list(EVIDENCE_KINDS),
        "requiredEvidenceFields": list(REQUIRED_EVIDENCE_FIELDS),
        "requiredBehaviorPathFields": list(REQUIRED_BEHAVIOR_PATH_FIELDS),
        "behaviorStages": list(BEHAVIORAL_STAGES),
        "stageStatuses": list(PATH_STATUSES),
        "behavioralClaimTypes": sorted(BEHAVIORAL_CLAIM_TYPES),
        "proofEvidence": {
            proof: sorted(kinds) for proof, kinds in PROOF_EVIDENCE_REQUIREMENTS.items()
        },
        "neutralArchitecture": {
            "verdict": NEUTRAL_VERDICT,
            "severity": NEUTRAL_SEVERITY,
            "requiresMode": NEUTRAL_MODE,
            "excludedClaimTypes": sorted(NEUTRAL_EXCLUDED_CLAIM_TYPES),
            "allowedProofLevels": sorted(VERIFIED_PROOF_LEVELS),
        },
        "validationConditions": [
            "P0/P1 claims require non-empty counterEvidence and cannot remain Proposed.",
            "Behavioral claims require an ordered entry -> decision/dispatch -> final effect path of at least three stages.",
            "Unknown path stages require Ambiguous or NeedsRuntimeProof.",
            "Confirmed and Info must be paired and are limited to verified non-codegen facts in architecture mode.",
        ],
        "modeObligations": list(MODE_OBLIGATIONS[normalized_mode]),
        "modeObligationRules": obligation_rules[normalized_mode],
    }
