#!/usr/bin/env python
"""Shared, dependency-free RAG data types.

Extracted so that rag_semantic (and other consumers) can import the shared
SearchOptions type without importing rag_search, which previously forced a
bidirectional module dependency resolved with in-function lazy imports.
This module must not import any other project module.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SearchOptions:
    mode: str = "auto"
    sources: list[str] = field(default_factory=list)
    projects: list[str] = field(default_factory=list)
    layers: list[str] = field(default_factory=list)
    doc_types: list[str] = field(default_factory=list)
    genres: list[str] = field(default_factory=list)
    extensions: list[str] = field(default_factory=list)
    required_terms: list[str] = field(default_factory=list)
    candidate_limit: int = 120
