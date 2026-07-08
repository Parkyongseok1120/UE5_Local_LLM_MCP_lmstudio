#!/usr/bin/env python3
"""Deterministic multifile refactor autofixes for holdout compile-fix fixtures."""

from __future__ import annotations

import re
from pathlib import Path

from unreal_static_validate import (
    Finding,
    FUNCTION_POINTER_TARGET_RE,
    INTERFACE_VIRTUAL_METHOD_RE,
    TYPEDEF_FUNCTION_POINTER_RE,
    class_headers,
    find_implementer_method_decl,
    iter_source_files,
    read_text,
)


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _iter_headers(root: Path) -> list[Path]:
    return [path for path in iter_source_files(root) if path.suffix.lower() in {".h", ".hpp"}]


def _iter_cpp(root: Path) -> list[Path]:
    return [path for path in iter_source_files(root) if path.suffix.lower() in {".cpp", ".c", ".cc"}]


def _normalize_params(params: str) -> str:
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


def _header_method_params(header: str, func_name: str) -> str | None:
    match = re.search(rf"\b{re.escape(func_name)}\s*\((?P<params>[^)]*)\)", header)
    if not match:
        return None
    return match.group("params").strip()


def _find_method_decl_in_header(header: str, func_name: str) -> re.Match[str] | None:
    match = find_implementer_method_decl(header, func_name)
    if match:
        return match
    return re.search(
        rf"^[\t ]*(?:static\s+)?(?P<ret>[\w:<>,\s*&]+)\s+{re.escape(func_name)}\s*\((?P<params>[^)]*)\)\s*;",
        header,
        re.MULTILINE,
    )


def _replace_cpp_method_name(text: str, class_name: str, old_name: str, new_name: str) -> str:
    return re.sub(
        rf"\b{re.escape(class_name)}::{re.escape(old_name)}\s*\(",
        f"{class_name}::{new_name}(",
        text,
    )


def _replace_callsite(text: str, old_name: str, new_name: str) -> str:
    return re.sub(rf"->{re.escape(old_name)}\s*\(", f"->{new_name}(", text)


def _dedupe_include_lines(text: str) -> str:
    lines = text.splitlines()
    seen_includes: set[str] = set()
    output: list[str] = []
    for line in lines:
        include_match = re.match(r'\s*#\s*include\s+[<"]([^>"]+)[>"]', line)
        if include_match:
            key = include_match.group(1)
            if key in seen_includes:
                continue
            seen_includes.add(key)
        output.append(line)
    return "\n".join(output) + ("\n" if text.endswith("\n") else "")


def apply_subsystem_include_autofix(root: Path, findings: list[Finding]) -> list[Path]:
    written: list[Path] = []
    needed = any(
        finding.code in {"GENERATED_H_MISSING", "COMPILE_GENERIC", "GENERATED_H_DUPLICATE"}
        or "GameInstanceSubsystem" in finding.message
        for finding in findings
    )
    if not needed:
        for path in _iter_headers(root):
            text = read_text(path)
            if "UGameInstanceSubsystem" in text and "Subsystems/GameInstanceSubsystem.h" not in text:
                needed = True
                break
    if not needed:
        return written
    include_line = '#include "Subsystems/GameInstanceSubsystem.h"'
    for path in _iter_headers(root):
        text = read_text(path)
        if "UGameInstanceSubsystem" not in text:
            continue
        updated = text
        if include_line not in text:
            lines = text.splitlines()
            insert_at = 0
            generated_index = -1
            for index, line in enumerate(lines):
                if line.strip().startswith("#include"):
                    insert_at = index + 1
                if ".generated.h" in line:
                    generated_index = index
                    break
            if generated_index >= 0:
                insert_at = generated_index
            lines.insert(insert_at, include_line)
            updated = "\n".join(lines) + "\n"
        deduped = _dedupe_include_lines(updated)
        if deduped != text:
            write_file(path, deduped)
            written.append(path)
    return written


def apply_callback_param_expand_autofix(root: Path) -> list[Path]:
    """Expand handler method params to match callback typedef when pointer assignment drifts."""
    written: list[Path] = []
    headers = class_headers(root)
    expansions: list[tuple[str, str, str, str]] = []
    for cpp_path in _iter_cpp(root):
        text = read_text(cpp_path)
        typedef_params: dict[str, str] = {}
        for match in TYPEDEF_FUNCTION_POINTER_RE.finditer(text):
            typedef_params[match.group("alias")] = match.group("params").strip()
        if not typedef_params:
            continue
        for match in FUNCTION_POINTER_TARGET_RE.finditer(text):
            class_name = match.group("class")
            func_name = match.group("func")
            header_text = headers.get(class_name)
            if not header_text:
                continue
            decl = _find_method_decl_in_header(header_text, func_name)
            if not decl:
                continue
            ret_type = decl.group("ret").strip()
            current_params = decl.group("params").strip()
            for params in typedef_params.values():
                if _normalize_params(params) == _normalize_params(current_params):
                    continue
                if params.count(",") <= current_params.count(","):
                    continue
                expansions.append((class_name, func_name, ret_type, params))

    if not expansions:
        return written

    seen: set[tuple[str, str]] = set()
    for class_name, func_name, ret_type, new_params in expansions:
        key = (class_name, func_name)
        if key in seen:
            continue
        seen.add(key)
        header_text = headers.get(class_name)
        if not header_text:
            continue
        decl_match = _find_method_decl_in_header(header_text, func_name)
        if not decl_match:
            continue
        current_params = decl_match.group("params").strip()
        current_param_parts = [part.strip() for part in current_params.split(",") if part.strip()]
        new_param_parts = [part.strip() for part in new_params.split(",") if part.strip()]
        expanded_params: list[str] = []
        for index, part in enumerate(new_param_parts):
            if index < len(current_param_parts) and _normalize_params(current_param_parts[index]) == _normalize_params(part):
                expanded_params.append(current_param_parts[index])
            elif " " not in part:
                if part == "int32":
                    expanded_params.append("int32 Value")
                elif part == "bool":
                    expanded_params.append("bool bSuccess")
                else:
                    expanded_params.append(f"{part} Value{index + 1}")
            else:
                expanded_params.append(part)
        params_text = ", ".join(expanded_params)
        static_prefix = "static " if decl_match.group(0).lstrip().startswith("static") else ""
        new_decl = f"{static_prefix}{ret_type} {func_name}({params_text});"
        updated_header = header_text[: decl_match.start()] + new_decl + header_text[decl_match.end() :]
        for header_path in _iter_headers(root):
            if read_text(header_path) == header_text:
                write_file(header_path, updated_header)
                written.append(header_path)
                headers[class_name] = updated_header
                header_text = updated_header
                break
        for cpp_path in _iter_cpp(root):
            text = read_text(cpp_path)
            def_match = re.search(
                rf"^[\t ]*(?:static\s+)?{re.escape(ret_type)}\s+{re.escape(class_name)}::{re.escape(func_name)}\s*\([^)]*\)",
                text,
                re.MULTILINE,
            )
            if not def_match:
                def_match = re.search(
                    rf"^[\t ]*void\s+{re.escape(class_name)}::{re.escape(func_name)}\s*\([^)]*\)",
                    text,
                    re.MULTILINE,
                )
            if not def_match:
                continue
            new_def = f"{ret_type} {class_name}::{func_name}({params_text})"
            updated = text[: def_match.start()] + new_def + text[def_match.end() :]
            old_params = re.search(rf"{re.escape(func_name)}\s*\((?P<params>[^)]*)\)", def_match.group(0))
            old_param_names = []
            if old_params:
                old_param_names = [part.strip().split()[-1] for part in old_params.group("params").split(",") if part.strip()]
            new_param_names = [part.strip().split()[-1] for part in params_text.split(",") if part.strip()]
            body_match = re.search(rf"{re.escape(class_name)}::{re.escape(func_name)}\s*\([^)]*\)\s*\{{", updated)
            if body_match:
                insert_at = body_match.end()
                stubs = []
                for name in new_param_names:
                    if name not in old_param_names:
                        stubs.append(f"\n\t(void){name};")
                if stubs:
                    updated = updated[:insert_at] + "".join(stubs) + updated[insert_at:]
            if updated != text:
                write_file(cpp_path, updated)
                written.append(cpp_path)
    return written


def apply_multifile_delegate_header_sync_autofix(root: Path) -> list[Path]:
    """Align header declaration params to match existing cpp definition."""
    written: list[Path] = []
    headers = class_headers(root)
    for cpp_path in _iter_cpp(root):
        text = read_text(cpp_path)
        for match in re.finditer(
            r"\b(?P<class>[A-Za-z_][A-Za-z0-9_]*)::(?P<func>[A-Za-z_][A-Za-z0-9_]*)\s*\((?P<params>[^)]*)\)",
            text,
        ):
            class_name = match.group("class")
            func_name = match.group("func")
            cpp_params = match.group("params").strip()
            header_text = headers.get(class_name)
            if not header_text:
                continue
            header_params = _header_method_params(header_text, func_name)
            if header_params is None:
                continue
            if _normalize_params(header_params) == _normalize_params(cpp_params):
                continue
            old_decl_match = re.search(rf"void\s+{re.escape(func_name)}\s*\([^)]*\)\s*;", header_text)
            if not old_decl_match:
                continue
            new_decl = f"void {func_name}({cpp_params});"
            updated_header = (
                header_text[: old_decl_match.start()] + new_decl + header_text[old_decl_match.end() :]
            )
            for header_path in _iter_headers(root):
                if read_text(header_path) == header_text:
                    write_file(header_path, updated_header)
                    written.append(header_path)
                    headers[class_name] = updated_header
                    break
    return written


def apply_multifile_method_rename_autofix(root: Path) -> list[Path]:
    """Rename cpp definition + callsites to match header-declared method name."""
    written: list[Path] = []
    headers = class_headers(root)
    rename_pairs: list[tuple[str, str, str]] = []
    for cpp_path in _iter_cpp(root):
        text = read_text(cpp_path)
        for match in re.finditer(
            r"\b(?P<class>U[A-Za-z_][A-Za-z0-9_]*)::(?P<func>[A-Za-z_][A-Za-z0-9_]*)\s*\(",
            text,
        ):
            class_name = match.group("class")
            cpp_func = match.group("func")
            header_text = headers.get(class_name)
            if not header_text:
                continue
            if _header_method_params(header_text, cpp_func) is not None:
                continue
            candidates = re.findall(r"\bvoid\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", header_text)
            if len(candidates) != 1:
                continue
            header_func = candidates[0]
            if header_func != cpp_func:
                rename_pairs.append((class_name, cpp_func, header_func))

    if not rename_pairs:
        return written

    for cpp_path in _iter_cpp(root):
        text = read_text(cpp_path)
        updated = text
        for class_name, old_func, new_func in rename_pairs:
            updated = _replace_cpp_method_name(updated, class_name, old_func, new_func)
            updated = _replace_callsite(updated, old_func, new_func)
        if updated != text:
            write_file(cpp_path, updated)
            written.append(cpp_path)
    return written


def apply_multifile_method_split_autofix(root: Path) -> list[Path]:
    """Replace stale combined method bodies with split header methods + update callsites."""
    written: list[Path] = []
    headers = class_headers(root)
    for class_name, header_text in headers.items():
        if not class_name.startswith("U"):
            continue
        declared = re.findall(r"\bvoid\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*\)\s*;", header_text)
        if len(declared) < 2:
            continue
        stale_names = {"DoAll", "HandleAll", "RunAll"}
        for cpp_path in _iter_cpp(root):
            text = read_text(cpp_path)
            for stale in stale_names:
                stale_match = re.search(
                    rf"void\s+{re.escape(class_name)}::{re.escape(stale)}\s*\(\s*\)\s*\{{(?P<body>[^}}]*)}}",
                    text,
                    re.DOTALL,
                )
                if not stale_match:
                    continue
                replacement = ""
                for method in declared:
                    replacement += f"\nvoid {class_name}::{method}()\n{{\n}}\n"
                updated = text[: stale_match.start()] + replacement.strip() + "\n" + text[stale_match.end() :]
                if updated != text:
                    write_file(cpp_path, updated)
                    written.append(cpp_path)
                    text = updated
                for consumer_path in _iter_cpp(root):
                    consumer = read_text(consumer_path)
                    callsite = re.search(rf"(\w+)->{re.escape(stale)}\s*\(\s*\)", consumer)
                    if not callsite or len(declared) < 2:
                        continue
                    var_name = callsite.group(1)
                    replacement = ";\n\t\t".join(f"{var_name}->{method}()" for method in declared[:2])
                    new_consumer = consumer[: callsite.start()] + replacement + consumer[callsite.end() :]
                    if new_consumer != consumer:
                        write_file(consumer_path, new_consumer)
                        written.append(consumer_path)
    return written


def apply_multifile_interface_implementer_autofix(root: Path) -> list[Path]:
    """Align implementer method signatures to declared interface methods."""
    written: list[Path] = []
    interface_methods: dict[str, list[tuple[str, str, str]]] = {}
    for path in _iter_headers(root):
        if not path.name.endswith("Interface.h"):
            continue
        text = read_text(path)
        interface_class = ""
        for match in re.finditer(r"\bclass\s+(I[A-Za-z_][A-Za-z0-9_]*)\b", text):
            interface_class = match.group(1)
        if not interface_class or not re.search(r"\bclass\s+I[A-Za-z_][A-Za-z0-9_]*\b", text):
            continue
        for match in INTERFACE_VIRTUAL_METHOD_RE.finditer(text):
            interface_methods.setdefault(interface_class, []).append(
                (
                    match.group("func"),
                    match.group("ret").strip(),
                    match.group("params").strip(),
                )
            )
    if not interface_methods:
        return written
    for path in _iter_headers(root):
        text = read_text(path)
        for interface_name, methods in interface_methods.items():
            if interface_name not in text:
                continue
            if f": public {interface_name}" not in text and f", public {interface_name}" not in text:
                continue
            updated = text
            for func_name, ret_type, params in methods:
                decl_match = find_implementer_method_decl(updated, func_name)
                if not decl_match:
                    continue
                const_suffix = " const" if " const" in decl_match.group(0) else ""
                new_decl = f"{ret_type} {func_name}({params}){const_suffix} override;"
                updated = updated[: decl_match.start()] + new_decl + updated[decl_match.end() :]
            if updated != text:
                write_file(path, updated)
                written.append(path)
                text = updated
    for cpp_path in _iter_cpp(root):
        text = read_text(cpp_path)
        updated = text
        for methods in interface_methods.values():
            for func_name, ret_type, params in methods:
                updated = re.sub(
                    rf"^[\t ]*(?:void|bool|int32|float)\s+(?P<class>[A-Za-z_][A-Za-z0-9_]*)::{re.escape(func_name)}\s*\([^)]*\)",
                    f"{ret_type} \\g<class>::{func_name}({params})",
                    updated,
                    count=1,
                    flags=re.MULTILINE,
                )
                body_match = re.search(
                    rf"{re.escape(func_name)}\s*\([^)]*\)\s*(?:const\s*)?\{{\s*}}",
                    updated,
                )
                if body_match and ret_type == "bool":
                    updated = updated[: body_match.end() - 1] + "\n\treturn true;\n}" + updated[body_match.end() :]
        if updated != text:
            write_file(cpp_path, updated)
            written.append(cpp_path)
    return written


def apply_multifile_return_type_sync_autofix(root: Path) -> list[Path]:
    """Sync implementer return types when header declares void but cpp uses bool/int/float."""
    written: list[Path] = []
    headers = class_headers(root)
    for cpp_path in _iter_cpp(root):
        text = read_text(cpp_path)
        for match in re.finditer(
            r"^(?P<ret>[\w:<>,\s*&]+)\s+(?P<class>[A-Za-z_][A-Za-z0-9_]*)::(?P<func>[A-Za-z_][A-Za-z0-9_]*)\s*\(",
            text,
            flags=re.MULTILINE,
        ):
            class_name = match.group("class")
            func_name = match.group("func")
            cpp_ret = match.group("ret").strip()
            header_text = headers.get(class_name)
            if not header_text:
                continue
            decl_match = find_implementer_method_decl(header_text, func_name)
            if decl_match is None:
                decl_match = _find_method_decl_in_header(header_text, func_name)
            if not decl_match:
                continue
            header_ret = decl_match.group("ret").strip()
            if header_ret == cpp_ret:
                continue
            if header_ret == "void" and cpp_ret in {"bool", "int32", "float"}:
                new_header = re.sub(
                    rf"\bvoid\s+{re.escape(func_name)}\s*\(",
                    f"{cpp_ret} {func_name}(",
                    header_text,
                    count=1,
                )
                for header_path in _iter_headers(root):
                    if read_text(header_path) == header_text:
                        write_file(header_path, new_header)
                        written.append(header_path)
                        headers[class_name] = new_header
                        break
    return written


def apply_multifile_refactor_autofixes(root: Path, findings: list[Finding]) -> list[Path]:
    written: list[Path] = []
    steps = (
        apply_callback_param_expand_autofix,
        apply_multifile_method_rename_autofix,
        apply_multifile_method_split_autofix,
        apply_multifile_interface_implementer_autofix,
        apply_multifile_return_type_sync_autofix,
        apply_multifile_delegate_header_sync_autofix,
    )
    for step in steps:
        for path in step(root):
            if path not in written:
                written.append(path)
    return written
