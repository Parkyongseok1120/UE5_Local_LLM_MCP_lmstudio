#!/usr/bin/env python
"""RPC and replication contract validators."""

from __future__ import annotations

import re
from pathlib import Path

from cpp_parse_utils import (
    extract_macro_blocks,
    mask_comments_and_strings,
)
from unreal_static_model import (
    CPP_HEADER_SUFFIXES,
    CPP_SOURCE_SUFFIXES,
    Finding,
)
from unreal_static_scan import (
    _find_enclosing_class_name,
    _is_class_definition,
    iter_function_blocks,
    iter_source_files,
    line_number,
    read_text,
)
from workspace_paths import filesystem_path_identity


def collect_rpc_declarations(root: Path) -> list[tuple[str, str, Path, int]]:
    declarations: list[tuple[str, str, Path, int]] = []
    for path in iter_source_files(root):
        if path.suffix.lower() not in CPP_HEADER_SUFFIXES:
            continue
        text = read_text(path)
        current_class = ""
        lines = text.splitlines()
        index = 0
        while index < len(lines):
            class_match = re.search(
                r"\bclass\s+(?:[A-Z0-9_]+_API\s+)?([A-Za-z_][A-Za-z0-9_]*)\b",
                lines[index],
            )
            if class_match:
                current_class = class_match.group(1)
            if "UFUNCTION" in lines[index] and re.search(r"\b(Server|Client|NetMulticast)\b", lines[index]):
                declaration_parts: list[str] = []
                cursor = index + 1
                while cursor < len(lines) and len(declaration_parts) < 8:
                    candidate = lines[cursor].strip()
                    cursor += 1
                    if not candidate or candidate.startswith("UFUNCTION") or candidate.startswith("UPROPERTY"):
                        continue
                    declaration_parts.append(candidate)
                    if ";" in candidate:
                        break
                declaration = " ".join(declaration_parts)
                name_match = re.search(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", declaration)
                if current_class and name_match:
                    declarations.append((current_class, name_match.group(1), path, index + 1))
                index = cursor
                continue
            index += 1
    return declarations

def validate_rpc_implementations(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    cpp_paths = [path for path in iter_source_files(root) if path.suffix.lower() in CPP_SOURCE_SUFFIXES]
    if not cpp_paths:
        return findings
    cpp_text = "\n".join(read_text(path) for path in cpp_paths)
    for class_name, function_name, path, line in collect_rpc_declarations(root):
        implementation = rf"\b{re.escape(class_name)}::{re.escape(function_name)}_Implementation\s*\("
        if re.search(implementation, cpp_text):
            continue
        findings.append(
            Finding(
                "error",
                str(path.relative_to(root)),
                line,
                "RPC_IMPLEMENTATION_MISSING",
                f"{class_name}::{function_name} is an RPC and needs a matching {function_name}_Implementation definition in .cpp.",
            )
        )
    return findings

def validate_replication_setup(
    root: Path,
    *,
    scope_cpp_paths: list[Path] | None = None,
    scope_texts: dict[Path, str] | None = None,
) -> list[Finding]:
    """Warn (never block) when DOREPLIFETIME is used without a matching, complete
    GetLifetimeReplicatedProps override. Missing the override means the property never
    replicates; missing the Super:: call silently drops base-class replicated props."""
    findings: list[Finding] = []
    cpp_paths = scope_cpp_paths
    if cpp_paths is None:
        cpp_paths = [path for path in iter_source_files(root) if path.suffix.lower() in CPP_SOURCE_SUFFIXES]
    for path in cpp_paths:
        text = scope_texts.get(path, read_text(path)) if scope_texts else read_text(path)
        if "DOREPLIFETIME" not in text:
            continue
        lifecycle_blocks = [
            block for block in iter_function_blocks(text) if re.search(r"::GetLifetimeReplicatedProps\s*\(", block[0])
        ]
        if not lifecycle_blocks:
            findings.append(
                Finding(
                    "warning",
                    str(path.relative_to(root)),
                    1,
                    "REPLICATION_SETUP_INCOMPLETE",
                    "DOREPLIFETIME is used but no GetLifetimeReplicatedProps override was found in this file.",
                )
            )
            continue
        for header, start, body in lifecycle_blocks:
            if re.search(r"\bSuper::GetLifetimeReplicatedProps\s*\(", body):
                continue
            findings.append(
                Finding(
                    "warning",
                    str(path.relative_to(root)),
                    line_number(text, start),
                    "REPLICATION_SETUP_INCOMPLETE",
                    "GetLifetimeReplicatedProps does not call Super::GetLifetimeReplicatedProps(...); base-class replicated properties will not be registered.",
                )
            )
    return findings

def extract_replicated_member_names(text: str) -> list[tuple[int, str]]:
    members: list[tuple[int, str]] = []
    for start, end, block in extract_macro_blocks(text, "UPROPERTY"):
        if not re.search(r"\bReplicated(?:Using\b|\b)", block):
            continue
        tail = text[end : end + 400]
        match = re.search(
            r"(?:^|\n)\s*(?:virtual\s+)?(?:static\s+)?(?:inline\s+)?"
            r"(?:[\w:<>,~*&]+\s+)+\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?:=|;|\()",
            tail,
        )
        if match:
            members.append((end + match.start("name"), match.group("name")))
    return members

DOREPLIFETIME_PROP_RES: dict[str, re.Pattern[str]] = {}

def doreplifetime_prop_pattern(prop: str, class_name: str | None = None) -> re.Pattern[str]:
    key = f"{(class_name or '*').lower()}:{prop.lower()}"
    cached = DOREPLIFETIME_PROP_RES.get(key)
    if cached is not None:
        return cached
    if class_name:
        compiled = re.compile(
            rf"\bDOREPLIFETIME(?:_CONDITION(?:_NOTIFY)?)?\s*\(\s*{re.escape(class_name)}\s*,\s*{re.escape(prop)}\b",
            re.MULTILINE | re.IGNORECASE,
        )
    else:
        compiled = re.compile(
            rf"\bDOREPLIFETIME(?:_CONDITION(?:_NOTIFY)?)?\s*\([^)]*\b{re.escape(prop)}\b",
            re.MULTILINE,
        )
    DOREPLIFETIME_PROP_RES[key] = compiled
    return compiled

REPLICATED_UPROPERTY_RE = re.compile(
    r"UPROPERTY\s*\([^)]*\bReplicated(?:Using\b[^)]*)?\)[^;]*?"
    r"\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*;",
    re.MULTILINE | re.DOTALL,
)

def validate_replicated_uproperty_without_doreplifetime(
    root: Path,
    *,
    scope_header_paths: list[Path] | None = None,
    scope_texts: dict[Path, str] | None = None,
    host_platform: str | None = None,
) -> list[Finding]:
    findings: list[Finding] = []
    header_paths = scope_header_paths
    if header_paths is None:
        header_paths = [path for path in iter_source_files(root) if path.suffix.lower() in CPP_HEADER_SUFFIXES]
    cpp_text_by_stem: dict[str, str] = {}
    if scope_texts is not None:
        for path, text in scope_texts.items():
            if path.suffix.lower() in CPP_SOURCE_SUFFIXES:
                cpp_text_by_stem[
                    filesystem_path_identity(
                        path.stem,
                        host_platform,
                        strip_project_uri=False,
                    )
                ] = text
    else:
        for path in iter_source_files(root):
            if path.suffix.lower() in CPP_SOURCE_SUFFIXES:
                cpp_text_by_stem[
                    filesystem_path_identity(
                        path.stem,
                        host_platform,
                        strip_project_uri=False,
                    )
                ] = read_text(path)
    for path in header_paths:
        header_text = scope_texts.get(path, read_text(path)) if scope_texts else read_text(path)
        stem_key = filesystem_path_identity(
            path.stem,
            host_platform,
            strip_project_uri=False,
        )
        cpp_text = mask_comments_and_strings(cpp_text_by_stem.get(stem_key, ""))
        class_names = [
            match.group(1)
            for match in re.finditer(
                r"\bclass\s+(?:[A-Z0-9_]+_API\s+)?([A-Za-z_][A-Za-z0-9_]*)\b",
                header_text,
            )
            if _is_class_definition(header_text, match.start())
        ]
        for offset, prop in extract_replicated_member_names(header_text):
            class_name = _find_enclosing_class_name(header_text, offset) or (class_names[0] if class_names else None)
            if class_name and doreplifetime_prop_pattern(prop, class_name).search(cpp_text):
                continue
            if not class_name and doreplifetime_prop_pattern(prop).search(cpp_text):
                continue
            findings.append(
                Finding(
                    "warning",
                    str(path.relative_to(root)),
                    line_number(header_text, offset),
                    "REPLICATED_UPROPERTY_WITHOUT_DOREPLIFETIME",
                    f'Replicated property "{prop}" has no visible DOREPLIFETIME registration in the matching .cpp.',
                )
            )
    return findings

__all__ = [
    'collect_rpc_declarations',
    'validate_rpc_implementations',
    'validate_replication_setup',
    'extract_replicated_member_names',
    'DOREPLIFETIME_PROP_RES',
    'doreplifetime_prop_pattern',
    'REPLICATED_UPROPERTY_RE',
    'validate_replicated_uproperty_without_doreplifetime',
]
