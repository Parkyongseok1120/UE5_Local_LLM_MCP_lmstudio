#!/usr/bin/env python3
"""Compile-fix and multifile guard policies for the LM Studio wrapper."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from unreal_static_validate import (
    Finding,
    build_cs_text,
    declared_build_modules,
    iter_source_files,
    public_build_modules,
    read_text,
)

from wrapper_evidence import (
    missing_definition_call_removal_blockers,
    multifile_required_surface_hint,
    multifile_surface_enforced,
    refactor_surface_evidence,
)

BUILD_CS_SCOPE_MARKERS = (
    "build.cs",
    "publicdependencymodulenames",
    "privatedependencymodulenames",
    "module dependency",
    "missing module",
)
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
POSITIVE_BUILD_CS_ACTION_RE = re.compile(
    r"\b(?:add|insert|modify|update|patch|edit|fix|declare|include)\b"
    r".{0,90}\b(?:build\.cs|publicdependencymodulenames|privatedependencymodulenames|module dependency|missing module)\b",
    re.I,
)

PENDING_CPP_FOLLOWUP = "awaiting_cpp_followup"
PENDING_HEADER_FOLLOWUP = "awaiting_header_followup"

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


def _request_sentences(request: str) -> list[str]:
    safe = re.sub(r"build\.cs", "build_cs", str(request or ""), flags=re.I)
    parts = [part.strip().replace("build_cs", "Build.cs") for part in re.split(r"[\n.;]+", safe) if part.strip()]
    return parts


def request_mentions_any(request: str, needles: tuple[str, ...]) -> bool:
    lowered = str(request or "").lower()
    return any(needle.lower() in lowered for needle in needles)


def request_mentions_gameplay_tags(request: str) -> bool:
    return request_mentions_any(request, ("gameplaytags", "gameplaytag", "GameplayTagContainer.h"))


def _positive_build_cs_request_text(request: str) -> str:
    kept: list[str] = []
    for sentence in _request_sentences(request):
        lowered = sentence.lower()
        mentions_scope = any(marker in lowered for marker in BUILD_CS_SCOPE_MARKERS)
        is_negative = mentions_scope and any(marker in lowered for marker in NEGATIVE_BUILD_CS_MARKERS)
        if not is_negative:
            kept.append(sentence)
    return "\n".join(kept)


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


def bundle_includes_build_cs(bundle: dict[str, Any]) -> bool:
    for item in bundle.get("files") or []:
        if str(item.get("path") or "").lower().endswith(".build.cs"):
            return True
    for item in bundle.get("patches") or []:
        if str(item.get("path") or "").lower().endswith(".build.cs"):
            return True
    return False


def bundle_text_for_path(bundle: dict[str, Any], suffix: str) -> str:
    parts: list[str] = []
    suffix_lower = suffix.lower()
    for item in bundle.get("files") or []:
        path = str(item.get("path") or "")
        if path.lower().endswith(suffix_lower):
            parts.append(str(item.get("content") or ""))
    for item in bundle.get("patches") or []:
        path = str(item.get("path") or "")
        if path.lower().endswith(suffix_lower):
            old_text = str(item.get("oldText") or "")
            new_text = str(item.get("newText") or "")
            parts.append(f"{old_text}\n{new_text}".strip())
    return "\n".join(parts)


def changed_paths_between(before: dict[str, str], after: dict[str, str]) -> list[str]:
    return [path for path in sorted(set(before) | set(after)) if before.get(path) != after.get(path)]


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


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def restore_changed_paths(root: Path, snapshot: dict[str, str], changed_paths: list[str]) -> None:
    for relative in changed_paths:
        target = safe_output_path(root, relative)
        old_text = snapshot.get(relative)
        if old_text is None:
            if target.exists() and target.is_file():
                target.unlink()
            continue
        write_file(target, old_text)


def source_uses_gameplay_tags(root: Path, *, public_only: bool = False) -> bool:
    from unreal_static_validate import include_visibility

    tokens = ("GameplayTagContainer.h", "FGameplayTag", "FGameplayTagContainer", "UGameplayTagsManager")
    for path in iter_source_files(root):
        if public_only and include_visibility(path) != "public":
            continue
        text = read_text(path)
        if any(token in text for token in tokens):
            return True
    return False


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
            "f2660",
            "c2660",
        )
    ):
        return True
    if "broadcast" in lower and "delegate" in lower:
        return True
    return False


def blueprint_native_event_impl_fix_context(
    request: str,
    route: dict[str, Any] | None = None,
    findings: list[Finding] | None = None,
) -> bool:
    if route and str(route.get("errorSubkind") or "") in {
        "BLUEPRINT_NATIVE_EVENT_IMPL_MISSING",
        "BLUEPRINT_NATIVE_EVENT_MANUAL_IMPL_DECL",
    }:
        return True
    if any(
        finding.code in {"BLUEPRINT_NATIVE_EVENT_IMPL_MISSING", "BLUEPRINT_NATIVE_EVENT_MANUAL_IMPL_DECL"}
        for finding in (findings or [])
    ):
        return True
    lower = request.lower()
    return "blueprintnativeevent" in lower or (
        "_implementation" in lower and "matching cpp" in lower
    )


def blueprint_native_event_dual_surface_blockers(bundle: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    header_impl = False
    cpp_impl = False
    for item in list(bundle.get("patches") or []) + [
        {"path": entry.get("path"), "newText": entry.get("content", "")} for entry in (bundle.get("files") or [])
    ]:
        path = str(item.get("path") or "").replace("\\", "/")
        text = str(item.get("newText") or item.get("content") or "")
        if not text:
            continue
        if path.lower().endswith((".h", ".hpp")) and re.search(
            r"\b\w+_Implementation\s*\([^;]*\)\s*(?:override\s*)?;",
            text,
            re.MULTILINE,
        ):
            header_impl = True
        if path.lower().endswith((".cpp", ".c", ".cc")) and re.search(
            r"\b\w+::\w+_Implementation\s*\(",
            text,
        ):
            cpp_impl = True
    if header_impl and cpp_impl:
        issues.append(
            "BlueprintNativeEvent fixes must not add manual _Implementation declarations to headers "
            "while also patching the .cpp body. Add only the cpp stub."
        )
    return issues


def multifile_exact_snippets(root: Path, paths: list[str], *, context_lines: int = 1) -> str:
    lines: list[str] = ["Exact current snippets (copy oldText from these):"]
    for rel in paths[:6]:
        path = root / rel.replace("/", "\\")
        if not path.is_file():
            continue
        text = read_text(path)
        file_lines = text.splitlines()
        if not file_lines:
            continue
        preview = "\n".join(file_lines[: min(len(file_lines), context_lines * 2 + 3)])
        lines.append(f"## {rel}\n{preview}")
    return "\n".join(lines) if len(lines) > 1 else ""


def pending_surfaces_after_partial(blockers: list[str]) -> set[str]:
    joined = "\n".join(blockers).lower()
    if "header changed without matching .cpp" in joined or (
        "header declaration changed without matching .cpp" in joined
    ):
        return {PENDING_CPP_FOLLOWUP}
    if ".cpp definition changed without matching header" in joined:
        return {PENDING_HEADER_FOLLOWUP}
    return set()


def scope_blocker_allows_partial_apply(mode: str, blockers: list[str], *, attempt: int = 1) -> bool:
    if str(mode or "") != "multifile_refactor":
        return False
    if attempt <= 1:
        return False
    joined = "\n".join(blockers).lower()
    return (
        "multifile fix incomplete" in joined
        or "header changed without matching .cpp" in joined
        or "header declaration changed without matching .cpp" in joined
    )


def route_forbidden_action_blockers(
    route: dict[str, Any] | None,
    bundle: dict[str, Any],
    *,
    request: str = "",
    mode: str = "",
) -> list[str]:
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
    broad_mode = str(route.get("broadMode") or "")
    module_fix = str(mode or "") == "module_fix" or broad_mode == "module_fix"
    if (
        "build.cs-first fix without module evidence" in forbidden
        and bundle_includes_build_cs(bundle)
        and not module_fix
        and not request_requests_build_cs_fix(request)
    ):
        issues.append(
            "The current error route forbids a Build.cs-first edit without module evidence. Patch the matching "
            "header/cpp surface instead, or show module evidence before changing Build.cs."
        )
    return issues


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
    if request_mentions_any(
        request,
        ("signature", "시그니처", "declaration", "definition", ".cpp", "parameter", "callback", "registration"),
    ):
        signature_findings = [
            finding
            for finding in findings
            if finding.code
            in {
                "CPP_FUNCTION_NOT_DECLARED_IN_HEADER",
                "CPP_FUNCTION_SIGNATURE_MISMATCH",
                "CPP_RETURN_TYPE_MISMATCH",
                "CALLBACK_FUNCTION_POINTER_MISMATCH",
                "INTERFACE_IMPLEMENTER_SIGNATURE_MISMATCH",
            }
        ]
        if signature_findings:
            issues.append(
                "The request appears to be a header/.cpp signature fix, but static validation still reports a .cpp/header mismatch."
            )
    callback_findings = [finding for finding in findings if finding.code == "CALLBACK_FUNCTION_POINTER_MISMATCH"]
    if callback_findings or request_mentions_any(request, ("callback", "registration", "parameter list")):
        if callback_findings:
            issues.append(
                "The callback registration still does not match the handler signature; update header, cpp, and registration together."
            )
    delegate_findings = [finding for finding in findings if finding.code == "DELEGATE_BROADCAST_SIGNATURE_MISMATCH"]
    if delegate_findings or request_mentions_any(request, ("broadcast", "delegate")):
        if delegate_findings:
            issues.append(
                "The delegate Broadcast callsite still does not match the declared payload; patch the exact .Broadcast(...) line in the matching cpp."
            )
    return issues


def edit_scope_blockers(
    request: str,
    before: dict[str, str],
    after: dict[str, str],
    root: Path,
    *,
    route: dict[str, Any] | None = None,
    findings: list[Finding] | None = None,
    mode: str = "",
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
        blueprint_impl = blueprint_native_event_impl_fix_context(request, route, findings)
        if changed_header and not changed_cpp and not blueprint_impl:
            if str(mode or "") == "multifile_refactor" or multifile_surface_enforced(mode, request):
                hint = multifile_required_surface_hint(mode, request, root)
                issues.append(
                    "Multifile fix incomplete: header changed without matching .cpp definition in the same patch. "
                    + (hint or "Update header and .cpp together, plus any callsite/consumer files.")
                )
            else:
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


def multifile_surface_blockers(
    request: str,
    before: dict[str, str],
    after: dict[str, str],
    root: Path,
    *,
    mode: str,
    pending_surfaces: set[str] | None = None,
    findings: list | None = None,
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
    pending = pending_surfaces or set()
    finding_codes = {str(getattr(finding, "code", "")) for finding in (findings or [])}
    cpp_only_return_drift = (
        "CPP_RETURN_TYPE_MISMATCH" in finding_codes
        and changed_cpp
        and not changed_headers
    )
    uproperty_authoritative_header = (
        cpp_only_return_drift
        or blueprint_native_event_impl_fix_context(request, findings=findings)
        or (
            any(term in lower for term in ("uproperty", "ufunction", "reflected score"))
            and changed_cpp
            and not changed_headers
        )
    )

    if any(term in lower for term in ("delegate", "signature", "declaration", "definition", "interface")):
        if changed_headers and not changed_cpp:
            if blueprint_native_event_impl_fix_context(request, findings=findings):
                pass
            elif PENDING_HEADER_FOLLOWUP not in pending:
                issues.append(
                    "Multifile fix incomplete: header declaration changed without matching .cpp definition. "
                    f"Changed: {', '.join(changed_headers)}"
                )
        elif changed_cpp and not changed_headers:
            if PENDING_CPP_FOLLOWUP not in pending and not uproperty_authoritative_header:
                issues.append(
                    "Multifile fix incomplete: .cpp definition changed without matching header declaration. "
                    f"Changed: {', '.join(changed_cpp)}"
                )

    if "interface" in lower and len(changed) < 2 and PENDING_CPP_FOLLOWUP not in pending and PENDING_HEADER_FOLLOWUP not in pending:
        issues.append(
            "Interface mismatch fixes require implementer header and cpp in the same patch. "
            f"Changed only: {', '.join(changed)}"
        )

    if any(term in lower for term in ("callback", "registration", "parameter list")) and len(changed) < 2:
        if not (PENDING_CPP_FOLLOWUP in pending and changed_cpp):
            if not (changed_headers and changed_cpp):
                issues.append(
                    "Callback parameter expansion requires declaration and definition updates together. "
                    f"Changed only: {', '.join(changed)}"
                )

    surface = refactor_surface_evidence(root, request, max_files=8, max_lines_per_file=4, max_chars=2000)
    consumer_paths = _consumer_source_paths(root, request)
    if (
        surface
        and len(changed) == 1
        and consumer_paths
        and any(path.lower() in surface.lower() for path in consumer_paths)
    ):
        if PENDING_CPP_FOLLOWUP not in pending:
            issues.append(
                "Multifile refactor likely needs callsite/consumer updates in addition to "
                f"{changed[0]}."
            )
    return issues


def _consumer_source_paths(root: Path, request: str) -> list[str]:
    lower = request.lower()
    if "consumer" not in lower and "callsite" not in lower and "split" not in lower:
        return []
    paths: list[str] = []
    for path in iter_source_files(root):
        rel = str(path.relative_to(root)).replace("\\", "/")
        name_lower = path.name.lower()
        if "consumer" in name_lower or "callsite" in name_lower:
            paths.append(rel)
    return paths
