#!/usr/bin/env python
"""Per-file validator registry and protected domain-validator dispatch."""

from __future__ import annotations

from pathlib import Path

from unreal_static_build import (
    validate_enhanced_input,
)
from unreal_static_crossfile import (
    validate_cpp_declarations,
)
from unreal_static_delegate import (
    validate_blueprint_assignable_delegate_types,
    validate_delegate_bind_without_unbind,
    validate_delegate_broadcast_consistency,
)
from unreal_static_include import (
    validate_component_registration_includes,
    validate_editor_only_runtime_includes,
    validate_include_paths_exist,
    validate_required_includes,
    validate_typo_includes,
)
from unreal_static_lifecycle import (
    validate_action_request_order,
    validate_actor_ctor_getworld,
    validate_component_subsystem_patterns,
    validate_component_timer_manager,
    validate_constructor_lifecycle_usage,
    validate_interrupt_param_ignored,
    validate_missing_super_lifecycle_call,
    validate_newobject_outer,
    validate_timer_set_without_clear,
    validate_unreal_lifecycle_overrides,
)
from unreal_static_model import (
    CPP_HEADER_SUFFIXES,
    CPP_SOURCE_SUFFIXES,
    SOURCE_ONLY_SUFFIXES,
    Finding,
)
from unreal_static_reflection import (
    validate_blueprint_native_event_declarations,
    validate_blueprintpure_missing_const,
    validate_generated_h,
    validate_private_blueprint_access,
    validate_project_uobject_type_visibility,
    validate_raw_new_delete_uobject,
    validate_raw_uobject_members,
    validate_reflected_namespace,
    validate_tobjectptr_without_uproperty,
    validate_uht_macros_in_conditional_blocks,
    validate_uobject_container_without_uproperty,
    validate_uproperty_category_without_exposure,
)
from unreal_static_safety import (
    validate_bool_member_parameter_types,
    validate_fvector_float_precision,
    validate_gengine_world_context,
    validate_hardcoded_asset_path,
    validate_known_bad_api_patterns,
    validate_static_mutable_container_members,
    validate_sync_load_in_gameplay,
    validate_unchecked_cast_result,
)


def _append_validator_internal_error(
    findings: list[Finding],
    path: Path,
    root: Path,
    validator_name: str,
    exc: Exception,
) -> None:
    rel = str(path.relative_to(root)).replace("\\", "/") if path.is_file() else str(path)
    findings.append(
        Finding(
            "warning",
            rel,
            1,
            "DOMAIN_VALIDATOR_INTERNAL_ERROR",
            f"{validator_name} failed: {exc}",
        )
    )

def _run_domain_validators(
    findings: list[Finding],
    path: Path,
    text: str,
    root: Path,
    domain_context: object | None,
) -> None:
    from domain_validators import (
        validate_animation_notify_lifecycle,
        validate_animinstance_thread_conservative,
        validate_animnotify_mutable_state_conservative,
        validate_component_preflight,
        validate_gas_asc_lifecycle_conservative,
        validate_gas_footprint,
        validate_replication_contract,
        validate_replication_ownership_conservative,
        validate_rpc_caller_ownership_conservative,
        validate_subsystem_lifecycle,
    )

    validators = (
        ("validate_component_preflight", validate_component_preflight),
        ("validate_subsystem_lifecycle", validate_subsystem_lifecycle),
        ("validate_replication_contract", validate_replication_contract),
        ("validate_gas_footprint", validate_gas_footprint),
        ("validate_animation_notify_lifecycle", validate_animation_notify_lifecycle),
        ("validate_replication_ownership_conservative", validate_replication_ownership_conservative),
        ("validate_rpc_caller_ownership_conservative", validate_rpc_caller_ownership_conservative),
        ("validate_gas_asc_lifecycle_conservative", validate_gas_asc_lifecycle_conservative),
        ("validate_animinstance_thread_conservative", validate_animinstance_thread_conservative),
        ("validate_animnotify_mutable_state_conservative", validate_animnotify_mutable_state_conservative),
    )
    for name, func in validators:
        try:
            findings.extend(func(path, text, root, domain_context))
        except (SyntaxError, UnicodeDecodeError) as exc:
            _append_validator_internal_error(findings, path, root, name, exc)
        except Exception as exc:
            _append_validator_internal_error(findings, path, root, name, exc)

def _run_per_file_validators(
    findings: list[Finding],
    path: Path,
    text: str,
    root: Path,
    *,
    headers: dict[str, str],
    bases: dict[str, str],
    delegate_arity_map: dict[str, int],
    declared_delegate_types: set[str],
    include_index: dict[str, list[str]],
    build_text_value: str,
    skip_include_path_checks: bool,
    write_mode: bool = False,
    include_owner_map: dict[str, list[str]] | None = None,
    domain_context: object | None = None,
) -> None:
    if path.name.endswith(".uplugin"):
        try:
            from plugin_project_context import validate_uplugin_descriptor

            rel = str(path.relative_to(root))
            for item in validate_uplugin_descriptor(path):
                findings.append(
                    Finding(
                        str(item.get("severity") or "error"),
                        rel,
                        1,
                        str(item.get("code") or "UPLUGIN_INVALID"),
                        str(item.get("message") or "Invalid .uplugin descriptor."),
                    )
                )
        except Exception as exc:
            _append_validator_internal_error(findings, path, root, "validate_uplugin_descriptor", exc)
        return
    if path.suffix.lower() in SOURCE_ONLY_SUFFIXES:
        findings.extend(validate_typo_includes(path, text, root))
        findings.extend(validate_component_subsystem_patterns(path, text, root))
        findings.extend(validate_gengine_world_context(path, text, root))
        findings.extend(validate_known_bad_api_patterns(path, text, root))
        findings.extend(validate_bool_member_parameter_types(path, text, root))
        _run_domain_validators(findings, path, text, root, domain_context)
    if path.suffix.lower() in CPP_HEADER_SUFFIXES:
        findings.extend(validate_generated_h(path, text, root))
        findings.extend(validate_reflected_namespace(path, text, root))
        findings.extend(validate_blueprint_assignable_delegate_types(path, text, root, declared_delegate_types))
        findings.extend(validate_uht_macros_in_conditional_blocks(path, text, root))
        findings.extend(validate_uproperty_category_without_exposure(path, text, root))
        findings.extend(validate_static_mutable_container_members(path, text, root))
        findings.extend(validate_unreal_lifecycle_overrides(path, text, root))
        findings.extend(validate_blueprint_native_event_declarations(path, text, root))
        findings.extend(validate_private_blueprint_access(path, text, root))
        findings.extend(validate_raw_uobject_members(path, text, root))
        findings.extend(validate_uobject_container_without_uproperty(path, text, root))
        findings.extend(validate_tobjectptr_without_uproperty(path, text, root))
        findings.extend(validate_blueprintpure_missing_const(path, text, root))
        findings.extend(validate_project_uobject_type_visibility(path, text, root, include_index))
        findings.extend(validate_required_includes(path, text, root))
        findings.extend(validate_component_registration_includes(path, text, root))
    if path.suffix.lower() in SOURCE_ONLY_SUFFIXES:
        findings.extend(validate_editor_only_runtime_includes(path, text, root))
        findings.extend(validate_enhanced_input(path, text, root, build_text_value))
        findings.extend(validate_action_request_order(path, text, root))
        if not skip_include_path_checks:
            findings.extend(
                validate_include_paths_exist(
                    path,
                    text,
                    root,
                    include_index,
                    write_mode=write_mode,
                    include_owner_map=include_owner_map,
                )
            )
    if path.suffix.lower() in CPP_SOURCE_SUFFIXES:
        findings.extend(validate_required_includes(path, text, root))
        findings.extend(validate_component_registration_includes(path, text, root))
        findings.extend(validate_constructor_lifecycle_usage(path, text, root))
        findings.extend(validate_newobject_outer(path, text, root))
        findings.extend(validate_component_timer_manager(path, text, root, bases))
        findings.extend(validate_cpp_declarations(path, text, root, headers))
        findings.extend(validate_delegate_broadcast_consistency(path, text, root, delegate_arity_map))
        findings.extend(validate_missing_super_lifecycle_call(path, text, root))
        findings.extend(validate_delegate_bind_without_unbind(path, text, root))
        findings.extend(validate_timer_set_without_clear(path, text, root))
        findings.extend(validate_interrupt_param_ignored(path, text, root))
        findings.extend(validate_unchecked_cast_result(path, text, root))
        findings.extend(validate_raw_new_delete_uobject(path, text, root))
        findings.extend(validate_actor_ctor_getworld(path, text, root, bases))
        findings.extend(validate_sync_load_in_gameplay(path, text, root))
        findings.extend(validate_hardcoded_asset_path(path, text, root))
        findings.extend(validate_fvector_float_precision(path, text, root))

__all__ = [
    "_append_validator_internal_error",
    "_run_domain_validators",
    "_run_per_file_validators",
]
