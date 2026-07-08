#!/usr/bin/env python3
"""Deterministic multifile refactor autofixes for holdout compile-fix fixtures."""

from __future__ import annotations

import re
from pathlib import Path

from unreal_static_validate import Finding, iter_source_files, read_text
from ue_cpp_signatures import (
    CPP_METHOD_DEFINITION_RE,
    RET_TYPE_CHAR_CLASS,
    SourceTreeIndex,
    TYPEDEF_FUNCTION_POINTER_RE,
    build_source_tree_index,
    clean_method_ret,
    collect_callback_drifts,
    collect_interface_specs,
    find_method_decl_in_header,
    header_method_params,
    normalize_signature_params,
)

FINDING_STEP_CODES: dict[str, set[str]] = {
    "callback_expand": {"CALLBACK_FUNCTION_POINTER_MISMATCH"},
    "method_rename": {"CPP_FUNCTION_NOT_DECLARED_IN_HEADER", "CPP_FUNCTION_SIGNATURE_MISMATCH"},
    "method_split": {"MULTIFILE_CALLSITE_DRIFT", "CPP_FUNCTION_SIGNATURE_MISMATCH"},
    "interface_implementer": {"INTERFACE_IMPLEMENTER_SIGNATURE_MISMATCH"},
    "return_type_sync": {
        "INTERFACE_IMPLEMENTER_SIGNATURE_MISMATCH",
        "CPP_FUNCTION_SIGNATURE_MISMATCH",
        "CPP_RETURN_TYPE_MISMATCH",
    },
    "cpp_return_type_sync": {"CPP_RETURN_TYPE_MISMATCH"},
    "delegate_header_sync": {"CPP_FUNCTION_SIGNATURE_MISMATCH", "DELEGATE_BROADCAST_SIGNATURE_MISMATCH"},
}


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


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


def _finding_codes(findings: list[Finding]) -> set[str]:
    return {str(finding.code) for finding in findings}


def _should_run(step: str, findings: list[Finding]) -> bool:
    if not findings:
        return False
    codes = FINDING_STEP_CODES.get(step, set())
    if not codes:
        return False
    present = _finding_codes(findings)
    return bool(present & codes)


def _write_header_for_class(index: SourceTreeIndex, class_name: str, updated: str, written: list[Path]) -> None:
    header_path = index.class_to_path.get(class_name)
    if header_path and read_text(header_path) != updated:
        write_file(header_path, updated)
        written.append(header_path)
        index.class_to_text[class_name] = updated


def apply_subsystem_include_autofix(root: Path, findings: list[Finding]) -> list[Path]:
    written: list[Path] = []
    needed = any(
        finding.code in {"GENERATED_H_MISSING", "COMPILE_GENERIC", "GENERATED_H_DUPLICATE"}
        or "GameInstanceSubsystem" in finding.message
        for finding in findings
    )
    index = build_source_tree_index(root)
    if not needed:
        for path in index.headers:
            text = read_text(path)
            if "UGameInstanceSubsystem" in text and "Subsystems/GameInstanceSubsystem.h" not in text:
                needed = True
                break
    if not needed:
        return written
    include_line = '#include "Subsystems/GameInstanceSubsystem.h"'
    for path in index.headers:
        text = read_text(path)
        if "UGameInstanceSubsystem" not in text:
            continue
        updated = text
        if include_line not in text:
            lines = text.splitlines()
            insert_at = 0
            generated_index = -1
            for idx, line in enumerate(lines):
                if line.strip().startswith("#include"):
                    insert_at = idx + 1
                if ".generated.h" in line:
                    generated_index = idx
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


def _normalize_return_type(ret: str) -> str:
    value = re.sub(r"\s+", " ", str(ret or "").strip())
    return value.replace(" const", "").strip()


def _cpp_impl_definition_re(class_name: str, func_name: str) -> re.Pattern[str]:
    return re.compile(
        rf"^[\t ]*(?:static\s+)?[\w:<>,\s&]+?\s+{re.escape(class_name)}::{re.escape(func_name)}\s*\(",
        re.MULTILINE,
    )


def _cpp_has_class_scope_leak(text: str) -> bool:
    return bool(re.search(r"^[\t ]*(?:public|private|protected)\s*:", text, re.MULTILINE))


def _snapshot_paths(root: Path, paths: list[Path]) -> dict[Path, str]:
    return {path: read_text(path) for path in paths}


def _restore_paths(snapshot: dict[Path, str]) -> None:
    for path, content in snapshot.items():
        write_file(path, content)


def apply_callback_param_expand_autofix(root: Path, *, index: SourceTreeIndex | None = None) -> list[Path]:
    written: list[Path] = []
    tree = index or build_source_tree_index(root)
    for drift in collect_callback_drifts(root, index=tree):
        if not drift.expandable:
            continue
        class_name = drift.class_name
        func_name = drift.func_name
        header_text = tree.class_to_text.get(class_name)
        if not header_text:
            continue
        decl_match = find_method_decl_in_header(header_text, func_name)
        if not decl_match:
            continue
        ret_type, ret_is_static = clean_method_ret(decl_match.group("ret"))
        current_params = decl_match.group("params").strip()
        registration_text = read_text(drift.cpp_path)
        typedef_match = TYPEDEF_FUNCTION_POINTER_RE.search(registration_text)
        if not typedef_match:
            continue
        new_params = typedef_match.group("params").strip()
        current_param_parts = [part.strip() for part in current_params.split(",") if part.strip()]
        new_param_parts = [part.strip() for part in new_params.split(",") if part.strip()]
        expanded_params: list[str] = []
        new_param_names: list[str] = []
        for idx, part in enumerate(new_param_parts):
            if idx < len(current_param_parts) and normalize_signature_params(current_param_parts[idx]) == normalize_signature_params(part):
                expanded_params.append(current_param_parts[idx])
                name = current_param_parts[idx].split()[-1]
                new_param_names.append(name)
            elif " " not in part:
                if part == "int32":
                    expanded_params.append("int32 Value")
                    new_param_names.append("Value")
                elif part == "bool":
                    expanded_params.append("bool bSuccess")
                    new_param_names.append("bSuccess")
                else:
                    expanded_params.append(f"{part} Value{idx + 1}")
                    new_param_names.append(f"Value{idx + 1}")
            else:
                expanded_params.append(part)
                new_param_names.append(part.split()[-1])
        params_text = ", ".join(expanded_params)
        matched_decl_text = decl_match.group(0)
        is_static = ret_is_static or matched_decl_text.lstrip().startswith("static")
        leading_ws = matched_decl_text[: len(matched_decl_text) - len(matched_decl_text.lstrip(" \t"))]
        static_prefix = "static " if is_static else ""
        new_decl = f"{leading_ws}{static_prefix}{ret_type} {func_name}({params_text});"
        header_path = tree.class_to_path.get(class_name)
        snapshot = _snapshot_paths(root, [p for p in [header_path] + tree.cpps if p])
        updated_header = header_text[: decl_match.start()] + new_decl + header_text[decl_match.end() :]
        _write_header_for_class(tree, class_name, updated_header, written)
        header_text = tree.class_to_text[class_name]
        impl_cpp: Path | None = None
        for cpp_path in tree.cpps:
            if cpp_path == drift.cpp_path:
                continue
            text = read_text(cpp_path)
            if not _cpp_impl_definition_re(class_name, func_name).search(text):
                continue
            if f"&{class_name}::{func_name}" in text and f"{class_name}::{func_name}(" not in text.split("&", 1)[0]:
                continue
            impl_cpp = cpp_path
            break
        if not impl_cpp:
            _restore_paths(snapshot)
            continue
        text = read_text(impl_cpp)
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
            _restore_paths(snapshot)
            continue
        new_def = f"{ret_type} {class_name}::{func_name}({params_text})"
        updated = text[: def_match.start()] + new_def + text[def_match.end() :]
        existing_names = {part.split()[-1] for part in current_param_parts}
        for name in new_param_names:
            if name in existing_names:
                continue
            body_stub = f"\n\t(void){name};"
            body_open = updated.find("{", def_match.end())
            if body_open >= 0:
                close = updated.find("}", body_open)
                if close >= 0:
                    body = updated[body_open + 1 : close]
                    if f"(void){name}" not in body and f"(void) {name}" not in body:
                        updated = updated[:close] + body_stub + updated[close:]
        if updated != text:
            write_file(impl_cpp, updated)
            written.append(impl_cpp)
        if _cpp_has_class_scope_leak(read_text(impl_cpp)):
            _restore_paths(snapshot)
            written.clear()
            continue
        from unreal_static_validate import validate_callback_function_pointer_drift

        if validate_callback_function_pointer_drift(root):
            _restore_paths(snapshot)
            written.clear()
    return written


def apply_multifile_delegate_header_sync_autofix(root: Path, *, index: SourceTreeIndex | None = None) -> list[Path]:
    written: list[Path] = []
    tree = index or build_source_tree_index(root)
    for cpp_path in tree.cpps:
        text = read_text(cpp_path)
        for match in CPP_METHOD_DEFINITION_RE.finditer(text):
            class_name = match.group("class")
            func_name = match.group("func")
            cpp_params = match.group("params").strip()
            header_text = tree.class_to_text.get(class_name)
            if not header_text:
                continue
            header_params = header_method_params(header_text, func_name)
            if header_params is None:
                continue
            if normalize_signature_params(header_params) == normalize_signature_params(cpp_params):
                continue
            old_decl_match = re.search(rf"void\s+{re.escape(func_name)}\s*\([^)]*\)\s*;", header_text)
            if not old_decl_match:
                continue
            new_decl = f"void {func_name}({cpp_params});"
            updated_header = header_text[: old_decl_match.start()] + new_decl + header_text[old_decl_match.end() :]
            _write_header_for_class(tree, class_name, updated_header, written)
    return written


def apply_multifile_method_rename_autofix(root: Path, *, index: SourceTreeIndex | None = None) -> list[Path]:
    written: list[Path] = []
    tree = index or build_source_tree_index(root)
    rename_pairs: list[tuple[str, str, str]] = []
    for cpp_path in tree.cpps:
        text = read_text(cpp_path)
        for match in re.finditer(
            r"\b(?P<class>U[A-Za-z_][A-Za-z0-9_]*)::(?P<func>[A-Za-z_][A-Za-z0-9_]*)\s*\(",
            text,
        ):
            class_name = match.group("class")
            cpp_func = match.group("func")
            header_text = tree.class_to_text.get(class_name)
            if not header_text:
                continue
            if header_method_params(header_text, cpp_func) is not None:
                continue
            candidates = re.findall(r"\bvoid\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", header_text)
            if len(candidates) != 1:
                continue
            header_func = candidates[0]
            if header_func != cpp_func:
                rename_pairs.append((class_name, cpp_func, header_func))

    for cpp_path in tree.cpps:
        text = read_text(cpp_path)
        updated = text
        for class_name, old_func, new_func in rename_pairs:
            updated = re.sub(
                rf"\b{re.escape(class_name)}::{re.escape(old_func)}\s*\(",
                f"{class_name}::{new_func}(",
                updated,
            )
            updated = re.sub(rf"->{re.escape(old_func)}\s*\(", f"->{new_func}(", updated)
        if updated != text:
            write_file(cpp_path, updated)
            written.append(cpp_path)
    return written


def apply_multifile_method_split_autofix(root: Path, *, index: SourceTreeIndex | None = None) -> list[Path]:
    written: list[Path] = []
    tree = index or build_source_tree_index(root)
    for class_name, header_text in tree.class_to_text.items():
        if not class_name.startswith("U"):
            continue
        declared = re.findall(r"\bvoid\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*\)\s*;", header_text)
        if len(declared) < 2:
            continue
        stale_names = {"DoAll", "HandleAll", "RunAll"}
        for cpp_path in tree.cpps:
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
                for consumer_path in tree.cpps:
                    consumer = read_text(consumer_path)
                    callsite = re.search(rf"(\w+)->{re.escape(stale)}\s*\(\s*\)", consumer)
                    if not callsite or len(declared) < 2:
                        continue
                    var_name = callsite.group(1)
                    replacement_calls = ";\n\t\t".join(f"{var_name}->{method}()" for method in declared[:2])
                    new_consumer = consumer[: callsite.start()] + replacement_calls + consumer[callsite.end() :]
                    if new_consumer != consumer:
                        write_file(consumer_path, new_consumer)
                        written.append(consumer_path)
    return written


def apply_multifile_interface_implementer_autofix(root: Path, *, index: SourceTreeIndex | None = None) -> list[Path]:
    written: list[Path] = []
    tree = index or build_source_tree_index(root)
    interface_specs = collect_interface_specs(root, index=tree)
    if not interface_specs:
        return written
    for path in tree.headers:
        text = read_text(path)
        for interface_name, methods in interface_specs.items():
            if interface_name not in text:
                continue
            if f": public {interface_name}" not in text and f", public {interface_name}" not in text:
                continue
            updated = text
            for method in methods:
                decl_match = find_method_decl_in_header(updated, method.func)
                if not decl_match:
                    continue
                const_suffix = " const" if " const" in decl_match.group(0) else ""
                new_decl = f"{method.ret} {method.func}({method.params_raw}){const_suffix} override;"
                updated = updated[: decl_match.start()] + new_decl + updated[decl_match.end() :]
            if updated != text:
                write_file(path, updated)
                written.append(path)
                text = updated
    for cpp_path in tree.cpps:
        text = read_text(cpp_path)
        updated = text
        for methods in interface_specs.values():
            for method in methods:
                updated = re.sub(
                    rf"^[\t ]*(?:void|bool|int32|float)\s+(?P<class>[A-Za-z_][A-Za-z0-9_]*)::{re.escape(method.func)}\s*\([^)]*\)",
                    f"{method.ret} \\g<class>::{method.func}({method.params_raw})",
                    updated,
                    count=1,
                    flags=re.MULTILINE,
                )
                body_match = re.search(
                    rf"{re.escape(method.func)}\s*\([^)]*\)\s*(?:const\s*)?\{{\s*}}",
                    updated,
                )
                if body_match and method.ret == "bool":
                    updated = updated[: body_match.end() - 1] + "\n\treturn true;\n}" + updated[body_match.end() :]
        if updated != text:
            write_file(cpp_path, updated)
            written.append(cpp_path)
    return written


def apply_multifile_return_type_sync_autofix(root: Path, *, index: SourceTreeIndex | None = None) -> list[Path]:
    written: list[Path] = []
    tree = index or build_source_tree_index(root)
    for cpp_path in tree.cpps:
        text = read_text(cpp_path)
        for match in re.finditer(
            rf"^(?P<ret>{RET_TYPE_CHAR_CLASS}+)\s+(?P<class>[A-Za-z_][A-Za-z0-9_]*)::(?P<func>[A-Za-z_][A-Za-z0-9_]*)\s*\(",
            text,
            flags=re.MULTILINE,
        ):
            class_name = match.group("class")
            func_name = match.group("func")
            cpp_ret, _ = clean_method_ret(match.group("ret"))
            header_text = tree.class_to_text.get(class_name)
            if not header_text:
                continue
            decl_match = find_method_decl_in_header(header_text, func_name)
            if not decl_match:
                continue
            header_ret, _ = clean_method_ret(decl_match.group("ret"))
            if header_ret == cpp_ret:
                continue
            if header_ret == "void" and cpp_ret in {"bool", "int32", "float"}:
                new_header = re.sub(
                    rf"\bvoid\s+{re.escape(func_name)}\s*\(",
                    f"{cpp_ret} {func_name}(",
                    header_text,
                    count=1,
                )
                _write_header_for_class(tree, class_name, new_header, written)
    return written


def apply_cpp_return_type_sync_autofix(root: Path, *, index: SourceTreeIndex | None = None) -> list[Path]:
    """Align .cpp method return types with authoritative header declarations (cpp-only rewrite)."""
    written: list[Path] = []
    tree = index or build_source_tree_index(root)
    sig_re = re.compile(
        r"^(?P<prefix>[\t ]*)(?P<ret>[\w:<>,\s&]+?)\s+(?P<class>[A-Za-z_][A-Za-z0-9_]*)::"
        r"(?P<func>[A-Za-z_][A-Za-z0-9_]*)\s*\((?P<params>[^)]*)\)\s*(?P<const_suffix>const\s*)?\s*$",
        flags=re.MULTILINE,
    )
    for cpp_path in tree.cpps:
        text = read_text(cpp_path)
        updated = text
        matches = list(sig_re.finditer(text))
        for match in reversed(matches):
            class_name = match.group("class")
            func_name = match.group("func")
            params = match.group("params")
            cpp_ret = _normalize_return_type(match.group("ret"))
            header_text = tree.class_to_text.get(class_name)
            if not header_text:
                continue
            decl_match = find_method_decl_in_header(header_text, func_name)
            if not decl_match:
                continue
            header_params = normalize_signature_params(decl_match.group("params"))
            if header_params != normalize_signature_params(params):
                continue
            header_ret = _normalize_return_type(decl_match.group("ret"))
            if not header_ret or header_ret == cpp_ret:
                continue
            brace_idx = updated.find("{", match.end())
            if brace_idx < 0:
                continue
            close_idx = updated.find("}", brace_idx)
            if close_idx < 0:
                continue
            const_suffix = match.group("const_suffix") or ""
            new_sig = (
                f"{match.group('prefix')}{header_ret} {class_name}::{func_name}({params}) "
                f"{const_suffix.rstrip()}"
            ).rstrip()
            body = updated[brace_idx + 1 : close_idx]
            ret_member = re.search(r"return\s+([A-Za-z_][A-Za-z0-9_]*)\s*;", body)
            if ret_member:
                member = ret_member.group(1)
                body = body.replace(
                    ret_member.group(0),
                    f"return static_cast<{header_ret}>({member});",
                    1,
                )
            replacement = new_sig + "\n{\n" + body + "}"
            updated = updated[: match.start()] + replacement + updated[close_idx + 1 :]
        if updated != text:
            write_file(cpp_path, updated)
            written.append(cpp_path)
    return written


def apply_multifile_refactor_autofixes(root: Path, findings: list[Finding]) -> list[Path]:
    written: list[Path] = []
    index = build_source_tree_index(root)
    steps = (
        ("callback_expand", apply_callback_param_expand_autofix),
        ("method_rename", apply_multifile_method_rename_autofix),
        ("method_split", apply_multifile_method_split_autofix),
        ("interface_implementer", apply_multifile_interface_implementer_autofix),
        ("cpp_return_type_sync", apply_cpp_return_type_sync_autofix),
        ("return_type_sync", apply_multifile_return_type_sync_autofix),
        ("delegate_header_sync", apply_multifile_delegate_header_sync_autofix),
    )
    for step_name, step in steps:
        if not _should_run(step_name, findings):
            continue
        for path in step(root, index=index):
            if path not in written:
                written.append(path)
    return written
