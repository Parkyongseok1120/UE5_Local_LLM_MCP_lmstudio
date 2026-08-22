#!/usr/bin/env python
"""Delegate declaration, broadcast, binding, and unbinding validators."""

from __future__ import annotations

import re
from pathlib import Path

from cpp_parse_utils import (
    extract_macro_blocks,
    find_balanced_parens,
    mask_comments_and_strings,
)
from unreal_static_model import (
    CPP_HEADER_SUFFIXES,
    CPP_SOURCE_SUFFIXES,
    Finding,
)
from unreal_static_scan import (
    _split_top_level_args,
    iter_class_method_blocks,
    iter_source_files,
    line_number,
    normalize_timer_handle,
    read_text,
)

DELEGATE_PARAM_COUNT_WORDS = {
    "One": 1,
    "Two": 2,
    "Three": 3,
    "Four": 4,
    "Five": 5,
    "Six": 6,
    "Seven": 7,
    "Eight": 8,
    "Nine": 9,
}

DELEGATE_DECLARE_RE = re.compile(
    r"\bDECLARE_(?:DYNAMIC_)?MULTICAST_DELEGATE(?:_(One|Two|Three|Four|Five|Six|Seven|Eight|Nine)Params?)?"
    r"\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)"
)

_DELEGATE_MEMBER_DECL_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s+([A-Za-z_][A-Za-z0-9_]*)\s*;")

def build_delegate_arity_map(root: Path) -> dict[str, int]:
    """Map delegate MEMBER variable names to their declared payload arg count.

    Two-pass project-wide scan: first collect delegate TYPE -> param count from
    DECLARE_(DYNAMIC_)MULTICAST_DELEGATE... macros, then collect MEMBER -> TYPE from
    plain `TypeName MemberName;` declarations and resolve to MEMBER -> param count.
    Members whose type can't be resolved this way are simply absent from the map,
    which callers treat as "unknown" rather than assuming zero arguments.
    """
    type_param_counts: dict[str, int] = {}
    for path in iter_source_files(root):
        if path.suffix.lower() not in CPP_HEADER_SUFFIXES:
            continue
        text = read_text(path)
        for match in DELEGATE_DECLARE_RE.finditer(text):
            word = match.group(1)
            delegate_type = match.group(2)
            type_param_counts[delegate_type] = DELEGATE_PARAM_COUNT_WORDS.get(word or "", 0)

    if not type_param_counts:
        return {}

    member_arity: dict[str, int] = {}
    for path in iter_source_files(root):
        if path.suffix.lower() not in CPP_HEADER_SUFFIXES:
            continue
        text = read_text(path)
        for match in _DELEGATE_MEMBER_DECL_RE.finditer(text):
            type_name, member_name = match.group(1), match.group(2)
            if type_name in type_param_counts:
                member_arity[member_name] = type_param_counts[type_name]
    return member_arity

def build_delegate_arity_map_from_texts(paths: list[Path], texts: dict[Path, str]) -> dict[str, int]:
    type_param_counts: dict[str, int] = {}
    for path in paths:
        if path.suffix.lower() not in CPP_HEADER_SUFFIXES:
            continue
        text = texts.get(path, read_text(path))
        for match in DELEGATE_DECLARE_RE.finditer(text):
            word = match.group(1)
            delegate_type = match.group(2)
            type_param_counts[delegate_type] = DELEGATE_PARAM_COUNT_WORDS.get(word or "", 0)
    if not type_param_counts:
        return {}
    member_arity: dict[str, int] = {}
    for path in paths:
        if path.suffix.lower() not in CPP_HEADER_SUFFIXES:
            continue
        text = texts.get(path, read_text(path))
        for match in _DELEGATE_MEMBER_DECL_RE.finditer(text):
            type_name, member_name = match.group(1), match.group(2)
            if type_name in type_param_counts:
                member_arity[member_name] = type_param_counts[type_name]
    return member_arity

def build_declared_delegate_types(root: Path) -> set[str]:
    declared: set[str] = set()
    for path in iter_source_files(root):
        if path.suffix.lower() not in CPP_HEADER_SUFFIXES:
            continue
        declared.update(match.group(2) for match in DELEGATE_DECLARE_RE.finditer(read_text(path)))
    return declared

def build_declared_delegate_types_from_texts(paths: list[Path], texts: dict[Path, str]) -> set[str]:
    declared: set[str] = set()
    for path in paths:
        if path.suffix.lower() not in CPP_HEADER_SUFFIXES:
            continue
        declared.update(match.group(2) for match in DELEGATE_DECLARE_RE.finditer(texts.get(path, read_text(path))))
    return declared

def validate_blueprint_assignable_delegate_types(
    path: Path,
    text: str,
    root: Path,
    declared_delegate_types: set[str] | None = None,
) -> list[Finding]:
    """Catch a generated-header failure where a local delegate macro was omitted.

    Name correlation keeps this narrow: `FOnStaminaChangedSignature OnStaminaChanged`
    is expected to have a project declaration, while unrelated engine delegate types
    are left to UHT/UBT.
    """
    findings: list[Finding] = []
    if path.suffix.lower() not in CPP_HEADER_SUFFIXES:
        return findings
    declared = declared_delegate_types or set()
    for _start, end, block in extract_macro_blocks(text, "UPROPERTY"):
        if not re.search(r"\bBlueprintAssignable\b", block):
            continue
        tail = text[end : end + 500]
        member = re.match(
            r"\s*(?:[A-Z0-9_]+_API\s+)?(?P<type>FOn[A-Za-z0-9_]+Signature)\s+(?P<name>On[A-Za-z0-9_]+)\s*;",
            tail,
        )
        if not member:
            continue
        delegate_type = member.group("type")
        member_name = member.group("name")
        if delegate_type in declared or delegate_type != f"F{member_name}Signature":
            continue
        findings.append(
            Finding(
                "error",
                str(path.relative_to(root)),
                line_number(text, end + member.start("type")),
                "BLUEPRINT_ASSIGNABLE_DELEGATE_UNDECLARED",
                f"{delegate_type} is used by BlueprintAssignable property {member_name} but no DECLARE_DYNAMIC_MULTICAST_DELEGATE macro declares it in project headers.",
            )
        )
    return findings

def validate_delegate_broadcast_consistency(
    path: Path, text: str, root: Path, arity_map: dict[str, int] | None = None
) -> list[Finding]:
    findings: list[Finding] = []
    if path.suffix.lower() not in CPP_SOURCE_SUFFIXES:
        return findings
    arity_map = arity_map or {}
    masked = mask_comments_and_strings(text)
    for match in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*)\.Broadcast\s*\(", masked):
        member_name = match.group(1)
        if member_name not in arity_map:
            continue
        open_index = masked.find("(", match.start())
        close_index = find_balanced_parens(masked, open_index)
        if close_index < 0:
            continue
        args = _split_top_level_args(masked[open_index + 1 : close_index])
        expected_arity = arity_map[member_name]
        actual_arity = len(args)
        if actual_arity == expected_arity:
            continue
        line_no = text[: match.start()].count("\n") + 1
        findings.append(
            Finding(
                "error",
                str(path.relative_to(root)),
                line_no,
                "DELEGATE_BROADCAST_SIGNATURE_MISMATCH",
                f"{member_name}.Broadcast(...) passes {actual_arity} argument(s), but its declared delegate requires {expected_arity}.",
            )
        )
    return findings

def _extract_delegate_receiver(body: str, bind_start: int) -> str | None:
    prefix = body[max(0, bind_start - 200) : bind_start]
    match = re.search(
        r"([\w:>\[\]\.\*]+(?:->|\.)\s*[\w]+)\s*\.\s*$",
        prefix,
    )
    if not match:
        return None
    return normalize_timer_handle(match.group(1))

def _parse_delegate_bind(body: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for match in re.finditer(r"\b(?:AddDynamic|AddUObject|BindUObject)\s*\(", body):
        open_index = match.end() - 1
        close_index = find_balanced_parens(body, open_index)
        if close_index == -1:
            continue
        receiver = _extract_delegate_receiver(body, match.start())
        if not receiver:
            continue
        args = _split_top_level_args(body[open_index + 1 : close_index])
        handler = args[1].strip() if len(args) > 1 else ""
        if handler.startswith("&"):
            pairs.append((receiver, normalize_timer_handle(handler)))
    return pairs

def _parse_delegate_unbind(body: str) -> list[tuple[str, str | None]]:
    pairs: list[tuple[str, str | None]] = []
    for match in re.finditer(r"\b(?:RemoveDynamic|RemoveAll|Unbind)\s*\(", body):
        open_index = match.end() - 1
        close_index = find_balanced_parens(body, open_index)
        if close_index == -1:
            continue
        receiver = _extract_delegate_receiver(body, match.start())
        if not receiver:
            continue
        args = _split_top_level_args(body[open_index + 1 : close_index])
        handler = args[1].strip() if len(args) > 1 else None
        if handler and handler.startswith("&"):
            handler = normalize_timer_handle(handler)
        pairs.append((receiver, handler))
    return pairs

DELEGATE_BIND_RE = re.compile(r"\b(?:AddDynamic|AddUObject|BindUObject)\s*\(")

DELEGATE_RAW_BIND_RE = re.compile(r"\b(?:AddRaw|BindRaw)\s*\(")

DELEGATE_UNBIND_RE = re.compile(r"\b(?:RemoveDynamic|RemoveAll|Unbind)\s*\(")

def validate_delegate_bind_without_unbind(path: Path, text: str, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    rel = str(path.relative_to(root))
    class_methods = iter_class_method_blocks(text)
    if not class_methods:
        return findings
    by_class: dict[str, list[tuple[str, int, str]]] = {}
    for class_name, method_name, start, body in class_methods:
        by_class.setdefault(class_name, []).append((method_name, start, body))
    for class_name, methods in by_class.items():
        binds: set[tuple[str, str]] = set()
        unbinds: set[tuple[str, str | None]] = set()
        first_bind_start: int | None = None
        for method_name, start, body in methods:
            for delegate, handler in _parse_delegate_bind(body):
                binds.add((delegate, handler))
                if first_bind_start is None:
                    first_bind_start = start
            for delegate, handler in _parse_delegate_unbind(body):
                unbinds.add((delegate, handler))
        missing = [
            pair
            for pair in binds
            if (pair[0], pair[1]) not in unbinds and (pair[0], None) not in unbinds
        ]
        if missing:
            findings.append(
                Finding(
                    "warning",
                    rel,
                    line_number(text, first_bind_start or methods[0][1]),
                    "DELEGATE_BIND_WITHOUT_UNBIND",
                    f"{class_name} binds delegates without matching RemoveDynamic/RemoveAll/Unbind in teardown"
                    f" ({len(missing)} unmatched).",
                )
            )
    for class_name, method_name, start, body in class_methods:
        if not DELEGATE_RAW_BIND_RE.search(body):
            continue
        findings.append(
            Finding(
                "warning",
                rel,
                line_number(text, start),
                "DELEGATE_RAW_BIND_LIFETIME",
                f"{class_name}::{method_name} uses AddRaw/BindRaw; verify UObject lifetime is safe.",
            )
        )
    return findings

__all__ = [
    'DELEGATE_PARAM_COUNT_WORDS',
    'DELEGATE_DECLARE_RE',
    '_DELEGATE_MEMBER_DECL_RE',
    'build_delegate_arity_map',
    'build_delegate_arity_map_from_texts',
    'build_declared_delegate_types',
    'build_declared_delegate_types_from_texts',
    'validate_blueprint_assignable_delegate_types',
    'validate_delegate_broadcast_consistency',
    '_extract_delegate_receiver',
    '_parse_delegate_bind',
    '_parse_delegate_unbind',
    'DELEGATE_BIND_RE',
    'DELEGATE_RAW_BIND_RE',
    'DELEGATE_UNBIND_RE',
    'validate_delegate_bind_without_unbind',
]
