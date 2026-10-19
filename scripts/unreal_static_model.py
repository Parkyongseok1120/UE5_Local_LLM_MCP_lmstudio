#!/usr/bin/env python
"""Shared finding model and source suffix contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from unreal_source_extensions import (
    UNREAL_CPP_IMPLEMENTATION_SUFFIXES,
    UNREAL_CPP_SOURCE_SUFFIXES,
    UNREAL_CPP_SUFFIXES,
    UNREAL_HEADER_SUFFIXES,
)

CPP_SOURCE_SUFFIXES = UNREAL_CPP_SOURCE_SUFFIXES

CPP_IMPLEMENTATION_SUFFIXES = UNREAL_CPP_IMPLEMENTATION_SUFFIXES

CPP_HEADER_SUFFIXES = UNREAL_HEADER_SUFFIXES

SOURCE_ONLY_SUFFIXES = UNREAL_CPP_SUFFIXES

IGNORED_PROJECT_DIRS = {
    ".git",
    ".vs",
    "Binaries",
    "DerivedDataCache",
    "golden",
    "Intermediate",
    "Saved",
    "Marketplace",
    "ThirdParty",
    "Engine",
}

@dataclass
class Finding:
    severity: str
    path: str
    line: int
    code: str
    message: str
    # Optional trailing metadata; the five positional fields stay compatible.
    # Keys: ruleId, contractRevision, contractIds, analysisKind (how it was
    # analysed), confidence (how sure), evidenceRefs, assumptions, limitations.
    # severity remains the impact; none of these substitute for each other.
    metadata: dict[str, Any] | None = None


@dataclass
class ValidationReport:
    """Whole-run result: findings plus what was and was not analysed."""
    findings: list[Finding]
    analysisCoverage: dict[str, Any]
    engineIdentity: dict[str, Any] | None
    contractRevision: str | None

__all__ = [
    'CPP_SOURCE_SUFFIXES',
    'CPP_IMPLEMENTATION_SUFFIXES',
    'CPP_HEADER_SUFFIXES',
    'SOURCE_ONLY_SUFFIXES',
    'IGNORED_PROJECT_DIRS',
    'Finding',
    'ValidationReport',
]
