#!/usr/bin/env python
"""Engine identity and version policy for the Unreal static validators.

The validators are lexical and their engine-fact tables are validated against
specific engine versions (``validatedEngineVersions`` in the contract file).
This module reads the project's engine identity through the single owners
(``workspace_paths`` for root resolution, ``engine_identity`` for reading) and
lowers the confidence of findings that depend on those tables when the engine
is not one of the validated versions. It never assumes a default version.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from engine_identity import describe_unreal_engine_identity
from unreal_contracts import engine_version_tier
from unreal_static_model import Finding

# Findings whose correctness depends on the version-specific override table.
VERSION_DEPENDENT_FINDING_CODES = frozenset({"INVALID_UNREAL_LIFECYCLE_OVERRIDE"})


def _unknown(reason: str, **extra: Any) -> dict[str, Any]:
    return {"status": "unknown", "engine": "unreal", "reason": reason[:180], **extra}


def resolve_static_engine_identity(project_root: Path) -> dict[str, Any]:
    """Identity of the engine the project at ``project_root`` is associated with."""
    root = Path(project_root)
    descriptors = sorted(root.glob("*.uproject")) if root.is_dir() else []
    if len(descriptors) != 1:
        return _unknown("expected exactly one .uproject at the project root", projectRoot=str(root))
    try:
        payload = json.loads(descriptors[0].read_text(encoding="utf-8-sig"))
        association = payload.get("EngineAssociation", "") if isinstance(payload, dict) else ""
        if not isinstance(association, str):
            return _unknown("EngineAssociation is not a string", projectRoot=str(root))
        from workspace_paths import resolve_engine_root_for_association

        resolution = resolve_engine_root_for_association(association.strip(), root)
    except (OSError, ValueError) as exc:
        return _unknown(f"project descriptor unreadable: {exc}", projectRoot=str(root))
    if resolution.get("ok") is not True or not resolution.get("engineRoot"):
        return _unknown(str(resolution.get("error") or "engine root not resolved"),
                        projectRoot=str(root), engineAssociation=association.strip())
    identity = describe_unreal_engine_identity(Path(str(resolution["engineRoot"])))
    return {**identity, "engineAssociation": association.strip()}


def apply_engine_version_policy(findings: list[Finding], identity: dict[str, Any],
                                contracts: dict[str, Any] | None) -> list[Finding]:
    """Downgrade version-dependent findings unless the engine is validated."""
    if not contracts or not any(str(f.code) in VERSION_DEPENDENT_FINDING_CODES for f in findings):
        return findings
    observed = identity.get("version") if identity.get("status") == "observed" else None
    tier = engine_version_tier(contracts, observed)
    if tier == "validated":
        return findings
    validated = ", ".join(contracts["validatedEngineVersions"])
    result: list[Finding] = []
    for finding in findings:
        if str(finding.code) not in VERSION_DEPENDENT_FINDING_CODES:
            result.append(finding)
            continue
        metadata = dict(finding.metadata or {})
        limitations = list(metadata.get("limitations") or [])
        limitations.append(f"override table validated on {validated}")
        metadata.update({"confidence": "medium", "engineVersionTier": tier, "limitations": limitations})
        result.append(replace(finding, severity="warning", metadata=metadata))
    return result


__all__ = ["VERSION_DEPENDENT_FINDING_CODES", "apply_engine_version_policy", "resolve_static_engine_identity"]
