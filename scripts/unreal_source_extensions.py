"""Shared Unreal C/C++ source extension policy for Python scanners."""

from __future__ import annotations

UNREAL_CPP_SOURCE_SUFFIXES = frozenset({".c", ".cc", ".cpp", ".cxx", ".mm"})
UNREAL_CPP_IMPLEMENTATION_SUFFIXES = frozenset({".cc", ".cpp", ".cxx", ".mm"})
UNREAL_HEADER_SUFFIXES = frozenset({".h", ".hh", ".hpp", ".hxx", ".inl", ".ipp"})
UNREAL_CPP_SUFFIXES = UNREAL_CPP_SOURCE_SUFFIXES | UNREAL_HEADER_SUFFIXES

# Build.cs files participate in module ownership checks, but ordinary unrelated
# text and script files must never broaden C/C++ declaration discovery.
UNREAL_SCAN_SUFFIXES = UNREAL_CPP_SUFFIXES | frozenset({".cs"})
