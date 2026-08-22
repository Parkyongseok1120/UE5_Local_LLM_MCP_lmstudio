#!/usr/bin/env python
"""Top-level validation orchestration and result-policy compatibility helpers."""

from __future__ import annotations

import re
from pathlib import Path

from unreal_static_build import (
    build_cs_text,
    build_cs_texts_by_module_root,
    load_include_owner_map,
    owning_build_cs_text,
    validate_build_modules,
    validate_include_owner_modules,
)
from unreal_static_crossfile import (
    validate_blueprint_native_event_implementations,
    validate_callback_function_pointer_drift,
    validate_cpp_declarations,
    validate_cpp_definitions_missing,
    validate_interface_implementer_drift,
    validate_multifile_callsite_drift,
)
from unreal_static_delegate import (
    build_declared_delegate_types,
    build_delegate_arity_map,
    build_delegate_arity_map_from_texts,
)
from unreal_static_include import (
    validate_duplicate_source_basenames,
    validate_typo_includes,
)
from unreal_static_lifecycle import (
    validate_unreal_lifecycle_overrides,
)
from unreal_static_model import (
    CPP_HEADER_SUFFIXES,
    CPP_SOURCE_SUFFIXES,
    SOURCE_ONLY_SUFFIXES,
    Finding,
)
from unreal_static_network import (
    validate_replicated_uproperty_without_doreplifetime,
    validate_replication_setup,
    validate_rpc_implementations,
)
from unreal_static_reflection import (
    validate_blueprint_native_event_declarations,
    validate_generated_h,
    validate_reflected_namespace,
    validate_tobjectptr_without_uproperty,
    validate_uobject_container_without_uproperty,
    validate_uproperty_category_without_exposure,
)
from unreal_static_registry import _run_per_file_validators
from unreal_static_scan import (
    _is_class_definition,
    _read_scope_texts,
    build_source_include_index,
    class_bases,
    class_bases_from_paths,
    class_headers,
    class_headers_from_paths,
    iter_source_files,
    read_text,
)
from workspace_paths import filesystem_path_identity


def validate_unreal_readiness(
    root: Path,
    module_graph_path: Path | None = None,
    *,
    lightweight: bool = False,
    skip_include_path_checks: bool = False,
    scope_paths: list[Path] | None = None,
) -> list[Finding]:
    if lightweight:
        return validate_unreal_readiness_lightweight(root)
    findings: list[Finding] = []
    build_text_value = build_cs_text(root)
    module_build_texts = build_cs_texts_by_module_root(root)
    if scope_paths is not None:
        from domain_validation_context import (
            DomainValidationContext,
            expand_domain_validation_scope,
            get_cached_domain_context,
        )

        expansion = expand_domain_validation_scope(root, scope_paths)
        scope = list(expansion.get("paths") or [])
        texts = _read_scope_texts(scope)
        domain_context = get_cached_domain_context(root, paths=scope, texts=texts, validation_mode="scoped")
        headers = class_headers_from_paths(scope, texts)
        header_paths: dict[str, Path] = dict(domain_context.headers_by_class)
        for path in scope:
            if path.suffix.lower() not in CPP_HEADER_SUFFIXES:
                continue
            module = domain_context.module_for_path(path)
            header_text = texts.get(path, "")
            for match in re.finditer(
                r"\bclass\s+(?:[A-Z0-9_]+_API\s+)?([A-Za-z_][A-Za-z0-9_]*)\b",
                header_text,
            ):
                if _is_class_definition(header_text, match.start()):
                    class_name = match.group(1)
                    from domain_validation_context import qualified_class_key

                    header_paths.setdefault(qualified_class_key(module, class_name), path)
                    header_paths.setdefault(class_name, path)
        if expansion.get("reasons"):
            import json as _json

            findings.append(
                Finding(
                    severity="info",
                    path=str(scope_paths[0]) if scope_paths else "",
                    line=0,
                    code="DOMAIN_VALIDATION_SCOPE_EXPANSION",
                    message=_json.dumps(
                        {
                            "requestedScope": expansion.get("requestedScope"),
                            "expandedScope": expansion.get("expandedScope"),
                            "reasons": expansion.get("reasons"),
                            "unresolved": expansion.get("unresolved"),
                        },
                        ensure_ascii=False,
                    ),
                )
            )
        bases = class_bases_from_paths(scope, texts)
        delegate_arity_map = build_delegate_arity_map_from_texts(scope, texts)
        declared_delegate_types = build_declared_delegate_types(root)
        include_index = build_source_include_index(root)
        include_owner_map = load_include_owner_map(module_graph_path) if module_graph_path else {}
        all_source_text = [texts[path] for path in scope]
        cpp_scope = [path for path in scope if path.suffix.lower() in CPP_SOURCE_SUFFIXES]
        header_scope = [path for path in scope if path.suffix.lower() in CPP_HEADER_SUFFIXES]
        for path in scope:
            _, per_file_build_text = owning_build_cs_text(
                path,
                root,
                module_build_texts,
                fallback=build_text_value if not module_build_texts else "",
            )
            _run_per_file_validators(
                findings,
                path,
                texts[path],
                root,
                headers=headers,
                bases=bases,
                delegate_arity_map=delegate_arity_map,
                declared_delegate_types=declared_delegate_types,
                include_index=include_index,
                build_text_value=per_file_build_text,
                skip_include_path_checks=skip_include_path_checks,
                write_mode=True,
                include_owner_map=include_owner_map,
                domain_context=domain_context,
            )
        findings.extend(
            validate_build_modules(
                root,
                "\n".join(all_source_text),
                build_text_value,
                scope_paths=scope,
                scope_texts=texts,
            )
        )
        findings.extend(
            validate_include_owner_modules(
                root,
                build_text_value,
                include_owner_map,
                scope_paths=scope,
            )
        )
        if cpp_scope:
            findings.extend(
                validate_cpp_definitions_missing(
                    root,
                    scope_headers=headers,
                    scope_header_paths=header_paths,
                    scope_cpp_paths=cpp_scope,
                    scope_texts=texts,
                )
            )
        findings.extend(
            validate_replication_setup(root, scope_cpp_paths=cpp_scope, scope_texts=texts)
        )
        findings.extend(
            validate_replicated_uproperty_without_doreplifetime(
                root,
                scope_header_paths=header_scope,
                scope_texts=texts,
            )
        )
        return findings

    include_owner_map = load_include_owner_map(module_graph_path) if module_graph_path else {}
    include_index = build_source_include_index(root)
    headers = class_headers(root)
    bases = class_bases(root)
    delegate_arity_map = build_delegate_arity_map(root)
    declared_delegate_types = build_declared_delegate_types(root)
    all_source_text = []
    all_paths = iter_source_files(root)
    from domain_validation_context import DomainValidationContext

    domain_context = DomainValidationContext.from_project(root, paths=all_paths)
    for path in all_paths:
        text = domain_context.text_for(path)
        all_source_text.append(text)
        _, per_file_build_text = owning_build_cs_text(
            path,
            root,
            module_build_texts,
            fallback=build_text_value if not module_build_texts else "",
        )
        _run_per_file_validators(
            findings,
            path,
            text,
            root,
            headers=headers,
            bases=bases,
            delegate_arity_map=delegate_arity_map,
            declared_delegate_types=declared_delegate_types,
            include_index=include_index,
            build_text_value=per_file_build_text,
            skip_include_path_checks=skip_include_path_checks,
            domain_context=domain_context,
        )
    findings.extend(validate_build_modules(root, "\n".join(all_source_text), build_text_value))
    findings.extend(validate_include_owner_modules(root, build_text_value, include_owner_map))
    findings.extend(validate_duplicate_source_basenames(root))
    findings.extend(validate_rpc_implementations(root))
    findings.extend(validate_blueprint_native_event_implementations(root))
    findings.extend(validate_replication_setup(root))
    findings.extend(validate_replicated_uproperty_without_doreplifetime(root))
    findings.extend(validate_cpp_definitions_missing(root))
    findings.extend(validate_interface_implementer_drift(root))
    findings.extend(validate_callback_function_pointer_drift(root))
    findings.extend(validate_multifile_callsite_drift(root))
    return findings

def validate_unreal_readiness_lightweight(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    headers = class_headers(root)
    for path in iter_source_files(root):
        if path.suffix.lower() not in SOURCE_ONLY_SUFFIXES:
            continue
        text = read_text(path)
        findings.extend(validate_typo_includes(path, text, root))
        if path.suffix.lower() in CPP_HEADER_SUFFIXES:
            findings.extend(validate_uproperty_category_without_exposure(path, text, root))
            findings.extend(validate_generated_h(path, text, root))
            findings.extend(validate_reflected_namespace(path, text, root))
            findings.extend(validate_unreal_lifecycle_overrides(path, text, root))
            findings.extend(validate_blueprint_native_event_declarations(path, text, root))
            findings.extend(validate_uobject_container_without_uproperty(path, text, root))
            findings.extend(validate_tobjectptr_without_uproperty(path, text, root))
        if path.suffix.lower() in CPP_SOURCE_SUFFIXES:
            findings.extend(validate_cpp_declarations(path, text, root, headers))
    return findings

def format_findings(findings: list[Finding]) -> str:
    if not findings:
        return "No static Unreal compile-readiness issues found."
    output_lines = ["Static Unreal compile-readiness findings:"]
    for finding in findings:
        location = f"{finding.path}:{finding.line}" if finding.line else finding.path
        output_lines.append(f"- [{finding.severity}] {finding.code} {location}: {finding.message}")
    return "\n".join(output_lines)

def has_static_errors(findings: list[Finding]) -> bool:
    return any(finding.severity == "error" for finding in findings)

def normalize_rel_path(value: str, host_platform: str | None = None) -> str:
    return filesystem_path_identity(value, host_platform, trim_outer_slashes=True)

DEFERRED_WRITE_COUNTERPART_CODES = frozenset(
    {
        "CPP_DEFINITION_MISSING",
        "RPC_IMPLEMENTATION_MISSING",
        "BLUEPRINT_NATIVE_EVENT_IMPL_MISSING",
    }
)

def has_blocking_write_errors(
    findings: list[Finding],
    write_target: str,
    host_platform: str | None = None,
) -> bool:
    """Whether a validate-on-write should roll back the just-written file.

    Only errors that are (a) not a deferred counterpart code and (b) located on the
    file that was just written count as blocking. Pre-existing errors in other files,
    and deferred counterpart findings anywhere, are surfaced as advisories instead.
    """
    target = normalize_rel_path(write_target, host_platform)
    for finding in findings:
        if finding.severity != "error":
            continue
        if finding.code in DEFERRED_WRITE_COUNTERPART_CODES:
            continue
        if normalize_rel_path(finding.path, host_platform) == target:
            return True
    return False

ACTIONABLE_DRIFT_CODES = frozenset(
    {
        "CPP_RETURN_TYPE_MISMATCH",
        "CALLBACK_FUNCTION_POINTER_MISMATCH",
        "CPP_FUNCTION_SIGNATURE_MISMATCH",
        "INTERFACE_IMPLEMENTER_SIGNATURE_MISMATCH",
        "DELEGATE_BROADCAST_SIGNATURE_MISMATCH",
        "MULTIFILE_CALLSITE_DRIFT",
    }
)

def has_actionable_static_findings(findings: list[Finding], *, mode: str = "") -> bool:
    if has_static_errors(findings):
        return True
    if str(mode or "") != "multifile_refactor":
        return False
    return any(str(finding.code) in ACTIONABLE_DRIFT_CODES for finding in findings)

def should_block_llm_apply_static_gate(findings: list[Finding], *, mode: str = "") -> bool:
    return has_actionable_static_findings(findings, mode=mode)

BLOCKING_STATIC_ERROR_CODES = {
    "DUPLICATE_SOURCE_BASENAME",
    "BLUEPRINT_NATIVE_EVENT_IMPL_MISSING",
    "BLUEPRINT_NATIVE_EVENT_MANUAL_IMPL_DECL",
    "BLUEPRINT_IMPLEMENTABLE_EVENT_INVALID_IMPL",
}

def has_blocking_static_errors(findings: list[Finding]) -> bool:
    for finding in findings:
        if finding.severity != "error":
            continue
        if finding.code.startswith("GENERATED_H"):
            return True
        if finding.code in BLOCKING_STATIC_ERROR_CODES:
            return True
        if finding.code == "INCLUDE_PATH_NOT_FOUND":
            return True
    return False

def can_run_autofix_ubt(findings: list[Finding], *, autofix_written: bool = False) -> bool:
    if has_blocking_static_errors(findings):
        return False
    if autofix_written:
        drift_codes = {
            "CPP_RETURN_TYPE_MISMATCH",
            "CALLBACK_FUNCTION_POINTER_MISMATCH",
            "BLUEPRINT_NATIVE_EVENT_IMPL_MISSING",
            "BLUEPRINT_NATIVE_EVENT_MANUAL_IMPL_DECL",
            "CPP_DEFINITION_MISSING",
        }
        if any(str(finding.code) in drift_codes for finding in findings):
            return False
        return True
    if not findings:
        return True
    if not has_static_errors(findings):
        return True
    return False

__all__ = [
    'validate_unreal_readiness',
    'validate_unreal_readiness_lightweight',
    'format_findings',
    'has_static_errors',
    'normalize_rel_path',
    'DEFERRED_WRITE_COUNTERPART_CODES',
    'has_blocking_write_errors',
    'ACTIONABLE_DRIFT_CODES',
    'has_actionable_static_findings',
    'should_block_llm_apply_static_gate',
    'BLOCKING_STATIC_ERROR_CODES',
    'has_blocking_static_errors',
    'can_run_autofix_ubt',
]
