#!/usr/bin/env python3
"""Shared C++ signature parsing for static validate and multifile autofix."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

INTERFACE_VIRTUAL_METHOD_RE = re.compile(
    r"virtual\s+(?P<ret>[\w:<>,\s*&]+)\s+(?P<func>[A-Za-z_][A-Za-z0-9_]*)\s*\((?P<params>[^)]*)\)\s*(?:const\s*)?(?:override\s*)?=\s*0\s*;",
)

_TYPE_TOKEN = r"[\w:,<>&]+(?:[ \t]+[\w:,<>&]+)*"

IMPLEMENTER_OVERRIDE_DECL_RE = re.compile(
    rf"^[\t ]*(?P<ret>{_TYPE_TOKEN})\s+(?P<func>[A-Za-z_][A-Za-z0-9_]*)\s*\((?P<params>[^)]*)\)\s*(?:const\s+)?override\s*;",
    re.MULTILINE,
)

STATIC_METHOD_DECL_RE = re.compile(
    rf"^[\t ]*(?:static\s+)?(?P<ret>{_TYPE_TOKEN})\s+(?P<func>[A-Za-z_][A-Za-z0-9_]*)\s*\((?P<params>[^)]*)\)\s*;",
    re.MULTILINE,
)

TYPEDEF_FUNCTION_POINTER_RE = re.compile(
    r"using\s+(?P<alias>\w+)\s*=\s*(?P<ret>[\w:<>,\s*&]+)\s*\(\s*\*\s*\)\s*\((?P<params>[^)]*)\)\s*;",
)

FUNCTION_POINTER_TARGET_RE = re.compile(
    r"&(?P<class>[A-Za-z_][A-Za-z0-9_]*)::(?P<func>[A-Za-z_][A-Za-z0-9_]*)",
)

CPP_METHOD_DEFINITION_RE = re.compile(
    r"\b(?P<class>[A-Za-z_][A-Za-z0-9_]*)::(?P<func>[A-Za-z_][A-Za-z0-9_]*)\s*\((?P<params>[^)]*)\)",
)


@dataclass(frozen=True)
class InterfaceMethod:
    func: str
    params_normalized: str
    params_raw: str
    ret: str


@dataclass(frozen=True)
class CallbackDrift:
    class_name: str
    func_name: str
    method_params: str
    typedef_alias: str
    typedef_params: str
    cpp_path: Path
    expandable: bool


@dataclass
class SourceTreeIndex:
    headers: list[Path] = field(default_factory=list)
    cpps: list[Path] = field(default_factory=list)
    class_to_text: dict[str, str] = field(default_factory=dict)
    class_to_path: dict[str, Path] = field(default_factory=dict)


def normalize_signature_params(params: str) -> str:
    value = re.sub(r"\s+", " ", str(params or "").strip())
    if not value or value == "void":
        return ""
    value = re.sub(r"=\s*[^,]+", "", value)
    value = value.replace(" const", "").strip()
    types: list[str] = []
    for part in value.split(","):
        part = part.strip()
        if part:
            types.append(part.split()[0])
    return ", ".join(types)


def find_method_decl_in_header(text: str, func_name: str) -> re.Match[str] | None:
    for match in IMPLEMENTER_OVERRIDE_DECL_RE.finditer(text):
        if match.group("func") == func_name:
            return match
    for candidate in STATIC_METHOD_DECL_RE.finditer(text):
        if candidate.group("func") == func_name:
            return candidate
    return re.search(
        rf"^[\t ]*(?P<ret>(?:[\w:,<>&]+(?:[ \t]+[\w:,<>&]+)*))\s+{re.escape(func_name)}\s*\((?P<params>[^)]*)\)\s*(?:const\s*)?(?:override\s*)?;",
        text,
        re.MULTILINE,
    )


def find_implementer_method_decl(text: str, func_name: str) -> re.Match[str] | None:
    for match in IMPLEMENTER_OVERRIDE_DECL_RE.finditer(text):
        if match.group("func") == func_name:
            return match
    return re.search(
        rf"^[\t ]*(?P<ret>(?:[\w:,<>&]+(?:[ \t]+[\w:,<>&]+)*))\s+{re.escape(func_name)}\s*\((?P<params>[^)]*)\)\s*(?:const\s*)?(?:override\s*)?;",
        text,
        re.MULTILINE,
    )


def parse_interface_virtual_methods(text: str) -> list[tuple[str, str, str]]:
    methods: list[tuple[str, str, str]] = []
    for match in INTERFACE_VIRTUAL_METHOD_RE.finditer(text):
        methods.append(
            (
                match.group("func"),
                normalize_signature_params(match.group("params")),
                match.group("ret").strip(),
            )
        )
    return methods


def header_method_params(header: str, func_name: str) -> str | None:
    match = re.search(rf"\b{re.escape(func_name)}\s*\((?P<params>[^)]*)\)", header)
    if not match:
        return None
    return match.group("params").strip()


def build_source_tree_index(root: Path) -> SourceTreeIndex:
    from unreal_static_validate import iter_source_files, read_text

    index = SourceTreeIndex()
    class_pattern = re.compile(r"\bclass\s+(?:[A-Z0-9_]+_API\s+)?([A-Za-z_][A-Za-z0-9_]*)\b")
    for path in iter_source_files(root):
        suffix = path.suffix.lower()
        if suffix in {".h", ".hpp"}:
            index.headers.append(path)
            text = read_text(path)
            for match in class_pattern.finditer(text):
                name = match.group(1)
                if name not in index.class_to_text:
                    index.class_to_text[name] = text
                    index.class_to_path[name] = path
        elif suffix in {".cpp", ".c", ".cc"}:
            index.cpps.append(path)
    return index


def collect_interface_specs(
    root: Path,
    *,
    index: SourceTreeIndex | None = None,
) -> dict[str, list[InterfaceMethod]]:
    from unreal_static_validate import iter_source_files, read_text

    specs: dict[str, list[InterfaceMethod]] = {}
    headers = index.headers if index else [path for path in iter_source_files(root) if path.suffix.lower() in {".h", ".hpp"}]
    for path in headers:
        text = read_text(path)
        if not re.search(r"\bclass\s+I[A-Za-z_][A-Za-z0-9_]*\b", text):
            continue
        interface_name = ""
        for match in re.finditer(r"\bclass\s+(I[A-Za-z_][A-Za-z0-9_]*)\b", text):
            interface_name = match.group(1)
        if not interface_name:
            continue
        methods = parse_interface_virtual_methods(text)
        if methods:
            specs[interface_name] = []
            for match in INTERFACE_VIRTUAL_METHOD_RE.finditer(text):
                specs[interface_name].append(
                    InterfaceMethod(
                        func=match.group("func"),
                        params_normalized=normalize_signature_params(match.group("params")),
                        params_raw=match.group("params").strip(),
                        ret=match.group("ret").strip(),
                    )
                )
    return specs


def collect_callback_drifts(
    root: Path,
    *,
    index: SourceTreeIndex | None = None,
) -> list[CallbackDrift]:
    from unreal_static_validate import iter_source_files, read_text

    drifts: list[CallbackDrift] = []
    tree = index or build_source_tree_index(root)
    cpps = tree.cpps if tree.cpps else [
        path for path in iter_source_files(root) if path.suffix.lower() in {".cpp", ".c", ".cc"}
    ]
    for cpp_path in cpps:
        text = read_text(cpp_path)
        if "using " not in text or "&" not in text:
            continue
        typedef_params: dict[str, str] = {}
        for match in TYPEDEF_FUNCTION_POINTER_RE.finditer(text):
            typedef_params[match.group("alias")] = match.group("params").strip()
        if not typedef_params:
            continue
        for match in FUNCTION_POINTER_TARGET_RE.finditer(text):
            class_name = match.group("class")
            func_name = match.group("func")
            header_text = tree.class_to_text.get(class_name)
            if not header_text:
                continue
            decl = find_method_decl_in_header(header_text, func_name)
            if not decl:
                continue
            method_params = normalize_signature_params(decl.group("params"))
            for alias, params in typedef_params.items():
                typedef_norm = normalize_signature_params(params)
                if method_params == typedef_norm:
                    continue
                expandable = params.count(",") > decl.group("params").count(",")
                drifts.append(
                    CallbackDrift(
                        class_name=class_name,
                        func_name=func_name,
                        method_params=method_params,
                        typedef_alias=alias,
                        typedef_params=typedef_norm,
                        cpp_path=cpp_path,
                        expandable=expandable,
                    )
                )
                break
    return drifts
