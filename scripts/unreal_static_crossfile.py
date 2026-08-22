#!/usr/bin/env python
"""Cross-file declaration, implementation, and signature drift validators."""

from __future__ import annotations

import re
from pathlib import Path

from cpp_parse_utils import (
    mask_comments_and_strings,
)
from ue_cpp_signatures import (
    clean_method_ret,
    collect_callback_drifts,
    collect_interface_specs,
    find_method_decl_in_header,
    normalize_signature_params,
)
from unreal_static_model import (
    CPP_HEADER_SUFFIXES,
    CPP_SOURCE_SUFFIXES,
    SOURCE_ONLY_SUFFIXES,
    Finding,
)
from unreal_static_reflection import (
    UE_DECLARATION_MACROS,
)
from unreal_static_scan import (
    _class_definition_text,
    class_headers,
    iter_source_files,
    line_number,
    read_text,
)


def _normalize_signature_params(params: str) -> str:
    return normalize_signature_params(params)

def _header_has_matching_signature(header: str, func_name: str, params: str) -> bool:
    wanted = normalize_signature_params(params)
    declaration_re = re.compile(rf"\b{re.escape(func_name)}\s*\((?P<params>[^)]*)\)")
    for declaration in declaration_re.finditer(header):
        if normalize_signature_params(declaration.group("params")) == wanted:
            return True
    return False

def _normalize_return_type(ret: str) -> str:
    value = re.sub(r"\s+", " ", str(ret or "").strip())
    for prefix in ("virtual ", "static ", "inline ", "FORCEINLINE ", "constexpr "):
        if value.startswith(prefix):
            value = value[len(prefix) :].lstrip()
    return value.replace(" const", "").strip()

def validate_cpp_declarations(path: Path, text: str, root: Path, headers: dict[str, str]) -> list[Finding]:
    findings: list[Finding] = []
    rel = str(path.relative_to(root))
    definition_re = re.compile(
        r"^(?P<ret>[\w:<>,~*&\s]+?)\s+(?P<class>[A-Za-z_][A-Za-z0-9_]*)::"
        r"(?P<func>~?[A-Za-z_][A-Za-z0-9_]*)\s*\((?P<params>[^)]*)\)",
        flags=re.MULTILINE,
    )
    for match in definition_re.finditer(text):
        class_name = match.group("class")
        func_name = match.group("func")
        params = match.group("params")
        cpp_ret = _normalize_return_type(match.group("ret"))
        header = headers.get(class_name)
        if not header:
            continue
        bare_func = func_name.lstrip("~")
        if bare_func == class_name:
            continue
        if func_name.endswith(("_Implementation", "_Validate")):
            base_name = re.sub(r"_(?:Implementation|Validate)$", "", func_name)
            if header and "BlueprintImplementableEvent" in header and func_name.endswith("_Implementation"):
                if re.search(r"UFUNCTION\s*\([^)]*BlueprintImplementableEvent", header):
                    findings.append(
                        Finding(
                            "error",
                            rel,
                            line_number(text, match.start()),
                            "BLUEPRINT_IMPLEMENTABLE_EVENT_INVALID_IMPL",
                            (
                                f"{class_name}::{func_name} must not be defined in .cpp for "
                                "BlueprintImplementableEvent; remove the invalid _Implementation body."
                            ),
                        )
                    )
                    continue
            if re.search(rf"\b{re.escape(base_name)}\s*\(", header):
                continue
        if _header_has_matching_signature(header, func_name, params):
            decl_match = find_method_decl_in_header(header, func_name)
            if decl_match:
                header_ret = _normalize_return_type(decl_match.group("ret"))
                if header_ret and cpp_ret and header_ret != cpp_ret:
                    findings.append(
                        Finding(
                            "warning",
                            rel,
                            line_number(text, match.start()),
                            "CPP_RETURN_TYPE_MISMATCH",
                            (
                                f"{class_name}::{func_name} return type in .cpp ({cpp_ret}) does not match "
                                f"the header declaration ({header_ret})."
                            ),
                        )
                    )
            continue
        if re.search(rf"\b{re.escape(func_name)}\s*\(", header):
            findings.append(
                Finding(
                    "warning",
                    rel,
                    line_number(text, match.start()),
                    "CPP_FUNCTION_SIGNATURE_MISMATCH",
                    f"{class_name}::{func_name} is implemented in .cpp with parameters that do not match the matching header declaration.",
                )
            )
            continue
        findings.append(
            Finding(
                "warning",
                rel,
                line_number(text, match.start()),
                "CPP_FUNCTION_NOT_DECLARED_IN_HEADER",
                f"{class_name}::{func_name} is implemented in .cpp but was not found in the matching header.",
            )
        )
    return findings

def collect_blueprint_native_event_declarations(root: Path) -> list[tuple[str, str, Path, int]]:
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
            if "BlueprintNativeEvent" in lines[index]:
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
                if current_class and not current_class.startswith("I") and name_match:
                    declarations.append((current_class, name_match.group(1), path, index + 1))
                index = cursor
                continue
            index += 1
    return declarations

def validate_blueprint_native_event_implementations(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    cpp_paths = [path for path in iter_source_files(root) if path.suffix.lower() in CPP_SOURCE_SUFFIXES]
    if not cpp_paths:
        return findings
    cpp_text = "\n".join(read_text(path) for path in cpp_paths)
    for class_name, event_name, path, line in collect_blueprint_native_event_declarations(root):
        rel = str(path.relative_to(root))
        header_text = read_text(path)
        manual_decl = re.search(
            rf"\b(?:virtual\s+)?void\s+{re.escape(event_name)}_Implementation\s*\([^;]*\)\s*(?:override\s*)?;",
            header_text,
        )
        if manual_decl:
            findings.append(
                Finding(
                    "error",
                    rel,
                    line_number(header_text, manual_decl.start()),
                    "BLUEPRINT_NATIVE_EVENT_MANUAL_IMPL_DECL",
                    (
                        f"Do not declare {event_name}_Implementation in the header for BlueprintNativeEvent; "
                        "UHT generates it. Implement only in .cpp."
                    ),
                )
            )
        implementation = rf"\b{re.escape(class_name)}::{re.escape(event_name)}_Implementation\s*\("
        if not re.search(implementation, cpp_text):
            findings.append(
                Finding(
                    "error",
                    rel,
                    line,
                    "BLUEPRINT_NATIVE_EVENT_IMPL_MISSING",
                    (
                        f"{class_name}::{event_name} is BlueprintNativeEvent and needs "
                        f"{event_name}_Implementation in the matching .cpp file."
                    ),
                )
            )
    return findings

def validate_cpp_definitions_missing(
    root: Path,
    *,
    scope_headers: dict[str, str] | None = None,
    scope_header_paths: dict[str, Path] | None = None,
    scope_cpp_paths: list[Path] | None = None,
    scope_texts: dict[Path, str] | None = None,
) -> list[Finding]:
    findings: list[Finding] = []
    headers = scope_headers if scope_headers is not None else class_headers(root)
    if scope_cpp_paths is not None:
        cpp_paths = scope_cpp_paths
        all_cpp = "\n".join(scope_texts.get(path, read_text(path)) for path in cpp_paths)
    else:
        cpp_paths = [path for path in iter_source_files(root) if path.suffix.lower() in CPP_SOURCE_SUFFIXES]
        if not cpp_paths:
            return findings
        all_cpp = "\n".join(read_text(path) for path in cpp_paths)
    method_decl_re = re.compile(
        r"^[ \t]*(?:virtual[ \t]+)?[\w:<>,~*& \t]+[ \t]+(?P<func>~?[A-Za-z_][A-Za-z0-9_]*)"
        r"\s*\([^;{}]*\)\s*(?:const\s*)?(?:override\s*)?;",
        re.MULTILINE,
    )
    for class_name, header_text in headers.items():
        if not class_name.startswith("U"):
            continue
        header_path = None
        if scope_header_paths and class_name in scope_header_paths:
            header_path = scope_header_paths[class_name]
        elif scope_header_paths is None:
            for path in iter_source_files(root):
                if path.suffix.lower() in CPP_HEADER_SUFFIXES and class_name in read_text(path):
                    header_path = path
                    break
        class_definition = _class_definition_text(header_text, class_name)
        if class_definition is None:
            continue
        class_text, class_offset = class_definition
        # Match declarations against a comment/string-masked copy.  The previous
        # regex ran over raw Doxygen text, so a sentence such as
        # "Check if a player can use an item (has it ...);" could be parsed as a
        # method named ``item`` and block an otherwise valid full-project scan.
        # mask_comments_and_strings preserves offsets, so finding line numbers
        # against the original header remains correct.
        masked_class_text = mask_comments_and_strings(class_text)
        for match in method_decl_re.finditer(masked_class_text):
            func_name = match.group("func")
            if func_name.startswith("~") or func_name in UE_DECLARATION_MACROS:
                continue
            window = masked_class_text[max(0, match.start() - 240) : match.start()]
            if "BlueprintImplementableEvent" in window:
                continue
            impl_name = func_name
            if "BlueprintNativeEvent" in window:
                impl_name = f"{func_name}_Implementation"
            impl = rf"\b{re.escape(class_name)}::{re.escape(impl_name)}\s*\("
            if re.search(impl, all_cpp):
                continue
            if header_path is None:
                continue
            findings.append(
                Finding(
                    "error",
                    str(header_path.relative_to(root)),
                    line_number(header_text, class_offset + match.start()),
                    "CPP_DEFINITION_MISSING",
                    f"{class_name}::{impl_name} is declared in the header but has no matching .cpp definition.",
                )
            )
    return findings

def validate_interface_implementer_drift(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    interface_specs = {
        name: [(m.func, m.params_normalized, m.ret) for m in methods]
        for name, methods in collect_interface_specs(root).items()
    }
    if not interface_specs:
        return findings

    for path in iter_source_files(root):
        if path.suffix.lower() not in CPP_HEADER_SUFFIXES:
            continue
        text = read_text(path)
        rel = str(path.relative_to(root)).replace("\\", "/")
        for interface_name, methods in interface_specs.items():
            if interface_name not in text:
                continue
            if re.search(rf"\bclass\s+{re.escape(interface_name)}\b", text):
                continue
            if f": public {interface_name}" not in text and f", public {interface_name}" not in text:
                continue
            for func_name, iface_params, iface_ret in methods:
                impl_match = find_method_decl_in_header(text, func_name)
                if not impl_match:
                    findings.append(
                        Finding(
                            "warning",
                            rel,
                            line_number(text, 0),
                            "INTERFACE_IMPLEMENTER_SIGNATURE_MISMATCH",
                            f"{interface_name} requires {func_name}({iface_params or 'void'}) but implementer declaration was not found.",
                        )
                    )
                    continue
                impl_params = normalize_signature_params(impl_match.group("params"))
                impl_ret, _ = clean_method_ret(impl_match.group("ret"))
                if impl_params != iface_params or impl_ret.replace("const", "").strip() != iface_ret.replace("const", "").strip():
                    findings.append(
                        Finding(
                            "warning",
                            rel,
                            line_number(text, impl_match.start()),
                            "INTERFACE_IMPLEMENTER_SIGNATURE_MISMATCH",
                            f"{func_name} implementer signature does not match {interface_name} ({iface_ret} {func_name} vs {impl_ret} {func_name}).",
                        )
                    )
    return findings

def validate_callback_function_pointer_drift(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for drift in collect_callback_drifts(root):
        rel = str(drift.cpp_path.relative_to(root)).replace("\\", "/")
        findings.append(
            Finding(
                "warning",
                rel,
                1,
                "CALLBACK_FUNCTION_POINTER_MISMATCH",
                (
                    f"{drift.class_name}::{drift.func_name} params ({drift.method_params or 'void'}) do not match "
                    f"callback typedef {drift.typedef_alias} ({drift.typedef_params or 'void'})."
                ),
            )
        )
    return findings

STALE_MULTIFILE_METHOD_NAMES = frozenset({"DoAll", "HandleAll", "RunAll"})

def validate_multifile_callsite_drift(root: Path) -> list[Finding]:
    """Detect consolidated method names still used after header method split."""
    findings: list[Finding] = []
    headers = class_headers(root)
    for class_name, header_text in headers.items():
        if not class_name.startswith("U"):
            continue
        declared = set(re.findall(r"\bvoid\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*\)\s*;", header_text))
        if len(declared) < 2:
            continue
        stale_in_project = STALE_MULTIFILE_METHOD_NAMES - declared
        if not stale_in_project:
            continue
        for path in iter_source_files(root):
            if path.suffix.lower() not in SOURCE_ONLY_SUFFIXES:
                continue
            text = read_text(path)
            rel = str(path.relative_to(root)).replace("\\", "/")
            for stale in stale_in_project:
                def_match = re.search(
                    rf"\bvoid\s+{re.escape(class_name)}::{re.escape(stale)}\s*\(",
                    text,
                )
                if def_match:
                    findings.append(
                        Finding(
                            "warning",
                            rel,
                            line_number(text, def_match.start()),
                            "MULTIFILE_CALLSITE_DRIFT",
                            (
                                f"{class_name}::{stale} is defined in cpp but header declares "
                                f"{', '.join(sorted(declared))} instead."
                            ),
                        )
                    )
                for call_match in re.finditer(rf"->{re.escape(stale)}\s*\(", text):
                    findings.append(
                        Finding(
                            "warning",
                            rel,
                            line_number(text, call_match.start()),
                            "MULTIFILE_CALLSITE_DRIFT",
                            (
                                f"Callsite uses {stale}() but {class_name} header declares "
                                f"{', '.join(sorted(declared))}."
                            ),
                        )
                    )
    return findings

__all__ = [
    '_normalize_signature_params',
    '_header_has_matching_signature',
    '_normalize_return_type',
    'validate_cpp_declarations',
    'collect_blueprint_native_event_declarations',
    'validate_blueprint_native_event_implementations',
    'validate_cpp_definitions_missing',
    'validate_interface_implementer_drift',
    'validate_callback_function_pointer_drift',
    'STALE_MULTIFILE_METHOD_NAMES',
    'validate_multifile_callsite_drift',
]
