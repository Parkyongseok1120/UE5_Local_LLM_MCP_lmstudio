#!/usr/bin/env python
"""LM Studio wrapper that writes Unreal prototype files, validates, and builds."""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import hashlib
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.request import Request, urlopen

from rag_context import assemble_context
from load_sampling_preset import preset_for_wrapper, profile_edit_limits, set_sampling_profile_for_model
from preflight_lmstudio import extract_assistant_text
from rag_search import SearchOptions, search as search_index
import token_budget
from workspace_paths import active_project_names, resolve_active_project_path, resolve_ubt_path
from error_taxonomy import mode_from_error_kind as taxonomy_mode_from_error_kind, route_error_action
from module_resolver import build_cs_has_module, resolve_modules_from_error, resolve_modules_from_text
from retry_state import make_attempt_record, make_validation_rejection_record, recommend_retry_action
from prompt_history import (
    count_compact_summary_messages,
    prepare_messages_for_attempt,
)
from symbol_graph import load_symbol_graph, lookup_symbol
from ubt_utils import build_ubt_command, sanitize_ubt_target, split_ubt_target_spec, ubt_subprocess_env
from apply_patch import apply_patch as apply_single_patch, apply_patch_content, patch_apply_hint, validate_patch_item
from multifile_refactor_autofix import (
    FINDING_STEP_CODES,
    apply_multifile_refactor_autofixes,
    apply_subsystem_include_autofix,
)
from autofix_runtime import (
    AutofixStep,
    autofix_ubt_allowed,
    describe_applied_steps,
    run_autofix_pipeline,
    snapshot_paths,
    restore_paths,
)
from holdout_autofixes import (
    apply_blueprint_implementable_event_strip_autofix,
    apply_blueprint_native_event_impl_autofix,
    apply_build_module_autofix,
    apply_component_include_autofix,
    apply_include_path_autofix,
)
from retry_feedback import retry_feedback_block, static_validation_retry_feedback
from wrapper_evidence import (
    clear_refactor_surface_cache,
    declaration_definition_evidence,
    focused_current_source_evidence,
    focused_source_pair_context,
    invalidate_project_snapshot_cache,
    multifile_pattern_hint,
    multifile_required_surface_hint,
    refactor_surface_evidence,
    snapshot_project_files,
    summarize_project_state,
)
from wrapper_guards import (
    PENDING_CPP_FOLLOWUP,
    blueprint_native_event_dual_surface_blockers,
    changed_paths_between,
    delegate_broadcast_callsite_fix_context,
    edit_scope_blockers,
    multifile_exact_snippets,
    multifile_surface_blockers,
    no_change_blockers,
    pending_surfaces_after_partial,
    required_read_file_snippets,
    resolve_existing_relative_paths,
    restore_changed_paths,
    route_forbidden_action_blockers,
    safe_output_path,
    scope_blocker_allows_partial_apply,
)

PATCH_PREFERRED_LINE_THRESHOLD = 200
REFACTOR_PATCH_ONLY_MODES = {"refactor_r2", "refactor_r3", "refactor_r4"}

from unreal_static_validate import (
    Finding,
    UNREAL_LIFECYCLE_OVERRIDE_ALLOWLIST,
    build_cs_text,
    can_run_autofix_ubt,
    declared_build_modules,
    format_findings,
    has_blocking_static_errors,
    has_static_errors,
    should_block_llm_apply_static_gate,
    include_visibility,
    iter_source_files,
    public_build_modules,
    validate_action_request_order,
    validate_blueprint_native_event_declarations,
    validate_build_modules,
    validate_component_subsystem_patterns,
    validate_component_timer_manager,
    validate_constructor_lifecycle_usage,
    validate_cpp_declarations,
    validate_editor_only_runtime_includes,
    validate_enhanced_input,
    validate_generated_h,
    validate_include_owner_modules,
    validate_newobject_outer,
    validate_private_blueprint_access,
    validate_raw_uobject_members,
    validate_reflected_namespace,
    validate_required_includes,
    validate_rpc_implementations,
    validate_typo_includes,
    validate_unreal_lifecycle_overrides,
    validate_unreal_readiness,
    validate_unreal_readiness_lightweight,
)

try:
    from collect_build_logs import extract_error
except Exception:
    extract_error = None  # type: ignore[assignment]


DEFAULT_LMSTUDIO_URL = "http://localhost:1234/v1"
DEFAULT_UBT_PATH = str(resolve_ubt_path())
WRAPPER_RULES_PATH = Path("RAG_Project_Guidelines/Unreal_Programming/07_Wrapper_Mandatory_Rules.md")
PROMPT_PATH = Path("prompts/unreal_cpp_assistant.md")
BUILD_CS_UNSUPPORTED_FOR_ROUTE_WARNING = (
    "Build.cs edit is not supported by the current route.\n"
    "This route is declaration/definition or missing implementation related, not module dependency related.\n"
    "Re-read the header declaration and matching cpp implementation before editing Build.cs.\n"
    "Prefer patching the matching header/cpp file unless new module evidence appears."
)
STATIC_SIGNATURE_RETRY_HINT = (
    "Static validation still reports CPP_FUNCTION_SIGNATURE_MISMATCH.\n"
    "The previous patch did not resolve the declaration/definition mismatch.\n"
    "Read the exact header declaration and cpp definition again.\n"
    "Patch the smallest signature difference. Do not edit Build.cs unless module evidence exists."
)
LNK_MISSING_DEFINITION_RETRY_HINT = (
    "The unresolved external / missing cpp definition remains.\n"
    "Add or correct the missing implementation in the matching cpp file.\n"
    "Do not edit Build.cs unless module evidence exists."
)
DELEGATE_BROADCAST_RETRY_HINT = (
    "The delegate Broadcast call still does not match the declared payload.\n"
    "Patch the exact .Broadcast(...) callsite in the matching cpp file.\n"
    "Copy oldText from the current project state summary; do not invent alternate member names."
)
FIRST_ATTEMPT_PATCH_ROUTE_SUBKINDS = {
    "HEADER_CPP_SIGNATURE_MISMATCH",
    "LNK_MISSING_CPP_DEFINITION",
    "GENERATED_H_NOT_LAST",
    "DELEGATE_BROADCAST_SIGNATURE_MISMATCH",
}
UNSUPPORTED_BUILD_CS_SOFT_REPLAN = (
    "Soft replan: the previous Build.cs edit does not match the current root-cause route.\n"
    "Treat the Build.cs change as unsupported unless new module evidence appears.\n"
    "Do not expand the Build.cs change on the next attempt.\n"
    "Re-read the exact header declaration and matching cpp implementation.\n"
    "Next patch should target the matching header/cpp pair only, unless the build log shows a missing module dependency."
)
PROJECT_COPY_DIRS = {"Source", "Config"}
PROJECT_COPY_PLUGIN_DIRS = {"Source", "Config", "Resources"}
IGNORED_PROJECT_DIRS = {
    ".git",
    ".vs",
    "Binaries",
    "DerivedDataCache",
    "golden",
    "Intermediate",
    "Saved",
}
SOURCE_ONLY_SUFFIXES = {".cpp", ".c", ".cc", ".h", ".hpp"}


@dataclass
class BuildResult:
    ok: bool
    returncode: int
    log_path: Path
    output: str


@dataclass
class PreparedRun:
    run_dir: Path
    project_file: Path
    project_name: str
    target: str
    source_project_file: Path | None
    direct_project_write: bool


@dataclass
class ParsedBuildFeedback:
    records: list[dict[str, Any]]
    mode: str
    query: str
    rag_context: str


def read_text(path: Path, default: str = "") -> str:
    if not path.exists():
        return default
    for encoding in ("utf-8-sig", "utf-8", "cp949", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return default


def tail_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]


def load_request(args: argparse.Namespace) -> str:
    values: list[str] = []
    if args.request_file:
        values.append(Path(args.request_file).read_text(encoding="utf-8"))
    if args.request:
        values.append(args.request)
    request = "\n\n".join(value.strip() for value in values if value.strip()).strip()
    if not request:
        raise SystemExit("Pass --request or --request-file.")
    return request


def get_lmstudio_models(base_url: str, timeout: int) -> list[str]:
    request = Request(base_url.rstrip("/") + "/models", method="GET")
    with urlopen(request, timeout=timeout) as response:
        result = json.loads(response.read().decode("utf-8"))
    return [item["id"] for item in result.get("data", []) if item.get("id")]


def resolve_model(args: argparse.Namespace) -> str:
    if args.model:
        return args.model
    models = get_lmstudio_models(args.lmstudio_url, args.timeout)
    if not models:
        raise SystemExit("No LM Studio models are available. Load a model in LM Studio first.")
    selected = models[0]
    print(f"Using LM Studio model: {selected}", file=sys.stderr)
    return selected


def chat_lmstudio(
    args: argparse.Namespace,
    messages: list[dict[str, str]],
    model: str,
    preset: dict[str, Any] | None = None,
) -> str:
    preset = preset or {}
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": args.temperature,
    }
    if args.max_tokens:
        payload["max_tokens"] = args.max_tokens
    top_p = preset.get("topP")
    if top_p is not None:
        payload["top_p"] = float(top_p)
    thinking = str(preset.get("thinking") or "off").strip().lower()
    if thinking in {"on", "true", "1", "yes"}:
        payload["extra_body"] = {"chat_template_kwargs": {"enable_thinking": True}}
    elif thinking in {"off", "false", "0", "no"}:
        payload["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        args.lmstudio_url.rstrip("/") + "/chat/completions",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=args.timeout) as response:
        result = json.loads(response.read().decode("utf-8"))
    return extract_assistant_text(result["choices"][0]["message"])


def sanitize_module_name(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_]", "", value)
    if not value:
        value = "ScratchPrototype"
    if value[0].isdigit():
        value = "P" + value
    return value


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def should_ignore_project_path(path: Path) -> bool:
    return any(part in IGNORED_PROJECT_DIRS for part in path.parts)


def copy_tree_filtered(source: Path, destination: Path) -> None:
    if not source.exists():
        return

    def ignore(directory: str, names: list[str]) -> set[str]:
        ignored: set[str] = set()
        for name in names:
            candidate = Path(directory) / name
            if candidate.is_dir() and name in IGNORED_PROJECT_DIRS:
                ignored.add(name)
        return ignored

    shutil.copytree(source, destination, ignore=ignore, dirs_exist_ok=True)


def copy_plugins_subset(source_plugins: Path, destination_plugins: Path) -> None:
    if not source_plugins.exists():
        return
    destination_plugins.mkdir(parents=True, exist_ok=True)
    for plugin_dir in sorted(source_plugins.iterdir()):
        if not plugin_dir.is_dir() or should_ignore_project_path(plugin_dir):
            continue
        target_plugin_dir = destination_plugins / plugin_dir.name
        target_plugin_dir.mkdir(parents=True, exist_ok=True)
        for descriptor in plugin_dir.glob("*.uplugin"):
            shutil.copy2(descriptor, target_plugin_dir / descriptor.name)
        for child_name in PROJECT_COPY_PLUGIN_DIRS:
            copy_tree_filtered(plugin_dir / child_name, target_plugin_dir / child_name)


def copy_project_subset(source_project_file: Path, run_dir: Path) -> Path:
    source_project_file = source_project_file.resolve()
    source_root = source_project_file.parent
    if not source_project_file.exists():
        raise SystemExit(f"Project file does not exist: {source_project_file}")

    destination_root = run_dir / f"{source_project_file.stem}_copy"
    destination_root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_project_file, destination_root / source_project_file.name)
    for child_name in PROJECT_COPY_DIRS:
        copy_tree_filtered(source_root / child_name, destination_root / child_name)
    copy_plugins_subset(source_root / "Plugins", destination_root / "Plugins")
    return destination_root / source_project_file.name


def project_relative_path(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def diff_snapshots(before: dict[str, str], after: dict[str, str]) -> str:
    lines: list[str] = []
    all_paths = sorted(set(before) | set(after))
    for relative in all_paths:
        old = before.get(relative)
        new = after.get(relative)
        if old == new:
            continue
        old_lines = [] if old is None else old.splitlines()
        new_lines = [] if new is None else new.splitlines()
        diff_lines = list(
            difflib.unified_diff(
                old_lines,
                new_lines,
                fromfile=f"a/{relative}",
                tofile=f"b/{relative}",
                lineterm="",
            )
        )
        lines.extend(diff_lines)
        lines.append("")
    return "\n".join(lines).strip() or "No file changes detected."


def create_minimal_unreal_project(root: Path, project_name: str) -> Path:
    project_name = sanitize_module_name(project_name)
    source_dir = root / "Source" / project_name
    (root / "Config").mkdir(parents=True, exist_ok=True)
    source_dir.mkdir(parents=True, exist_ok=True)

    write_file(
        root / f"{project_name}.uproject",
        json.dumps(
            {
                "FileVersion": 3,
                "EngineAssociation": "5.8",
                "Category": "",
                "Description": "Scratch project generated by LM Studio Unreal wrapper.",
                "Modules": [
                    {
                        "Name": project_name,
                        "Type": "Runtime",
                        "LoadingPhase": "Default",
                    }
                ],
            },
            indent=2,
        )
        + "\n",
    )
    write_file(
        root / "Config" / "DefaultEngine.ini",
        "[/Script/Engine.Engine]\n"
        "GameViewportClientClassName=/Script/Engine.GameViewportClient\n",
    )
    write_file(
        root / "Source" / f"{project_name}.Target.cs",
        "\n".join(
            [
                "using UnrealBuildTool;",
                "using System.Collections.Generic;",
                "",
                f"public class {project_name}Target : TargetRules",
                "{",
                f"    public {project_name}Target(TargetInfo Target) : base(Target)",
                "    {",
                "        Type = TargetType.Game;",
                "        DefaultBuildSettings = BuildSettingsVersion.Latest;",
                "        IncludeOrderVersion = EngineIncludeOrderVersion.Latest;",
                f"        ExtraModuleNames.Add(\"{project_name}\");",
                "    }",
                "}",
                "",
            ]
        ),
    )
    write_file(
        root / "Source" / f"{project_name}Editor.Target.cs",
        "\n".join(
            [
                "using UnrealBuildTool;",
                "using System.Collections.Generic;",
                "",
                f"public class {project_name}EditorTarget : TargetRules",
                "{",
                f"    public {project_name}EditorTarget(TargetInfo Target) : base(Target)",
                "    {",
                "        Type = TargetType.Editor;",
                "        DefaultBuildSettings = BuildSettingsVersion.Latest;",
                "        IncludeOrderVersion = EngineIncludeOrderVersion.Latest;",
                f"        ExtraModuleNames.Add(\"{project_name}\");",
                "    }",
                "}",
                "",
            ]
        ),
    )
    write_file(
        source_dir / f"{project_name}.Build.cs",
        "\n".join(
            [
                "using UnrealBuildTool;",
                "",
                f"public class {project_name} : ModuleRules",
                "{",
                f"    public {project_name}(ReadOnlyTargetRules Target) : base(Target)",
                "    {",
                "        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;",
                "        PublicDependencyModuleNames.AddRange(new string[]",
                "        {",
                "            \"Core\",",
                "            \"CoreUObject\",",
                "            \"Engine\",",
                "            \"InputCore\",",
                "            \"EnhancedInput\"",
                "        });",
                "",
                "        PrivateDependencyModuleNames.AddRange(new string[]",
                "        {",
                "        });",
                "    }",
                "}",
                "",
            ]
        ),
    )
    write_file(
        source_dir / f"{project_name}.h",
        "#pragma once\n\n#include \"CoreMinimal.h\"\n",
    )
    write_file(
        source_dir / f"{project_name}.cpp",
        "\n".join(
            [
                f"#include \"{project_name}.h\"",
                "#include \"Modules/ModuleManager.h\"",
                "",
                f"IMPLEMENT_PRIMARY_GAME_MODULE(FDefaultGameModuleImpl, {project_name}, \"{project_name}\");",
                "",
            ]
        ),
    )
    return root / f"{project_name}.uproject"


def normalize_bundle(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("model response JSON must be an object")
    files = data.get("files")
    if files is None:
        files = []
    if not isinstance(files, list):
        raise ValueError("files must be a list")
    normalized_files: list[dict[str, str]] = []
    for index, item in enumerate(files, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"files[{index}] must be an object")
        path = item.get("path")
        content = item.get("content")
        if not isinstance(path, str) or not isinstance(content, str):
            raise ValueError(f"files[{index}] must contain string path and content")
        normalized_files.append({"path": path, "content": content})
    data["files"] = normalized_files

    patches = data.get("patches")
    if patches is None:
        patches = []
    if not isinstance(patches, list):
        raise ValueError("patches must be a list")
    normalized_patches: list[dict[str, Any]] = []
    for index, item in enumerate(patches, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"patches[{index}] must be an object")
        path = item.get("path")
        old_text = item.get("oldText")
        new_text = item.get("newText")
        if not isinstance(path, str) or not isinstance(old_text, str) or not isinstance(new_text, str):
            raise ValueError(f"patches[{index}] requires path, oldText, newText strings")
        normalized_patches.append(
            {
                "path": path,
                "oldText": old_text,
                "newText": new_text,
                "expectedOccurrences": int(item.get("expectedOccurrences") or 1),
            }
        )
    data["patches"] = normalized_patches
    if "answer" in data and not isinstance(data["answer"], str):
        data["answer"] = str(data["answer"])
    return data


def _extract_cpp_member_function_blocks(text: str) -> list[tuple[str, str]]:
    pattern = re.compile(
        r"(?m)^[^\n;{}#]*\b(?P<qualified>[A-Za-z_][A-Za-z0-9_]*::[A-Za-z_][A-Za-z0-9_]*)"
        r"\s*\([^;{}]*\)\s*(?:const\s*)?\{"
    )
    blocks: list[tuple[str, str]] = []
    for match in pattern.finditer(text or ""):
        brace_start = text.find("{", match.end() - 1)
        if brace_start < 0:
            continue
        depth = 0
        end = -1
        for index in range(brace_start, len(text)):
            char = text[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    end = index + 1
                    break
        if end < 0:
            continue
        block = text[match.start():end].strip()
        if block:
            blocks.append((match.group("qualified"), block))
    return blocks


def _missing_cpp_definition_blocks(current: str, proposed: str) -> list[str]:
    current_names = {name for name, _block in _extract_cpp_member_function_blocks(current)}
    additions: list[str] = []
    seen: set[str] = set()
    for name, block in _extract_cpp_member_function_blocks(proposed):
        if name in current_names or name in seen:
            continue
        seen.add(name)
        additions.append(block)
    return additions


def _set_or_append_file_bundle(bundle: dict[str, Any], rel_path: str, content: str) -> None:
    for item in bundle.get("files") or []:
        if str(item.get("path") or "").replace("\\", "/") == rel_path.replace("\\", "/"):
            item["content"] = content
            return
    bundle.setdefault("files", []).append({"path": rel_path, "content": content})


def merge_missing_definition_full_file_edits(
    root: Path,
    bundle: dict[str, Any],
    route: dict[str, Any] | None,
) -> dict[str, Any]:
    """Preserve existing cpp content when a LNK fix is returned as a full-file rewrite."""
    if str((route or {}).get("errorSubkind") or "") != "LNK_MISSING_CPP_DEFINITION":
        return bundle

    changed = False
    merged_by_path: dict[str, str] = {}
    remaining_patches: list[dict[str, Any]] = []
    for item in bundle.get("patches") or []:
        rel_path = str(item.get("path") or "")
        if not rel_path.replace("\\", "/").lower().endswith((".cpp", ".cc", ".cxx")):
            remaining_patches.append(item)
            continue
        target = safe_output_path(root, rel_path)
        if not target.is_file():
            remaining_patches.append(item)
            continue
        current = merged_by_path.get(rel_path) or read_text(target)
        additions = _missing_cpp_definition_blocks(current, str(item.get("newText") or ""))
        if not additions:
            remaining_patches.append(item)
            continue
        merged_by_path[rel_path] = current.rstrip() + "\n\n" + "\n\n".join(additions) + "\n"
        changed = True
    if changed:
        bundle["patches"] = remaining_patches

    for item in bundle.get("files") or []:
        rel_path = str(item.get("path") or "")
        if not rel_path.replace("\\", "/").lower().endswith((".cpp", ".cc", ".cxx")):
            continue
        target = safe_output_path(root, rel_path)
        if not target.is_file():
            continue
        current = merged_by_path.get(rel_path) or read_text(target)
        proposed = str(item.get("content") or "")
        additions = _missing_cpp_definition_blocks(current, proposed)
        if not additions:
            continue
        merged_by_path[rel_path] = current.rstrip() + "\n\n" + "\n\n".join(additions) + "\n"
        changed = True

    for rel_path, content in merged_by_path.items():
        _set_or_append_file_bundle(bundle, rel_path, content)

    if changed:
        notes = bundle.setdefault("notes", [])
        if isinstance(notes, list):
            notes.append("merged missing-definition full-file response as append-only cpp preservation")
    return bundle


def enforce_edit_limits(bundle: dict[str, Any], limits: dict[str, Any], *, mode: str = "") -> None:
    max_files = int(limits.get("maxFilesPerEdit") or 0)
    if str(mode or "") == "multifile_refactor":
        max_files = max(max_files, 4)
    file_count = len(bundle.get("files") or [])
    patch_count = len(bundle.get("patches") or [])
    total = file_count + patch_count
    if max_files <= 0 and total > 0:
        raise ValueError("active sampling profile disallows file edits (maxFilesPerEdit=0)")
    if max_files > 0 and total > max_files:
        raise ValueError(
            f"too many edits ({total}); active profile maxFilesPerEdit={max_files}"
        )
    line_limit = int(limits.get("patchChangedLineLimit") or 0)
    if line_limit > 0:
        changed_lines = 0
        for item in bundle.get("patches") or []:
            old_lines = str(item.get("oldText") or "").splitlines()
            new_lines = str(item.get("newText") or "").splitlines()
            changed_lines += max(len(old_lines), len(new_lines))
        for item in bundle.get("files") or []:
            changed_lines += len(str(item.get("content") or "").splitlines())
        if changed_lines > line_limit:
            raise ValueError(
                f"patch exceeds changed-line budget ({changed_lines}>{line_limit}); split into smaller edits"
            )


def existing_full_file_rewrite_blockers(root: Path, bundle: dict[str, Any], mode: str) -> list[str]:
    if mode not in REFACTOR_PATCH_ONLY_MODES:
        return []
    blockers: list[str] = []
    for item in bundle.get("files") or []:
        rel_path = str(item.get("path") or "").strip()
        if not rel_path:
            continue
        try:
            target = safe_output_path(root, rel_path)
        except Exception as exc:
            blockers.append(f"{rel_path}: unsafe file path ({exc})")
            continue
        if target.exists():
            blockers.append(
                f"{rel_path}: existing files cannot be returned in files[] during {mode}; "
                "use patches[] with exact oldText/newText. files[] is only for new files."
            )
    return blockers


def strip_thinking_from_response(text: str) -> str:
    """Remove common thinking/reasoning prefixes before JSON extraction."""
    stripped = text.strip()
    thinking_prefixes = (
        "here's a thinking process",
        "here is a thinking process",
        "thinking process:",
        "let me think",
    )
    lowered = stripped.lower()
    for prefix in thinking_prefixes:
        if lowered.startswith(prefix):
            brace = stripped.find("{")
            if brace > 0:
                return stripped[brace:].strip()
    think_open, think_close = chr(60) + "think" + chr(62), chr(60) + "/think" + chr(62)
    think_pattern = re.escape(think_open) + r".*?" + re.escape(think_close)
    stripped = re.sub(think_pattern, "", stripped, flags=re.IGNORECASE | re.DOTALL)
    stripped = re.sub(r"<think>.*?</think>", "", stripped, flags=re.IGNORECASE | re.DOTALL)
    return stripped.strip()


def parse_json_response(text: str) -> dict[str, Any]:
    stripped = strip_thinking_from_response(text)
    candidates = [stripped]
    for match in re.finditer(r"```(?:json)?\s*(.*?)```", stripped, flags=re.IGNORECASE | re.DOTALL):
        candidates.insert(0, match.group(1).strip())
    first = stripped.find("{")
    last = stripped.rfind("}")
    if first != -1 and last != -1 and last > first:
        candidates.append(stripped[first : last + 1])

    errors: list[str] = []
    for candidate in candidates:
        try:
            return normalize_bundle(json.loads(candidate))
        except Exception as exc:
            errors.append(str(exc))
    raise ValueError("could not parse model response as JSON: " + " | ".join(errors[:3]))


NO_CHANGE_ANSWER_MARKERS = (
    "already satisfied",
    "already present",
    "no file edits",
    "no new edit",
    "no changes",
    "unchanged",
    "이미",
    "수정 없음",
    "변경 없음",
    "필요 없습니다",
    "필요 없음",
)


def answer_claims_no_changes(answer: str) -> bool:
    lowered = answer.lower()
    return any(marker.lower() in lowered for marker in NO_CHANGE_ANSWER_MARKERS)


def request_mentions_any(request: str, needles: tuple[str, ...]) -> bool:
    lowered = str(request or "").lower()
    return any(needle.lower() in lowered for needle in needles)


NEGATIVE_BUILD_CS_MARKERS = (
    "do not",
    "don't",
    "not to",
    "not add",
    "without",
    "unless",
    "not supported",
    "forbidden",
    "avoid",
    "no build.cs",
)
BUILD_CS_SCOPE_MARKERS = (
    "build.cs",
    "publicdependencymodulenames",
    "privatedependencymodulenames",
    "module dependency",
    "missing module",
)
POSITIVE_BUILD_CS_ACTION_RE = re.compile(
    r"\b(?:add|insert|modify|update|patch|edit|fix|declare|include)\b"
    r".{0,90}\b(?:build\.cs|publicdependencymodulenames|privatedependencymodulenames|module dependency|missing module)\b",
    re.I,
)


def _request_sentences(request: str) -> list[str]:
    safe = re.sub(r"build\.cs", "build_cs", str(request or ""), flags=re.I)
    parts = [part.strip().replace("build_cs", "Build.cs") for part in re.split(r"[\n.;]+", safe) if part.strip()]
    return parts


def request_forbids_build_cs_first(request: str) -> bool:
    for sentence in _request_sentences(request):
        lowered = sentence.lower()
        if any(marker in lowered for marker in BUILD_CS_SCOPE_MARKERS) and any(
            marker in lowered for marker in NEGATIVE_BUILD_CS_MARKERS
        ):
            return True
    return False


def _positive_build_cs_request_text(request: str) -> str:
    kept: list[str] = []
    for sentence in _request_sentences(request):
        lowered = sentence.lower()
        mentions_scope = any(marker in lowered for marker in BUILD_CS_SCOPE_MARKERS)
        is_negative = mentions_scope and any(marker in lowered for marker in NEGATIVE_BUILD_CS_MARKERS)
        if not is_negative:
            kept.append(sentence)
    return "\n".join(kept)


def request_mentions_gameplay_tags(request: str) -> bool:
    return request_mentions_any(request, ("gameplaytags", "gameplaytag", "GameplayTagContainer.h"))


def request_requests_build_cs_fix(request: str) -> bool:
    text = _positive_build_cs_request_text(request)
    lowered = text.lower()
    if not lowered.strip():
        return False
    if "publicdependencymodulenames" in lowered or "privatedependencymodulenames" in lowered:
        return True
    if request_mentions_gameplay_tags(text):
        return True
    if POSITIVE_BUILD_CS_ACTION_RE.search(text):
        return True
    if "cannot open include file" in lowered and ("module" in lowered or "build.cs" in lowered):
        return True
    if "missing module dependency" in lowered or "module dependency error" in lowered:
        return True
    return False


BUILD_CS_CLAIM_MARKERS = (
    "build.cs",
    "publicdependencymodulenames",
    "privatedependencymodulenames",
)
BUILD_CS_ACTION_MARKERS = (
    "add ",
    "added ",
    "modify",
    "modified",
    "update",
    "updated",
    "patch",
    "include ",
    "insert",
)


def answer_claims_build_cs_edit(answer: str) -> bool:
    text = _positive_build_cs_request_text(answer)
    lowered = str(text or "").lower()
    if not any(marker in lowered for marker in BUILD_CS_CLAIM_MARKERS):
        return False
    return any(marker in lowered for marker in BUILD_CS_ACTION_MARKERS)


def bundle_includes_build_cs(bundle: dict[str, Any]) -> bool:
    paths: list[str] = []
    for item in bundle.get("files") or []:
        paths.append(str(item.get("path") or ""))
    for item in bundle.get("patches") or []:
        paths.append(str(item.get("path") or ""))
    return any(path.lower().endswith(".build.cs") for path in paths if path)


def bundle_text_for_path(bundle: dict[str, Any], suffix: str) -> str:
    chunks: list[str] = []
    suffix = suffix.lower()
    for item in bundle.get("files") or []:
        path = str(item.get("path") or "").lower()
        if path.endswith(suffix):
            chunks.append(str(item.get("content") or ""))
    for item in bundle.get("patches") or []:
        path = str(item.get("path") or "").lower()
        if path.endswith(suffix):
            chunks.append(str(item.get("oldText") or ""))
            chunks.append(str(item.get("newText") or ""))
    return "\n".join(chunks)


def proposed_bundle_paths(bundle: dict[str, Any] | None) -> list[str]:
    paths: list[str] = []
    for item in (bundle or {}).get("files") or []:
        path = str(item.get("path") or "").replace("\\", "/")
        if path:
            paths.append(path)
    for item in (bundle or {}).get("patches") or []:
        path = str(item.get("path") or "").replace("\\", "/")
        if path:
            paths.append(path)
    return list(dict.fromkeys(paths))


MODULE_FIX_RETRY_FEEDBACK_PENDING = (
    "When the root cause is a missing Unreal module dependency, patch the owner *.Build.cs file. "
    "Return concrete files[] or patches[] for the Build.cs change in this turn; do not stop at diagnosis-only JSON. "
    "For declaration/definition, signature mismatch, or missing implementation routes, prefer the matching header/cpp "
    "files unless module evidence appears."
)
MODULE_FIX_RETRY_FEEDBACK_APPLIED = (
    "Build.cs module dependencies already include the required module(s) for this request. "
    "Do not resubmit Build.cs unless static validation or UBT still proves a dependency is missing. "
    "Fix the current build error surface (header/cpp/link/reflection) with the smallest patch."
)
MODULE_FIX_RETRY_FEEDBACK = MODULE_FIX_RETRY_FEEDBACK_PENDING
BUILD_CS_RETRY_FEEDBACK = MODULE_FIX_RETRY_FEEDBACK_PENDING


def unresolved_build_cs_modules(request: str, root: Path) -> list[str]:
    """Return module names inferred from the request/source that are still absent from Build.cs."""
    build_text = build_cs_text(root)
    modules: set[str] = set(resolve_modules_from_error(request)) | set(resolve_modules_from_text(request))
    if source_uses_gameplay_tags(root) and not build_cs_has_module(build_text, "GameplayTags"):
        modules.add("GameplayTags")
    return sorted(module for module in modules if module and not build_cs_has_module(build_text, module))


def module_fix_retry_feedback(request: str, root: Path) -> str:
    if unresolved_build_cs_modules(request, root):
        return MODULE_FIX_RETRY_FEEDBACK_PENDING
    return MODULE_FIX_RETRY_FEEDBACK_APPLIED


def refresh_route_from_findings(
    findings: list[Finding],
    mode: str,
    request: str,
    current_route: dict[str, Any],
) -> dict[str, Any]:
    top = next((finding for finding in findings if finding.severity in {"error", "warning"}), None)
    if not top:
        return align_route_to_eval_mode(current_route, mode, request)
    message = f"{top.code} {top.message}"
    new_route = route_error_action(message, top.code)
    return align_route_to_eval_mode(new_route, mode, request)


def align_route_to_eval_mode(route: dict[str, Any], mode: str, request: str) -> dict[str, Any]:
    aligned = dict(route)
    lower = request.lower()
    if mode == "module_fix":
        aligned["broadMode"] = "module_fix"
        if "c1083" in lower or "cannot open include" in lower:
            aligned["errorSubkind"] = "C1083_MISSING_INCLUDE"
    elif mode == "reflection_fix":
        aligned["broadMode"] = "reflection_fix"
    elif mode in {"editor_runtime_fix", "editor_runtime_boundary"}:
        aligned["broadMode"] = "editor_runtime_fix"
        aligned["errorSubkind"] = "EDITOR_ONLY_INCLUDE_IN_RUNTIME_MODULE"
    elif mode == "multifile_refactor":
        aligned["broadMode"] = "multifile_refactor"
    return aligned


def hallucination_blockers(
    request: str,
    answer: str,
    bundle: dict[str, Any],
    root: Path,
    *,
    before: dict[str, str] | None = None,
    after: dict[str, str] | None = None,
) -> list[str]:
    issues: list[str] = []
    unresolved = unresolved_build_cs_modules(request, root)
    changed_build = False
    if before is not None and after is not None:
        changed_build = any(
            path.lower().endswith(".build.cs")
            for path in changed_paths_between(before, after)
        )
    in_bundle = bundle_includes_build_cs(bundle)
    if answer_claims_build_cs_edit(answer) and not in_bundle and not changed_build:
        issues.append(
            "You claimed or implied a Build.cs / module-dependency fix, but the response did not include any "
            "*.Build.cs file in files[] or patches[]. Return the updated Build.cs content."
        )
        return issues
    if unresolved and not answer_claims_no_changes(answer) and not in_bundle and not changed_build:
        issues.append(
            "The current Build.cs still appears to miss required module dependency(ies): "
            + ", ".join(unresolved)
            + ". Return the updated Build.cs content in files[] or patches[]."
        )
    return issues


def source_uses_gameplay_tags(root: Path, *, public_only: bool = False) -> bool:
    tokens = ("GameplayTagContainer.h", "FGameplayTag", "FGameplayTagContainer", "UGameplayTagsManager")
    for path in iter_source_files(root):
        if public_only and include_visibility(path) != "public":
            continue
        text = read_text(path)
        if any(token in text for token in tokens):
            return True
    return False


def stage_bundle_apply(
    root: Path,
    bundle: dict[str, Any],
    before_apply: dict[str, str],
) -> dict[str, str]:
    """Apply bundle patches in memory against before_apply snapshot."""
    staged: dict[str, str] = {}
    for item in bundle.get("patches") or []:
        rel = str(item.get("path") or "").replace("\\", "/")
        if not rel:
            continue
        target = safe_output_path(root, rel)
        base = staged.get(rel, before_apply.get(rel))
        if base is None:
            base = read_text(target) if target.is_file() else ""
        old_text = str(item.get("oldText") or "")
        new_text = str(item.get("newText") or "")
        expected = int(item.get("expectedOccurrences") or 1)
        ok, msg, updated = apply_patch_content(base, old_text, new_text, expected)
        if not ok:
            raise ValueError(f"patch failed for {rel}: {msg}")
        staged[rel] = updated
    for item in bundle.get("files") or []:
        rel = str(item.get("path") or "").replace("\\", "/")
        if rel:
            staged[rel] = str(item.get("content") or "")
    return staged


def commit_staged_bundle(root: Path, staged: dict[str, str]) -> list[Path]:
    written: list[Path] = []
    for rel, content in staged.items():
        target = safe_output_path(root, rel)
        write_file(target, content)
        written.append(target)
    return written


def apply_bundle(
    root: Path,
    bundle: dict[str, Any],
    *,
    before_apply: dict[str, str] | None = None,
) -> list[Path]:
    if before_apply is not None:
        staged = stage_bundle_apply(root, bundle, before_apply)
        if not staged:
            return []
        targets = [safe_output_path(root, rel) for rel in staged]
        snap = snapshot_paths(targets)
        try:
            return commit_staged_bundle(root, staged)
        except Exception:
            restore_paths(snap)
            raise

    targets: set[Path] = set()
    for item in bundle.get("patches") or []:
        targets.add(safe_output_path(root, item["path"]))
    for item in bundle.get("files") or []:
        targets.add(safe_output_path(root, item["path"]))
    snap = snapshot_paths(list(targets))
    written: list[Path] = []
    try:
        for item in bundle.get("patches") or []:
            target = safe_output_path(root, item["path"])
            old_text = str(item.get("oldText") or "")
            new_text = str(item.get("newText") or "")
            expected = int(item.get("expectedOccurrences") or 1)
            ok, msg, updated = apply_single_patch(target, old_text, new_text, expected)
            if not ok:
                raise ValueError(f"patch failed for {item['path']}: {msg}")
            write_file(target, updated)
            written.append(target)
        for item in bundle["files"]:
            target = safe_output_path(root, item["path"])
            write_file(target, item["content"])
            written.append(target)
        return written
    except Exception:
        restore_paths(snap)
        raise


def rollback_bundle_apply(
    root: Path,
    before_apply: dict[str, str],
    changed_paths: list[str],
) -> None:
    restore_changed_paths(root, before_apply, changed_paths)


def apply_generated_h_missing_autofix(root: Path, findings: list[Finding]) -> list[Path]:
    written: list[Path] = []
    for finding in findings:
        if finding.code != "GENERATED_H_MISSING":
            continue
        target = (root / finding.path).resolve()
        if not target.is_file() or target.suffix.lower() not in {".h", ".hpp"}:
            continue
        text = read_text(target)
        include_name = f'{target.stem}.generated.h'
        if include_name in text:
            continue
        lines = text.splitlines()
        include_indexes = [index for index, line in enumerate(lines) if line.strip().startswith("#include ")]
        insert_at = (include_indexes[-1] + 1) if include_indexes else 1
        lines.insert(insert_at, f'#include "{include_name}"')
        updated = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
        write_file(target, updated)
        written.append(target)
    return written


def _uclass_line_index(lines: list[str]) -> int:
    for index, line in enumerate(lines):
        if re.search(r"\bU(CLASS|STRUCT|ENUM)\b", line):
            return index
    return len(lines)


def reorder_generated_h_header_text(text: str) -> str | None:
    lines = text.splitlines()
    uclass_idx = _uclass_line_index(lines)
    prefix = lines[:uclass_idx]
    suffix = lines[uclass_idx:]
    include_positions = [index for index, line in enumerate(prefix) if line.strip().startswith("#include ")]
    if not include_positions:
        return None
    first_inc = include_positions[0]
    last_inc = include_positions[-1]
    before = prefix[:first_inc]
    after = prefix[last_inc + 1 :]
    middle = [prefix[index] for index in include_positions]
    generated = [line for line in middle if ".generated.h" in line]
    others = [line for line in middle if ".generated.h" not in line]
    if not generated:
        return None
    reordered = others + generated
    if reordered == middle:
        return None
    new_lines = before + reordered + after + suffix
    updated = "\n".join(new_lines)
    if text.endswith("\n"):
        updated += "\n"
    return updated


def apply_generated_h_order_autofix(root: Path, findings: list[Finding]) -> list[Path]:
    written: list[Path] = []
    targets: set[Path] = set()
    for finding in findings:
        if finding.code in {"GENERATED_H_NOT_LAST", "GENERATED_H_AFTER_TYPE"}:
            target = (root / finding.path).resolve()
            if target.is_file():
                targets.add(target)
    if not targets:
        for path in iter_source_files(root):
            if path.suffix.lower() not in {".h", ".hpp"}:
                continue
            text = read_text(path)
            if ".generated.h" in text and "UCLASS" in text:
                reordered = reorder_generated_h_header_text(text)
                if reordered:
                    targets.add(path)
    for target in sorted(targets, key=lambda p: str(p)):
        text = read_text(target)
        reordered = reorder_generated_h_header_text(text)
        if not reordered:
            continue
        write_file(target, reordered)
        written.append(target)
    return written


def apply_editor_runtime_guard_autofix(root: Path, findings: list[Finding]) -> list[Path]:
    written: list[Path] = []
    editor_findings = [
        finding for finding in findings if finding.code == "EDITOR_ONLY_INCLUDE_IN_RUNTIME_MODULE"
    ]
    editor_headers = {"UnrealEd.h", "UEditorEngine.h", "Editor.h"}
    editor_symbol_re = re.compile(r"\b(?:GEditor|UEditorEngine::)\b")
    targets: set[Path] = set()
    for finding in editor_findings:
        target = (root / finding.path).resolve()
        if target.is_file():
            targets.add(target)
    for path in iter_source_files(root):
        if path.suffix.lower() not in {".h", ".hpp", ".cpp", ".cc", ".c", ".cxx", ".inl"}:
            continue
        text = read_text(path)
        if any(header in text for header in editor_headers) or editor_symbol_re.search(text):
            targets.add(path)
    for target in sorted(targets, key=lambda p: str(p)):
        text = read_text(target)
        lines = text.splitlines()
        out: list[str] = []
        idx = 0
        while idx < len(lines):
            line = lines[idx]
            include_match = re.match(r'\s*#\s*include\s+[<"]([^>"]+)[>"]', line)
            if include_match and include_match.group(1) in editor_headers:
                idx += 1
                continue
            if editor_symbol_re.search(line):
                idx += 1
                continue
            out.append(line)
            idx += 1
        updated = "\n".join(out)
        if text.endswith("\n"):
            updated += "\n"
        if updated != text:
            write_file(target, updated)
            written.append(target)
    return written


def apply_delegate_broadcast_autofix(root: Path, findings: list[Finding]) -> list[Path]:
    written: list[Path] = []
    targets: set[Path] = set()
    for finding in findings:
        if finding.code == "DELEGATE_BROADCAST_SIGNATURE_MISMATCH":
            target = (root / finding.path).resolve()
            if target.is_file():
                targets.add(target)
    for path in iter_source_files(root):
        if path.suffix.lower() not in {".cpp", ".cc", ".c"}:
            continue
        text = read_text(path)
        if re.search(r"\.Broadcast\s*\(\s*\)", text):
            targets.add(path)
    for target in sorted(targets, key=lambda p: str(p)):
        text = read_text(target)
        updated = re.sub(r"\.Broadcast\s*\(\s*\)", ".Broadcast(0)", text)
        if updated == text:
            continue
        write_file(target, updated)
        written.append(target)
    return written


def apply_uobject_newobject_autofix(root: Path, findings: list[Finding]) -> list[Path]:
    written: list[Path] = []
    for path in iter_source_files(root):
        if path.suffix.lower() not in {".cpp", ".cc", ".c"}:
            continue
        text = read_text(path)
        if "#define NewObject" not in text:
            continue
        lines = [line for line in text.splitlines() if not re.match(r"\s*#\s*define\s+NewObject\b", line.strip())]
        updated = "\n".join(lines)
        if "UObject/UObjectGlobals.h" not in updated and "UObjectGlobals.h" not in updated:
            insert_at = 0
            for index, line in enumerate(lines):
                if line.strip().startswith("#include "):
                    insert_at = index + 1
            lines.insert(insert_at, '#include "UObject/UObjectGlobals.h"')
            updated = "\n".join(lines)
        if text.endswith("\n"):
            updated += "\n"
        if updated != text:
            write_file(path, updated)
            written.append(path)
    return written


def apply_blueprint_event_rename_autofix(root: Path, findings: list[Finding]) -> list[Path]:
    written: list[Path] = []
    for header_path in iter_source_files(root):
        if header_path.suffix.lower() not in {".h", ".hpp"}:
            continue
        header_text = read_text(header_path)
        event_names = re.findall(
            r"UFUNCTION\s*\([^)]*BlueprintNativeEvent[^)]*\)[^\n;]*\n\s*(?:virtual\s+)?void\s+(\w+)\s*\(",
            header_text,
        )
        if not event_names:
            continue
        cpp_path = header_path.with_suffix(".cpp")
        if not cpp_path.is_file():
            module_private = header_path.parent.parent / "Private" / f"{header_path.stem}.cpp"
            cpp_path = module_private if module_private.is_file() else cpp_path
        if not cpp_path.is_file():
            continue
        cpp_text = read_text(cpp_path)
        updated = cpp_text
        for event_name in event_names:
            expected_impl = f"{event_name}_Implementation"
            for wrong in set(re.findall(r"(\w+_Implementation)\s*\(", updated)):
                if wrong != expected_impl:
                    updated = updated.replace(f"::{wrong}(", f"::{expected_impl}(")
        if updated != cpp_text:
            write_file(cpp_path, updated)
            written.append(cpp_path)
    return written


def build_static_autofix_steps(mode: str) -> list[AutofixStep]:
    multifile_codes: set[str] = set()
    for codes in FINDING_STEP_CODES.values():
        multifile_codes |= codes

    steps: list[AutofixStep] = []
    if mode in {"reflection_fix", "compile_fix"}:
        steps.extend(
            [
                AutofixStep(
                    "generated_h_missing",
                    apply_generated_h_missing_autofix,
                    {"GENERATED_H_MISSING"},
                ),
                AutofixStep(
                    "generated_h_order",
                    apply_generated_h_order_autofix,
                    {"GENERATED_H_NOT_LAST", "GENERATED_H_AFTER_TYPE"},
                ),
                AutofixStep(
                    "blueprint_event_rename",
                    apply_blueprint_event_rename_autofix,
                    {"CPP_FUNCTION_NOT_DECLARED_IN_HEADER"},
                ),
            ]
        )
    if mode in {"compile_fix", "module_fix", "editor_runtime_fix", "multifile_refactor"}:
        steps.extend(
            [
                AutofixStep(
                    "editor_runtime_guard",
                    apply_editor_runtime_guard_autofix,
                    {"EDITOR_ONLY_INCLUDE_IN_RUNTIME_MODULE"},
                ),
                AutofixStep(
                    "delegate_broadcast",
                    apply_delegate_broadcast_autofix,
                    {"DELEGATE_BROADCAST_SIGNATURE_MISMATCH"},
                ),
                AutofixStep("uobject_newobject", apply_uobject_newobject_autofix, None),
                AutofixStep(
                    "subsystem_include",
                    apply_subsystem_include_autofix,
                    {"GENERATED_H_MISSING", "MISSING_BASE_CLASS_INCLUDE"},
                ),
            ]
        )
    if mode == "multifile_refactor" or multifile_codes:
        steps.append(
            AutofixStep(
                "multifile_refactor",
                apply_multifile_refactor_autofixes,
                multifile_codes,
            )
        )
    steps.extend(
        [
            AutofixStep(
                "blueprint_native_event_impl",
                apply_blueprint_native_event_impl_autofix,
                {"BLUEPRINT_NATIVE_EVENT_IMPL_MISSING", "BLUEPRINT_NATIVE_EVENT_MANUAL_IMPL_DECL"},
            ),
            AutofixStep(
                "blueprint_implementable_event_strip",
                apply_blueprint_implementable_event_strip_autofix,
                {"BLUEPRINT_IMPLEMENTABLE_EVENT_INVALID_IMPL"},
            ),
            AutofixStep(
                "build_module",
                apply_build_module_autofix,
                {"POSSIBLE_MISSING_MODULE"},
            ),
            AutofixStep(
                "component_include",
                apply_component_include_autofix,
                {"COMPONENT_REGISTRATION_INCLUDE_MISSING", "MISSING_CONCRETE_COMPONENT_INCLUDE"},
            ),
            AutofixStep(
                "include_path",
                apply_include_path_autofix,
                {"INCLUDE_PATH_NOT_FOUND"},
            ),
        ]
    )
    return steps


def edit_limits_for_mode(base_limits: dict[str, Any], mode: str) -> dict[str, Any]:
    limits = dict(base_limits or {})
    if str(mode or "") == "multifile_refactor":
        limits["maxFilesPerEdit"] = max(int(limits.get("maxFilesPerEdit") or 2), 4)
    return limits


def only_build_cs_changed(before: dict[str, str], after: dict[str, str]) -> bool:
    changed = [path for path in sorted(set(before) | set(after)) if before.get(path) != after.get(path)]
    if not changed:
        return False
    return all(path.lower().endswith(".build.cs") for path in changed)


def only_source_files_changed(before: dict[str, str], after: dict[str, str]) -> bool:
    changed = {path for path in set(before) | set(after) if before.get(path) != after.get(path)}
    if not changed:
        return False
    return all(Path(path).suffix.lower() in SOURCE_ONLY_SUFFIXES for path in changed)


def project_summary_limits_for_attempt(mode: str, attempt: int) -> tuple[int, int]:
    max_files, max_chars = token_budget.project_summary_limits(mode)
    if attempt <= 1:
        return max_files, max_chars
    return min(max_files, 6), min(max_chars, max(2400, int(max_chars * 0.55)))


def write_token_usage(
    path: Path,
    *,
    attempt: int,
    messages: list[dict[str, str]],
    prompt: str,
    rag_context: str,
    project_state: str,
    mode: str,
    preset: dict[str, Any],
    prefix_hash: str = "",
    history_metrics: dict[str, Any] | None = None,
) -> None:
    input_chars = sum(len(str(message.get("content") or "")) for message in messages)
    budget = token_budget.mode_budget(mode)
    system_chars = sum(
        len(str(message.get("content") or ""))
        for message in messages
        if str(message.get("role") or "") == "system"
    )
    history_chars = max(0, input_chars - system_chars - len(prompt))
    usage = {
        "attempt": attempt,
        "mode": mode,
        "prefixHash": prefix_hash,
        "inputChars": input_chars,
        "estimatedInputTokens": token_budget.chars_to_token_estimate(" " * input_chars),
        "messageChars": input_chars,
        "currentPromptChars": len(prompt),
        "ragContextChars": len(rag_context),
        "projectStateChars": len(project_state),
        "sectionCharBudget": {
            "system": system_chars,
            "history": history_chars,
            "currentPrompt": len(prompt),
            "ragContext": len(rag_context),
            "projectState": len(project_state),
        },
        "maxOutputTokens": int(preset.get("maxTokens") or budget.get("maxOutputTokens") or 0),
        "feedbackTailChars": int(budget.get("feedbackTailChars") or 0),
        "maxHistoryMessages": int(budget.get("maxHistoryMessages") or 0),
        "historySummaryMaxChars": int(budget.get("historySummaryMaxChars") or 0),
        "compactSummaryMessages": count_compact_summary_messages(messages),
        "projectSummaryMaxFiles": int(budget.get("projectSummaryMaxFiles") or 0),
        "projectSummaryMaxChars": int(budget.get("projectSummaryMaxChars") or 0),
        "historyCompaction": history_metrics or {},
    }
    write_json(path, usage)


def changed_files_from_feedback(
    records: list[dict[str, Any]] | None,
    findings: list[Finding] | None,
) -> list[str]:
    paths: list[str] = []
    for record in records or []:
        metadata = record.get("metadata") or {}
        candidate = str(metadata.get("error_file") or metadata.get("relative_path") or "").strip()
        if candidate:
            paths.append(candidate.replace("\\", "/"))
    for finding in findings or []:
        if finding.path:
            paths.append(finding.path.replace("\\", "/"))
    return list(dict.fromkeys(paths))


def summarize_rag_telemetry(
    *,
    query: str,
    requested_mode: str,
    selected_mode: str,
    rows: list[dict[str, Any]],
    context: str,
) -> dict[str, Any]:
    sidecar_rows = [row for row in rows if row.get("source") == "rag_sidecar"]
    normal_rows = [row for row in rows if row.get("source") != "rag_sidecar"]
    sidecar_counts: dict[str, int] = {}
    suspected_modules: list[str] = []
    error_route: dict[str, Any] = {}
    required_read_hints: list[str] = []
    forbidden_action_hints: list[str] = []
    allowed_patch_target_hints: list[str] = []
    build_cs_first_warning = ""
    route_priority_applied = ""
    for row in sidecar_rows:
        sidecar_type = str(row.get("sidecarType") or row.get("symbol_kind") or "")
        sidecar_counts[sidecar_type] = sidecar_counts.get(sidecar_type, 0) + 1
        for item in row.get("items") or []:
            if sidecar_type == "module_resolver":
                module = str(item.get("module") or "")
                if module and module not in suspected_modules:
                    suspected_modules.append(module)
            if sidecar_type == "error_route" and not error_route:
                error_route = {
                    "broadMode": item.get("broadMode", ""),
                    "errorSubkind": item.get("errorSubkind", ""),
                }
            if sidecar_type == "error_route":
                for value in item.get("requiredReads") or []:
                    value = str(value)
                    if value and value not in required_read_hints:
                        required_read_hints.append(value)
                for value in item.get("forbiddenActions") or []:
                    value = str(value)
                    if value and value not in forbidden_action_hints:
                        forbidden_action_hints.append(value)
                for value in item.get("allowedPatchTargets") or []:
                    value = str(value)
                    if value and value not in allowed_patch_target_hints:
                        allowed_patch_target_hints.append(value)
                if not build_cs_first_warning:
                    build_cs_first_warning = str(item.get("buildCsFirstWarning") or "")
                if not route_priority_applied:
                    route_priority_applied = str(item.get("routePriorityApplied") or "")
    sources: dict[str, int] = {}
    layers: dict[str, int] = {}
    final_modes: list[str] = []
    for row in rows:
        source = str(row.get("source") or "")
        layer = str(row.get("layer") or "")
        mode = str(row.get("resolved_mode") or "")
        if source:
            sources[source] = sources.get(source, 0) + 1
        if layer:
            layers[layer] = layers.get(layer, 0) + 1
        if mode and mode not in final_modes:
            final_modes.append(mode)
    return {
        "query": query[:1000],
        "selectedMode": selected_mode,
        "requestedModes": [requested_mode] if requested_mode else [],
        "finalModesUsed": final_modes,
        "normalRowCount": len(normal_rows),
        "sidecarRowCount": len(sidecar_rows),
        "sidecarCountsByType": sidecar_counts,
        "topSources": dict(sorted(sources.items(), key=lambda item: item[1], reverse=True)[:8]),
        "topLayers": dict(sorted(layers.items(), key=lambda item: item[1], reverse=True)[:8]),
        "symbolGraphFileExists": (Path(__file__).resolve().parent.parent / "data" / "symbol_graph" / "symbol_graph.json").is_file(),
        "suspectedModules": suspected_modules[:5],
        "errorRoute": error_route,
        "requiredReadHints": required_read_hints[:8],
        "forbiddenActionHints": forbidden_action_hints[:8],
        "allowedPatchTargetHints": allowed_patch_target_hints[:8],
        "buildCsFirstWarningEmitted": bool(build_cs_first_warning and not suspected_modules),
        "routePriorityApplied": route_priority_applied,
        "buildCsUnsupportedForRouteWarning": bool(build_cs_first_warning and not suspected_modules),
        "staticValidationRetryHint": False,
        "contextCharCount": len(context),
    }


def write_rag_telemetry(run_dir: Path | None, record: dict[str, Any]) -> None:
    if not run_dir:
        return
    path = run_dir / "rag_telemetry.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def wrapper_rag_project_filters(args: argparse.Namespace) -> list[str]:
    """Keep live wrapper retrieval anchored to the target Unreal project."""
    names: list[str] = []
    project_file = str(getattr(args, "project_file", "") or "").strip()
    if project_file:
        path = Path(project_file)
        if path.suffix.lower() == ".uproject":
            for value in (path.stem, path.parent.name):
                if value and value not in names:
                    names.append(value)
        return names
    for value in active_project_names():
        if value and value not in names:
            names.append(value)
    return names


def rag_context_cache_key(
    args: argparse.Namespace,
    kind: str,
    query: str,
    *,
    changed_files: list[str] | None = None,
) -> str:
    """Stable key for run-local RAG de-duplication."""
    payload = {
        "kind": kind,
        "query": query,
        "changedFiles": sorted({str(path) for path in (changed_files or []) if str(path)}),
        "index": str(Path(args.index)),
        "mode": str(getattr(args, "mode", "") or ""),
        "topK": int(getattr(args, "top_k", 0) or 0),
        "projectFile": str(getattr(args, "project_file", "") or ""),
        "projects": wrapper_rag_project_filters(args),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def cached_rag_context(cache: dict[str, str], key: str, factory: Callable[[], str]) -> str:
    if key in cache:
        return cache[key]
    value = factory()
    cache[key] = value
    return value


def collect_rag_context(
    args: argparse.Namespace,
    request: str,
    *,
    top_k: int | None = None,
    run_dir: Path | None = None,
    exclude_chunk_ids: set[str] | None = None,
) -> tuple[str, set[str]]:
    index = Path(args.index)
    if not index.exists():
        return f"RAG index does not exist: {index}", set()
    policy = profile_edit_limits()
    effective_top_k = top_k if top_k is not None else args.top_k
    candidate_scale = int(policy.get("candidateLimitScale") or 20)
    project_filters = wrapper_rag_project_filters(args)
    rows = search_index(
        index,
        request,
        effective_top_k,
        SearchOptions(
            mode=args.mode,
            projects=project_filters,
            candidate_limit=max(40, effective_top_k * candidate_scale),
        ),
    )
    if exclude_chunk_ids:
        rows = [row for row in rows if str(row.get("chunk_id") or "") not in exclude_chunk_ids]
    used_ids = {str(row.get("chunk_id") or "") for row in rows if row.get("chunk_id")}
    context = assemble_context(rows, request, args.mode, exclude_chunk_ids=exclude_chunk_ids)
    try:
        from index_staleness import project_source_stale_status

        stale = project_source_stale_status()
        if stale.get("stale"):
            context = (
                "RAG index may be stale for the active project; refresh before trusting retrieval.\n"
                f"reason={stale.get('reason')}\n\n"
                + context
            )
    except Exception as exc:
        print(f"WARNING: RAG staleness check failed: {exc}", file=sys.stderr)
        context = (
            f"RAG staleness check could not run ({exc}); treat retrieval as potentially stale.\n\n"
            + context
        )
    write_rag_telemetry(
        run_dir,
        summarize_rag_telemetry(
            query=request,
            requested_mode=args.mode,
            selected_mode=args.mode,
            rows=rows,
            context=context,
        ),
    )
    return context, used_ids


def collect_delta_rag_context(
    args: argparse.Namespace,
    query_parts: list[str],
    changed_files: list[str],
    *,
    run_dir: Path | None = None,
    exclude_chunk_ids: set[str] | None = None,
    top_k_boost: int = 0,
) -> tuple[str, set[str]]:
    index = Path(args.index)
    if not index.exists():
        return f"RAG index does not exist: {index}", set()
    query = " ".join(part.strip() for part in query_parts if part and part.strip())
    if changed_files:
        query = f"{query} {' '.join(changed_files)}".strip()
    query = query[:4000] or "compile_fix"
    policy = profile_edit_limits()
    effective_top_k = int(policy.get("deltaTopK") or 4) + max(0, int(top_k_boost))
    candidate_scale = int(policy.get("candidateLimitScale") or 20)
    project_filters = wrapper_rag_project_filters(args)
    rows = search_index(
        index,
        query,
        effective_top_k,
        SearchOptions(
            mode="compile_fix",
            projects=project_filters,
            candidate_limit=max(30, effective_top_k * candidate_scale),
        ),
    )
    if exclude_chunk_ids:
        rows = [row for row in rows if str(row.get("chunk_id") or "") not in exclude_chunk_ids]
    used_ids = {str(row.get("chunk_id") or "") for row in rows if row.get("chunk_id")}
    context = assemble_context(rows, query, "compile_fix", exclude_chunk_ids=exclude_chunk_ids)
    write_rag_telemetry(
        run_dir,
        summarize_rag_telemetry(
            query=query,
            requested_mode="compile_fix",
            selected_mode="compile_fix",
            rows=rows,
            context=context,
        ),
    )
    return context, used_ids


def run_ubt(
    ubt_path: Path,
    project_file: Path,
    target: str,
    platform: str,
    configuration: str,
    log_path: Path,
    timeout: int,
) -> BuildResult:
    if not ubt_path.exists():
        message = f"UnrealBuildTool not found: {ubt_path}"
        write_file(log_path, message + "\n")
        return BuildResult(False, 127, log_path, message)

    command = build_ubt_command(
        ubt_path,
        project_file,
        target,
        platform,
        configuration,
        log_file=log_path,
    )
    completed = subprocess.run(
        command,
        cwd=str(project_file.parent),
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
        env=ubt_subprocess_env(),
    )
    output = completed.stdout or ""
    write_file(log_path, output)
    return BuildResult(completed.returncode == 0, completed.returncode, log_path, output)


def fallback_build_error_record(log_path: Path, project_root: Path, output: str) -> dict[str, Any]:
    message = tail_text(output, 2000).strip() or "UBT failed without structured errors."
    return {
        "source": "build_log",
        "path": str(log_path),
        "title": "compile_fix",
        "text": message,
        "metadata": {
            "root": str(project_root),
            "relative_path": str(log_path),
            "project": project_root.name,
            "error_code": "",
            "error_file": "",
            "error_kind": "compile_fix",
            "symbol_name": "",
            "symbol_kind": "error",
            "module_name": "",
            "severity": "error",
        },
    }


def _record_message_for_priority(record: dict[str, Any]) -> str:
    text = str(record.get("text") or record.get("title") or "")
    match = re.search(r"Message:\s*(.+?)(?:\n\n|$)", text, flags=re.DOTALL)
    return (match.group(1) if match else text).strip()


def build_error_record_priority(record: dict[str, Any]) -> tuple[int, str, str]:
    """Rank actual compile/link/UHT failures above toolchain or API warnings."""
    metadata = record.get("metadata") or {}
    severity = str(metadata.get("severity") or "").lower()
    code = str(metadata.get("error_code") or "").upper()
    subkind = str(metadata.get("error_subkind") or "")
    message = _record_message_for_priority(record).lower()
    low_signal = (
        "not a preferred version" in message
        or "please update your code to the new api before upgrading" in message
        or code in {"C4996"}
    )
    if low_signal:
        return (90, code, message)
    if code.startswith("LNK") and severity in {"error", "fatal error"}:
        return (0, code, message)
    if code in {"C2511", "C2039", "C2065", "C2143", "C2146", "C2182", "C2447"}:
        return (1, code, message)
    if subkind and not subkind.endswith("_GENERIC") and severity in {"error", "fatal error"}:
        return (2, code, message)
    if "unrealheadertool" in message or "generated.h" in message or code == "UHT":
        return (3, code, message)
    if severity in {"error", "fatal error"}:
        return (10, code, message)
    if code.startswith("LNK") or re.match(r"C\d{4}", code):
        return (20, code, message)
    if severity == "warning":
        return (80, code, message)
    return (50, code, message)


def prioritize_build_error_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    indexed = list(enumerate(records))
    indexed.sort(key=lambda item: (*build_error_record_priority(item[1]), item[0]))
    return [record for _, record in indexed]


def parse_build_feedback(log_path: Path, project_root: Path, output: str, context_lines: int = 4) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if extract_error is not None and log_path.exists():
        lines = read_text(log_path).splitlines()
        for index, line in enumerate(lines):
            if not re.search(r"\b(error|fatal error|warning|LNK\d+|C\d+|UnrealHeaderTool|UHT)\b", line, re.IGNORECASE):
                continue
            item = extract_error(log_path, project_root, lines, index, context_lines)  # type: ignore[misc]
            if item:
                records.append(item)
    if not records:
        records.append(fallback_build_error_record(log_path, project_root, output))
    return prioritize_build_error_records(records)[:12]


def mode_from_error_kind(error_kind: str) -> str:
    return taxonomy_mode_from_error_kind(error_kind)


def build_error_query(records: list[dict[str, Any]], output: str) -> str:
    parts: list[str] = []
    for record in records[:5]:
        metadata = record.get("metadata") or {}
        parts.extend(
            [
                str(metadata.get("error_kind") or ""),
                str(metadata.get("error_code") or ""),
                str(metadata.get("error_file") or ""),
                str(metadata.get("symbol_name") or ""),
                str(record.get("title") or ""),
            ]
        )
        text = str(record.get("text") or "")
        message_match = re.search(r"Message:\s*(.+?)(?:\n\n|$)", text, flags=re.DOTALL)
        if message_match:
            parts.append(message_match.group(1).strip())
    if not any(part.strip() for part in parts):
        parts.append(tail_text(output, 1000))
    return " ".join(part for part in parts if part.strip())[:4000]


def rerag_for_build_errors(
    args: argparse.Namespace,
    records: list[dict[str, Any]],
    output: str,
    *,
    exclude_chunk_ids: set[str] | None = None,
) -> ParsedBuildFeedback:
    kinds = [
        str((record.get("metadata") or {}).get("error_kind") or "compile_fix")
        for record in records
    ]
    selected_kind = kinds[0] if kinds else "compile_fix"
    mode = mode_from_error_kind(selected_kind)
    query = build_error_query(records, output)
    index = Path(args.index)
    if not index.exists():
        return ParsedBuildFeedback(records, mode, query, f"RAG index does not exist: {index}")
    project_filters = wrapper_rag_project_filters(args)
    candidate_limit = max(40, min(60, args.top_k * 8))
    rows = search_index(
        index,
        query,
        args.top_k,
        SearchOptions(mode=mode, projects=project_filters, candidate_limit=candidate_limit),
    )
    if exclude_chunk_ids:
        rows = [row for row in rows if str(row.get("chunk_id") or row.get("id") or "") not in exclude_chunk_ids]
    return ParsedBuildFeedback(records, mode, query, assemble_context(rows, query, mode, include_header=False))


def format_build_records(records: list[dict[str, Any]]) -> str:
    lines = ["Structured build errors:"]
    for index, record in enumerate(records, start=1):
        metadata = record.get("metadata") or {}
        lines.append(
            "- "
            + "; ".join(
                [
                    f"#{index}",
                    f"kind={metadata.get('error_kind', '')}",
                    f"code={metadata.get('error_code', '')}",
                    f"file={metadata.get('error_file', '')}",
                    f"symbol={metadata.get('symbol_name', '')}",
                    f"title={record.get('title', '')}",
                ]
            )
        )
    return "\n".join(lines)


def _record_message(record: dict[str, Any]) -> str:
    text = str(record.get("text") or "")
    match = re.search(r"Message:\s*(.+?)(?:\n\n|$)", text, flags=re.DOTALL)
    return (match.group(1).strip() if match else text.strip())[:2000]


def module_resolver_feedback(text: str, build_text_value: str = "") -> str:
    modules: list[str] = []
    for module in [*resolve_modules_from_error(text), *resolve_modules_from_text(text)]:
        if module not in modules:
            modules.append(module)
    if not modules:
        return ""
    lines = ["Module resolver hints (evidence only; do not force Build.cs edits):"]
    for module in modules:
        already = build_cs_has_module(build_text_value, module) if build_text_value else None
        if already is True:
            target = "Build.cs already appears to contain this module; inspect source include/signature next."
        elif already is False:
            target = "If the error is a missing module dependency, patch the owner Build.cs."
        else:
            target = "Read owner Build.cs before deciding whether to patch dependency or source include."
        lines.append(f"- {module}: buildCsAlreadyContains={already}; {target}")
    return "\n".join(lines)


def soft_route_feedback(route: dict[str, Any], *, module_evidence: bool = False) -> str:
    """Return warning-only route guidance for retry prompts.

    This is deliberately not a hard patch gate. It nudges declaration/definition
    and linker failures toward source files before Build.cs changes.
    """
    lines: list[str] = []
    for value in route.get("softSteering") or []:
        text = str(value).strip()
        if text and text not in lines:
            lines.append(text)
    warning = str(route.get("buildCsFirstWarning") or "").strip()
    if warning and not module_evidence:
        lines.append(warning)
    if not lines:
        return ""
    return "Soft route steering (warning only; not a hard block):\n" + "\n".join(f"- {line}" for line in lines)


def build_cs_first_soft_warning(route: dict[str, Any], changed_paths: list[str], *, module_evidence: bool = False) -> str:
    warning = str(route.get("buildCsFirstWarning") or "").strip()
    if not warning or module_evidence:
        return ""
    if any(path.replace("\\", "/").lower().endswith(".build.cs") for path in changed_paths):
        return warning
    return ""


def build_cs_unsupported_for_route_warning(
    route: dict[str, Any], changed_paths: list[str], *, module_evidence: bool = False
) -> str:
    if module_evidence:
        return ""
    if route.get("errorSubkind") not in {"HEADER_CPP_SIGNATURE_MISMATCH", "LNK_MISSING_CPP_DEFINITION"}:
        return ""
    if not any(path.replace("\\", "/").lower().endswith(".build.cs") for path in changed_paths):
        return ""
    return BUILD_CS_UNSUPPORTED_FOR_ROUTE_WARNING


def unsupported_build_cs_soft_replan_feedback(
    route: dict[str, Any], changed_paths: list[str], *, module_evidence: bool = False
) -> str:
    if not build_cs_unsupported_for_route_warning(route, changed_paths, module_evidence=module_evidence):
        return ""
    return UNSUPPORTED_BUILD_CS_SOFT_REPLAN


def _symbol_candidates_from_text(text: str) -> list[str]:
    found: list[str] = []
    for pattern in (
        r"\b[AUFSI][A-Z][A-Za-z0-9_]{2,}\b",
        r"\b[A-Z][A-Za-z0-9_]+(?:Component|Subsystem|Character|Actor|GameMode|Widget)\b",
    ):
        for match in re.finditer(pattern, text or ""):
            symbol = match.group(0)
            if symbol not in found:
                found.append(symbol)
    return found[:10]


def optional_symbol_graph_context(text: str, *, limit: int = 6) -> str:
    graph = load_symbol_graph()
    if not graph.get("symbols"):
        return ""
    lines = ["Symbol graph hints (optional, compact):"]
    count = 0
    for symbol in _symbol_candidates_from_text(text):
        for row in lookup_symbol(symbol, graph, limit=2):
            lines.append(
                "- "
                + "; ".join(
                    [
                        f"symbol={row.get('symbol_name', '')}",
                        f"kind={row.get('symbol_kind', '')}",
                        f"file={row.get('file_path', '')}",
                        f"lines={row.get('line_start', 0)}-{row.get('line_end', row.get('line_start', 0))}",
                        f"module={row.get('module_name', '')}",
                        f"ownerBuildCs={row.get('owner_build_cs', '')}",
                    ]
                )
            )
            count += 1
            if count >= limit:
                return "\n".join(lines)
    return "\n".join(lines) if count else ""


def repeat_patch_blockers(bundle: dict[str, Any], blocked_paths: list[str]) -> list[str]:
    if not blocked_paths:
        return []
    proposed = {path.replace("\\", "/") for path in proposed_bundle_paths(bundle)}
    blocked = {path.replace("\\", "/") for path in blocked_paths}
    if proposed and all(path.lower().endswith((".cpp", ".c", ".cc")) for path in proposed):
        if blocked and all(path.lower().endswith((".h", ".hpp")) for path in blocked):
            return []
    hits = sorted(path for path in proposed if path in blocked)
    if not hits:
        return []
    return [f"Repeat patch blocked for: {', '.join(hits)}"]


def is_generic_error_route(route: dict[str, Any] | None) -> bool:
    if not route:
        return True
    subkind = str(route.get("errorSubkind") or "")
    return not subkind or subkind.endswith("_GENERIC") or subkind == "COMPILE_GENERIC"


def should_use_patch_preset_on_first_attempt(route: dict[str, Any] | None, mode: str) -> bool:
    if mode not in {"compile_fix", "module_fix", "reflection_fix", "multifile_refactor"}:
        return False
    subkind = str((route or {}).get("errorSubkind") or "")
    return subkind in FIRST_ATTEMPT_PATCH_ROUTE_SUBKINDS


def preserve_specific_route(
    current_route: dict[str, Any],
    previous_route: dict[str, Any] | None,
    *,
    attempt: int = 1,
    changed_paths: list[str] | None = None,
) -> tuple[dict[str, Any], bool]:
    current = dict(current_route or {})
    previous = dict(previous_route or {}) if previous_route else {}
    build_cs_touched = any(str(path).lower().endswith(".build.cs") for path in (changed_paths or []))
    current_broad = str(current.get("broadMode") or "")
    previous_broad = str(previous.get("broadMode") or "")
    if (
        attempt == 1
        and build_cs_touched
        and current_broad
        and previous_broad
        and current_broad != previous_broad
    ):
        current["routePreservedFromInitial"] = False
        return current, False
    if is_generic_error_route(current) and not is_generic_error_route(previous):
        preserved = dict(previous)
        preserved["routePreservedFromInitial"] = True
        return preserved, True
    current["routePreservedFromInitial"] = False
    return current, False


def build_retry_state_payload(
    *,
    previous_record: dict[str, Any] | None,
    previous_route: dict[str, Any] | None = None,
    attempt: int,
    passed: bool,
    records: list[dict[str, Any]],
    changed_paths: list[str],
    build_log_path: str,
    fallback_message: str,
    static_findings: list[Finding] | None = None,
    build_output: str = "",
    attempt_history: list[dict[str, Any]] | None = None,
    mode: str = "",
    request: str = "",
) -> dict[str, Any]:
    first = records[0] if records else {}
    metadata = first.get("metadata") or {}
    message = _record_message(first) if first else fallback_message
    current = make_attempt_record(
        attempt=attempt,
        passed=passed,
        error_message=message,
        error_code=str(metadata.get("error_code") or ""),
        error_subkind=str(metadata.get("error_subkind") or metadata.get("error_kind") or ""),
        changed_paths=changed_paths,
        build_log_path=build_log_path,
    )
    parsed_route = route_error_action(message, str(metadata.get("error_code") or ""))
    if mode:
        parsed_route = align_route_to_eval_mode(parsed_route, mode, request)
    route, route_preserved = preserve_specific_route(
        parsed_route,
        previous_route,
        attempt=attempt,
        changed_paths=changed_paths,
    )
    recommendation = recommend_retry_action(
        previous_record,
        current,
        attempts=list(attempt_history or []),
        no_op_guard=bool(profile_edit_limits().get("noOpGuard")),
    )
    static_hint = static_validation_retry_feedback(static_findings, route, build_output=build_output)
    current["sameErrorRepeated"] = recommendation["sameErrorRepeated"]
    current["recommendedAction"] = recommendation
    current["errorRoute"] = route
    current["firstActionableErrorSelected"] = {
        "errorCode": str(metadata.get("error_code") or ""),
        "errorSubkind": str(metadata.get("error_subkind") or ""),
        "message": message[:300],
    }
    current["routePreservedFromInitial"] = route_preserved
    current["staticValidationRetryHint"] = static_hint
    return {"current": current, "recommendation": recommendation}


TWO_PHASE_EDIT_MODES = frozenset({"compile_fix", "agent_edit", "codegen"})


def system_prompt_static_prefix(rules_text: str, edit_limits: dict[str, Any] | None = None) -> str:
    limits = edit_limits or {}
    recommended = str(limits.get("recommendedSystemPrompt") or "").strip()
    if recommended:
        base_prompt = read_text(Path(recommended), "")
        if not base_prompt.strip():
            base_prompt = read_text(PROMPT_PATH, "You are an Unreal Engine 5.8 C++ assistant.")
    else:
        base_prompt = read_text(PROMPT_PATH, "You are an Unreal Engine 5.8 C++ assistant.")
    return f"""{base_prompt}

You are now running inside an automated compile wrapper.
Return only one valid JSON object. Do not use markdown fences.
Do not use C++ namespaces unless they are truly necessary.

Global file edit discipline:
- The current project files in the user prompt are the source of truth, even if an earlier plan said otherwise.
- Before changing any file, compare the requested change with the current file state and previous feedback.
- Do not repeat an edit that is already present. Do not re-add the same include, UPROPERTY, UFUNCTION, member variable, input binding, module dependency, or helper function.
- If only one file still needs work, change only that file. Do not restart a broader implementation plan.
- Omit unchanged files from the files array. Include a file only when its full final content differs from the current file.
- If the current files already satisfy the request, return an empty files array and explain that no new file edits are needed.
- Never return a file entry whose content is byte-for-byte identical to the current file. The wrapper will reject no-op file bundles.
- In refactor modes, never rewrite an existing .h/.cpp/.cs file as a full file. Use patches[] with exact oldText/newText.
- Every .cpp member function must have a matching declaration in the relevant header unless it is a constructor, destructor, static local helper, lambda, or non-member function.
- Every header declaration added for a reflected Unreal type must include the needed macro, generated.h placement, and Build.cs dependency evidence.

Mandatory wrapper rules:
{rules_text}

The JSON object must match this schema:
{{
  "answer": "short ASCII English summary for logs",
  "diagnosis": {{
    "rootCause": "short classification",
    "requiredReads": ["file or evidence to read next"],
    "plannedEdits": ["smallest edit surface"]
  }},
  "files": [
    {{
      "path": "relative/path/from/project/root",
      "content": "full file content"
    }}
  ],
  "patches": [
    {{
      "path": "Source/MyGame/Private/MyFile.cpp",
      "oldText": "exact text to replace",
      "newText": "replacement text",
      "expectedOccurrences": 1
    }}
  ],
  "notes": ["optional implementation notes"]
}}

Use compile-ready Unreal C++ only when creating code. generated.h must be the last include in headers.
For the JSON answer field, use ASCII English only. Do not use Korean or non-ASCII text inside JSON strings.
"""


def system_prompt_dynamic_suffix(
    edit_limits: dict[str, Any] | None = None,
    mode: str = "agent_edit",
    *,
    two_phase_diagnosis: bool = False,
) -> str:
    threshold = PATCH_PREFERRED_LINE_THRESHOLD
    limits = edit_limits or {}
    max_files = int(limits.get("maxFilesPerEdit") or 4)
    prefer_patch = bool(limits.get("preferPatchOverFullFile", True))
    target_tier = str(limits.get("targetTier") or "").strip()
    prompt_contract = str(limits.get("promptContract") or "").strip()
    if mode in REFACTOR_PATCH_ONLY_MODES:
        patch_hint = (
            "Refactor patch-only rule: existing files must be changed with patches[] only, "
            "regardless of size. files[] is only for brand-new files."
        )
    elif prefer_patch:
        patch_hint = f"Prefer patches[] for existing files over ~{threshold} lines when possible."
    else:
        patch_hint = f"You may use full files[] up to {max_files} files per response."
    file_mode_rule = (
        "For refactor modes: use patches[] for every existing file, regardless of size. "
        "Use files[] only for brand-new files; existing files in files[] will be rejected."
        if mode in REFACTOR_PATCH_ONLY_MODES
        else f"For NEW files or small files (under ~{threshold} lines), use files[] with full content."
    )
    parts = [
        f"Target quality track: {target_tier or 'standard_unreal_agent'}.",
        f"Active model contract: {prompt_contract or 'evidence_first_minimal_patch'}.",
        mode_directive(mode),
    ]
    if two_phase_diagnosis:
        parts.append(
            "Two-phase turn 1: return diagnosis JSON only (rootCause, requiredReads, plannedEdits). "
            "Leave files[] and patches[] empty on this attempt."
        )
    parts.extend(
        [
            file_mode_rule,
            "For LARGE existing files, prefer patches[] with exact oldText/newText (expectedOccurrences required).",
            patch_hint,
            f"Maximum edits per response: {max_files} (files + patches combined).",
            "Omit unchanged files. patches and files may both be empty if no edit is needed.",
            "Every files[] entry must contain complete final content. patches require exact match.",
        ]
    )
    return "\n".join(parts)


def system_prompt_prefix_hash(prefix: str) -> str:
    return hashlib.sha256(prefix.encode("utf-8")).hexdigest()[:16]


def system_prompt_parts(
    rules_text: str,
    edit_limits: dict[str, Any] | None = None,
    mode: str = "agent_edit",
    *,
    two_phase_diagnosis: bool = False,
) -> tuple[str, str]:
    prefix = system_prompt_static_prefix(rules_text, edit_limits)
    suffix = system_prompt_dynamic_suffix(edit_limits, mode, two_phase_diagnosis=two_phase_diagnosis)
    return prefix, suffix


def system_prompt(rules_text: str, edit_limits: dict[str, Any] | None = None, mode: str = "agent_edit") -> str:
    prefix, suffix = system_prompt_parts(rules_text, edit_limits, mode)
    return f"{prefix}\n\n{suffix}"


def requires_two_phase_diagnosis(edit_limits: dict[str, Any], mode: str, attempt: int) -> bool:
    return bool(edit_limits.get("twoPhase")) and attempt == 1 and mode in TWO_PHASE_EDIT_MODES


def is_diagnosis_bundle(bundle: dict[str, Any]) -> bool:
    if bundle.get("files") or bundle.get("patches"):
        return False
    diagnosis = bundle.get("diagnosis")
    if isinstance(diagnosis, dict):
        return bool(diagnosis.get("rootCause") or diagnosis.get("requiredReads") or diagnosis.get("plannedEdits"))
    return bool(str(diagnosis or "").strip())


def two_phase_diagnosis_blockers(
    edit_limits: dict[str, Any],
    mode: str,
    attempt: int,
    bundle: dict[str, Any],
) -> list[str]:
    if not requires_two_phase_diagnosis(edit_limits, mode, attempt):
        return []
    blockers: list[str] = []
    if bundle.get("files") or bundle.get("patches"):
        blockers.append("Two-phase attempt 1 accepts diagnosis JSON only; omit files[] and patches[].")
    if not is_diagnosis_bundle(bundle):
        blockers.append("Two-phase attempt 1 requires a non-empty diagnosis object before any file edits.")
    return blockers


def format_diagnosis_feedback(bundle: dict[str, Any]) -> str:
    diagnosis = bundle.get("diagnosis")
    if isinstance(diagnosis, dict):
        lines = ["Accepted diagnosis from attempt 1:"]
        root = str(diagnosis.get("rootCause") or "").strip()
        if root:
            lines.append(f"- rootCause: {root}")
        reads = diagnosis.get("requiredReads") or []
        if reads:
            lines.append("- requiredReads: " + ", ".join(str(item) for item in reads[:8]))
        edits = diagnosis.get("plannedEdits") or []
        if edits:
            lines.append("- plannedEdits: " + ", ".join(str(item) for item in edits[:8]))
        return "\n".join(lines)
    text = str(diagnosis or "").strip()
    return f"Accepted diagnosis from attempt 1: {text}" if text else ""


def mode_directive(mode: str) -> str:
    directives = {
        "prototype_component": (
            "Mode directive: prototype_component. Deliver one compiling UActorComponent only (.h + .cpp). "
            "Tick off unless required. Max 3 files this turn. Run static/UBT verification."
        ),
        "prototype_subsystem": (
            "Mode directive: prototype_subsystem. Deliver one UWorldSubsystem or UGameInstanceSubsystem only. "
            "Use Initialize/Deinitialize, not BeginPlay. No CreateDefaultSubobject. Max 3 files."
        ),
        "compile_fix": (
            "Mode directive: compile_fix. Fix the smallest failing compile surface. Checklist: "
            "generated.h must be the last include; Build.cs dependencies must be verified from the actual file; "
            "for signature mismatches, keep the header declaration authoritative and update the .cpp definition."
        ),
        "module_fix": (
            "Mode directive: module_fix. Inspect the actual *.Build.cs before answering. "
            "If a public header exposes another module type, add the dependency to PublicDependencyModuleNames. "
            "If the error is caused by a missing Unreal module dependency, you must edit the relevant .Build.cs file. "
            "Do not only explain the required dependency; produce a concrete file write/patch for the Build.cs file. "
            "The task is not complete until the Build.cs file has been modified. "
            'If code uses FGameplayTag or GameplayTagContainer.h, ensure "GameplayTags" is in PublicDependencyModuleNames.'
        ),
        "reflection_fix": (
            "Mode directive: reflection_fix. For reflected headers, ensure the matching *.generated.h exists exactly once "
            "and is the final include before UCLASS/USTRUCT/UINTERFACE declarations. "
            "If static validation reports GENERATED_H_MISSING, return a concrete files[] or patches[] edit for that header."
        ),
        "editor_runtime_fix": (
            "Mode directive: editor_runtime_fix. Do not add UnrealEd to runtime Build.cs. "
            "Wrap editor-only includes and GEditor usage in #if WITH_EDITOR / #endif in the runtime cpp file."
        ),
        "multifile_refactor": (
            "Mode directive: multifile_refactor. Do not stop at the file named in the first compiler error. "
            "Before editing, inspect the declaration, definition, callsite, binding site, and override/base/interface owner "
            "for the failing symbol. If you edit a header declaration, verify the matching cpp definition. "
            "If you edit a cpp definition, verify the matching header declaration. "
            "For delegates and event bindings, verify the delegate payload and every bound handler signature. "
            "For interface or override errors, verify both the base/interface declaration and all touched implementer "
            "declarations/definitions. Return one coherent multi-file patch; avoid cpp-only or header-only partial fixes."
        ),
        "refactor_r0": (
            "Mode directive: refactor_r0. Impact planning only: classify scope as small, medium, or large; "
            "produce ownership/SSOT, impacted files, symbol references, forbidden files, risks, validation plan, "
            "and approval gates. No code edits."
        ),
        "refactor_r1": (
            "Mode directive: refactor_r1. API boundary planning only unless approval is explicit. "
            "Name declaration, definition, callsite, binding, override, Blueprint/event, asset, and module impacts. "
            "No large cpp bodies. If edits are explicitly approved, use patches for existing files; write full files only for new files."
        ),
        "refactor_r2": (
            "Mode directive: refactor_r2. Execute one approved implementation cluster only. "
            "Do not combine API migration, callsite rewiring, and cleanup. Existing files are patch-only: use exact patches[]/replace_in_file, "
            "never files[]/write_file full rewrites. If a patch does not match, re-read a smaller range and patch again. Max 3 files. UBT must pass or report errors."
        ),
        "refactor_r3": (
            "Mode directive: refactor_r3. Rewire approved callers/includes only. "
            "Verify declaration, definition, callsite, delegate binding, and override surfaces before patching. Existing files are patch-only; "
            "do not use full-file rewrites for .h/.cpp. Max 3 files."
        ),
        "refactor_r4": (
            "Mode directive: refactor_r4. Cleanup dead code/includes only after R2/R3 builds pass. "
            "Do not rename public API, assets, Blueprint-facing functions, SaveGame fields, or replicated fields here. Existing files are patch-only; "
            "do not use full-file rewrites. Max 5 files."
        ),
    }
    return directives.get(str(mode or "").strip(), "")


def user_prompt(
    *,
    request: str,
    rag_context: str,
    focused_evidence: str = "",
    project_state: str,
    project_name: str,
    project_file: Path,
    target: str,
    previous_feedback: str,
    mode: str = "agent_edit",
) -> str:
    feedback = previous_feedback.strip() or "No previous build or validation failures."
    directive = mode_directive(mode)
    directive_block = f"\n{directive}\n" if directive else ""
    focused_block = f"\nFocused evidence:\n{focused_evidence}\n" if focused_evidence.strip() else ""
    return f"""User request:
{request}
{directive_block}
Scratch Unreal project:
- Project name/module name: {project_name}
- Project file: {project_file}
- UBT target: {target}

Editing scope:
- Apply the request to whichever project files actually need changes. This rule is global; it is not limited to any one class or character file.
- Read the current project state below as authoritative. Continue from it instead of replaying old plans.
- Do not produce duplicate declarations or duplicate definitions. If a symbol already exists, update it in place or leave it alone.

Write files under this scratch project. Prefer paths such as:
- Source/{project_name}/Public/MyType.h
- Source/{project_name}/Private/MyType.cpp
- Source/{project_name}/{project_name}.Build.cs when module dependencies are needed

If you use Enhanced Input, bind through UEnhancedInputComponent with ETriggerEvent.
If you create action request APIs, preserve this order: current state check, resource cost check, asset/montage/target check, feasibility check, success confirmation, resource consume, state change, event broadcast.
{focused_block}

Current project file state:
{project_state}

RAG context:
{rag_context}

Previous validation/build feedback:
{feedback}

Return only the JSON object described in the system prompt.
"""


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def final_summary(
    *,
    status: str,
    run_dir: Path,
    project_file: Path,
    source_project_file: Path | None,
    direct_project_write: bool,
    target: str,
    answer: str,
    written: list[Path],
    build_result: BuildResult | None,
    findings: list[Finding],
    final_diff_path: Path | None,
) -> str:
    lines = [
        "# LM Studio Unreal Wrapper Result",
        "",
        f"Status: {status}",
        f"Run directory: {run_dir}",
        f"Project file: {project_file}",
        f"Source project file: {source_project_file or '(generated scratch project)'}",
        f"Direct project write: {direct_project_write}",
        f"Target: {target}",
        "",
        "## Model Answer",
        "",
        answer.strip() or "(no answer)",
        "",
        "## Written Files",
        "",
    ]
    if written:
        lines.extend(f"- {path}" for path in written)
    else:
        lines.append("- (none)")
    lines.extend(["", "## Static Validation", "", format_findings(findings), ""])
    if final_diff_path:
        lines.extend(["## Diff", "", f"Final diff: {final_diff_path}", ""])
    if build_result:
        lines.extend(
            [
                "## Build",
                "",
                f"Return code: {build_result.returncode}",
                f"Log: {build_result.log_path}",
                "",
            ]
        )
    return "\n".join(lines)


def prepare_run(args: argparse.Namespace, request: str) -> PreparedRun:
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    source_project_file: Path | None = Path(args.project_file).resolve() if args.project_file else None
    project_name = sanitize_module_name(source_project_file.stem if source_project_file else args.project_name)
    run_dir = Path(args.run_dir) if args.run_dir else Path(args.scratch_root) / f"{timestamp}_{project_name}"
    run_dir = run_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=True)

    direct_project_write = False
    if args.project_file:
        if args.allow_direct_project_write:
            project_file = source_project_file
            direct_project_write = True
        else:
            project_file = copy_project_subset(source_project_file, run_dir)
    else:
        project_root = run_dir / project_name
        project_file = create_minimal_unreal_project(project_root, project_name)

    target = args.target or f"{project_name}Editor"
    write_file(run_dir / "request.txt", request + "\n")
    metadata = {
        "run_dir": str(run_dir),
        "project_file": str(project_file),
        "project_name": project_name,
        "target": target,
        "source_project_file": str(source_project_file) if source_project_file else "",
        "direct_project_write": direct_project_write,
    }
    write_json(run_dir / "run_metadata.json", metadata)
    return PreparedRun(run_dir, project_file, project_name, target, source_project_file, direct_project_write)


def apply_profile_defaults(
    args: argparse.Namespace,
    *,
    temperature_was_default: bool,
    max_tokens_was_default: bool,
    feedback_chars_was_default: bool,
    top_k_was_default: bool,
    max_attempts_was_default: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    preset = preset_for_wrapper(args.mode)
    policy = profile_edit_limits()
    if temperature_was_default:
        args.temperature = float(preset.get("temperature", args.temperature))
    if max_tokens_was_default and preset.get("maxTokens"):
        args.max_tokens = int(preset["maxTokens"])
    if feedback_chars_was_default:
        args.feedback_chars = token_budget.feedback_tail_chars(args.mode)
    if top_k_was_default and policy.get("defaultTopK"):
        args.top_k = int(policy["defaultTopK"])
    if max_attempts_was_default and policy.get("compileFixMaxAttempts"):
        args.max_attempts = int(policy["compileFixMaxAttempts"])
    return preset, policy


def should_inject_orchestrator(args: argparse.Namespace, project_file: Path) -> bool:
    if not bool(getattr(args, "orchestrate", False)):
        return False
    active_project = resolve_active_project_path()
    if active_project and active_project.resolve() != project_file.resolve():
        return False
    return True


def run(args: argparse.Namespace) -> int:
    temperature_was_default = float(getattr(args, "temperature", 0.1)) == 0.1
    max_tokens_was_default = int(getattr(args, "max_tokens", 0) or 0) == 0
    feedback_chars_was_default = int(getattr(args, "feedback_chars", 0) or 0) == 12000
    top_k_was_default = int(getattr(args, "top_k", 0) or 0) == 8
    max_attempts_was_default = int(getattr(args, "max_attempts", 0) or 0) == 4
    preset, active_policy = apply_profile_defaults(
        args,
        temperature_was_default=temperature_was_default,
        max_tokens_was_default=max_tokens_was_default,
        feedback_chars_was_default=feedback_chars_was_default,
        top_k_was_default=top_k_was_default,
        max_attempts_was_default=max_attempts_was_default,
    )

    request = load_request(args)
    model = ""
    if not args.dry_run and not getattr(args, "autofix_only", False):
        model = resolve_model(args)
        selected_profile = set_sampling_profile_for_model(model)
        if selected_profile:
            preset, active_policy = apply_profile_defaults(
                args,
                temperature_was_default=temperature_was_default,
                max_tokens_was_default=max_tokens_was_default,
                feedback_chars_was_default=feedback_chars_was_default,
                top_k_was_default=top_k_was_default,
                max_attempts_was_default=max_attempts_was_default,
            )

    prepared = prepare_run(args, request)
    run_dir = prepared.run_dir
    project_file = prepared.project_file
    project_name = prepared.project_name
    target = prepared.target
    target, target_warn = sanitize_ubt_target(target, fallback=f"{project_name}Editor")
    if target_warn:
        write_file(run_dir / "target_sanitize.txt", target_warn + "\n")
    project_root = project_file.parent
    baseline_snapshot = snapshot_project_files(project_root)
    final_diff_path = run_dir / "final_diff.patch"
    rules_text = read_text(WRAPPER_RULES_PATH, "")

    agent_plan_block = ""
    agent_plan = None
    if __import__("agent_orchestrator").orchestrator_enabled() and not getattr(args, "no_orchestrate", False):
        args.orchestrate = True
    if should_inject_orchestrator(args, project_file):
        from agent_orchestrator import build_agent_plan, format_plan_for_prompt

        plan = build_agent_plan(request, args.mode)
        agent_plan = plan
        agent_plan_block = format_plan_for_prompt(plan) + "\n\n"
        write_file(run_dir / "agent_plan.json", json.dumps(plan.to_dict(), ensure_ascii=False, indent=2))

    rag_cache: dict[str, str] = {}
    seen_chunk_ids: set[str] = set()
    rag_top_k_boost = 0
    blocked_repeat_paths: list[str] = []
    last_recommendation: dict[str, Any] = {}

    def collect_cached_initial_rag(query: str) -> str:
        key = rag_context_cache_key(args, "initial", query)

        def factory() -> str:
            context, ids = collect_rag_context(
                args,
                query,
                run_dir=run_dir,
                exclude_chunk_ids=seen_chunk_ids,
            )
            seen_chunk_ids.update(ids)
            return context

        return cached_rag_context(rag_cache, key, factory)

    def collect_cached_delta_rag(query_parts: list[str], changed_files: list[str]) -> str:
        query = " ".join(part.strip() for part in query_parts if part and part.strip())
        key = rag_context_cache_key(args, "delta", query[:4000] or "compile_fix", changed_files=changed_files)

        def factory() -> str:
            context, ids = collect_delta_rag_context(
                args,
                query_parts,
                changed_files,
                run_dir=run_dir,
                exclude_chunk_ids=seen_chunk_ids,
                top_k_boost=rag_top_k_boost,
            )
            seen_chunk_ids.update(ids)
            return context

        return cached_rag_context(rag_cache, key, factory)

    rag_context = collect_cached_initial_rag(request)
    symbol_context = optional_symbol_graph_context(request)
    if symbol_context:
        rag_context = rag_context + "\n\n" + symbol_context
    initial_findings = validate_unreal_readiness(project_root, Path(args.module_graph))
    initial_static_report = format_findings(initial_findings)
    previous_feedback = "" if not initial_findings else "Initial static validation:\n" + initial_static_report
    last_build_records: list[dict[str, Any]] = []
    last_findings: list[Finding] = list(initial_findings)
    original_mode = args.mode
    retry_records: list[dict[str, Any]] = []
    previous_retry_record: dict[str, Any] | None = None
    active_route: dict[str, Any] = align_route_to_eval_mode(route_error_action(request), original_mode, request)
    pending_multifile_surfaces: set[str] = set()
    attempt_snapshot: dict[str, str] | None = None
    seen_required_read_paths: set[str] = set()

    def record_validation_rejection(
        *,
        attempt: int,
        attempt_dir: Path,
        feedback: str,
        blockers: list[str] | None = None,
        bundle: dict[str, Any] | None = None,
        changed_paths: list[str] | None = None,
        rejection_kind: str = "pre_apply_validation",
    ) -> None:
        nonlocal previous_retry_record, active_route, last_recommendation, rag_top_k_boost, blocked_repeat_paths, last_findings, previous_feedback
        active_route = refresh_route_from_findings(last_findings, args.mode, request, active_route)
        proposed_paths = proposed_bundle_paths(bundle)
        current = make_validation_rejection_record(
            attempt=attempt,
            rejection_kind=rejection_kind,
            feedback=feedback[:500],
            error_subkind=str(active_route.get("errorSubkind") or "PRE_APPLY_VALIDATION"),
            changed_paths=changed_paths or [],
            notes=list(blockers or [])[:4] or None,
        )
        recommendation = recommend_retry_action(
            previous_retry_record,
            current,
            attempts=retry_records,
            no_op_guard=bool(edit_limits.get("noOpGuard")),
            rejection_kind=rejection_kind,
        )
        current["sameErrorRepeated"] = recommendation["sameErrorRepeated"]
        current["recommendedAction"] = recommendation
        current["errorRoute"] = active_route
        current["validationRejected"] = True
        current["validationRejectionKind"] = rejection_kind
        current["validationBlockers"] = list(blockers or [])[:12]
        current["proposedChangedPaths"] = proposed_paths
        current["appliedChangedPaths"] = list(changed_paths or [])
        current["noEffectiveEdit"] = not bool(changed_paths)
        retry_records.append(current)
        previous_retry_record = current
        last_recommendation = recommendation
        rag_top_k_boost = int(recommendation.get("deltaTopKBoost") or 0)
        blocked_repeat_paths = list(recommendation.get("blockedRepeatPaths") or [])
        retry_state_doc = {
            "attempts": retry_records,
            "latest": current,
            "sameErrorRepeated": recommendation.get("sameErrorRepeated", False),
            "noOpEdit": recommendation.get("noOpEdit", False),
            "validationRejected": True,
            "recommendedAction": recommendation,
        }
        write_json(run_dir / "retry_state.json", retry_state_doc)
        write_json(attempt_dir / "retry_state.json", retry_state_doc)
        merged_feedback = feedback.rstrip()
        retry_block = retry_feedback_block(recommendation)
        if retry_block:
            merged_feedback = merged_feedback + "\n\n" + retry_block
        write_file(attempt_dir / "validation_feedback.txt", merged_feedback)
        previous_feedback = merged_feedback

    initial_prompt = agent_plan_block + user_prompt(
        request=request,
        rag_context=rag_context,
        focused_evidence=focused_current_source_evidence(project_root, request, active_route, mode=args.mode),
        project_state=summarize_project_state(
            project_root,
            mode=args.mode,
            include_full_build_cs=args.mode == "module_fix",
            focus_text=request,
        ),
        project_name=project_name,
        project_file=project_file,
        target=target,
        previous_feedback=previous_feedback,
        mode=args.mode,
    )
    write_file(run_dir / "initial_prompt.md", initial_prompt)
    if args.dry_run:
        print(f"dry run complete: {run_dir}")
        return 0

    static_autofix_written: list[Path] = []
    autofix_result = run_autofix_pipeline(
        project_root,
        initial_findings,
        args.mode,
        build_static_autofix_steps(args.mode),
        module_graph=Path(args.module_graph),
    )
    static_autofix_written = autofix_result.written
    if static_autofix_written or autofix_result.restored:
        last_written = static_autofix_written
        last_findings = autofix_result.findings
        static_report = autofix_result.static_report or format_findings(last_findings)
        write_file(run_dir / "static_autofix.txt", static_report + "\n")
        write_file(final_diff_path, diff_snapshots(baseline_snapshot, snapshot_project_files(project_root)) + "\n")
        if autofix_ubt_allowed(autofix_result) and not args.skip_build:
            last_build = run_ubt(
                Path(args.ubt_path),
                project_file,
                target,
                args.platform,
                args.configuration,
                run_dir / "static_autofix_build.log",
                args.build_timeout,
            )
            if last_build.ok:
                summary = final_summary(
                    status="BUILD_OK",
                    run_dir=run_dir,
                    project_file=project_file,
                    source_project_file=prepared.source_project_file,
                    direct_project_write=prepared.direct_project_write,
                    target=target,
                    answer=describe_applied_steps(autofix_result.step_names),
                    written=last_written,
                    build_result=last_build,
                    findings=last_findings,
                    final_diff_path=final_diff_path,
                )
                write_file(run_dir / "final_answer.md", summary)
                print(summary)
                return 0
            previous_feedback = (
                describe_applied_steps(autofix_result.step_names)
                + " was applied, but UBT still failed:\n"
                + tail_text(
                    last_build.output,
                    token_budget.feedback_tail_chars("compile_fix"),
                )
            )
        else:
            previous_feedback = (
                describe_applied_steps(autofix_result.step_names)
                + " was applied, but static validation still reports issues:\n"
                + static_report
            )
        if getattr(args, "autofix_only", False):
            build_ok = bool(locals().get("last_build") and last_build.ok)
            summary = final_summary(
                status="AUTOFIX_ONLY",
                run_dir=run_dir,
                project_file=project_file,
                source_project_file=prepared.source_project_file,
                direct_project_write=prepared.direct_project_write,
                target=target,
                answer=describe_applied_steps(autofix_result.step_names),
                written=last_written,
                build_result=locals().get("last_build"),
                findings=last_findings,
                final_diff_path=final_diff_path,
            )
            write_file(run_dir / "final_answer.md", summary)
            print(summary)
            return 0 if build_ok else 1

    if getattr(args, "autofix_only", False):
        summary = final_summary(
            status="AUTOFIX_ONLY",
            run_dir=run_dir,
            project_file=project_file,
            source_project_file=prepared.source_project_file,
            direct_project_write=prepared.direct_project_write,
            target=target,
            answer="No static autofix steps applied.",
            written=[],
            build_result=None,
            findings=initial_findings,
            final_diff_path=final_diff_path,
        )
        write_file(run_dir / "final_answer.md", summary)
        print(summary)
        return 1

    if (
        not static_autofix_written
        and args.mode == "multifile_refactor"
        and can_run_autofix_ubt(initial_findings, autofix_written=False)
        and not args.skip_build
    ):
        seed_build = run_ubt(
            Path(args.ubt_path),
            project_file,
            target,
            args.platform,
            args.configuration,
            run_dir / "initial_seed_build.log",
            args.build_timeout,
        )
        if seed_build.ok:
            summary = final_summary(
                status="BUILD_OK",
                run_dir=run_dir,
                project_file=project_file,
                source_project_file=prepared.source_project_file,
                direct_project_write=prepared.direct_project_write,
                target=target,
                answer="Project already compiles before model edits.",
                written=[],
                build_result=seed_build,
                findings=initial_findings,
                final_diff_path=final_diff_path,
            )
            write_file(run_dir / "final_answer.md", summary)
            print(summary)
            return 0
        previous_feedback = "Initial compile before model edits failed:\n" + tail_text(
            seed_build.output,
            token_budget.feedback_tail_chars("compile_fix"),
        )
        last_build = seed_build

    edit_limits = edit_limits_for_mode(profile_edit_limits(), original_mode)
    prompt_prefix = system_prompt_static_prefix(rules_text, edit_limits)
    prompt_prefix_hash = system_prompt_prefix_hash(prompt_prefix)
    prompt_mode = original_mode
    prompt_suffix = system_prompt_dynamic_suffix(
        edit_limits,
        prompt_mode,
        two_phase_diagnosis=requires_two_phase_diagnosis(edit_limits, prompt_mode, 1),
    )
    messages = [{"role": "system", "content": f"{prompt_prefix}\n\n{prompt_suffix}"}]
    last_answer = ""
    last_written: list[Path] = []
    last_build: BuildResult | None = None
    diagnosis_feedback = ""

    for attempt in range(1, args.max_attempts + 1):
        before_apply = None
        after_apply = None
        clear_refactor_surface_cache()
        if last_recommendation.get("action") == "stop_diagnosis_report":
            previous_feedback = (
                "Retry escalation stopped the compile loop after repeated identical errors. "
                "Emit diagnosis only; do not patch further in this run."
            )
            break
        keep_specialized_mode = original_mode in {
            "module_fix",
            "reflection_fix",
            "multifile_refactor",
            "editor_runtime_fix",
        }
        budget_mode = original_mode if keep_specialized_mode or attempt == 1 else "compile_fix"
        if attempt >= 2 and original_mode in {
            "agent_edit", "codegen", "compile_fix",
            "prototype_component", "prototype_subsystem",
        }:
            args.mode = "compile_fix"
        elif keep_specialized_mode:
            args.mode = original_mode
        if attempt >= 2 and args.mode == "compile_fix":
            active_route = align_route_to_eval_mode(active_route, "compile_fix", request)
        if attempt == 1:
            prompt_mode = original_mode
        else:
            prompt_mode = args.mode
        prompt_suffix = system_prompt_dynamic_suffix(
            edit_limits,
            prompt_mode,
            two_phase_diagnosis=requires_two_phase_diagnosis(edit_limits, original_mode, attempt),
        )
        if messages and messages[0].get("role") == "system":
            messages[0] = {"role": "system", "content": f"{prompt_prefix}\n\n{prompt_suffix}"}
        if attempt >= 2:
            changed_files = changed_files_from_feedback(last_build_records, last_findings)
            query_parts = [request]
            if last_build_records:
                query_parts.append(build_error_query(last_build_records, last_build.output if last_build else ""))
            rag_context = collect_cached_delta_rag(query_parts, changed_files)
            symbol_context = optional_symbol_graph_context(" ".join(query_parts))
            if symbol_context:
                rag_context = rag_context + "\n\n" + symbol_context
        attempt_snapshot = snapshot_project_files(project_root)

        attempt_dir = run_dir / f"attempt_{attempt}"
        attempt_dir.mkdir(parents=True, exist_ok=True)
        first_attempt_patch_route = attempt == 1 and should_use_patch_preset_on_first_attempt(active_route, args.mode)
        attempt_prefix = (
            f"Compile loop attempt {attempt}/{args.max_attempts}. "
            "List at most 3 assumptions; apply one minimal diff this turn.\n\n"
        )
        if attempt == 1 and original_mode == "multifile_refactor":
            surface_hint = multifile_required_surface_hint(original_mode, request, project_root)
            if surface_hint:
                previous_feedback = (previous_feedback + "\n\n" + surface_hint).strip()
        if last_recommendation.get("action") == "require_multifile_surfaces":
            surface_hint = multifile_required_surface_hint(original_mode, request, project_root)
            snippet_paths = list(dict.fromkeys((last_recommendation.get("blockedRepeatPaths") or []) + changed_files_from_feedback(last_build_records, last_findings)))
            snippet_block = multifile_exact_snippets(project_root, snippet_paths)
            extra = "\n".join(part for part in (surface_hint, snippet_block) if part)
            if extra:
                previous_feedback = (previous_feedback + "\n\n" + extra).strip()
        if first_attempt_patch_route:
            attempt_prefix += (
                "Route-specific first attempt: use the patch sampling profile and patch only the exact "
                "matching header/cpp evidence unless new module evidence appears.\n\n"
            )
        focus_text = "\n".join(
            [
                request,
                previous_feedback,
                build_error_query(last_build_records, last_build.output if last_build else "") if last_build_records else "",
            ]
        )
        summary_max_files, summary_max_chars = project_summary_limits_for_attempt(budget_mode, attempt)
        project_state = summarize_project_state(
            project_root,
            max_files=summary_max_files,
            max_chars=summary_max_chars,
            mode=budget_mode,
            include_full_build_cs=args.mode == "module_fix" or active_route.get("broadMode") == "module_fix",
            focus_text=focus_text,
        )
        prompt = user_prompt(
            request=request,
            rag_context=rag_context,
            focused_evidence=focused_current_source_evidence(
                project_root,
                focus_text,
                active_route,
                mode=original_mode,
            ),
            project_state=project_state,
            project_name=project_name,
            project_file=project_file,
            target=target,
            previous_feedback=attempt_prefix + diagnosis_feedback + previous_feedback,
            mode=original_mode if attempt >= 2 else args.mode,
        )
        messages, history_metrics = prepare_messages_for_attempt(
            messages,
            budget_mode,
            attempt=attempt,
            history_turns=int(edit_limits.get("historyTurns") or 0) or None,
        )
        compile_patch_turn = (
            attempt >= 2
            or first_attempt_patch_route
            or original_mode == "multifile_refactor"
        )
        attempt_preset = preset_for_wrapper(args.mode, compile_patch=compile_patch_turn)
        if float(getattr(args, "temperature", 0.1)) == 0.1 or attempt >= 2:
            args.temperature = float(attempt_preset.get("temperature", args.temperature))
        if attempt_preset.get("maxTokens"):
            args.max_tokens = int(attempt_preset["maxTokens"])
        messages.append({"role": "user", "content": prompt})
        write_token_usage(
            attempt_dir / "token_usage.json",
            attempt=attempt,
            messages=messages,
            prompt=prompt,
            rag_context=rag_context,
            project_state=project_state,
            mode=budget_mode,
            preset=attempt_preset,
            prefix_hash=prompt_prefix_hash,
            history_metrics=history_metrics,
        )
        raw_response = chat_lmstudio(args, messages, model, attempt_preset)
        write_file(attempt_dir / "model_response.txt", raw_response)
        messages.append({"role": "assistant", "content": raw_response})

        try:
            bundle = parse_json_response(raw_response)
            bundle = merge_missing_definition_full_file_edits(project_root, bundle, active_route)
            enforce_edit_limits(bundle, edit_limits, mode=original_mode)
        except ValueError as exc:
            previous_feedback = f"Edit limit exceeded: {exc}"
            write_file(attempt_dir / "validation_feedback.txt", previous_feedback)
            record_validation_rejection(
                attempt=attempt,
                attempt_dir=attempt_dir,
                feedback=previous_feedback,
                blockers=[str(exc)],
                rejection_kind="edit_limit_exceeded",
            )
            continue
        except Exception as exc:
            previous_feedback = f"Model response was not valid JSON: {exc}"
            write_file(attempt_dir / "validation_feedback.txt", previous_feedback)
            record_validation_rejection(
                attempt=attempt,
                attempt_dir=attempt_dir,
                feedback=previous_feedback,
                blockers=[str(exc)],
                rejection_kind="invalid_json",
            )
            continue

        try:
            repeat_blockers = repeat_patch_blockers(bundle, blocked_repeat_paths)
            if repeat_blockers:
                previous_feedback = "Repeat patch escalation blocked the response:\n" + "\n".join(
                    f"- {issue}" for issue in repeat_blockers
                )
                write_file(attempt_dir / "validation_feedback.txt", previous_feedback)
                record_validation_rejection(
                    attempt=attempt,
                    attempt_dir=attempt_dir,
                    feedback=previous_feedback,
                    blockers=repeat_blockers,
                    bundle=bundle,
                    rejection_kind="repeat_patch_blocked",
                )
                continue
            dual_surface_blockers = blueprint_native_event_dual_surface_blockers(bundle)
            if dual_surface_blockers:
                previous_feedback = "Pre-apply guard rejected the patch:\n" + "\n".join(
                    f"- {issue}" for issue in dual_surface_blockers
                )
                write_file(attempt_dir / "validation_feedback.txt", previous_feedback)
                record_validation_rejection(
                    attempt=attempt,
                    attempt_dir=attempt_dir,
                    feedback=previous_feedback,
                    blockers=dual_surface_blockers,
                    bundle=bundle,
                    rejection_kind="pre_apply_validation",
                )
                continue
            if agent_plan is not None:
                from agent_orchestrator import verify_edit_allowed

                gate = verify_edit_allowed(
                    agent_plan,
                    files_count=len(bundle.get("files") or []),
                    patches_count=len(bundle.get("patches") or []),
                )
                if not gate.get("ok"):
                    previous_feedback = "Orchestrator blocked edit: " + "; ".join(gate.get("issues") or [])
                    write_file(attempt_dir / "validation_feedback.txt", previous_feedback)
                    record_validation_rejection(
                        attempt=attempt,
                        attempt_dir=attempt_dir,
                        feedback=previous_feedback,
                        blockers=list(gate.get("issues") or []),
                        bundle=bundle,
                        rejection_kind="orchestrator_gate",
                    )
                    continue
            write_json(attempt_dir / "model_response.json", bundle)
        except Exception as exc:
            previous_feedback = f"Model response was not valid JSON: {exc}"
            write_file(attempt_dir / "validation_feedback.txt", previous_feedback)
            record_validation_rejection(
                attempt=attempt,
                attempt_dir=attempt_dir,
                feedback=previous_feedback,
                blockers=[str(exc)],
                rejection_kind="invalid_json",
            )
            continue

        two_phase_blockers = two_phase_diagnosis_blockers(edit_limits, original_mode, attempt, bundle)
        if two_phase_blockers:
            previous_feedback = (
                "Two-phase diagnosis turn rejected the response:\n"
                + "\n".join(f"- {issue}" for issue in two_phase_blockers)
            )
            write_file(attempt_dir / "validation_feedback.txt", previous_feedback)
            record_validation_rejection(
                attempt=attempt,
                attempt_dir=attempt_dir,
                feedback=previous_feedback,
                blockers=two_phase_blockers,
                bundle=bundle,
                rejection_kind="two_phase_diagnosis",
            )
            continue
        if requires_two_phase_diagnosis(edit_limits, original_mode, attempt) and is_diagnosis_bundle(bundle):
            diagnosis_feedback = format_diagnosis_feedback(bundle) + "\n\n"
            write_file(attempt_dir / "validation_feedback.txt", "Accepted diagnosis-only response for attempt 1.")
            write_json(attempt_dir / "diagnosis.json", bundle.get("diagnosis") or {})
            continue

        last_answer = str(bundle.get("answer") or "")
        hall_blockers = hallucination_blockers(request, last_answer, bundle, project_root)
        if hall_blockers:
            previous_feedback = (
                module_fix_retry_feedback(request, project_root)
                + "\n"
                + "\n".join(f"- {issue}" for issue in hall_blockers)
            )
            write_file(attempt_dir / "validation_feedback.txt", previous_feedback)
            record_validation_rejection(
                attempt=attempt,
                attempt_dir=attempt_dir,
                feedback=previous_feedback,
                blockers=hall_blockers,
                bundle=bundle,
                rejection_kind="hallucination_blocker",
            )
            continue
        route_blockers = route_forbidden_action_blockers(
            active_route,
            bundle,
            request=request,
            mode=original_mode,
        )
        if route_blockers:
            previous_feedback = (
                "Edit rejected because it violates the current error route forbidden actions:\n"
                + "\n".join(f"- {issue}" for issue in route_blockers)
                + "\nUse the current project state as authoritative and patch only the allowed root-cause surface."
            )
            write_file(attempt_dir / "validation_feedback.txt", previous_feedback)
            record_validation_rejection(
                attempt=attempt,
                attempt_dir=attempt_dir,
                feedback=previous_feedback,
                blockers=route_blockers,
                bundle=bundle,
                rejection_kind="route_forbidden_action",
            )
            continue
        rewrite_blockers = existing_full_file_rewrite_blockers(project_root, bundle, original_mode)
        if rewrite_blockers:
            previous_feedback = (
                "Refactor edit rejected because it attempted a full-file rewrite of an existing file:\n"
                + "\n".join(f"- {issue}" for issue in rewrite_blockers)
                + "\nFor refactor modes, re-read the exact current range and return patches[] only. "
                "If replace text no longer matches, narrow the oldText or split into smaller exact patches; "
                "do not use files[] or write_file for existing .h/.cpp files."
            )
            write_file(attempt_dir / "validation_feedback.txt", previous_feedback)
            record_validation_rejection(
                attempt=attempt,
                attempt_dir=attempt_dir,
                feedback=previous_feedback,
                blockers=rewrite_blockers,
                bundle=bundle,
                rejection_kind="full_file_rewrite_blocker",
            )
            continue
        has_edits = bool(bundle["files"]) or bool(bundle.get("patches"))
        if not has_edits:
            last_findings = validate_unreal_readiness(project_root, Path(args.module_graph))
            static_report = format_findings(last_findings)
            if not args.allow_empty_files and not answer_claims_no_changes(last_answer):
                blockers = no_change_blockers(request, project_root, last_findings)
                module_prefix = (
                    module_fix_retry_feedback(request, project_root) + "\n"
                    if original_mode == "module_fix"
                    else ""
                )
                empty_context_parts: list[str] = []
                if last_build is not None and not last_build.ok:
                    empty_context_parts.append(
                        f"UBT still failing (return code {last_build.returncode}).\n"
                        f"Log path: {last_build.log_path}\n"
                        + tail_text(last_build.output, token_budget.feedback_tail_chars("compile_fix"))
                    )
                elif any(
                    marker in previous_feedback
                    for marker in ("UBT still failed", "compile before model edits failed", "autofix was applied, but UBT still failed")
                ):
                    empty_context_parts.append(previous_feedback)
                drift_report = format_findings(
                    [
                        finding
                        for finding in last_findings
                        if finding.code in {"CALLBACK_FUNCTION_POINTER_MISMATCH", "CPP_RETURN_TYPE_MISMATCH"}
                    ]
                )
                if drift_report.strip():
                    empty_context_parts.append(drift_report)
                previous_feedback = (
                    module_prefix
                    + "Model returned no files without clearly saying the current files already satisfy the request. "
                    "Inspect the current project state and either return the smallest changed file bundle, or return "
                    "an empty files array with explicit evidence that no new edit is needed.\n"
                    + static_report
                    + ("\nNo-change blockers:\n" + "\n".join(f"- {issue}" for issue in blockers) if blockers else "")
                    + ("\n\n" + "\n\n".join(empty_context_parts) if empty_context_parts else "")
                )
                diagnosis = bundle.get("diagnosis") if isinstance(bundle.get("diagnosis"), dict) else {}
                required_read_hints = [str(item) for item in (diagnosis.get("requiredReads") or [])]
                resolved_required_reads = resolve_existing_relative_paths(project_root, required_read_hints)
                is_repeat_empty_files = bool(retry_records) and retry_records[-1].get(
                    "validationRejectionKind"
                ) == "empty_files_without_evidence"
                if is_repeat_empty_files:
                    seen_required_read_paths.update(resolved_required_reads)
                else:
                    seen_required_read_paths.clear()
                    seen_required_read_paths.update(resolved_required_reads)
                required_read_paths = (
                    list(seen_required_read_paths) if is_repeat_empty_files else resolved_required_reads
                )
                required_read_block = required_read_file_snippets(project_root, required_read_paths)
                if required_read_block:
                    previous_feedback = previous_feedback + "\n\n" + required_read_block
                record_validation_rejection(
                    attempt=attempt,
                    attempt_dir=attempt_dir,
                    feedback=previous_feedback,
                    blockers=blockers,
                    bundle=bundle,
                    rejection_kind="empty_files_without_evidence",
                )
                continue
            write_file(attempt_dir / "static_validation.txt", static_report + "\n")
            blockers = no_change_blockers(request, project_root, last_findings)
            if blockers and not args.allow_empty_files:
                previous_feedback = (
                    BUILD_CS_RETRY_FEEDBACK + "\n"
                    + "No-change response rejected because the request is not satisfied:\n"
                    + "\n".join(f"- {issue}" for issue in blockers)
                    + "\nInspect the authoritative project state and return the smallest required file change."
                )
                write_file(attempt_dir / "validation_feedback.txt", previous_feedback)
                record_validation_rejection(
                    attempt=attempt,
                    attempt_dir=attempt_dir,
                    feedback=previous_feedback,
                    blockers=blockers,
                    bundle=bundle,
                    rejection_kind="no_change_blocker",
                )
                continue
            if has_static_errors(last_findings) and not args.skip_static_gate:
                previous_feedback = static_report
                record_validation_rejection(
                    attempt=attempt,
                    attempt_dir=attempt_dir,
                    feedback=previous_feedback,
                    bundle=bundle,
                    rejection_kind="empty_files_static_gate",
                )
                continue
            write_file(final_diff_path, diff_snapshots(baseline_snapshot, snapshot_project_files(project_root)) + "\n")
            summary = final_summary(
                status="NO_FILE_CHANGES",
                run_dir=run_dir,
                project_file=project_file,
                source_project_file=prepared.source_project_file,
                direct_project_write=prepared.direct_project_write,
                target=target,
                answer=last_answer,
                written=[],
                build_result=None,
                findings=last_findings,
                final_diff_path=final_diff_path,
            )
            write_file(run_dir / "final_answer.md", summary)
            print(summary)
            return 0

        try:
            before_apply = attempt_snapshot if attempt_snapshot is not None else snapshot_project_files(project_root)
            last_written = apply_bundle(project_root, bundle, before_apply=before_apply)
            after_apply = snapshot_project_files(project_root)
            applied_paths = changed_paths_between(before_apply, after_apply)
            invalidate_project_snapshot_cache(project_root, relative_paths=applied_paths)
            attempt_diff = diff_snapshots(before_apply, after_apply)
            write_file(attempt_dir / "diff.patch", attempt_diff + "\n")
            if attempt_diff == "No file changes detected.":
                last_written = []
                rollback_bundle_apply(project_root, before_apply, applied_paths)
                attempt_snapshot = before_apply
                previous_feedback = (
                    "Model returned file entries, but their content produced no effective changes. "
                    "Do not resend identical file contents. If the request is already satisfied, return an empty "
                    "files array with evidence. Otherwise inspect the current files and change only the missing part."
                )
                write_file(attempt_dir / "validation_feedback.txt", previous_feedback)
                record_validation_rejection(
                    attempt=attempt,
                    attempt_dir=attempt_dir,
                    feedback=previous_feedback,
                    bundle=bundle,
                    rejection_kind="no_effective_file_change",
                )
                continue
            guard_findings = validate_unreal_readiness(project_root, Path(args.module_graph))
            last_findings = guard_findings
            active_route = refresh_route_from_findings(guard_findings, args.mode, request, active_route)
            scope_blockers = edit_scope_blockers(
                request,
                before_apply,
                after_apply,
                project_root,
                route=active_route,
                findings=guard_findings,
                mode=original_mode,
            )
            if scope_blockers:
                rejected_changed_paths = changed_paths_between(before_apply, after_apply)
                partial_apply = scope_blocker_allows_partial_apply(
                    original_mode, scope_blockers, attempt=attempt
                )
                if partial_apply:
                    pending_multifile_surfaces |= pending_surfaces_after_partial(scope_blockers)
                    last_written = [
                        project_root / path for path in rejected_changed_paths
                    ]
                    attempt_snapshot = after_apply
                    snippet_block = multifile_exact_snippets(project_root, rejected_changed_paths)
                else:
                    rollback_bundle_apply(project_root, before_apply, rejected_changed_paths)
                    last_written = []
                    attempt_snapshot = before_apply
                retry_tail = BUILD_CS_RETRY_FEEDBACK if any("Build.cs" in issue for issue in scope_blockers) else ""
                previous_feedback = (
                    (retry_tail + "\n" if retry_tail else "")
                    + "Edit rejected because it does not match the requested compile-fix scope:\n"
                    + "\n".join(f"- {issue}" for issue in scope_blockers)
                    + "\nUse the current project state as authoritative and change the specific file(s) needed."
                )
                if partial_apply:
                    previous_feedback += (
                        "\nPartial header change was kept. Next attempt must patch the matching .cpp "
                        "(and any callsite files) in the same response."
                    )
                    if snippet_block:
                        previous_feedback += "\n\n" + snippet_block
                write_file(attempt_dir / "validation_feedback.txt", previous_feedback)
                record_validation_rejection(
                    attempt=attempt,
                    attempt_dir=attempt_dir,
                    feedback=previous_feedback,
                    blockers=scope_blockers,
                    bundle=bundle,
                    changed_paths=[] if not partial_apply else rejected_changed_paths,
                    rejection_kind="edit_scope_blocker",
                )
                continue
            surface_blockers = multifile_surface_blockers(
                request,
                before_apply,
                after_apply,
                project_root,
                mode=original_mode,
                pending_surfaces=pending_multifile_surfaces,
                findings=guard_findings,
            )
            if surface_blockers:
                rejected_changed_paths = changed_paths_between(before_apply, after_apply)
                partial_apply = scope_blocker_allows_partial_apply(
                    original_mode, surface_blockers, attempt=attempt
                )
                if partial_apply:
                    pending_multifile_surfaces |= pending_surfaces_after_partial(surface_blockers)
                    last_written = [project_root / path for path in rejected_changed_paths]
                    attempt_snapshot = after_apply
                    snippet_block = multifile_exact_snippets(project_root, rejected_changed_paths)
                else:
                    rollback_bundle_apply(project_root, before_apply, rejected_changed_paths)
                    last_written = []
                    attempt_snapshot = before_apply
                previous_feedback = (
                    "Multifile surface coverage rejected the edit:\n"
                    + "\n".join(f"- {issue}" for issue in surface_blockers)
                    + "\nReturn one coherent patch that updates every required declaration, definition, and callsite."
                )
                if partial_apply:
                    previous_feedback += (
                        "\nPartial change was kept. Next attempt must complete the remaining required surfaces."
                    )
                    if snippet_block:
                        previous_feedback += "\n\n" + snippet_block
                write_file(attempt_dir / "validation_feedback.txt", previous_feedback)
                record_validation_rejection(
                    attempt=attempt,
                    attempt_dir=attempt_dir,
                    feedback=previous_feedback,
                    blockers=surface_blockers,
                    bundle=bundle,
                    changed_paths=[] if not partial_apply else rejected_changed_paths,
                    rejection_kind="multifile_incomplete",
                )
                continue
            pending_multifile_surfaces.clear()
        except Exception as exc:
            if before_apply is not None and after_apply is not None:
                rollback_bundle_apply(
                    project_root,
                    before_apply,
                    changed_paths_between(before_apply, after_apply),
                )
                attempt_snapshot = before_apply
            elif before_apply is not None:
                attempt_snapshot = before_apply
            hint = ""
            if "bundle" in locals():
                for item in (bundle.get("patches") or []):
                    patch_path = safe_output_path(project_root, item["path"])
                    hint = patch_apply_hint(patch_path, str(item.get("oldText") or ""))
                    if hint:
                        break
            previous_feedback = f"File application failed: {exc}{hint}"
            static_hint = static_validation_retry_feedback(last_findings, active_route)
            if static_hint:
                previous_feedback += "\n\n" + static_hint
            write_file(attempt_dir / "validation_feedback.txt", previous_feedback)
            record_validation_rejection(
                attempt=attempt,
                attempt_dir=attempt_dir,
                feedback=previous_feedback,
                blockers=[str(exc)],
                bundle=bundle if "bundle" in locals() else None,
                rejection_kind="file_application_failed",
            )
            continue

        lightweight_static = only_source_files_changed(before_apply, after_apply)
        build_cs_only = only_build_cs_changed(before_apply, after_apply)
        last_findings = validate_unreal_readiness(
            project_root,
            Path(args.module_graph),
            lightweight=lightweight_static,
            skip_include_path_checks=build_cs_only,
        )
        static_report = format_findings(last_findings)
        write_file(attempt_dir / "static_validation.txt", static_report + "\n")
        if should_block_llm_apply_static_gate(last_findings, mode=original_mode) and not args.skip_static_gate:
            rejected_paths = changed_paths_between(before_apply, after_apply)
            rollback_bundle_apply(project_root, before_apply, rejected_paths)
            attempt_snapshot = before_apply
            last_written = []
            previous_feedback = static_report
            static_hint = static_validation_retry_feedback(last_findings, active_route)
            if static_hint:
                previous_feedback += "\n\n" + static_hint
            record_validation_rejection(
                attempt=attempt,
                attempt_dir=attempt_dir,
                feedback=previous_feedback,
                bundle=bundle,
                changed_paths=[],
                rejection_kind="static_gate",
            )
            continue

        attempt_snapshot = after_apply

        if args.skip_build:
            write_file(final_diff_path, diff_snapshots(baseline_snapshot, snapshot_project_files(project_root)) + "\n")
            summary = final_summary(
                status="SKIPPED_BUILD",
                run_dir=run_dir,
                project_file=project_file,
                source_project_file=prepared.source_project_file,
                direct_project_write=prepared.direct_project_write,
                target=target,
                answer=last_answer,
                written=last_written,
                build_result=None,
                findings=last_findings,
                final_diff_path=final_diff_path,
            )
            write_file(run_dir / "final_answer.md", summary)
            print(summary)
            return 0

        last_build = run_ubt(
            Path(args.ubt_path),
            project_file,
            target,
            args.platform,
            args.configuration,
            attempt_dir / "ubt.log",
            args.build_timeout,
        )
        if last_build.ok:
            changed_paths_success: list[str] = []
            for path in last_written:
                try:
                    changed_paths_success.append(str(path.relative_to(project_root)).replace("\\", "/"))
                except ValueError:
                    changed_paths_success.append(str(path))
            success_record = make_attempt_record(
                attempt=attempt,
                passed=True,
                changed_paths=changed_paths_success,
                build_log_path=str(attempt_dir / "ubt.log"),
            )
            write_json(
                run_dir / "retry_state.json",
                {
                    "attempts": list(retry_records) + [success_record],
                    "latest": success_record,
                    "sameErrorRepeated": False,
                    "noOpEdit": False,
                    "recommendedAction": {"action": "done"},
                },
            )
            if attempt >= 2 and last_build_records:
                try:
                    from failure_memory import append_failure_memory, maybe_auto_reindex_failure_memory
                    from load_sampling_preset import resolve_profile_name

                    meta = (last_build_records[0].get("metadata") or {}) if last_build_records else {}
                    append_failure_memory(
                        Path(__file__).resolve().parent.parent / "data" / "failure_memory",
                        project_name,
                        error_subkind=str(meta.get("error_subkind") or "COMPILE_GENERIC"),
                        error_code=str(meta.get("error_code") or ""),
                        symbol_name=str(meta.get("symbol_name") or ""),
                        failed_summary=previous_feedback[:500] if previous_feedback else "",
                        fix_summary=last_answer[:500] if last_answer else "",
                        changed_files=[str(p.relative_to(project_root)) for p in last_written],
                        diff_excerpt=read_text(final_diff_path) or "",
                        rag_evidence_ids=[],
                        original_request=request[:500],
                        failed_output_summary=previous_feedback[:500] if previous_feedback else "",
                        retry_count=attempt,
                        model=model,
                        sampling_profile=resolve_profile_name(),
                    )
                    maybe_auto_reindex_failure_memory(Path(__file__).resolve().parent.parent)
                except Exception:
                    pass
            write_file(final_diff_path, diff_snapshots(baseline_snapshot, snapshot_project_files(project_root)) + "\n")
            summary = final_summary(
                status="BUILD_OK",
                run_dir=run_dir,
                project_file=project_file,
                source_project_file=prepared.source_project_file,
                direct_project_write=prepared.direct_project_write,
                target=target,
                answer=last_answer,
                written=last_written,
                build_result=last_build,
                findings=last_findings,
                final_diff_path=final_diff_path,
            )
            write_file(run_dir / "final_answer.md", summary)
            print(summary)
            if agent_plan is not None and agent_plan.plan_slices:
                from plan_slice_state import (
                    init_slice_state,
                    load_slice_state,
                    mark_slice_complete,
                    next_slice_prompt,
                    save_slice_state,
                )

                slice_path = run_dir / "plan_slice_state.json"
                slice_state = load_slice_state(slice_path)
                if not slice_state.get("slices"):
                    slice_state = init_slice_state(agent_plan.plan_slices)
                slice_state = mark_slice_complete(
                    slice_state,
                    project_root=project_root,
                    written_paths=last_written,
                    plan_slices=agent_plan.plan_slices,
                )
                save_slice_state(slice_path, slice_state)
                if int(slice_state.get("activeSliceIndex") or 0) < len(agent_plan.plan_slices):
                    previous_feedback = next_slice_prompt(slice_state, agent_plan.plan_slices)
                    write_file(run_dir / "next_slice_prompt.txt", previous_feedback + "\n")
                    continue
            return 0

        build_records = parse_build_feedback(last_build.log_path, project_root, last_build.output)
        last_build_records = build_records
        parsed_feedback = rerag_for_build_errors(
            args,
            build_records,
            last_build.output,
            exclude_chunk_ids=seen_chunk_ids,
        )
        write_json(attempt_dir / "structured_errors.json", parsed_feedback.records)
        write_file(attempt_dir / "structured_errors.md", format_build_records(parsed_feedback.records) + "\n")
        write_file(attempt_dir / "failure_rag_context.md", parsed_feedback.rag_context + "\n")
        changed_paths = []
        for path in last_written:
            try:
                changed_paths.append(str(path.relative_to(project_root)).replace("\\", "/"))
            except ValueError:
                changed_paths.append(str(path).replace("\\", "/"))
        retry_payload = build_retry_state_payload(
            previous_record=previous_retry_record,
            attempt=attempt,
            passed=False,
            previous_route=active_route,
            records=parsed_feedback.records,
            changed_paths=changed_paths,
            build_log_path=str(last_build.log_path),
            fallback_message=tail_text(last_build.output, 1000),
            static_findings=last_findings,
            build_output=last_build.output,
            attempt_history=retry_records,
            mode=original_mode,
            request=request,
        )
        module_block = module_resolver_feedback(
            "\n".join([request, build_error_query(parsed_feedback.records, last_build.output)]),
            build_cs_text(project_root),
        )
        route_block = "Error route:\n" + json.dumps(retry_payload["current"].get("errorRoute") or {}, ensure_ascii=False, indent=2)
        route_feedback = soft_route_feedback(
            retry_payload["current"].get("errorRoute") or {},
            module_evidence=bool(module_block),
        )
        build_cs_warning = build_cs_first_soft_warning(
            retry_payload["current"].get("errorRoute") or {},
            changed_paths,
            module_evidence=bool(module_block),
        )
        unsupported_build_cs_warning = build_cs_unsupported_for_route_warning(
            retry_payload["current"].get("errorRoute") or {},
            changed_paths,
            module_evidence=bool(module_block),
        )
        soft_replan_feedback = unsupported_build_cs_soft_replan_feedback(
            retry_payload["current"].get("errorRoute") or {},
            changed_paths,
            module_evidence=bool(module_block),
        )
        retry_payload["current"]["buildCsUnsupportedForRouteWarning"] = bool(unsupported_build_cs_warning)
        retry_payload["current"]["softReplanTriggered"] = bool(soft_replan_feedback)
        retry_records.append(retry_payload["current"])
        previous_retry_record = retry_payload["current"]
        last_recommendation = retry_payload["recommendation"]
        rag_top_k_boost = int(last_recommendation.get("deltaTopKBoost") or 0)
        blocked_repeat_paths = list(last_recommendation.get("blockedRepeatPaths") or [])
        active_route = retry_payload["current"].get("errorRoute") or active_route
        retry_state_doc = {
            "attempts": retry_records,
            "latest": retry_payload["current"],
            "sameErrorRepeated": retry_payload["recommendation"].get("sameErrorRepeated", False),
            "noOpEdit": retry_payload["recommendation"].get("noOpEdit", False),
            "recommendedAction": retry_payload["recommendation"],
        }
        write_json(run_dir / "retry_state.json", retry_state_doc)
        write_json(attempt_dir / "retry_state.json", retry_state_doc)
        static_retry_hint = str(retry_payload["current"].get("staticValidationRetryHint") or "")
        retry_block = retry_feedback_block(retry_payload["recommendation"])
        failure_rag_note = ""
        if parsed_feedback.rag_context and attempt < args.max_attempts:
            failure_rag_note = (
                "Failure-specific RAG context was written to failure_rag_context.md in this attempt folder."
            )
        previous_feedback = "\n\n".join(
            [part for part in [
                static_report,
                f"UBT failed with return code {last_build.returncode}.",
                f"Log path: {last_build.log_path}",
                format_build_records(parsed_feedback.records),
                route_block,
                module_block,
                route_feedback,
                f"Build.cs-first soft warning: {build_cs_warning}" if build_cs_warning else "",
                f"Unsupported Build.cs route warning: {unsupported_build_cs_warning}" if unsupported_build_cs_warning else "",
                f"Unsupported Build.cs soft replan: {soft_replan_feedback}" if soft_replan_feedback else "",
                f"Static validation retry hint: {static_retry_hint}" if static_retry_hint else "",
                retry_block,
                failure_rag_note,
                tail_text(last_build.output, token_budget.feedback_tail_chars("compile_fix")),
            ] if part]
        )
        write_file(attempt_dir / "validation_feedback.txt", previous_feedback)

    write_file(final_diff_path, diff_snapshots(baseline_snapshot, snapshot_project_files(project_root)) + "\n")
    summary = final_summary(
        status="FAILED",
        run_dir=run_dir,
        project_file=project_file,
        source_project_file=prepared.source_project_file,
        direct_project_write=prepared.direct_project_write,
        target=target,
        answer=last_answer or "최대 시도 횟수 내에 컴파일 가능한 결과를 만들지 못했습니다.",
        written=last_written,
        build_result=last_build,
        findings=last_findings,
        final_diff_path=final_diff_path,
    )
    write_file(run_dir / "final_answer.md", summary)
    print(summary)
    return 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate, apply, validate, and compile Unreal C++ files through LM Studio.")
    parser.add_argument("--request", default="", help="User implementation request.")
    parser.add_argument("--request-file", default="", help="Text file containing the user request.")
    parser.add_argument("--index", default="data/unreal58/rag.sqlite")
    parser.add_argument("--module-graph", default="data/unreal58/raw_module_graph.jsonl")
    parser.add_argument("--mode", default="agent_edit")
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--lmstudio-url", default=DEFAULT_LMSTUDIO_URL)
    parser.add_argument("--model", default="")
    parser.add_argument("--temperature", type=float, default=0.1)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--max-tokens", type=int, default=0)
    parser.add_argument("--max-attempts", type=int, default=4)
    parser.add_argument("--feedback-chars", type=int, default=12000)
    parser.add_argument("--scratch-root", default="data/wrapper_runs")
    parser.add_argument("--run-dir", default="")
    parser.add_argument("--project-name", default="ScratchPrototype")
    parser.add_argument("--project-file", default="")
    parser.add_argument("--allow-direct-project-write", action="store_true")
    parser.add_argument("--target", default="")
    parser.add_argument("--platform", default="Win64")
    parser.add_argument("--configuration", default="Development")
    parser.add_argument("--ubt-path", default=DEFAULT_UBT_PATH)
    parser.add_argument("--build-timeout", type=int, default=1200)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--skip-static-gate", action="store_true")
    parser.add_argument("--allow-empty-files", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--autofix-only",
        action="store_true",
        help="Run static autofix pipeline and UBT only; skip LM Studio edits.",
    )
    parser.add_argument("--orchestrate", action="store_true", help="Inject agent orchestrator plan into prompts")
    parser.add_argument("--no-orchestrate", action="store_true", help="Disable orchestrator even when env enabled")
    args = parser.parse_args()
    if args.no_orchestrate:
        args.orchestrate = False
    elif not args.orchestrate:
        import os
        args.orchestrate = os.environ.get("UNREAL_AGENT_ORCHESTRATE", "1").strip().lower() not in {"0", "false", "no", "off"}
    return args


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
