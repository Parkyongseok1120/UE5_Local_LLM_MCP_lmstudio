#!/usr/bin/env python
"""LM Studio wrapper that writes Unreal prototype files, validates, and builds."""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from rag_context import assemble_context
from load_sampling_preset import preset_for_wrapper, profile_edit_limits, set_sampling_profile_for_model
from preflight_lmstudio import extract_assistant_text
from rag_search import SearchOptions, search as search_index
import token_budget
from workspace_paths import resolve_active_project_path, resolve_ubt_path

try:
    from collect_build_logs import extract_error
except Exception:
    extract_error = None  # type: ignore[assignment]


DEFAULT_LMSTUDIO_URL = "http://localhost:1234/v1"
DEFAULT_UBT_PATH = str(resolve_ubt_path())
WRAPPER_RULES_PATH = Path("RAG_Project_Guidelines/Unreal_Programming/07_Wrapper_Mandatory_Rules.md")
PROMPT_PATH = Path("prompts/unreal_cpp_assistant.md")
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
class Finding:
    severity: str
    path: str
    line: int
    code: str
    message: str


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
)


def summarize_interesting_lines(text: str, max_lines: int = 14) -> list[str]:
    results: list[str] = []
    seen: set[str] = set()
    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line or len(line) > 220:
            continue
        if not any(pattern.search(line) for pattern in PROJECT_STATE_LINE_PATTERNS):
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
) -> str:
    default_files, default_chars = token_budget.project_summary_limits(mode)
    max_files = default_files if max_files is None else max_files
    max_chars = default_chars if max_chars is None else max_chars
    snapshot = snapshot_project_files(root)
    if not snapshot:
        return "Current project file state summary: no text project files found."

    lines = [
        "Current project file state summary (authoritative):",
        "- Treat these files as already existing. Do not re-add declarations, includes, modules, or bindings that are already present.",
        "- Return complete content only for files that need a new change; omitted files remain unchanged.",
    ]
    modes_with_full_build_cs = frozenset({"module_fix", "compile_fix"})
    included = 0
    if mode in modes_with_full_build_cs:
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
        if relative.startswith("Source/"):
            return (0, relative)
        if relative.startswith("Plugins/"):
            return (1, relative)
        if relative.startswith("Config/"):
            return (2, relative)
        return (3, relative)

    for relative, text in sorted(snapshot.items(), key=state_sort_key):
        if included >= max_files:
            lines.append(f"- ... {len(snapshot) - included} additional file(s) omitted from this summary.")
            break
        suffix = Path(relative).suffix.lower()
        if suffix not in {".h", ".hpp", ".cpp", ".c", ".cc", ".cs", ".ini", ".json", ".uproject", ".uplugin"}:
            continue
        if relative.lower().endswith(".build.cs") and mode in modes_with_full_build_cs:
            continue
        interesting = summarize_interesting_lines(text)
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
                "EngineAssociation": "5.7",
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


def enforce_edit_limits(bundle: dict[str, Any], limits: dict[str, Any]) -> None:
    max_files = int(limits.get("maxFilesPerEdit") or 0)
    file_count = len(bundle.get("files") or [])
    patch_count = len(bundle.get("patches") or [])
    total = file_count + patch_count
    if max_files <= 0 and total > 0:
        raise ValueError("active sampling profile disallows file edits (maxFilesPerEdit=0)")
    if max_files > 0 and total > max_files:
        raise ValueError(
            f"too many edits ({total}); active profile maxFilesPerEdit={max_files}"
        )


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
    lowered = str(answer or "").lower()
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


BUILD_CS_RETRY_FEEDBACK = (
    "If the error is caused by a missing Unreal module dependency, you must edit the relevant .Build.cs file. "
    "Do not only explain the required dependency. You must produce a concrete file write/patch for the Build.cs file. "
    "The task is not complete until the Build.cs file has been modified.\n"
    "If code includes FGameplayTag, FGameplayTagContainer, UGameplayTagsManager, or GameplayTag-related headers, "
    'check whether "GameplayTags" exists in PublicDependencyModuleNames or PrivateDependencyModuleNames. '
    'If it is missing, modify the module Build.cs file and add "GameplayTags".'
)


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
    uses_gameplay_tags = source_uses_gameplay_tags(root)
    needs_build_cs = (
        request_mentions_any(
            request,
            ("build.cs", "publicdependencymodulenames", "module dependency", "gameplaytags", "gameplaytag"),
        )
        or uses_gameplay_tags
        or answer_claims_build_cs_edit(answer)
    )
    if not needs_build_cs:
        return issues
    changed_build = False
    if before is not None and after is not None:
        changed_build = any(
            path.lower().endswith(".build.cs")
            for path in changed_paths_between(before, after)
        )
    in_bundle = bundle_includes_build_cs(bundle)
    if (answer_claims_build_cs_edit(answer) or (needs_build_cs and not answer_claims_no_changes(answer))) and not in_bundle and not changed_build:
        issues.append(
            "You claimed or implied a Build.cs / module-dependency fix, but the response did not include any "
            "*.Build.cs file in files[] or patches[]. Return the updated Build.cs content."
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
    if request_mentions_any(request, ("gameplaytags", "gameplaytag", "GameplayTagContainer.h", "Build.cs")) or uses_gameplay_tags:
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
    return issues


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


def edit_scope_blockers(request: str, before: dict[str, str], after: dict[str, str], root: Path) -> list[str]:
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

    if request_mentions_any(request, ("gameplaytags", "gameplaytag", "GameplayTagContainer.h")) or uses_gameplay_tags:
        if "GameplayTags" not in declared_modules:
            issues.append(
                'GameplayTags is still missing from Build.cs; inspect the actual *.Build.cs and add it to PublicDependencyModuleNames when a public header exposes GameplayTags types.'
            )
        elif public_uses_gameplay_tags and "GameplayTags" not in public_modules:
            issues.append(
                'GameplayTags is declared outside PublicDependencyModuleNames, but a public header exposes GameplayTags types.'
            )
    if request_mentions_any(request, ("build.cs", "publicdependencymodulenames", "module dependency")):
        if not changed_build:
            issues.append(
                "The request targets Build.cs/module dependencies, but this edit did not change any *.Build.cs file."
            )
    if request_mentions_any(request, ("signature", "시그니처", "declaration", "definition", ".cpp")):
        if changed_header and not changed_cpp:
            issues.append(
                "The request appears to require matching the .cpp definition to the header declaration; do not change only the header unless the request explicitly asks for that."
            )
    return issues


def apply_bundle(root: Path, bundle: dict[str, Any]) -> list[Path]:
    written: list[Path] = []
    for item in bundle.get("patches") or []:
        target = safe_output_path(root, item["path"])
        ok, msg, updated = apply_single_patch(
            target,
            item["oldText"],
            item["newText"],
            int(item.get("expectedOccurrences") or 1),
        )
        if not ok:
            raise ValueError(f"patch failed for {item['path']}: {msg}")
        write_file(target, updated)
        written.append(target)
    for item in bundle["files"]:
        target = safe_output_path(root, item["path"])
        write_file(target, item["content"])
        written.append(target)
    return written


def iter_source_files(root: Path) -> list[Path]:
    suffixes = {".h", ".hpp", ".cpp", ".c", ".cc", ".cs"}
    ignored = {"Binaries", "Intermediate", "Saved", "DerivedDataCache"}
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        if any(part in ignored for part in path.parts):
            continue
        files.append(path)
    return files


def line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def include_lines(text: str) -> list[tuple[int, str]]:
    results: list[tuple[int, str]] = []
    for index, line in enumerate(text.splitlines(), start=1):
        match = re.match(r'\s*#\s*include\s+[<"]([^>"]+)[>"]', line)
        if match:
            results.append((index, match.group(1)))
    return results


REFLECTED_TYPE_RE = re.compile(r"\b(UCLASS|USTRUCT|UENUM|UINTERFACE)\s*\(")
REFLECTION_RE = re.compile(r"\b(UCLASS|USTRUCT|UENUM|UINTERFACE|GENERATED_BODY)\s*\(")
EDITOR_ONLY_INCLUDES = (
    "UnrealEd.h",
    "Editor.h",
    "EditorUtilityWidget.h",
    "EditorUtilitySubsystem.h",
    "Kismet2/",
    "AssetToolsModule.h",
    "LevelEditor.h",
)
RAW_UOBJECT_MEMBER_RE = re.compile(
    r"\b(?:UObject|AActor|APawn|AController|UActorComponent|USceneComponent|UDataAsset|"
    r"UTexture(?:2D)?|UMaterial(?:Interface|Instance)?|USoundBase|UAnimMontage)\s*\*\s*"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?:=\s*[^;]+)?;"
)
BASE_CLASS_INCLUDES = {
    "AActor": "GameFramework/Actor.h",
    "APawn": "GameFramework/Pawn.h",
    "ACharacter": "GameFramework/Character.h",
    "APlayerController": "GameFramework/PlayerController.h",
    "AGameModeBase": "GameFramework/GameModeBase.h",
    "AController": "GameFramework/Controller.h",
    "UObject": "UObject/Object.h",
    "UActorComponent": "Components/ActorComponent.h",
    "USceneComponent": "Components/SceneComponent.h",
    "UDataAsset": "Engine/DataAsset.h",
    "USaveGame": "GameFramework/SaveGame.h",
    "UUserWidget": "Blueprint/UserWidget.h",
    "UGameInstanceSubsystem": "Subsystems/GameInstanceSubsystem.h",
    "UWorldSubsystem": "Subsystems/WorldSubsystem.h",
    "UEngineSubsystem": "Subsystems/EngineSubsystem.h",
    "UInterface": "UObject/Interface.h",
}
CPP_SYMBOL_INCLUDES = {
    "UGameplayStatics::": "Kismet/GameplayStatics.h",
    "ConstructorHelpers::": "UObject/ConstructorHelpers.h",
    "DrawDebug": "DrawDebugHelpers.h",
    "DOREPLIFETIME": "Net/UnrealNetwork.h",
    "FObjectInitializer": "UObject/ObjectMacros.h",
}


def validate_generated_h(path: Path, text: str, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    includes = include_lines(text)
    generated = [(line, value) for line, value in includes if value.endswith(".generated.h")]
    if REFLECTION_RE.search(text) and not generated:
        findings.append(
            Finding(
                "error",
                str(path.relative_to(root)),
                1,
                "GENERATED_H_MISSING",
                f'Reflected Unreal header must include "{path.stem}.generated.h" as its last include.',
            )
        )
    if len(generated) > 1:
        findings.append(
            Finding(
                "error",
                str(path.relative_to(root)),
                generated[1][0],
                "GENERATED_H_DUPLICATE",
                "generated.h include appears more than once.",
            )
        )
    if generated:
        last_include_line = max(line for line, _ in includes)
        if generated[0][0] != last_include_line:
            findings.append(
                Finding(
                    "error",
                    str(path.relative_to(root)),
                    generated[0][0],
                    "GENERATED_H_NOT_LAST",
                    "generated.h must be the last include in the header.",
                )
            )
    return findings


def validate_reflected_namespace(path: Path, text: str, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    brace_depth = 0
    namespace_depths: list[int] = []
    pending_namespace = False
    for index, line in enumerate(text.splitlines(), start=1):
        namespace_match = re.search(r"\bnamespace\s+[A-Za-z_][A-Za-z0-9_:]*\b", line)
        if namespace_match and "{" in line:
            namespace_depths.append(brace_depth + 1)
            pending_namespace = False
        elif namespace_match and ";" not in line:
            pending_namespace = True
        elif pending_namespace and "{" in line:
            namespace_depths.append(brace_depth + 1)
            pending_namespace = False
        match = REFLECTED_TYPE_RE.search(line)
        if match and namespace_depths:
            findings.append(
                Finding(
                    "error",
                    str(path.relative_to(root)),
                    index,
                    "REFLECTED_TYPE_IN_NAMESPACE",
                    f"{match.group(1)} reflected types should not be wrapped in a new C++ namespace.",
                )
            )
        brace_depth += line.count("{") - line.count("}")
        while namespace_depths and brace_depth < namespace_depths[-1]:
            namespace_depths.pop()
    return findings


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


def validate_blueprint_native_event_declarations(path: Path, text: str, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if "BlueprintNativeEvent" not in line:
            continue

        declaration_parts: list[str] = []
        cursor = index + 1
        while cursor < len(lines) and len(declaration_parts) < 6:
            candidate = lines[cursor].strip()
            cursor += 1
            if not candidate or candidate.startswith("UPROPERTY") or candidate.startswith("UFUNCTION"):
                continue
            declaration_parts.append(candidate)
            if ";" in candidate:
                break
        declaration = " ".join(declaration_parts)
        name_match = re.search(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", declaration)
        function_name = name_match.group(1) if name_match else ""

        if "= 0" in declaration or re.search(r"\bvirtual\b.*=\s*0\s*;", declaration):
            findings.append(
                Finding(
                    "error",
                    str(path.relative_to(root)),
                    cursor,
                    "BLUEPRINT_NATIVE_EVENT_PURE_VIRTUAL",
                    "BlueprintNativeEvent UFUNCTION declarations should not be made pure virtual; implement the generated _Implementation method instead.",
                )
            )

        if function_name:
            duplicate_re = re.compile(
                rf"\bvirtual\b[^\n;]*\b{re.escape(function_name)}\s*\([^;]*\)\s*(?:const\s*)?=\s*0\s*;"
            )
            for duplicate in duplicate_re.finditer(text):
                duplicate_line = line_number(text, duplicate.start())
                if duplicate_line > index + 1:
                    findings.append(
                        Finding(
                            "error",
                            str(path.relative_to(root)),
                            duplicate_line,
                            "BLUEPRINT_NATIVE_EVENT_DUPLICATE_VIRTUAL",
                            f"{function_name} duplicates a BlueprintNativeEvent as a pure virtual function; use {function_name}_Implementation in implementers.",
                        )
                    )
                    break
    return findings


def module_name_for_source_path(path: Path) -> str:
    parts = list(path.parts)
    lowered = [part.lower() for part in parts]
    if "source" not in lowered:
        return ""
    source_index = lowered.index("source")
    if source_index + 1 >= len(parts):
        return ""
    return parts[source_index + 1]


def validate_editor_only_runtime_includes(path: Path, text: str, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    module_name = module_name_for_source_path(path)
    if "editor" in module_name.lower():
        return findings
    for line, include_path in include_lines(text):
        if any(marker.lower() in include_path.lower() for marker in EDITOR_ONLY_INCLUDES):
            findings.append(
                Finding(
                    "error",
                    str(path.relative_to(root)),
                    line,
                    "EDITOR_ONLY_INCLUDE_IN_RUNTIME_MODULE",
                    f'Runtime module source includes editor-only header "{include_path}". Move it to an Editor module or guard/remove the dependency.',
                )
            )
    return findings


def validate_raw_uobject_members(path: Path, text: str, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    lines = text.splitlines()
    for index, line in enumerate(lines, start=1):
        if "(" in line:
            continue
        match = RAW_UOBJECT_MEMBER_RE.search(line)
        if not match:
            continue
        nearby = "\n".join(lines[max(0, index - 5) : index])
        if "UPROPERTY" in nearby or "TObjectPtr" in line:
            continue
        findings.append(
            Finding(
                "warning",
                str(path.relative_to(root)),
                index,
                "RAW_UOBJECT_MEMBER_WITHOUT_UPROPERTY",
                f'Raw UObject member "{match.group("name")}" is not visibly tracked by UPROPERTY/TObjectPtr and may be unsafe for garbage collection.',
            )
        )
    return findings


def validate_private_blueprint_access(path: Path, text: str, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    lines = text.splitlines()
    access = "private"
    index = 0
    while index < len(lines):
        line = lines[index]
        type_match = re.match(r"\s*(class|struct)\s+(?:[A-Z0-9_]+_API\s+)?[A-Za-z_][A-Za-z0-9_]*\b", line)
        if type_match:
            access = "private" if type_match.group(1) == "class" else "public"
        access_match = re.match(r"\s*(public|protected|private)\s*:", line)
        if access_match:
            access = access_match.group(1)
        if "UPROPERTY" in line:
            start = index
            block = line
            while ")" not in block and index + 1 < len(lines):
                index += 1
                block += "\n" + lines[index]
            if (
                access == "private"
                and re.search(r"\bBlueprintRead(?:Only|Write)\b", block)
                and "AllowPrivateAccess" not in block
            ):
                findings.append(
                    Finding(
                        "error",
                        str(path.relative_to(root)),
                        start + 1,
                        "PRIVATE_BLUEPRINT_ACCESS",
                        'private BlueprintReadOnly/BlueprintReadWrite UPROPERTY requires meta=(AllowPrivateAccess="true").',
                    )
                )
        index += 1
    return findings


def has_include(text: str, include_path: str) -> bool:
    return any(value == include_path or value.endswith("/" + include_path) for _, value in include_lines(text))


def class_base_names(text: str) -> dict[str, str]:
    bases: dict[str, str] = {}
    pattern = re.compile(
        r"\bclass\s+(?:[A-Z0-9_]+_API\s+)?(?P<class>[A-Za-z_][A-Za-z0-9_]*)\s*:\s*public\s+(?P<base>[A-Za-z_][A-Za-z0-9_]*)"
    )
    for match in pattern.finditer(text):
        bases[match.group("class")] = match.group("base")
    return bases


def class_bases(root: Path) -> dict[str, str]:
    bases: dict[str, str] = {}
    for path in root.rglob("*.h"):
        bases.update(class_base_names(read_text(path)))
    return bases


def validate_required_includes(path: Path, text: str, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    rel = str(path.relative_to(root))
    if path.suffix.lower() in {".h", ".hpp"}:
        for class_name, base_name in class_base_names(text).items():
            required = BASE_CLASS_INCLUDES.get(base_name)
            if required and not has_include(text, required):
                findings.append(
                    Finding(
                        "warning",
                        rel,
                        line_number(text, text.find(class_name)),
                        "MISSING_BASE_CLASS_INCLUDE",
                        f'{class_name} derives from {base_name}; include "{required}" directly before the generated header.',
                    )
                )
        if "FTimerHandle" in text and not has_include(text, "TimerManager.h"):
            findings.append(
                Finding(
                    "warning",
                    rel,
                    line_number(text, text.find("FTimerHandle")),
                    "MISSING_TIMER_MANAGER_INCLUDE",
                    'FTimerHandle in a header usually requires "TimerManager.h".',
                )
            )
        if ("FGameplayTag" in text or "FGameplayTagContainer" in text) and not has_include(text, "GameplayTagContainer.h"):
            findings.append(
                Finding(
                    "warning",
                    rel,
                    line_number(text, max(text.find("FGameplayTag"), text.find("FGameplayTagContainer"))),
                    "MISSING_GAMEPLAY_TAG_INCLUDE",
                    'Gameplay tag value types require "GameplayTagContainer.h" in the header that exposes them.',
                )
            )
    if path.suffix.lower() in {".cpp", ".c", ".cc"}:
        for token, include_path in CPP_SYMBOL_INCLUDES.items():
            token_index = text.find(token)
            if token_index != -1 and not has_include(text, include_path):
                findings.append(
                    Finding(
                        "warning",
                        rel,
                        line_number(text, token_index),
                        "MISSING_CPP_SYMBOL_INCLUDE",
                        f'Code uses {token}; include "{include_path}" in this .cpp file.',
                    )
                )
    return findings


def iter_cpp_definition_blocks(text: str) -> list[tuple[str, str, int, int, str]]:
    blocks: list[tuple[str, str, int, int, str]] = []
    pattern = re.compile(
        r"(?P<class>[A-Za-z_][A-Za-z0-9_]*)::(?P<func>~?[A-Za-z_][A-Za-z0-9_]*)\s*\([^;{}]*\)\s*(?::[^{;]*)?\{",
        flags=re.MULTILINE,
    )
    for match in pattern.finditer(text):
        open_index = match.end() - 1
        close_index = find_matching_brace(text, open_index)
        if close_index == -1:
            continue
        blocks.append(
            (
                match.group("class"),
                match.group("func"),
                match.start(),
                close_index,
                text[open_index + 1 : close_index],
            )
        )
    return blocks


def block_for_offset(blocks: list[tuple[str, str, int, int, str]], offset: int) -> tuple[str, str, int, int, str] | None:
    for block in blocks:
        if block[2] <= offset <= block[3]:
            return block
    return None


def validate_constructor_lifecycle_usage(path: Path, text: str, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    rel = str(path.relative_to(root))
    blocks = iter_cpp_definition_blocks(text)
    for token, code, message in (
        (
            "CreateDefaultSubobject<",
            "CREATE_DEFAULT_SUBOBJECT_OUTSIDE_CONSTRUCTOR",
            "CreateDefaultSubobject should only be used in the owning class constructor.",
        ),
        (
            "ConstructorHelpers::",
            "CONSTRUCTOR_HELPERS_OUTSIDE_CONSTRUCTOR",
            "ConstructorHelpers asset lookup should be limited to constructors.",
        ),
    ):
        for match in re.finditer(re.escape(token), text):
            block = block_for_offset(blocks, match.start())
            if not block or block[0] != block[1]:
                findings.append(Finding("error", rel, line_number(text, match.start()), code, message))
    for class_name, func_name, offset, _, body in blocks:
        if class_name == func_name and re.search(r"\bSpawnActor\s*<", body):
            findings.append(
                Finding(
                    "error",
                    rel,
                    line_number(text, offset),
                    "SPAWN_ACTOR_IN_CONSTRUCTOR",
                    "Do not spawn actors from constructors; move spawning to BeginPlay or an explicit runtime factory.",
                )
            )
    return findings


def validate_newobject_outer(path: Path, text: str, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    rel = str(path.relative_to(root))
    for match in re.finditer(r"\bNewObject\s*<[^>]+>\s*\(\s*\)", text):
        findings.append(
            Finding(
                "warning",
                rel,
                line_number(text, match.start()),
                "NEWOBJECT_WITHOUT_OUTER",
                "NewObject without an explicit Outer is easy to garbage-collect incorrectly; pass an owning UObject and store retained objects in UPROPERTY.",
            )
        )
    return findings


def validate_component_timer_manager(path: Path, text: str, root: Path, bases: dict[str, str]) -> list[Finding]:
    findings: list[Finding] = []
    rel = str(path.relative_to(root))
    for class_name, _, offset, _, body in iter_cpp_definition_blocks(text):
        if bases.get(class_name) != "UActorComponent":
            continue
        for match in re.finditer(r"\bGetWorldTimerManager\s*\(", body):
            findings.append(
                Finding(
                    "error",
                    rel,
                    line_number(text, offset + match.start()),
                    "COMPONENT_GET_WORLD_TIMER_MANAGER",
                    "UActorComponent should use GetWorld()->GetTimerManager() after validating GetWorld(), not GetWorldTimerManager().",
                )
            )
    return findings


def find_build_cs_files(root: Path) -> list[Path]:
    return [path for path in root.rglob("*.Build.cs") if path.is_file() and not should_ignore_project_path(path)]


def build_cs_text(root: Path) -> str:
    parts = []
    for path in find_build_cs_files(root):
        parts.append(read_text(path))
    return "\n".join(parts)


from apply_patch import apply_patch as apply_single_patch
from parse_build_cs import declared_modules_from_text, public_modules_from_text

PATCH_PREFERRED_LINE_THRESHOLD = 200


def declared_build_modules(build_text_value: str) -> set[str]:
    return declared_modules_from_text(build_text_value)


def public_build_modules(build_text_value: str) -> set[str]:
    return public_modules_from_text(build_text_value)


def load_include_owner_map(path: Path) -> dict[str, list[str]]:
    cache_key = str(path.resolve()) if path else ""
    cache = getattr(load_include_owner_map, "_cache", {})
    if cache_key in cache:
        return cache[cache_key]
    owners: dict[str, list[str]] = {}
    if not path or not path.exists():
        return owners
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            metadata = item.get("metadata") or {}
            if metadata.get("symbol_kind") != "include_owner":
                continue
            include_path = str(metadata.get("include_path") or metadata.get("symbol_name") or "")
            owner_modules = [str(value) for value in metadata.get("owner_modules") or [] if value]
            if not include_path or not owner_modules:
                continue
            keys = {
                include_path,
                include_path.replace("\\", "/"),
                Path(include_path).name,
            }
            for key in keys:
                owners.setdefault(key, [])
                for module_name in owner_modules:
                    if module_name not in owners[key]:
                        owners[key].append(module_name)
    cache[cache_key] = owners
    setattr(load_include_owner_map, "_cache", cache)
    return owners


def module_name_from_build_file(path: Path) -> str:
    name = path.name
    if name.endswith(".Build.cs"):
        return name[: -len(".Build.cs")]
    return path.stem


def local_module_names(root: Path) -> set[str]:
    return {module_name_from_build_file(path) for path in find_build_cs_files(root)}


def include_visibility(path: Path) -> str:
    parts = {part.lower() for part in path.parts}
    if path.suffix.lower() in {".h", ".hpp"} and "private" not in parts:
        return "public"
    return "private"


def find_include_owner(include_path: str, owner_map: dict[str, list[str]]) -> list[str]:
    normalized = include_path.replace("\\", "/")
    candidates = [
        normalized,
        Path(normalized).name,
    ]
    for candidate in candidates:
        if candidate in owner_map:
            return owner_map[candidate]
    return []


def validate_enhanced_input(path: Path, text: str, root: Path, build_text: str) -> list[Finding]:
    findings: list[Finding] = []
    rel = str(path.relative_to(root))
    for match in re.finditer(r"\b(?:PlayerInputComponent|InputComponent)\s*->\s*BindAction\s*\(", text):
        findings.append(
            Finding(
                "error",
                rel,
                line_number(text, match.start()),
                "DIRECT_BIND_ACTION",
                "Enhanced Input code must cast to UEnhancedInputComponent and bind with ETriggerEvent.",
            )
        )
    uses_enhanced = any(
        token in text
        for token in (
            "UEnhancedInputComponent",
            "UEnhancedInputLocalPlayerSubsystem",
            "UInputAction",
            "UInputMappingContext",
            "ETriggerEvent",
        )
    )
    if uses_enhanced and "EnhancedInput" not in build_text:
        findings.append(
            Finding(
                "error",
                rel,
                1,
                "MISSING_ENHANCED_INPUT_MODULE",
                'Enhanced Input types require "EnhancedInput" in the module Build.cs dependencies.',
            )
        )
    if uses_enhanced:
        for match in re.finditer(r"->\s*BindAction\s*\(", text):
            statement_end = text.find(";", match.start())
            statement = text[match.start() : statement_end if statement_end != -1 else match.end() + 200]
            if "ETriggerEvent::" not in statement:
                findings.append(
                    Finding(
                        "error",
                        rel,
                        line_number(text, match.start()),
                        "ENHANCED_BIND_WITHOUT_TRIGGER_EVENT",
                        "Enhanced Input BindAction must use an ETriggerEvent argument.",
                    )
                )
    if uses_enhanced and path.suffix.lower() == ".cpp" and "EnhancedInputComponent.h" not in text:
        findings.append(
            Finding(
                "warning",
                rel,
                1,
                "MISSING_ENHANCED_INPUT_INCLUDE",
                'Code uses Enhanced Input; check that "EnhancedInputComponent.h" and related headers are included where needed.',
            )
        )
    return findings


def class_headers(root: Path) -> dict[str, str]:
    headers: dict[str, str] = {}
    for path in root.rglob("*.h"):
        text = read_text(path)
        for match in re.finditer(r"\bclass\s+(?:[A-Z0-9_]+_API\s+)?([A-Za-z_][A-Za-z0-9_]*)\b", text):
            headers.setdefault(match.group(1), text)
    return headers


def _normalize_signature_params(params: str) -> str:
    value = re.sub(r"\s+", " ", str(params or "").strip())
    if not value or value == "void":
        return ""
    value = re.sub(r"=\s*[^,]+", "", value)
    return value.replace(" const", "").strip()


def _header_has_matching_signature(header: str, func_name: str, params: str) -> bool:
    wanted = _normalize_signature_params(params)
    declaration_re = re.compile(rf"\b{re.escape(func_name)}\s*\((?P<params>[^)]*)\)")
    for declaration in declaration_re.finditer(header):
        if _normalize_signature_params(declaration.group("params")) == wanted:
            return True
    return False


def validate_cpp_declarations(path: Path, text: str, root: Path, headers: dict[str, str]) -> list[Finding]:
    findings: list[Finding] = []
    rel = str(path.relative_to(root))
    definition_re = re.compile(
        r"^[\w:<>,~*&\s]+\b(?P<class>[A-Za-z_][A-Za-z0-9_]*)::(?P<func>~?[A-Za-z_][A-Za-z0-9_]*)\s*\((?P<params>[^)]*)\)",
        flags=re.MULTILINE,
    )
    for match in definition_re.finditer(text):
        class_name = match.group("class")
        func_name = match.group("func")
        params = match.group("params")
        header = headers.get(class_name)
        if not header:
            continue
        bare_func = func_name.lstrip("~")
        if bare_func == class_name:
            continue
        if func_name.endswith(("_Implementation", "_Validate")):
            base_name = re.sub(r"_(?:Implementation|Validate)$", "", func_name)
            if re.search(rf"\b{re.escape(base_name)}\s*\(", header):
                continue
        if _header_has_matching_signature(header, func_name, params):
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


def collect_rpc_declarations(root: Path) -> list[tuple[str, str, Path, int]]:
    declarations: list[tuple[str, str, Path, int]] = []
    for path in root.rglob("*.h"):
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
    if not any(True for _ in root.rglob("*.cpp")):
        return findings
    cpp_text = "\n".join(read_text(path) for path in root.rglob("*.cpp"))
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


def validate_build_modules(root: Path, source_text: str, build_text_value: str) -> list[Finding]:
    findings: list[Finding] = []
    module_rules = {
        "GameplayTags": ("FGameplayTag", "FGameplayTagContainer", "UGameplayTagsManager"),
        "UMG": ("UUserWidget", "UWidget", "UButton", "UTextBlock"),
        "AIModule": ("AAIController", "UBehaviorTree", "UBlackboardComponent"),
        "Niagara": ("UNiagaraComponent", "UNiagaraSystem", "UNiagaraFunctionLibrary"),
    }
    build_files = find_build_cs_files(root)
    rel = str(build_files[0].relative_to(root)) if build_files else "Source/*.Build.cs"
    declared_modules = declared_build_modules(build_text_value)
    for module_name, tokens in module_rules.items():
        if module_name in declared_modules:
            continue
        if any(token in source_text for token in tokens):
            severity = "error" if module_name == "GameplayTags" else "warning"
            findings.append(
                Finding(
                    severity,
                    rel,
                    1,
                    "POSSIBLE_MISSING_MODULE",
                    f"Code appears to use {module_name} types; verify Build.cs dependencies.",
                )
            )
    return findings


def validate_include_owner_modules(
    root: Path,
    build_text_value: str,
    owner_map: dict[str, list[str]],
) -> list[Finding]:
    findings: list[Finding] = []
    if not owner_map:
        return findings
    declared = declared_build_modules(build_text_value)
    public_declared = public_build_modules(build_text_value)
    local_modules = local_module_names(root)
    for path in iter_source_files(root):
        if path.suffix.lower() not in {".h", ".hpp", ".cpp", ".c", ".cc"}:
            continue
        text = read_text(path)
        visibility = include_visibility(path)
        for line, include_path in include_lines(text):
            owner_modules = find_include_owner(include_path, owner_map)
            if not owner_modules:
                continue
            candidate_modules = [
                module_name
                for module_name in owner_modules
                if module_name not in local_modules and module_name not in {"Core", "CoreUObject"}
            ]
            if not candidate_modules:
                continue
            missing = [module_name for module_name in candidate_modules if module_name not in declared]
            if missing:
                dependency_kind = "PublicDependencyModuleNames" if visibility == "public" else "PrivateDependencyModuleNames"
                findings.append(
                    Finding(
                        "warning",
                        str(path.relative_to(root)),
                        line,
                        "MISSING_INCLUDE_OWNER_MODULE",
                        f'Include "{include_path}" belongs to module(s) {", ".join(missing)}; add to {dependency_kind}.',
                    )
                )
                continue
            if visibility == "public":
                private_only = [module_name for module_name in candidate_modules if module_name not in public_declared]
                if private_only:
                    findings.append(
                        Finding(
                            "warning",
                            str(path.relative_to(root)),
                            line,
                            "PUBLIC_HEADER_PRIVATE_MODULE",
                            f'Public header includes "{include_path}" from {", ".join(private_only)}; prefer PublicDependencyModuleNames.',
                        )
                    )
    return findings


def find_matching_brace(text: str, open_index: int) -> int:
    depth = 0
    index = open_index
    in_string: str | None = None
    escape = False
    while index < len(text):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == in_string:
                in_string = None
        else:
            if char in {'"', "'"}:
                in_string = char
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return index
        index += 1
    return -1


def iter_function_blocks(text: str) -> list[tuple[str, int, str]]:
    blocks: list[tuple[str, int, str]] = []
    pattern = re.compile(
        r"(?P<header>(?:[\w:<>,~*&]+\s+)+(?P<name>[A-Za-z_][A-Za-z0-9_:~]*)\s*\([^;{}]*\)\s*(?:const\s*)?)\{",
        flags=re.MULTILINE,
    )
    for match in pattern.finditer(text):
        header = re.sub(r"\s+", " ", match.group("header")).strip()
        name = match.group("name")
        if name in {"if", "for", "while", "switch", "catch"}:
            continue
        open_index = match.end() - 1
        close_index = find_matching_brace(text, open_index)
        if close_index == -1:
            continue
        blocks.append((header, match.start(), text[open_index + 1 : close_index]))
    return blocks


ACTION_STAGE_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("current state check", ("Can", "Is", "Has", "State", "Status", "Current", "bIs", "IsValid")),
    ("resource cost check", ("Cost", "Resource", "Stamina", "Mana", "Ammo", "Energy", "Cooldown", "CanAfford")),
    ("asset/montage/target check", ("Target", "Montage", "Asset", "Anim", "Action", "IsValid")),
    ("feasibility check", ("Feasible", "Validate", "Allowed", "Eligible", "Trace", "Can")),
    ("success confirmation", ("Success", "Succeeded", "Result", "bSuccess", "return true")),
    ("resource consume", ("Consume", "Spend", "Commit", "ApplyCost", "Deduct", "Remove")),
    ("state change", ("Set", "State", "Status", "Current", "Enter", "Exit", "bIs")),
    ("event broadcast", ("Broadcast", "Delegate", ".On", "OnAction", "OnRequest")),
]


def first_stage_index(body: str, tokens: tuple[str, ...]) -> int:
    lowered = body.lower()
    indexes = []
    for token in tokens:
        index = lowered.find(token.lower())
        if index != -1:
            indexes.append(index)
    return min(indexes) if indexes else -1


def likely_action_request(header: str, body: str) -> bool:
    value = f"{header}\n{body}"
    if not re.search(r"\b(Request|Try|Attempt|Start|Begin|Perform|Execute|Use|Commit)[A-Za-z0-9_]*", header):
        return False
    return bool(
        re.search(
            r"\b(Action|Interact|Ability|Attack|Use|Cast|Montage|Target|Resource|Cost|Consume|Broadcast)\b",
            value,
        )
    )


def validate_action_request_order(path: Path, text: str, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for header, offset, body in iter_function_blocks(text):
        if not likely_action_request(header, body):
            continue
        stage_indexes = [(name, first_stage_index(body, tokens)) for name, tokens in ACTION_STAGE_PATTERNS]
        present = [(name, index) for name, index in stage_indexes if index != -1]
        missing = [name for name, index in stage_indexes if index == -1]
        if len(present) < 5:
            findings.append(
                Finding(
                    "warning",
                    str(path.relative_to(root)),
                    line_number(text, offset),
                    "ACTION_REQUEST_ORDER_INCOMPLETE",
                    "Likely action request function is missing visible stages: " + ", ".join(missing[:5]) + ".",
                )
            )
            continue
        present_indexes = [index for _, index in present]
        if present_indexes != sorted(present_indexes):
            findings.append(
                Finding(
                    "warning",
                    str(path.relative_to(root)),
                    line_number(text, offset),
                    "ACTION_REQUEST_ORDER_MISMATCH",
                    "Likely action request stages appear out of the required validation/consume/state/broadcast order.",
                )
            )
    return findings


def validate_component_subsystem_patterns(path: Path, text: str, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    rel = str(path.relative_to(root))
    name_lower = path.name.lower()
    is_subsystem = "subsystem" in name_lower or any(
        token in text for token in ("UWorldSubsystem", "UGameInstanceSubsystem", "UEngineSubsystem")
    )
    is_component = "component" in name_lower or "UActorComponent" in text

    if is_subsystem:
        if "CreateDefaultSubobject" in text:
            findings.append(
                Finding(
                    "error",
                    rel,
                    0,
                    "SUBSYSTEM_CREATE_SUBOBJECT",
                    "Subsystems must not use CreateDefaultSubobject; subsystems are not Actors.",
                )
            )
        class_stem = path.stem
        ctor_match = re.search(
            rf"\b{re.escape(class_stem)}::{re.escape(class_stem)}\s*\([^)]*\)\s*\{{",
            text,
        )
        if ctor_match:
            ctor_end = text.find("}", ctor_match.end())
            ctor_body = text[ctor_match.end() : ctor_end if ctor_end != -1 else ctor_match.end()]
            for match in re.finditer(r"\b(?:GetWorld|GEngine|SpawnActor)\s*\(", ctor_body):
                findings.append(
                    Finding(
                        "error",
                        rel,
                        line_number(text, ctor_match.start() + match.start()),
                        "SUBSYSTEM_WORLD_SPAWN_IN_CTOR",
                        "Avoid GetWorld/GEngine/SpawnActor in subsystem constructors.",
                    )
                )
        if "PrimaryComponentTick" in text or "TickComponent" in text:
            findings.append(
                Finding(
                    "warning",
                    rel,
                    0,
                    "SUBSYSTEM_TICK_PATTERN",
                    "Subsystems should prefer timers/delegates over component-style Tick patterns.",
                )
            )

    if is_component and path.suffix.lower() in {".cpp", ".cc"}:
        class_name = path.stem
        ctor_pattern = rf"\b{re.escape(class_name)}\s*::\s*{re.escape(class_name)}\s*\("
        if re.search(ctor_pattern, text):
            for match in re.finditer(r"\bGetWorld\s*\(\s*\)", text):
                findings.append(
                    Finding(
                        "warning",
                        rel,
                        line_number(text, match.start()),
                        "COMPONENT_CTOR_GETWORLD",
                        "Avoid GetWorld() in component constructors; use BeginPlay when world access is required.",
                    )
                )
    return findings


def validate_typo_includes(path: Path, text: str, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    if path.suffix.lower() not in {".h", ".hpp", ".cpp", ".c", ".cc", ".inl"}:
        return findings
    rel = str(path.relative_to(root))
    for match in re.finditer(r'#include\s+"([^"]+)"', text):
        include_path = match.group(1)
        if include_path.startswith("Game/Framework/"):
            corrected = include_path.replace("Game/Framework/", "GameFramework/", 1)
            findings.append(
                Finding(
                    "error",
                    rel,
                    line_number(text, match.start()),
                    "BAD_INCLUDE_PATH",
                    f'Invalid include "{include_path}". Use "{corrected}" instead.',
                )
            )
    return findings


def validate_unreal_readiness(
    root: Path,
    module_graph_path: Path | None = None,
    *,
    lightweight: bool = False,
) -> list[Finding]:
    if lightweight:
        return validate_unreal_readiness_lightweight(root)
    findings: list[Finding] = []
    build_text_value = build_cs_text(root)
    include_owner_map = load_include_owner_map(module_graph_path) if module_graph_path else {}
    headers = class_headers(root)
    bases = class_bases(root)
    all_source_text = []
    for path in iter_source_files(root):
        text = read_text(path)
        all_source_text.append(text)
        if path.suffix.lower() in {".h", ".hpp", ".cpp", ".c", ".cc"}:
            findings.extend(validate_typo_includes(path, text, root))
            findings.extend(validate_component_subsystem_patterns(path, text, root))
        if path.suffix.lower() in {".h", ".hpp"}:
            findings.extend(validate_generated_h(path, text, root))
            findings.extend(validate_reflected_namespace(path, text, root))
            findings.extend(validate_blueprint_native_event_declarations(path, text, root))
            findings.extend(validate_private_blueprint_access(path, text, root))
            findings.extend(validate_raw_uobject_members(path, text, root))
            findings.extend(validate_required_includes(path, text, root))
        if path.suffix.lower() in {".h", ".hpp", ".cpp", ".c", ".cc"}:
            findings.extend(validate_editor_only_runtime_includes(path, text, root))
            findings.extend(validate_enhanced_input(path, text, root, build_text_value))
            findings.extend(validate_action_request_order(path, text, root))
        if path.suffix.lower() in {".cpp", ".c", ".cc"}:
            findings.extend(validate_required_includes(path, text, root))
            findings.extend(validate_constructor_lifecycle_usage(path, text, root))
            findings.extend(validate_newobject_outer(path, text, root))
            findings.extend(validate_component_timer_manager(path, text, root, bases))
            findings.extend(validate_cpp_declarations(path, text, root, headers))
    findings.extend(validate_build_modules(root, "\n".join(all_source_text), build_text_value))
    findings.extend(validate_include_owner_modules(root, build_text_value, include_owner_map))
    findings.extend(validate_rpc_implementations(root))
    return findings


def validate_unreal_readiness_lightweight(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    headers = class_headers(root)
    for path in iter_source_files(root):
        if path.suffix.lower() not in SOURCE_ONLY_SUFFIXES:
            continue
        text = read_text(path)
        findings.extend(validate_typo_includes(path, text, root))
        if path.suffix.lower() in {".h", ".hpp"}:
            findings.extend(validate_generated_h(path, text, root))
            findings.extend(validate_reflected_namespace(path, text, root))
            findings.extend(validate_blueprint_native_event_declarations(path, text, root))
        if path.suffix.lower() in {".cpp", ".c", ".cc"}:
            findings.extend(validate_cpp_declarations(path, text, root, headers))
    return findings


def only_source_files_changed(before: dict[str, str], after: dict[str, str]) -> bool:
    changed = {path for path in set(before) | set(after) if before.get(path) != after.get(path)}
    if not changed:
        return False
    return all(Path(path).suffix.lower() in SOURCE_ONLY_SUFFIXES for path in changed)


COMPACT_SUMMARY_PREFIX = "Conversation compact summary:"


def _first_matching_line(text: str, markers: tuple[str, ...]) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        lower = stripped.lower()
        if any(marker in lower for marker in markers):
            return stripped[:220]
    return ""


def _extract_json_payload(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped:
        return None
    try:
        value = json.loads(stripped)
    except Exception:
        match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
        if not match:
            return None
        try:
            value = json.loads(match.group(0))
        except Exception:
            return None
    return value if isinstance(value, dict) else None


def _extract_paths(value: Any) -> list[str]:
    paths: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if key.lower() in {"path", "file", "relative_path", "target"} and isinstance(child, str):
                    if re.search(r"\.(h|hpp|cpp|c|cc|cs|ini|json|uproject|uplugin|md|txt)$", child, re.IGNORECASE):
                        paths.append(child.replace("\\", "/"))
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return list(dict.fromkeys(paths))[:12]


def trim_compact_summary(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    header = COMPACT_SUMMARY_PREFIX
    note = "\n...[compact summary truncated]\n"
    if max_chars <= len(header) + len(note) + 80:
        return (header + note + text[-max(0, max_chars - len(header) - len(note)):]).strip()
    head_limit = min(max_chars // 3, 700)
    head = text[:head_limit].rstrip()
    tail_limit = max_chars - len(head) - len(note)
    tail = text[-max(0, tail_limit):].lstrip()
    if not head.startswith(header):
        head = header
    return (head + note + tail).strip()


def summarize_compacted_messages(messages: list[dict[str, str]], max_chars: int) -> str:
    lines: list[str] = [
        COMPACT_SUMMARY_PREFIX,
        f"- Compacted messages: {len(messages)}",
    ]
    carried_summaries: list[str] = []
    facts: list[str] = []
    touched_paths: list[str] = []

    for message in messages:
        role = str(message.get("role") or "unknown")
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        if content.startswith(COMPACT_SUMMARY_PREFIX):
            carried_summaries.append(tail_text(content, max_chars // 3).strip())
            continue
        payload = _extract_json_payload(content)
        if payload is not None:
            answer = str(payload.get("answer") or "").strip()
            if answer:
                facts.append(f"{role}: answer={answer[:220]}")
            paths = _extract_paths(payload)
            if paths:
                touched_paths.extend(paths)
                facts.append(f"{role}: edited/touched {', '.join(paths[:6])}")
            continue
        if role == "user":
            request = _first_matching_line(content, ("user request:", "compile loop attempt", "previous validation", "build feedback", "error", "failed"))
            if request:
                facts.append(f"user: {request}")
        else:
            summary = _first_matching_line(content, ("error", "failed", "validation", "build", "patched", "wrote", "answer"))
            if summary:
                facts.append(f"{role}: {summary}")

    for summary in carried_summaries[-2:]:
        if summary:
            lines.append("- Prior summary:")
            lines.extend(f"  {line}" for line in summary.splitlines()[:12])
    if touched_paths:
        unique_paths = list(dict.fromkeys(touched_paths))[:12]
        lines.append("- Files touched or proposed: " + ", ".join(unique_paths))
    if facts:
        lines.append("- Important prior facts:")
        lines.extend(f"  - {fact}" for fact in facts[-18:])
    lines.append("- Instruction: use this as continuity only; current project state and latest RAG/build feedback remain authoritative.")
    return trim_compact_summary("\n".join(lines), max_chars)


def cap_message_history(messages: list[dict[str, str]], mode: str = "execute") -> list[dict[str, str]]:
    if len(messages) <= 1:
        return messages
    budget = token_budget.mode_budget(mode)
    max_messages = int(budget.get("maxHistoryMessages") or 8)
    history_attempts = int(budget.get("historyAttempts") or 2)
    summary_chars = int(budget.get("historySummaryMaxChars") or 2400)
    keep_tail = min(max(max_messages - 1, 0), history_attempts * 2)
    if keep_tail <= 0:
        keep_tail = 4
    if len(messages) <= 1 + keep_tail:
        return messages
    compacted = messages[1:-keep_tail]
    summary = summarize_compacted_messages(compacted, summary_chars)
    return [messages[0], {"role": "system", "content": summary}] + messages[-keep_tail:]


def count_compact_summary_messages(messages: list[dict[str, str]]) -> int:
    return sum(1 for message in messages if str(message.get("content") or "").startswith(COMPACT_SUMMARY_PREFIX))


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
) -> None:
    input_chars = sum(len(str(message.get("content") or "")) for message in messages)
    input_chars += len(prompt) + len(rag_context) + len(project_state)
    budget = token_budget.mode_budget(mode)
    usage = {
        "attempt": attempt,
        "mode": mode,
        "inputChars": input_chars,
        "estimatedInputTokens": token_budget.chars_to_token_estimate(" " * input_chars),
        "maxOutputTokens": int(preset.get("maxTokens") or budget.get("maxOutputTokens") or 0),
        "feedbackTailChars": int(budget.get("feedbackTailChars") or 0),
        "maxHistoryMessages": int(budget.get("maxHistoryMessages") or 0),
        "historySummaryMaxChars": int(budget.get("historySummaryMaxChars") or 0),
        "compactSummaryMessages": count_compact_summary_messages(messages),
        "projectSummaryMaxFiles": int(budget.get("projectSummaryMaxFiles") or 0),
        "projectSummaryMaxChars": int(budget.get("projectSummaryMaxChars") or 0),
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


def collect_rag_context(args: argparse.Namespace, request: str, *, top_k: int | None = None) -> str:
    index = Path(args.index)
    if not index.exists():
        return f"RAG index does not exist: {index}"
    policy = profile_edit_limits()
    effective_top_k = top_k if top_k is not None else args.top_k
    candidate_scale = int(policy.get("candidateLimitScale") or 20)
    rows = search_index(
        index,
        request,
        effective_top_k,
        SearchOptions(
            mode=args.mode,
            candidate_limit=max(40, effective_top_k * candidate_scale),
        ),
    )
    return assemble_context(rows, request, args.mode)


def collect_delta_rag_context(
    args: argparse.Namespace,
    query_parts: list[str],
    changed_files: list[str],
) -> str:
    index = Path(args.index)
    if not index.exists():
        return f"RAG index does not exist: {index}"
    query = " ".join(part.strip() for part in query_parts if part and part.strip())
    if changed_files:
        query = f"{query} {' '.join(changed_files)}".strip()
    query = query[:4000] or "compile_fix"
    policy = profile_edit_limits()
    effective_top_k = int(policy.get("deltaTopK") or 4)
    candidate_scale = int(policy.get("candidateLimitScale") or 20)
    rows = search_index(
        index,
        query,
        effective_top_k,
        SearchOptions(mode="compile_fix", candidate_limit=max(30, effective_top_k * candidate_scale)),
    )
    return assemble_context(rows, query, "compile_fix")


def format_findings(findings: list[Finding]) -> str:
    if not findings:
        return "No static Unreal compile-readiness issues found."
    lines = ["Static Unreal compile-readiness findings:"]
    for finding in findings:
        location = f"{finding.path}:{finding.line}" if finding.line else finding.path
        lines.append(f"- [{finding.severity}] {finding.code} {location}: {finding.message}")
    return "\n".join(lines)


def has_static_errors(findings: list[Finding]) -> bool:
    return any(finding.severity == "error" for finding in findings)


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

    command = [
        str(ubt_path),
        target,
        platform,
        configuration,
        f"-Project={project_file}",
        "-WaitMutex",
    ]
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
    return records[:12]


from error_taxonomy import mode_from_error_kind as taxonomy_mode_from_error_kind


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
    rows = search_index(
        index,
        query,
        args.top_k,
        SearchOptions(mode=mode, candidate_limit=max(120, args.top_k * 20)),
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


def system_prompt(rules_text: str, edit_limits: dict[str, Any] | None = None) -> str:
    base_prompt = read_text(PROMPT_PATH, "You are an Unreal Engine 5.8 C++ assistant.")
    threshold = PATCH_PREFERRED_LINE_THRESHOLD
    limits = edit_limits or {}
    max_files = int(limits.get("maxFilesPerEdit") or 4)
    prefer_patch = bool(limits.get("preferPatchOverFullFile", True))
    target_tier = str(limits.get("targetTier") or "").strip()
    prompt_contract = str(limits.get("promptContract") or "").strip()
    patch_hint = (
        f"Prefer patches[] for existing files over ~{threshold} lines when possible."
        if prefer_patch
        else f"You may use full files[] up to {max_files} files per response."
    )
    return f"""{base_prompt}

You are now running inside an automated compile wrapper.
Return only one valid JSON object. Do not use markdown fences.
Do not use C++ namespaces unless they are truly necessary.
Target quality track: {target_tier or "standard_unreal_agent"}.
Active model contract: {prompt_contract or "evidence_first_minimal_patch"}.

Global file edit discipline:
- The current project files in the user prompt are the source of truth, even if an earlier plan said otherwise.
- Before changing any file, compare the requested change with the current file state and previous feedback.
- Do not repeat an edit that is already present. Do not re-add the same include, UPROPERTY, UFUNCTION, member variable, input binding, module dependency, or helper function.
- If only one file still needs work, change only that file. Do not restart a broader implementation plan.
- Omit unchanged files from the files array. Include a file only when its full final content differs from the current file.
- If the current files already satisfy the request, return an empty files array and explain that no new file edits are needed.
- Never return a file entry whose content is byte-for-byte identical to the current file. The wrapper will reject no-op file bundles.
- Every .cpp member function must have a matching declaration in the relevant header unless it is a constructor, destructor, static local helper, lambda, or non-member function.
- Every header declaration added for a reflected Unreal type must include the needed macro, generated.h placement, and Build.cs dependency evidence.

Mandatory wrapper rules:
{rules_text}

The JSON object must match this schema:
{{
  "answer": "short ASCII English summary for logs",
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

For NEW files or small files (under ~{threshold} lines), use files[] with full content.
For LARGE existing files, prefer patches[] with exact oldText/newText (expectedOccurrences required).
{patch_hint}
Maximum edits per response: {max_files} (files + patches combined).
Omit unchanged files. patches and files may both be empty if no edit is needed.
Every files[] entry must contain complete final content. patches require exact match.
Use compile-ready Unreal C++ only when creating code. generated.h must be the last include in headers.
For the JSON answer field, use ASCII English only. Do not use Korean or non-ASCII text inside JSON strings.
"""


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
        "refactor_r0": (
            "Mode directive: refactor_r0. Discover only — SSOT table, impact files, risks. No code edits."
        ),
        "refactor_r1": (
            "Mode directive: refactor_r1. Boundary headers/interfaces only. No large cpp bodies. Max 3 files."
        ),
        "refactor_r2": (
            "Mode directive: refactor_r2. Move one implementation cluster. Max 3 files. UBT must pass or report errors."
        ),
        "refactor_r3": (
            "Mode directive: refactor_r3. Rewire callers/includes only. Max 3 files."
        ),
        "refactor_r4": (
            "Mode directive: refactor_r4. Cleanup dead code/includes. Max 5 files."
        ),
    }
    return directives.get(str(mode or "").strip(), "")


def user_prompt(
    *,
    request: str,
    rag_context: str,
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
        f"# LM Studio Unreal Wrapper Result",
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

    rag_context = collect_rag_context(args, request)
    initial_findings = validate_unreal_readiness(project_root, Path(args.module_graph))
    initial_static_report = format_findings(initial_findings)
    previous_feedback = "" if not initial_findings else "Initial static validation:\n" + initial_static_report
    last_build_records: list[dict[str, Any]] = []
    last_findings: list[Finding] = list(initial_findings)
    original_mode = args.mode

    initial_prompt = agent_plan_block + user_prompt(
        request=request,
        rag_context=rag_context,
        project_state=summarize_project_state(project_root, mode=args.mode),
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
        static_autofix_written = apply_generated_h_missing_autofix(project_root, initial_findings)
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

    edit_limits = profile_edit_limits()
    messages = [{"role": "system", "content": system_prompt(rules_text, edit_limits)}]
    last_answer = ""
    last_written: list[Path] = []
    last_build: BuildResult | None = None

    for attempt in range(1, args.max_attempts + 1):
        budget_mode = "compile_fix" if attempt >= 2 else original_mode
        if attempt >= 2 and original_mode in {
            "agent_edit", "codegen", "compile_fix", "module_fix", "reflection_fix",
            "prototype_component", "prototype_subsystem",
        }:
            args.mode = "compile_fix"
        if attempt >= 2:
            changed_files = changed_files_from_feedback(last_build_records, last_findings)
            query_parts = [request]
            if last_build_records:
                query_parts.append(build_error_query(last_build_records, last_build.output if last_build else ""))
            rag_context = collect_delta_rag_context(args, query_parts, changed_files)
        elif attempt == 1:
            rag_context = collect_rag_context(args, request)

        attempt_dir = run_dir / f"attempt_{attempt}"
        attempt_dir.mkdir(parents=True, exist_ok=True)
        attempt_prefix = (
            f"Compile loop attempt {attempt}/{args.max_attempts}. "
            "List at most 3 assumptions; apply one minimal diff this turn.\n\n"
        )
        project_state = summarize_project_state(project_root, mode=budget_mode)
        prompt = user_prompt(
            request=request,
            rag_context=rag_context,
            project_state=project_state,
            project_name=project_name,
            project_file=project_file,
            target=target,
            previous_feedback=attempt_prefix + previous_feedback,
            mode=original_mode if attempt >= 2 else args.mode,
        )
        if attempt >= 3:
            messages = cap_message_history(messages, budget_mode)
        attempt_preset = preset_for_wrapper(args.mode, compile_patch=attempt >= 2)
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
        )
        raw_response = chat_lmstudio(args, messages, model, attempt_preset)
        write_file(attempt_dir / "model_response.txt", raw_response)
        messages.append({"role": "assistant", "content": raw_response})

        try:
            bundle = parse_json_response(raw_response)
            enforce_edit_limits(bundle, edit_limits)
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
                    continue
            write_json(attempt_dir / "model_response.json", bundle)
        except Exception as exc:
            previous_feedback = f"Model response was not valid JSON: {exc}"
            write_file(attempt_dir / "validation_feedback.txt", previous_feedback)
            continue

        last_answer = str(bundle.get("answer") or "")
        hall_blockers = hallucination_blockers(request, last_answer, bundle, project_root)
        if hall_blockers:
            previous_feedback = (
                BUILD_CS_RETRY_FEEDBACK + "\n"
                + "\n".join(f"- {issue}" for issue in hall_blockers)
            )
            write_file(attempt_dir / "validation_feedback.txt", previous_feedback)
            continue
        has_edits = bool(bundle["files"]) or bool(bundle.get("patches"))
        if not has_edits:
            if not args.allow_empty_files and not answer_claims_no_changes(last_answer):
                last_findings = validate_unreal_readiness(project_root, Path(args.module_graph))
                static_report = format_findings(last_findings)
                blockers = no_change_blockers(request, project_root, last_findings)
                previous_feedback = (
                    "Model returned no files without clearly saying the current files already satisfy the request. "
                    "Inspect the current project state and either return the smallest changed file bundle, or return "
                    "an empty files array with explicit evidence that no new edit is needed.\n"
                    + static_report
                    + ("\nNo-change blockers:\n" + "\n".join(f"- {issue}" for issue in blockers) if blockers else "")
                )
                write_file(attempt_dir / "validation_feedback.txt", previous_feedback)
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
                continue
            if has_static_errors(last_findings) and not args.skip_static_gate:
                previous_feedback = static_report
                write_file(attempt_dir / "validation_feedback.txt", previous_feedback)
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
                continue
            scope_blockers = edit_scope_blockers(request, before_apply, after_apply, project_root)
            if scope_blockers:
                restore_changed_paths(project_root, before_apply, changed_paths_between(before_apply, after_apply))
                last_written = []
                retry_tail = BUILD_CS_RETRY_FEEDBACK if any("Build.cs" in issue for issue in scope_blockers) else ""
                previous_feedback = (
                    (retry_tail + "\n" if retry_tail else "")
                    + "Edit rejected because it does not match the requested compile-fix scope:\n"
                    + "\n".join(f"- {issue}" for issue in scope_blockers)
                    + "\nUse the current project state as authoritative and change the specific file(s) needed."
                )
                write_file(attempt_dir / "validation_feedback.txt", previous_feedback)
                continue
        except Exception as exc:
            previous_feedback = f"File application failed: {exc}"
            write_file(attempt_dir / "validation_feedback.txt", previous_feedback)
            continue

        lightweight_static = only_source_files_changed(before_apply, after_apply)
        last_findings = validate_unreal_readiness(
            project_root,
            Path(args.module_graph),
            lightweight=lightweight_static,
        )
        static_report = format_findings(last_findings)
        write_file(attempt_dir / "static_validation.txt", static_report + "\n")
        if has_static_errors(last_findings) and not args.skip_static_gate:
            previous_feedback = static_report
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
        previous_feedback = "\n\n".join(
            [
                static_report,
                f"UBT failed with return code {last_build.returncode}.",
                f"Log path: {last_build.log_path}",
                format_build_records(parsed_feedback.records),
                f"Failure-specific RAG mode: {parsed_feedback.mode}",
                "Failure-specific RAG context:",
                parsed_feedback.rag_context,
                tail_text(last_build.output, token_budget.feedback_tail_chars("compile_fix")),
            ]
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
