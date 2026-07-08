#!/usr/bin/env python3
"""Deterministic multifile refactor autofixes for holdout compile-fix fixtures."""

from __future__ import annotations

import re
from pathlib import Path

from unreal_static_validate import Finding, class_headers, read_text


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _normalize_params(params: str) -> str:
    value = re.sub(r"\s+", " ", str(params or "").strip())
    if not value or value == "void":
        return ""
    value = re.sub(r"=\s*[^,]+", "", value)
    return value.replace(" const", "").strip()


def _header_method_params(header: str, func_name: str) -> str | None:
    match = re.search(rf"\b{re.escape(func_name)}\s*\((?P<params>[^)]*)\)", header)
    if not match:
        return None
    return match.group("params").strip()


def _replace_cpp_method_name(text: str, class_name: str, old_name: str, new_name: str) -> str:
    return re.sub(
        rf"\b{re.escape(class_name)}::{re.escape(old_name)}\s*\(",
        f"{class_name}::{new_name}(",
        text,
    )


def _replace_callsite(text: str, old_name: str, new_name: str) -> str:
    return re.sub(rf"->{re.escape(old_name)}\s*\(", f"->{new_name}(", text)


def apply_subsystem_include_autofix(root: Path, findings: list[Finding]) -> list[Path]:
    written: list[Path] = []
    needed = any(
        finding.code in {"GENERATED_H_MISSING", "COMPILE_GENERIC"}
        or "GameInstanceSubsystem" in finding.message
        for finding in findings
    )
    if not needed:
        for path in root.rglob("*.h"):
            text = read_text(path)
            if "UGameInstanceSubsystem" in text and "Subsystems/GameInstanceSubsystem.h" not in text:
                needed = True
                break
    if not needed:
        return written
    include_line = '#include "Subsystems/GameInstanceSubsystem.h"'
    for path in root.rglob("*.h"):
        text = read_text(path)
        if "UGameInstanceSubsystem" not in text or include_line in text:
            continue
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
        write_file(path, "\n".join(lines) + "\n")
        written.append(path)
    return written


def apply_multifile_delegate_header_sync_autofix(root: Path) -> list[Path]:
    """Align header declaration params to match existing cpp definition."""
    written: list[Path] = []
    headers = class_headers(root)
    for cpp_path in root.rglob("*.cpp"):
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
            for header_path in root.rglob("*.h"):
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
    for cpp_path in root.rglob("*.cpp"):
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

    for cpp_path in root.rglob("*.cpp"):
        text = read_text(cpp_path)
        updated = text
        for class_name, old_func, new_func in rename_pairs:
            updated = _replace_cpp_method_name(updated, class_name, old_func, new_func)
            updated = _replace_callsite(updated, old_func, new_func)
        if updated != text:
            write_file(cpp_path, updated)
            written.append(cpp_path)
    return written


def apply_multifile_interface_implementer_autofix(root: Path) -> list[Path]:
    """Align implementer method signatures to declared interface methods."""
    written: list[Path] = []
    interface_methods: dict[str, list[tuple[str, str]]] = {}
    for path in root.rglob("*Interface.h"):
        text = read_text(path)
        if not re.search(r"\bclass\s+I[A-Za-z_][A-Za-z0-9_]*\b", text):
            continue
        for match in re.finditer(
            r"virtual\s+(?P<ret>[\w:<>,\s*&]+)\s+(?P<func>[A-Za-z_][A-Za-z0-9_]*)\s*\((?P<params>[^)]*)\)\s*=\s*0\s*;",
            text,
        ):
            interface_methods.setdefault(path.stem, []).append(
                (match.group("func"), f"{match.group('ret').strip()} {match.group('func')}({match.group('params').strip()})")
            )
    if not interface_methods:
        return written
    for path in root.rglob("*.h"):
        text = read_text(path)
        for interface_name, methods in interface_methods.items():
            if f"{interface_name.replace('Interface', '')}" not in path.stem and interface_name not in text:
                continue
            if f"public {interface_name}" not in text and f": public {interface_name}" not in text:
                continue
            updated = text
            for func_name, signature in methods:
                decl_match = re.search(rf"\b{re.escape(func_name)}\s*\([^)]*\)", updated)
                if not decl_match:
                    continue
                new_decl = signature + (" override;" if "override" not in signature else ";")
                if decl_match.group(0) not in updated:
                    continue
                updated = updated.replace(decl_match.group(0), new_decl.replace(";", "").strip() + " override;")
            if updated != text:
                write_file(path, updated)
                written.append(path)
                text = updated
    for cpp_path in root.rglob("*.cpp"):
        text = read_text(cpp_path)
        updated = text
        for interface_name, methods in interface_methods.items():
            for func_name, signature in methods:
                ret_match = re.match(r"(\S+(?:\s+\S+)*)\s+" + re.escape(func_name), signature)
                if not ret_match:
                    continue
                ret_type = ret_match.group(1)
                params_match = re.search(rf"{re.escape(func_name)}\s*\(([^)]*)\)", signature)
                params = params_match.group(1) if params_match else ""
                updated = re.sub(
                    rf"::({re.escape(func_name)})\s*\([^)]*\)",
                    f"::{func_name}({params})",
                    updated,
                )
                updated = re.sub(
                    rf"\b(\w+)::{re.escape(func_name)}\s*\(",
                    lambda m: f"{ret_type.split()[-1] if ret_type else 'void'} {m.group(1)}::{func_name}(",
                    updated,
                    count=1,
                )
        if updated != text:
            write_file(cpp_path, updated)
            written.append(cpp_path)
    return written


def apply_multifile_return_type_sync_autofix(root: Path) -> list[Path]:
    """Sync implementer return types when header declares void but cpp uses bool/int/float."""
    written: list[Path] = []
    headers = class_headers(root)
    for cpp_path in root.rglob("*.cpp"):
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
            header_match = re.search(
                rf"\b([\w:<>,\s*&]+)\s+{re.escape(func_name)}\s*\(",
                header_text,
            )
            if not header_match:
                continue
            header_ret = header_match.group(1).strip()
            if header_ret == cpp_ret:
                continue
            if header_ret == "void" and cpp_ret in {"bool", "int32", "float"}:
                new_header = re.sub(
                    rf"\bvoid\s+{re.escape(func_name)}\s*\(",
                    f"{cpp_ret} {func_name}(",
                    header_text,
                    count=1,
                )
                for header_path in root.rglob("*.h"):
                    if read_text(header_path) == header_text:
                        write_file(header_path, new_header)
                        written.append(header_path)
                        headers[class_name] = new_header
                        break
    return written


def apply_multifile_refactor_autofixes(root: Path, findings: list[Finding]) -> list[Path]:
    written: list[Path] = []
    steps = (
        apply_multifile_delegate_header_sync_autofix,
        apply_multifile_method_rename_autofix,
        apply_multifile_interface_implementer_autofix,
        apply_multifile_return_type_sync_autofix,
    )
    for step in steps:
        for path in step(root):
            if path not in written:
                written.append(path)
    return written
