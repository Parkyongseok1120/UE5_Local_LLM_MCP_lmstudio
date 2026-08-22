#!/usr/bin/env python
"""Shared finding model and source suffix contracts."""

from __future__ import annotations

from dataclasses import dataclass

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

__all__ = [
    'CPP_SOURCE_SUFFIXES',
    'CPP_IMPLEMENTATION_SUFFIXES',
    'CPP_HEADER_SUFFIXES',
    'SOURCE_ONLY_SUFFIXES',
    'IGNORED_PROJECT_DIRS',
    'Finding',
]
