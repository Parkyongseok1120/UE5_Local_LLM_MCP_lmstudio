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
from ubt_utils import build_ubt_command, split_ubt_target_spec
from apply_patch import apply_patch as apply_single_patch, patch_apply_hint, validate_patch_item

PATCH_PREFERRED_LINE_THRESHOLD = 200
REFACTOR_PATCH_ONLY_MODES = {"refactor_r2", "refactor_r3", "refactor_r4"}

from unreal_static_validate import (
    Finding,
    UNREAL_LIFECYCLE_OVERRIDE_ALLOWLIST,
    build_cs_text,
    declared_build_modules,
    format_findings,
    has_static_errors,
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
ALLOWED_SUFFIXES = {
    ".h",
    ".hpp",
    ".cpp",
    ".c",
    ".cc",
    ".cs",
    ".ini",
    ".json",
    ".uproject",
    ".uplugin",
    ".md",
    ".txt",
}
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
_PROJECT_SNAPSHOT_CACHE: dict[str, dict[str, tuple[float, str]]] = {}
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


def is_text_project_file(path: Path) -> bool:
    if not path.is_file() or path.suffix.lower() not in ALLOWED_SUFFIXES:
        return False
    return not should_ignore_project_path(path)


def snapshot_project_files(root: Path) -> dict[str, str]:
    root_key = str(root.resolve())
    cache = _PROJECT_SNAPSHOT_CACHE.setdefault(root_key, {})
    snapshot: dict[str, str] = {}
    seen: set[str] = set()
    for path in sorted(root.rglob("*")):
        if not is_text_project_file(path):
            continue
        try:
            relative = project_relative_path(path, root)
        except ValueError:
            continue
        seen.add(relative)
        mtime = path.stat().st_mtime
        cached = cache.get(relative)
        if cached and cached[0] == mtime:
            snapshot[relative] = cached[1]
        else:
            content = read_text(path)
            cache[relative] = (mtime, content)
            snapshot[relative] = content
    for stale in set(cache) - seen:
        del cache[stale]
    return snapshot


PROJECT_STATE_LINE_PATTERNS = (
    re.compile(r"\b(?:UCLASS|USTRUCT|UENUM|UINTERFACE|UFUNCTION|UPROPERTY|GENERATED_BODY)\b"),
    re.compile(r"\b(?:class|struct|enum\s+class|enum)\s+(?:[A-Z0-9_]+_API\s+)?[A-Za-z_][A-Za-z0-9_]*\b"),
    re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*::[~A-Za-z_][A-Za-z0-9_]*\s*\("),
    re.compile(r"\b(?:Public|Private)DependencyModuleNames\b|\bExtraModuleNames\b"),
    re.compile(r"\bDECLARE_(?:DYNAMIC_)?(?:MULTICAST_)?DELEGATE\b|\bFTimerHandle\b"),
    re.compile(r"\.Broadcast\s*\("),
)
HEADER_MEMBER_DECL_RE = re.compile(
    r"^(?:virtual\s+|static\s+|inline\s+|FORCEINLINE\s+|explicit\s+)*"
    r"(?:[A-Za-z_][A-Za-z0-9_:<>*&,\s]+\s+)+"
    r"[~A-Za-z_][A-Za-z0-9_]*\s*\([^;{}]*\)\s*"
    r"(?:const\s*)?(?:override\s*)?(?:final\s*)?;\s*$"
)


def summarize_interesting_lines(text: str, max_lines: int = 14, *, suffix: str = "") -> list[str]:
    results: list[str] = []
    seen: set[str] = set()
    is_header = suffix.lower() in {".h", ".hpp"}
    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line or len(line) > 220:
            continue
        if not any(pattern.search(line) for pattern in PROJECT_STATE_LINE_PATTERNS) and not (
            is_header and HEADER_MEMBER_DECL_RE.search(line)
        ):
            continue
        if line in seen:
            continue
        results.append(line)
        seen.add(line)
        if len(results) >= max_lines:
            break
    return results


def summarize_project_state(
    root: Path,
    max_files: int | None = None,
    max_chars: int | None = None,
    mode: str = "execute",
    include_full_build_cs: bool | None = None,
    focus_text: str = "",
) -> str:
    default_files, default_chars = token_budget.project_summary_limits(mode)
    max_files = default_files if max_files is None else max_files
    max_chars = default_chars if max_chars is None else max_chars
    snapshot = snapshot_project_files(root)
    if not snapshot:
        return "Current project file state summary: no text project files found."

    focus_paths = project_summary_focus_paths(root, focus_text, snapshot) if focus_text else []
    focus_order = {relative: index for index, relative in enumerate(focus_paths)}
    lines = [
        "Current project file state summary (authoritative):",
        "- Treat these files as already existing. Do not re-add declarations, includes, modules, or bindings that are already present.",
        "- For existing files, prefer exact patches. Return complete content only for new files unless the mode explicitly allows full-file replacement.",
    ]
    if focus_paths:
        lines.append("- Focused files are summarized first: " + ", ".join(focus_paths[:8]))
    modes_with_full_build_cs = frozenset({"module_fix"})
    show_full_build_cs = include_full_build_cs if include_full_build_cs is not None else mode in modes_with_full_build_cs
    included = 0
    if show_full_build_cs:
        build_entries = sorted(
            (rel, txt) for rel, txt in snapshot.items() if rel.lower().endswith(".build.cs")
        )
        if build_entries:
            lines.append(
                "- Full *.Build.cs content below (authoritative for PublicDependencyModuleNames / module dependencies):"
            )
            for relative, text in build_entries:
                lines.append(f"- {relative} ({len(text.splitlines())} lines, full file):")
                for line in text.splitlines():
                    lines.append(f"    {line}")
                included += 1
                current = "\n".join(lines)
                if len(current) >= max_chars:
                    lines.append("- ... project state summary truncated.")
                    return "\n".join(lines)

    def state_sort_key(item: tuple[str, str]) -> tuple[int, str]:
        relative = item[0]
        if relative in focus_order:
            return (focus_order[relative], relative)
        if relative.startswith("Source/"):
            return (1000, relative)
        if relative.startswith("Plugins/"):
            return (2000, relative)
        if relative.startswith("Config/"):
            return (3000, relative)
        return (4000, relative)

    for relative, text in sorted(snapshot.items(), key=state_sort_key):
        if included >= max_files:
            lines.append(f"- ... {len(snapshot) - included} additional file(s) omitted from this summary.")
            break
        suffix = Path(relative).suffix.lower()
        if suffix not in {".h", ".hpp", ".cpp", ".c", ".cc", ".cs", ".ini", ".json", ".uproject", ".uplugin"}:
            continue
        if relative.lower().endswith(".build.cs") and show_full_build_cs:
            continue
        interesting = summarize_interesting_lines(text, suffix=suffix)
        included += 1
        line_count = len(text.splitlines())
        lines.append(f"- {relative} ({line_count} lines)")
        for item in interesting:
            lines.append(f"  - {item}")
        current = "\n".join(lines)
        if len(current) >= max_chars:
            lines.append("- ... project state summary truncated.")
            break
    return "\n".join(lines)


def _candidate_source_paths_from_text(root: Path, text: str) -> list[Path]:
    candidates: list[Path] = []
    root_resolved = root.resolve()
    path_pattern = re.compile(r"(?:[A-Za-z]:[\\/][^\s:'\"]+|Source[\\/][^\s:'\"]+)\.(?:h|hpp|cpp|c|cc)", re.I)
    for match in path_pattern.finditer(text or ""):
        raw = match.group(0).strip().strip(".,);]")
        path = Path(raw)
        if not path.is_absolute():
            path = root / raw.replace("\\", "/")
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved.is_file() and root_resolved in resolved.parents:
            candidates.append(resolved)
    symbol_matches = re.findall(r"\b([AUFSI][A-Za-z0-9_]{3,})::([~A-Za-z_][A-Za-z0-9_]*)\b", text or "")
    class_names = {item[0] for item in symbol_matches}
    func_names = {item[1].lstrip("~") for item in symbol_matches}
    for match in re.finditer(r"\b([AUFSI][A-Za-z0-9_]{3,})\b", text or ""):
        class_names.add(match.group(1))
    if class_names or func_names:
        for path in iter_source_files(root):
            if path.suffix.lower() not in {".h", ".hpp", ".cpp", ".c", ".cc"}:
                continue
            content = read_text(path)
            if any(name in content for name in class_names) or any(f"{name}(" in content for name in func_names):
                candidates.append(path.resolve())
    return list(dict.fromkeys(candidates))


def _matching_source_pair_paths(root: Path, path: Path) -> list[Path]:
    results = [path]
    stem = path.stem
    suffix = path.suffix.lower()
    wanted_suffixes = {".h", ".hpp", ".cpp", ".c", ".cc"} - {suffix}
    for candidate in iter_source_files(root):
        if candidate.stem == stem and candidate.suffix.lower() in wanted_suffixes:
            results.append(candidate.resolve())
    return list(dict.fromkeys(results))


def project_summary_focus_paths(root: Path, focus_text: str, snapshot: dict[str, str] | None = None) -> list[str]:
    snapshot = snapshot if snapshot is not None else snapshot_project_files(root)
    root_resolved = root.resolve()
    rels: list[str] = []

    def add_relative(relative: str) -> None:
        if relative in snapshot and relative not in rels:
            rels.append(relative)
        stem = Path(relative).stem
        for candidate in snapshot:
            candidate_path = Path(candidate)
            if candidate_path.stem == stem and candidate_path.suffix.lower() in {".h", ".hpp", ".cpp", ".c", ".cc"}:
                if candidate not in rels:
                    rels.append(candidate)

    path_pattern = re.compile(
        r"(?:[A-Za-z]:[\\/][^\s:'\"]+|(?:Source|Plugins|Config)[\\/][^\s:'\"]+)"
        r"\.(?:h|hpp|cpp|c|cc|cs|ini|json|uproject|uplugin)",
        re.I,
    )
    for match in path_pattern.finditer(focus_text or ""):
        raw = match.group(0).strip().strip(".,);]")
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = root / raw.replace("\\", "/")
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved.is_file() and root_resolved in resolved.parents:
            try:
                add_relative(project_relative_path(resolved, root))
            except ValueError:
                continue

    terms = _refactor_symbol_terms(focus_text)
    if terms:
        for relative, text in snapshot.items():
            suffix = Path(relative).suffix.lower()
            if suffix not in {".h", ".hpp", ".cpp", ".c", ".cc"}:
                continue
            if any(term in text for term in terms):
                add_relative(relative)
    return rels


def focused_source_pair_context(
    root: Path,
    focus_text: str,
    *,
    max_files: int = 2,
    max_chars: int = 2800,
    snippet_chars: int = 900,
) -> str:
    paths: list[Path] = []
    for candidate in _candidate_source_paths_from_text(root, focus_text):
        for paired in _matching_source_pair_paths(root, candidate):
            if paired.suffix.lower() in {".h", ".hpp", ".cpp", ".c", ".cc"} and paired not in paths:
                paths.append(paired)
    if not paths:
        return ""
    lines = [
        "Focused current source evidence (authoritative; prefer this over generic RAG examples):",
        "- Compare these matching header/cpp files before editing.",
        "- Do not copy helper names from generic examples unless they already exist here.",
    ]
    root_resolved = root.resolve()
    for path in paths[:max_files]:
        try:
            relative = path.resolve().relative_to(root_resolved).as_posix()
        except ValueError:
            continue
        text = read_text(path)
        snippet = text.strip()
        if len(snippet) > snippet_chars:
            snippet = snippet[:snippet_chars].rstrip() + "\n..."
        lines.append(f"\n## {relative}")
        lines.append("```")
        lines.append(snippet)
        lines.append("```")
        if len("\n".join(lines)) >= max_chars:
            lines.append("\n- ... focused evidence truncated.")
            break
    return "\n".join(lines)[:max_chars]


CLASS_DECL_RE = re.compile(
    r"\bclass\s+(?:[A-Z0-9_]+_API\s+)?(?P<name>[AUFSI][A-Za-z0-9_]*)\b"
)
HEADER_DECL_DETAIL_RE = re.compile(
    r"^\s*(?:virtual\s+|static\s+|inline\s+|FORCEINLINE\s+|explicit\s+)*"
    r"(?P<ret>[A-Za-z_][A-Za-z0-9_:<>*&,\s]*?)\s+"
    r"(?P<name>[~A-Za-z_][A-Za-z0-9_]*)\s*"
    r"\((?P<args>[^;{}]*)\)\s*"
    r"(?P<qualifiers>(?:const\s*)?(?:override\s*)?(?:final\s*)?)\s*;\s*$"
)
CPP_DEFINITION_DETAIL_RE = re.compile(
    r"\b(?P<class>[A-Za-z_][A-Za-z0-9_]*)::(?P<name>[~A-Za-z_][A-Za-z0-9_]*)\s*\((?P<args>[^)]*)\)"
)


def _relative_project_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path).replace("\\", "/")


def _class_names_from_header(text: str) -> list[str]:
    names: list[str] = []
    for match in CLASS_DECL_RE.finditer(text or ""):
        name = match.group("name")
        if name not in names:
            names.append(name)
    return names


def _header_member_declarations(text: str) -> list[dict[str, Any]]:
    classes = _class_names_from_header(text)
    current_class = classes[0] if classes else ""
    declarations: list[dict[str, Any]] = []
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        class_match = CLASS_DECL_RE.search(raw_line)
        if class_match:
            current_class = class_match.group("name")
            continue
        stripped = raw_line.strip()
        match = HEADER_DECL_DETAIL_RE.match(stripped)
        if not match:
            continue
        name = match.group("name")
        if not current_class or name == current_class or name == f"~{current_class}":
            continue
        declarations.append(
            {
                "class": current_class,
                "name": name,
                "args": re.sub(r"\s+", " ", match.group("args")).strip(),
                "raw": stripped,
                "line": line_no,
            }
        )
    return declarations


def _cpp_member_definitions(text: str, class_names: set[str]) -> list[dict[str, Any]]:
    definitions: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        for match in CPP_DEFINITION_DETAIL_RE.finditer(raw_line):
            cls = match.group("class")
            if class_names and cls not in class_names:
                continue
            name = match.group("name")
            key = (cls, name, re.sub(r"\s+", " ", match.group("args")).strip())
            if key in seen:
                continue
            seen.add(key)
            definitions.append(
                {
                    "class": cls,
                    "name": name,
                    "args": key[2],
                    "raw": raw_line.strip(),
                    "line": line_no,
                }
            )
    return definitions


def _call_site_count(text: str, function_name: str) -> int:
    count = 0
    call_re = re.compile(rf"\b{re.escape(function_name)}\s*\(")
    definition_re = re.compile(rf"::\s*{re.escape(function_name)}\s*\(")
    for raw_line in text.splitlines():
        if definition_re.search(raw_line):
            continue
        if call_re.search(raw_line):
            count += 1
    return count


def missing_definition_call_removal_blockers(before: dict[str, str], after: dict[str, str]) -> list[str]:
    issues: list[str] = []
    before_cpp_by_stem = {
        Path(path).stem: path
        for path in before
        if Path(path).suffix.lower() in {".cpp", ".c", ".cc"}
    }
    for header_path, header_text in before.items():
        if Path(header_path).suffix.lower() not in {".h", ".hpp"}:
            continue
        cpp_path = before_cpp_by_stem.get(Path(header_path).stem)
        if not cpp_path:
            continue
        before_cpp = before.get(cpp_path, "")
        after_cpp = after.get(cpp_path, before_cpp)
        declarations = _header_member_declarations(header_text)
        class_names = {str(item["class"]) for item in declarations if item.get("class")}
        before_definitions = {
            (item["class"], item["name"]) for item in _cpp_member_definitions(before_cpp, class_names)
        }
        after_definitions = {
            (item["class"], item["name"]) for item in _cpp_member_definitions(after_cpp, class_names)
        }
        for decl in declarations:
            key = (decl["class"], decl["name"])
            if key in before_definitions or key not in after_definitions:
                continue
            before_calls = _call_site_count(before_cpp, str(decl["name"]))
            after_calls = _call_site_count(after_cpp, str(decl["name"]))
            if before_calls > 0 and after_calls < before_calls:
                issues.append(
                    f"The edit added {decl['class']}::{decl['name']} but removed existing call site(s) in {cpp_path}. "
                    "For missing-definition fixes, add the implementation without deleting existing callers unless the request explicitly asks for removal."
                )
    return issues


def _matching_header_cpp_pairs(root: Path, focus_text: str, *, fallback: bool) -> list[tuple[Path, Path]]:
    pairs: list[tuple[Path, Path]] = []

    def add_pair(header: Path, cpp: Path) -> None:
        pair = (header.resolve(), cpp.resolve())
        if pair not in pairs:
            pairs.append(pair)

    for candidate in _candidate_source_paths_from_text(root, focus_text):
        matched = _matching_source_pair_paths(root, candidate)
        headers = [path for path in matched if path.suffix.lower() in {".h", ".hpp"}]
        cpps = [path for path in matched if path.suffix.lower() in {".cpp", ".c", ".cc"}]
        for header in headers:
            for cpp in cpps:
                add_pair(header, cpp)

    if fallback and not pairs:
        headers = [
            path
            for path in iter_source_files(root)
            if path.suffix.lower() in {".h", ".hpp"} and "Source" in path.parts
        ]
        by_stem = {
            path.stem: path
            for path in iter_source_files(root)
            if path.suffix.lower() in {".cpp", ".c", ".cc"} and "Source" in path.parts
        }
        for header in headers:
            cpp = by_stem.get(header.stem)
            if cpp:
                add_pair(header, cpp)
    return pairs


def _route_needs_decl_definition_evidence(route: dict[str, Any] | None, focus_text: str) -> bool:
    subkind = str((route or {}).get("errorSubkind") or "")
    if subkind in {"LNK_MISSING_CPP_DEFINITION", "HEADER_CPP_SIGNATURE_MISMATCH"}:
        return True
    lowered = str(focus_text or "").lower()
    return any(
        marker in lowered
        for marker in (
            "lnk2019",
            "unresolved external",
            "missing cpp definition",
            "signature mismatch",
            "declaration",
            "definition",
        )
    )


def declaration_definition_evidence(
    root: Path,
    focus_text: str,
    route: dict[str, Any] | None = None,
    *,
    max_pairs: int = 3,
    max_chars: int = 3600,
) -> str:
    fallback = _route_needs_decl_definition_evidence(route, focus_text)
    pairs = _matching_header_cpp_pairs(root, focus_text, fallback=fallback)
    if not pairs:
        return ""

    analyzed: list[dict[str, Any]] = []
    for header, cpp in pairs:
        header_text = read_text(header)
        cpp_text = read_text(cpp)
        declarations = _header_member_declarations(header_text)
        class_names = {str(item["class"]) for item in declarations if item.get("class")}
        definitions = _cpp_member_definitions(cpp_text, class_names)
        definition_names = {(item["class"], item["name"]) for item in definitions}
        missing = [
            item
            for item in declarations
            if (item["class"], item["name"]) not in definition_names
            and re.search(rf"\b{re.escape(str(item['name']))}\s*\(", cpp_text)
        ]
        analyzed.append(
            {
                "header": header,
                "cpp": cpp,
                "declarations": declarations,
                "definitions": definitions,
                "missing": missing,
            }
        )

    analyzed.sort(key=lambda item: (0 if item["missing"] else 1, _relative_project_path(root, item["header"])))
    if fallback and not any(item["missing"] for item in analyzed):
        analyzed = [item for item in analyzed if item["declarations"] or item["definitions"]]
    if not analyzed:
        return ""

    lines = [
        "Current declaration/definition evidence (authoritative; prefer this over generic RAG examples):",
        "- These facts are extracted from the current project files, not from examples.",
        "- Do not copy function names from generic RAG examples unless they appear below.",
        "- For missing-definition fixes, add the implementation without deleting existing call sites.",
    ]
    for item in analyzed[:max_pairs]:
        header = item["header"]
        cpp = item["cpp"]
        lines.append("")
        lines.append(f"## {_relative_project_path(root, header)} <-> {_relative_project_path(root, cpp)}")
        missing = item["missing"]
        if missing:
            lines.append("Declared without matching cpp definition and called/needed in cpp:")
            for decl in missing[:8]:
                args = f"({decl['args']})"
                lines.append(f"- {decl['class']}::{decl['name']}{args} from header line {decl['line']}: {decl['raw']}")
        declarations = item["declarations"]
        if declarations:
            lines.append("Header member declarations:")
            for decl in declarations[:10]:
                lines.append(f"- line {decl['line']}: {decl['raw']}")
        definitions = item["definitions"]
        if definitions:
            lines.append("Existing cpp member definitions:")
            for definition in definitions[:10]:
                lines.append(f"- line {definition['line']}: {definition['raw']}")
        if len("\n".join(lines)) >= max_chars:
            lines.append("- ... declaration/definition evidence truncated.")
            break
    return "\n".join(lines)[:max_chars]


def _refactor_symbol_terms(focus_text: str) -> list[str]:
    terms: list[str] = []
    for cls, func in re.findall(r"\b([AUFSI][A-Za-z0-9_]{3,})::([~A-Za-z_][A-Za-z0-9_]*)\b", focus_text or ""):
        for value in (cls, func.lstrip("~")):
            if value and value not in terms:
                terms.append(value)
    for match in re.finditer(r"\b([AUFSI][A-Za-z0-9_]{4,})\b", focus_text or ""):
        value = match.group(1)
        if value not in terms:
            terms.append(value)
    return terms[:12]


def _refactor_line_role(line: str, terms: list[str], suffix: str) -> str:
    stripped = line.strip()
    if re.search(r"\b(?:AddDynamic|AddUObject|BindAction|BindUFunction|DECLARE_.*DELEGATE)\b", stripped):
        return "binding/delegate"
    if "override" in stripped:
        return "override"
    if "::" in stripped and any(term in stripped for term in terms):
        return "definition"
    if suffix.lower() in {".h", ".hpp"} and any(term in stripped for term in terms):
        if ";" in stripped or "UFUNCTION" in stripped or "UPROPERTY" in stripped:
            return "declaration"
    if any(re.search(rf"\b{re.escape(term)}\s*\(", stripped) for term in terms):
        return "callsite"
    return ""


def refactor_surface_evidence(
    root: Path,
    focus_text: str,
    *,
    max_files: int = 6,
    max_lines_per_file: int = 8,
    max_chars: int = 4200,
) -> str:
    terms = _refactor_symbol_terms(focus_text)
    if not terms:
        return ""

    paths: list[Path] = []
    for candidate in _candidate_source_paths_from_text(root, focus_text):
        for paired in _matching_source_pair_paths(root, candidate):
            if paired not in paths:
                paths.append(paired)

    for path in iter_source_files(root):
        if path in paths or path.suffix.lower() not in {".h", ".hpp", ".cpp", ".c", ".cc"}:
            continue
        text = read_text(path)
        if any(term in text for term in terms):
            paths.append(path)
        if len(paths) >= max_files * 2:
            break

    if not paths:
        return ""

    lines = [
        "Multifile/refactor surface evidence (current project only):",
        "- Verify declaration -> definition -> callsite -> binding -> override before patching.",
        "- Prefer exact patches on the listed files; do not rewrite unrelated files.",
        "- Symbol terms: " + ", ".join(terms),
    ]
    root_resolved = root.resolve()
    files_added = 0
    for path in paths:
        if files_added >= max_files:
            break
        try:
            rel = path.resolve().relative_to(root_resolved).as_posix()
        except ValueError:
            continue
        text = read_text(path)
        file_lines: list[str] = []
        for line_no, raw_line in enumerate(text.splitlines(), start=1):
            if not any(term in raw_line for term in terms) and not re.search(
                r"\b(?:AddDynamic|AddUObject|BindAction|BindUFunction|DECLARE_.*DELEGATE|override)\b",
                raw_line,
            ):
                continue
            role = _refactor_line_role(raw_line, terms, path.suffix)
            if not role:
                continue
            compact = re.sub(r"\s+", " ", raw_line).strip()
            file_lines.append(f"- {role} line {line_no}: {compact}")
            if len(file_lines) >= max_lines_per_file:
                break
        if not file_lines:
            continue
        lines.append("")
        lines.append(f"## {rel}")
        lines.extend(file_lines)
        files_added += 1
        if len("\n".join(lines)) >= max_chars:
            lines.append("- ... refactor surface evidence truncated.")
            break
    return "\n".join(lines)[:max_chars] if files_added else ""


def _should_include_refactor_surface(mode: str) -> bool:
    normalized = str(mode or "").strip().lower()
    return normalized == "multifile_refactor" or normalized.startswith("refactor_")


def focused_current_source_evidence(
    root: Path,
    focus_text: str,
    route: dict[str, Any] | None = None,
    *,
    mode: str = "",
    max_chars: int = 6000,
) -> str:
    declaration_evidence = declaration_definition_evidence(root, focus_text, route)
    refactor_evidence = refactor_surface_evidence(root, focus_text) if _should_include_refactor_surface(mode) else ""
    pair_context = "" if (declaration_evidence or refactor_evidence) else focused_source_pair_context(root, focus_text)
    return "\n\n".join(
        part for part in (declaration_evidence, refactor_evidence, pair_context) if part.strip()
    )[:max_chars]


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


def safe_output_path(root: Path, relative_path: str) -> Path:
    if not relative_path or "\x00" in relative_path:
        raise ValueError("empty or invalid path")
    raw = Path(relative_path.replace("/", "\\"))
    if raw.is_absolute() or raw.drive:
        raise ValueError(f"absolute paths are not allowed: {relative_path}")
    if any(part in {"..", ""} for part in raw.parts):
        raise ValueError(f"path traversal is not allowed: {relative_path}")
    suffix = raw.suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError(f"unsupported file suffix: {relative_path}")
    parts = raw.parts
    allowed = False
    if parts[0] in {"Source", "Config"}:
        allowed = True
    elif parts[0] == "Plugins" and len(parts) >= 3:
        if parts[2] == "Source" or raw.suffix.lower() == ".uplugin":
            allowed = True
    elif len(parts) == 1 and raw.suffix.lower() == ".uproject":
        allowed = True
    if not allowed:
        raise ValueError(
            "writes are limited to Source/, Config/, project .uproject, "
            "and Plugins/<Plugin>/Source/ or plugin descriptors"
        )
    target = (root / raw).resolve()
    resolved_root = root.resolve()
    if target != resolved_root and resolved_root not in target.parents:
        raise ValueError(f"path escapes workspace: {relative_path}")
    return target


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
        max_files = max(max_files, 3)
    file_count = len(bundle.get("files") or [])
    patch_count = len(bundle.get("patches") or [])
    total = file_count + patch_count
    if max_files <= 0 and total > 0:
        raise ValueError("active sampling profile disallows file edits (maxFilesPerEdit=0)")
    if max_files > 0 and total > max_files:
        raise ValueError(
            f"too many edits ({total}); active profile maxFilesPerEdit={max_files}"
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
        aligned["broadMode"] = "module_fix"
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


def route_forbidden_action_blockers(route: dict[str, Any] | None, bundle: dict[str, Any]) -> list[str]:
    """Reject route-specific forbidden edits without globally hard-enforcing patch targets."""
    route = route or {}
    forbidden = " ".join(str(item).lower() for item in route.get("forbiddenActions") or [])
    if not forbidden:
        return []

    issues: list[str] = []
    build_cs_text_value = bundle_text_for_path(bundle, ".build.cs")
    if (
        "adding unrealed to runtime module" in forbidden
        and bundle_includes_build_cs(bundle)
        and "UnrealEd" in build_cs_text_value
    ):
        issues.append(
            "The current error route forbids fixing runtime/editor boundary drift by adding UnrealEd to a runtime "
            "module Build.cs. Remove the Build.cs dependency change and guard or isolate the editor-only source API."
        )
    if (
        "build.cs-first fix without module evidence" in forbidden
        and bundle_includes_build_cs(bundle)
        and not request_requests_build_cs_fix(str(route.get("errorSubkind") or ""))
    ):
        issues.append(
            "The current error route forbids a Build.cs-first edit without module evidence. Patch the matching "
            "header/cpp surface instead, or show module evidence before changing Build.cs."
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


def no_change_blockers(request: str, root: Path, findings: list[Finding]) -> list[str]:
    issues: list[str] = []
    build_text_value = build_cs_text(root)
    declared_modules = declared_build_modules(build_text_value)
    public_modules = public_build_modules(build_text_value)
    uses_gameplay_tags = source_uses_gameplay_tags(root)
    public_uses_gameplay_tags = source_uses_gameplay_tags(root, public_only=True)
    if request_mentions_gameplay_tags(request) or uses_gameplay_tags:
        if "GameplayTags" not in declared_modules:
            issues.append(
                'The request mentions GameplayTags, but the current Build.cs still does not declare "GameplayTags".'
            )
        elif public_uses_gameplay_tags and "GameplayTags" not in public_modules:
            issues.append(
                'A public header exposes GameplayTags types, but Build.cs does not declare "GameplayTags" in PublicDependencyModuleNames.'
            )
    if request_mentions_any(request, ("generated.h", "uht", "unrealheadertool")):
        generated_findings = [finding for finding in findings if finding.code.startswith("GENERATED_H")]
        if generated_findings:
            issues.append(
                "The request is a reflection/generated.h fix, but static validation still reports generated.h issues."
            )
    if request_mentions_any(request, ("signature", "시그니처", "declaration", "definition", ".cpp")):
        signature_findings = [
            finding
            for finding in findings
            if finding.code in {"CPP_FUNCTION_NOT_DECLARED_IN_HEADER", "CPP_FUNCTION_SIGNATURE_MISMATCH"}
        ]
        if signature_findings:
            issues.append(
                "The request appears to be a header/.cpp signature fix, but static validation still reports a .cpp/header mismatch."
            )
    delegate_findings = [finding for finding in findings if finding.code == "DELEGATE_BROADCAST_SIGNATURE_MISMATCH"]
    if delegate_findings or request_mentions_any(request, ("broadcast", "delegate")):
        if delegate_findings:
            issues.append(
                "The delegate Broadcast callsite still does not match the declared payload; patch the exact .Broadcast(...) line in the matching cpp."
            )
    return issues


def delegate_broadcast_callsite_fix_context(
    request: str,
    route: dict[str, Any] | None = None,
    findings: list[Finding] | None = None,
) -> bool:
    if route and route.get("errorSubkind") == "DELEGATE_BROADCAST_SIGNATURE_MISMATCH":
        return True
    if any(finding.code == "DELEGATE_BROADCAST_SIGNATURE_MISMATCH" for finding in (findings or [])):
        return True
    lower = request.lower()
    if any(term in lower for term in ("handler signature", "declaration and definition", "implementer header")):
        return False
    if any(
        term in lower
        for term in (
            "broadcast call",
            "does not take 0 arguments",
            "too few arguments",
            "payload",
            "fons",
            "c2660",
        )
    ):
        return True
    if "broadcast" in lower and "delegate" in lower:
        return True
    return False


def multifile_surface_enforced(mode: str, request: str) -> bool:
    if str(mode or "") == "multifile_refactor":
        return True
    lower = str(request or "").lower()
    markers = (
        "multi-file",
        "multifile",
        "across header",
        "declaration, definition",
        "call site",
        "callsite",
        "registration callsite",
        "implementer header",
        "consumer cpp",
    )
    return any(marker in lower for marker in markers)


def changed_paths_between(before: dict[str, str], after: dict[str, str]) -> list[str]:
    changed = [path for path in sorted(set(before) | set(after)) if before.get(path) != after.get(path)]
    return changed


def restore_changed_paths(root: Path, snapshot: dict[str, str], changed_paths: list[str]) -> None:
    for relative in changed_paths:
        target = safe_output_path(root, relative)
        old_text = snapshot.get(relative)
        if old_text is None:
            if target.exists() and target.is_file():
                target.unlink()
            continue
        write_file(target, old_text)


def edit_scope_blockers(
    request: str,
    before: dict[str, str],
    after: dict[str, str],
    root: Path,
    *,
    route: dict[str, Any] | None = None,
    findings: list[Finding] | None = None,
) -> list[str]:
    issues: list[str] = []
    changed = changed_paths_between(before, after)
    changed_lower = [path.lower() for path in changed]
    changed_build = any(path.endswith(".build.cs") for path in changed_lower)
    changed_cpp = any(Path(path).suffix.lower() in {".cpp", ".c", ".cc"} for path in changed)
    changed_header = any(Path(path).suffix.lower() in {".h", ".hpp"} for path in changed)
    uses_gameplay_tags = source_uses_gameplay_tags(root)
    public_uses_gameplay_tags = source_uses_gameplay_tags(root, public_only=True)
    declared_modules = declared_build_modules(build_cs_text(root))
    public_modules = public_build_modules(build_cs_text(root))

    if request_mentions_gameplay_tags(request) or uses_gameplay_tags:
        if "GameplayTags" not in declared_modules:
            issues.append(
                'GameplayTags is still missing from Build.cs; inspect the actual *.Build.cs and add it to PublicDependencyModuleNames when a public header exposes GameplayTags types.'
            )
        elif public_uses_gameplay_tags and "GameplayTags" not in public_modules:
            issues.append(
                'GameplayTags is declared outside PublicDependencyModuleNames, but a public header exposes GameplayTags types.'
            )
    if request_requests_build_cs_fix(request):
        if not changed_build:
            issues.append(
                "The request targets Build.cs/module dependencies, but this edit did not change any *.Build.cs file."
            )
    if request_mentions_any(request, ("signature", "시그니처", "declaration", "definition", ".cpp")):
        if changed_header and not changed_cpp:
            issues.append(
                "The request appears to require matching the .cpp definition to the header declaration; do not change only the header unless the request explicitly asks for that."
            )
    if request_mentions_any(request, ("lnk2019", "unresolved external", "missing cpp definition", "missing implementation")):
        issues.extend(missing_definition_call_removal_blockers(before, after))
    callsite_only_delegate = delegate_broadcast_callsite_fix_context(request, route, findings)
    if request_mentions_any(request, ("delegate", "broadcast")) and not callsite_only_delegate:
        if changed_cpp and not changed_header:
            issues.append(
                "Delegate handler signature fixes must update the header declaration and .cpp definition together."
            )
    return issues


def apply_bundle(root: Path, bundle: dict[str, Any]) -> list[Path]:
    written: list[Path] = []
    for item in bundle.get("patches") or []:
        target = safe_output_path(root, item["path"])
        old_text = str(item.get("oldText") or "")
        new_text = str(item.get("newText") or "")
        expected = int(item.get("expectedOccurrences") or 1)
        ok, msg = validate_patch_item(target, old_text, new_text, expected)
        if not ok:
            raise ValueError(f"patch failed for {item['path']}: {msg}")
        _, _, updated = apply_single_patch(target, old_text, new_text, expected)
        write_file(target, updated)
        written.append(target)
    for item in bundle["files"]:
        target = safe_output_path(root, item["path"])
        write_file(target, item["content"])
        written.append(target)
    return written



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
    targets: set[Path] = set()
    for finding in editor_findings:
        target = (root / finding.path).resolve()
        if target.is_file():
            targets.add(target)
    for path in iter_source_files(root):
        if path.suffix.lower() not in {".cpp", ".cc", ".c"}:
            continue
        text = read_text(path)
        if "UnrealEd.h" in text and "WITH_EDITOR" not in text:
            targets.add(path)
    for target in sorted(targets, key=lambda p: str(p)):
        text = read_text(target)
        lines = text.splitlines()
        out: list[str] = []
        idx = 0
        while idx < len(lines):
            line = lines[idx]
            stripped = line.strip()
            if '#include "UnrealEd.h"' in line or "#include <UnrealEd.h>" in line:
                idx += 1
                continue
            if "GEditor->" in line:
                if not out or out[-1] != "#if WITH_EDITOR":
                    out.append("#if WITH_EDITOR")
                out.append("\t// Editor API isolated from runtime module linkage.")
                out.append("#endif")
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


def edit_limits_for_mode(base_limits: dict[str, Any], mode: str) -> dict[str, Any]:
    limits = dict(base_limits or {})
    if str(mode or "") == "multifile_refactor":
        limits["maxFilesPerEdit"] = max(int(limits.get("maxFilesPerEdit") or 2), 3)
    return limits


def multifile_surface_blockers(
    request: str,
    before: dict[str, str],
    after: dict[str, str],
    root: Path,
    *,
    mode: str,
) -> list[str]:
    if not multifile_surface_enforced(mode, request):
        return []
    changed = changed_paths_between(before, after)
    if not changed:
        return []
    changed_headers = [path for path in changed if Path(path).suffix.lower() in {".h", ".hpp"}]
    changed_cpp = [path for path in changed if Path(path).suffix.lower() in {".cpp", ".c", ".cc"}]
    lower = request.lower()
    issues: list[str] = []

    if any(term in lower for term in ("delegate", "signature", "declaration", "definition", "interface")):
        if changed_headers and not changed_cpp:
            issues.append(
                "Multifile fix incomplete: header declaration changed without matching .cpp definition. "
                f"Changed: {', '.join(changed_headers)}"
            )
        elif changed_cpp and not changed_headers:
            issues.append(
                "Multifile fix incomplete: .cpp definition changed without matching header declaration. "
                f"Changed: {', '.join(changed_cpp)}"
            )

    if "interface" in lower and len(changed) < 2:
        issues.append(
            "Interface mismatch fixes require implementer header and cpp in the same patch. "
            f"Changed only: {', '.join(changed)}"
        )

    if any(term in lower for term in ("callback", "registration", "parameter list")) and len(changed) < 2:
        issues.append(
            "Callback parameter expansion requires declaration, definition, and registration updates together. "
            f"Changed only: {', '.join(changed)}"
        )

    surface = refactor_surface_evidence(root, request, max_files=8, max_lines_per_file=4, max_chars=2000)
    if surface and len(changed) == 1 and ("callsite" in surface.lower() or "consumer" in lower):
        issues.append(
            "Multifile refactor likely needs callsite/consumer updates in addition to "
            f"{changed[0]}."
        )
    return issues


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

    command = build_ubt_command(ubt_path, project_file, target, platform, configuration)
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


def rerag_for_build_errors(args: argparse.Namespace, records: list[dict[str, Any]], output: str) -> ParsedBuildFeedback:
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
    rows = search_index(
        index,
        query,
        args.top_k,
        SearchOptions(mode=mode, projects=project_filters, candidate_limit=max(120, args.top_k * 20)),
    )
    return ParsedBuildFeedback(records, mode, query, assemble_context(rows, query, mode))


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


def static_validation_retry_feedback(
    findings: list[Finding] | None,
    route: dict[str, Any],
    *,
    build_output: str = "",
) -> str:
    if any(finding.code == "CPP_FUNCTION_SIGNATURE_MISMATCH" for finding in findings or []):
        return STATIC_SIGNATURE_RETRY_HINT
    if any(finding.code == "DELEGATE_BROADCAST_SIGNATURE_MISMATCH" for finding in findings or []):
        return DELEGATE_BROADCAST_RETRY_HINT
    if route.get("errorSubkind") == "DELEGATE_BROADCAST_SIGNATURE_MISMATCH":
        return DELEGATE_BROADCAST_RETRY_HINT
    if route.get("errorSubkind") == "LNK_MISSING_CPP_DEFINITION":
        if re.search(r"\bLNK2019\b|unresolved external|missing cpp definition", build_output or "", re.I):
            return LNK_MISSING_DEFINITION_RETRY_HINT
    return ""


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


def retry_feedback_block(recommendation: dict[str, Any]) -> str:
    lines: list[str] = []
    if recommendation.get("sameErrorRepeated"):
        lines.append(
            "The previous attempt repeated the same error. Do not repeat the same patch. "
            "Reclassify the root cause and read the required files from errorRoute before editing."
        )
    repeat_count = int(recommendation.get("sameErrorRepeatCount") or 0)
    if repeat_count >= 2:
        lines.append(
            f"Same error repeated {repeat_count} times. Change patch target and gather broader evidence before editing."
        )
    if recommendation.get("noOpEdit"):
        lines.append("The previous attempt produced no effective file change. Do not submit the same patch again.")
    for hint in recommendation.get("requiredPromptHints") or []:
        if hint not in lines:
            lines.append(str(hint))
    return "\n".join(lines)


def repeat_patch_blockers(bundle: dict[str, Any], blocked_paths: list[str]) -> list[str]:
    if not blocked_paths:
        return []
    proposed = {path.replace("\\", "/") for path in proposed_bundle_paths(bundle)}
    hits = sorted(path for path in proposed if path in set(blocked_paths))
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
            f"For LARGE existing files, prefer patches[] with exact oldText/newText (expectedOccurrences required).",
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
    if not args.dry_run:
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
        nonlocal previous_retry_record
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
    if args.mode in {"reflection_fix", "compile_fix"}:
        static_autofix_written.extend(apply_generated_h_missing_autofix(project_root, initial_findings))
        static_autofix_written.extend(apply_generated_h_order_autofix(project_root, initial_findings))
    if args.mode in {"compile_fix", "module_fix", "editor_runtime_fix"}:
        static_autofix_written.extend(apply_editor_runtime_guard_autofix(project_root, initial_findings))
        static_autofix_written.extend(apply_delegate_broadcast_autofix(project_root, initial_findings))
        static_autofix_written.extend(apply_uobject_newobject_autofix(project_root, initial_findings))
    if args.mode in {"reflection_fix", "compile_fix"}:
        static_autofix_written.extend(apply_blueprint_event_rename_autofix(project_root, initial_findings))
    if static_autofix_written:
        last_written = static_autofix_written
        last_findings = validate_unreal_readiness(project_root, Path(args.module_graph))
        static_report = format_findings(last_findings)
        write_file(run_dir / "static_autofix.txt", static_report + "\n")
        write_file(final_diff_path, diff_snapshots(baseline_snapshot, snapshot_project_files(project_root)) + "\n")
        if not has_static_errors(last_findings) and not args.skip_build:
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
                    answer="Applied safe static generated.h autofix.",
                    written=last_written,
                    build_result=last_build,
                    findings=last_findings,
                    final_diff_path=final_diff_path,
                )
                write_file(run_dir / "final_answer.md", summary)
                print(summary)
                return 0
            previous_feedback = "Safe static generated.h autofix was applied, but UBT still failed:\n" + tail_text(
                last_build.output,
                token_budget.feedback_tail_chars("compile_fix"),
            )
        else:
            previous_feedback = "Safe static generated.h autofix was applied, but static validation still reports issues:\n" + static_report

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
        elif attempt == 1:
            rag_context = collect_cached_initial_rag(request)
            symbol_context = optional_symbol_graph_context(request)
            if symbol_context:
                rag_context = rag_context + "\n\n" + symbol_context

        attempt_dir = run_dir / f"attempt_{attempt}"
        attempt_dir.mkdir(parents=True, exist_ok=True)
        first_attempt_patch_route = attempt == 1 and should_use_patch_preset_on_first_attempt(active_route, args.mode)
        attempt_prefix = (
            f"Compile loop attempt {attempt}/{args.max_attempts}. "
            "List at most 3 assumptions; apply one minimal diff this turn.\n\n"
        )
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
                mode=original_mode if attempt >= 2 else args.mode,
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
        compile_patch_turn = attempt >= 2 or first_attempt_patch_route
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
        route_blockers = route_forbidden_action_blockers(active_route, bundle)
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
            if not args.allow_empty_files and not answer_claims_no_changes(last_answer):
                last_findings = validate_unreal_readiness(project_root, Path(args.module_graph))
                static_report = format_findings(last_findings)
                blockers = no_change_blockers(request, project_root, last_findings)
                module_prefix = (
                    module_fix_retry_feedback(request, project_root) + "\n"
                    if original_mode == "module_fix"
                    else ""
                )
                previous_feedback = (
                    module_prefix
                    + "Model returned no files without clearly saying the current files already satisfy the request. "
                    "Inspect the current project state and either return the smallest changed file bundle, or return "
                    "an empty files array with explicit evidence that no new edit is needed.\n"
                    + static_report
                    + ("\nNo-change blockers:\n" + "\n".join(f"- {issue}" for issue in blockers) if blockers else "")
                )
                write_file(attempt_dir / "validation_feedback.txt", previous_feedback)
                record_validation_rejection(
                    attempt=attempt,
                    attempt_dir=attempt_dir,
                    feedback=previous_feedback,
                    blockers=blockers,
                    bundle=bundle,
                    rejection_kind="empty_files_without_evidence",
                )
                continue
            last_findings = validate_unreal_readiness(project_root, Path(args.module_graph))
            static_report = format_findings(last_findings)
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
                write_file(attempt_dir / "validation_feedback.txt", previous_feedback)
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
            before_apply = snapshot_project_files(project_root)
            last_written = apply_bundle(project_root, bundle)
            after_apply = snapshot_project_files(project_root)
            attempt_diff = diff_snapshots(before_apply, after_apply)
            write_file(attempt_dir / "diff.patch", attempt_diff + "\n")
            if attempt_diff == "No file changes detected.":
                last_written = []
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
            scope_blockers = edit_scope_blockers(
                request,
                before_apply,
                after_apply,
                project_root,
                route=active_route,
                findings=last_findings,
            )
            if scope_blockers:
                rejected_changed_paths = changed_paths_between(before_apply, after_apply)
                restore_changed_paths(project_root, before_apply, rejected_changed_paths)
                last_written = []
                retry_tail = BUILD_CS_RETRY_FEEDBACK if any("Build.cs" in issue for issue in scope_blockers) else ""
                previous_feedback = (
                    (retry_tail + "\n" if retry_tail else "")
                    + "Edit rejected because it does not match the requested compile-fix scope:\n"
                    + "\n".join(f"- {issue}" for issue in scope_blockers)
                    + "\nUse the current project state as authoritative and change the specific file(s) needed."
                )
                write_file(attempt_dir / "validation_feedback.txt", previous_feedback)
                record_validation_rejection(
                    attempt=attempt,
                    attempt_dir=attempt_dir,
                    feedback=previous_feedback,
                    blockers=scope_blockers,
                    bundle=bundle,
                    changed_paths=[],
                    rejection_kind="edit_scope_blocker",
                )
                continue
            surface_blockers = multifile_surface_blockers(
                request,
                before_apply,
                after_apply,
                project_root,
                mode=original_mode,
            )
            if surface_blockers:
                rejected_changed_paths = changed_paths_between(before_apply, after_apply)
                restore_changed_paths(project_root, before_apply, rejected_changed_paths)
                last_written = []
                previous_feedback = (
                    "Multifile surface coverage rejected the edit:\n"
                    + "\n".join(f"- {issue}" for issue in surface_blockers)
                    + "\nReturn one coherent patch that updates every required declaration, definition, and callsite."
                )
                write_file(attempt_dir / "validation_feedback.txt", previous_feedback)
                record_validation_rejection(
                    attempt=attempt,
                    attempt_dir=attempt_dir,
                    feedback=previous_feedback,
                    blockers=surface_blockers,
                    bundle=bundle,
                    changed_paths=[],
                    rejection_kind="multifile_incomplete",
                )
                continue
        except Exception as exc:
            hint = ""
            if "bundle" in locals():
                for item in (bundle.get("patches") or []):
                    target = safe_output_path(project_root, item["path"])
                    hint = patch_apply_hint(target, str(item.get("oldText") or ""))
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
        if has_static_errors(last_findings) and not args.skip_static_gate:
            previous_feedback = static_report
            record_validation_rejection(
                attempt=attempt,
                attempt_dir=attempt_dir,
                feedback=previous_feedback,
                bundle=bundle,
                changed_paths=changed_paths_between(before_apply, after_apply),
                rejection_kind="static_gate",
            )
            continue

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
            return 0

        build_records = parse_build_feedback(last_build.log_path, project_root, last_build.output)
        last_build_records = build_records
        parsed_feedback = rerag_for_build_errors(args, build_records, last_build.output)
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
                f"Failure-specific RAG mode: {parsed_feedback.mode}",
                "Failure-specific RAG context:",
                parsed_feedback.rag_context,
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
        answer=last_answer or "최대 시도 횟수 안에 컴파일 가능한 결과를 만들지 못했습니다.",
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
