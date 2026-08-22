#!/usr/bin/env python
"""Unreal reflection, UHT, and reflected ownership validators."""

from __future__ import annotations

import re
from pathlib import Path

from cpp_parse_utils import (
    extract_macro_blocks,
    find_balanced_parens,
    mask_comments_and_strings,
)
from unreal_static_model import (
    CPP_HEADER_SUFFIXES,
    Finding,
)
from unreal_static_scan import (
    include_lines,
    iter_function_blocks,
    line_number,
    lines_in_function_blocks,
)

REFLECTED_TYPE_RE = re.compile(r"\b(UCLASS|USTRUCT|UENUM|UINTERFACE)\s*\(")

REFLECTION_RE = re.compile(r"\b(UCLASS|USTRUCT|UENUM|UINTERFACE|GENERATED_BODY)\s*\(")

UE_DECLARATION_MACROS = {
    "UCLASS",
    "USTRUCT",
    "UENUM",
    "UINTERFACE",
    "UFUNCTION",
    "UPROPERTY",
    "GENERATED_BODY",
    "GENERATED_UCLASS_BODY",
}

RAW_UOBJECT_MEMBER_RE = re.compile(
    r"\b(?:UObject|AActor|APawn|AController|UActorComponent|USceneComponent|UDataAsset|"
    r"UTexture(?:2D)?|UMaterial(?:Interface|Instance)?|USoundBase|UAnimMontage)\s*\*\s*"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?:=\s*[^;]+)?;"
)

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
        uclass_idx = _uclass_line_index(text)
        for line_no, _ in generated:
            if line_no > uclass_idx:
                findings.append(
                    Finding(
                        "error",
                        str(path.relative_to(root)),
                        line_no,
                        "GENERATED_H_AFTER_TYPE",
                        "generated.h must appear in the include block before UCLASS/USTRUCT, not after the type body.",
                    )
                )
                break
    return findings

def _uclass_line_index(text: str) -> int:
    for index, line in enumerate(text.splitlines(), start=1):
        if re.search(r"\bU(CLASS|STRUCT|ENUM)\b", line):
            return index
    return len(text.splitlines()) + 1

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

UHT_REFLECTION_MACROS = (
    "UCLASS",
    "USTRUCT",
    "UENUM",
    "UINTERFACE",
    "UDELEGATE",
    "UPROPERTY",
    "UFUNCTION",
    "GENERATED_BODY",
    "GENERATED_UCLASS_BODY",
    "GENERATED_USTRUCT_BODY",
    "GENERATED_IINTERFACE_BODY",
)

UHT_REFLECTION_MACRO_LINE_RE = re.compile(r"^\s*(" + "|".join(UHT_REFLECTION_MACROS) + r")\s*\(")

UHT_CONDITION_ALLOWED_TOKENS = frozenset({"WITH_EDITOR", "WITH_EDITORONLY_DATA", "defined"})

INCLUDE_GUARD_MACRO_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*_H_{0,2}$")

def uht_condition_is_allowlisted(directive: str, condition: str) -> bool:
    condition = condition.split("//", 1)[0]
    condition = re.sub(r"/\*.*?\*/", " ", condition).strip()
    if directive == "ifndef" and INCLUDE_GUARD_MACRO_RE.match(condition):
        return True
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", condition)
    if not tokens:
        return False
    return all(token in UHT_CONDITION_ALLOWED_TOKENS for token in tokens)

def validate_uht_macros_in_conditional_blocks(path: Path, text: str, root: Path) -> list[Finding]:
    """Flag reflection macros inside preprocessor conditionals that UHT cannot parse.

    UHT only evaluates WITH_EDITOR / WITH_EDITORONLY_DATA blocks (including their
    #else branches). Reflection macros inside anything else -- most commonly
    `#if !UE_BUILD_SHIPPING` -- fail at build time with confusing UHT errors.
    """
    findings: list[Finding] = []
    rel = str(path.relative_to(root))
    # Each frame: (is_allowlisted, condition_text, directive_line)
    stack: list[tuple[bool, str, int]] = []
    reported_frame_lines: set[int] = set()
    for index, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        directive_match = re.match(r"#\s*(if|ifdef|ifndef|elif|else|endif)\b(.*)$", stripped)
        if directive_match:
            directive = directive_match.group(1)
            remainder = directive_match.group(2).strip()
            if directive in {"if", "ifdef", "ifndef"}:
                stack.append((uht_condition_is_allowlisted(directive, remainder), remainder, index))
            elif directive == "elif" and stack:
                allowed, _, start_line = stack[-1]
                stack[-1] = (
                    allowed and uht_condition_is_allowlisted("if", remainder),
                    remainder,
                    start_line,
                )
            elif directive == "endif" and stack:
                stack.pop()
            # #else keeps the frame's allow status: UHT parses both branches of
            # WITH_EDITOR blocks, and the else-branch of a disallowed block is
            # just as illegal for reflection macros.
            continue
        if not stack or all(frame[0] for frame in stack):
            continue
        macro_match = UHT_REFLECTION_MACRO_LINE_RE.match(stripped)
        if not macro_match:
            continue
        offending = next(frame for frame in stack if not frame[0])
        if offending[2] in reported_frame_lines:
            continue
        reported_frame_lines.add(offending[2])
        condition_display = offending[1] or "(no condition)"
        findings.append(
            Finding(
                "error",
                rel,
                index,
                "UHT_MACRO_IN_CONDITIONAL_BLOCK",
                (
                    f"{macro_match.group(1)} is inside the preprocessor block opened at line {offending[2]} "
                    f"(`{condition_display}`). UHT only parses WITH_EDITOR / WITH_EDITORONLY_DATA conditionals. "
                    "Declare reflection macros (UCLASS/UPROPERTY/UFUNCTION/GENERATED_BODY) unconditionally in the "
                    "header and guard only the implementation in the .cpp (e.g. wrap the function body in "
                    "#if !UE_BUILD_SHIPPING, or use a runtime check)."
                ),
            )
        )
    return findings

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
        if "UPROPERTY" in nearby:
            continue
        if "TObjectPtr" in line:
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

PROJECT_UOBJECT_REFERENCE_RE = re.compile(
    r"\b(?:TObjectPtr\s*<\s*)?(U[A-Z][A-Za-z0-9_]*)\s*(?:\*|>)"
)

def validate_project_uobject_type_visibility(
    path: Path,
    text: str,
    root: Path,
    include_index: dict[str, list[str]],
) -> list[Finding]:
    """Require a direct include or forward declaration for project UObject pointer types."""
    if path.suffix.lower() not in CPP_HEADER_SUFFIXES:
        return []
    masked = mask_comments_and_strings(text)
    included_basenames = {Path(value).name.lower() for _, value in include_lines(text)}
    project_header_basenames = {Path(key).name.lower() for key in include_index}
    findings: list[Finding] = []
    seen: set[str] = set()
    for match in PROJECT_UOBJECT_REFERENCE_RE.finditer(masked):
        type_name = match.group(1)
        if type_name in seen:
            continue
        expected_header = f"{type_name[1:]}.h".lower()
        if expected_header not in project_header_basenames:
            continue
        seen.add(type_name)
        if expected_header in included_basenames:
            continue
        if re.search(rf"\bclass\s+(?:[A-Z0-9_]+_API\s+)?{re.escape(type_name)}\s*(?:;|:)", masked):
            continue
        findings.append(
            Finding(
                "error",
                str(path.relative_to(root)),
                line_number(text, match.start()),
                "PROJECT_UOBJECT_TYPE_NOT_VISIBLE",
                f"{type_name} is a project UObject pointer type but this header neither includes {type_name[1:]}.h nor forward-declares `class {type_name};`.",
            )
        )
    return findings

def has_direct_uproperty_annotation(lines: list[str], line_index: int) -> bool:
    """Backward-compatible wrapper around member_has_uproperty()."""
    text = "\n".join(lines)
    line_starts = [0]
    for line in lines:
        line_starts.append(line_starts[-1] + len(line) + 1)
    if line_index < 0 or line_index >= len(line_starts) - 1:
        return False
    return member_has_uproperty(text, line_starts[line_index])

def member_has_uproperty(text: str, member_offset: int) -> bool:
    masked = mask_comments_and_strings(text)
    gap_macro_re = re.compile(
        r"\b(UFUNCTION|GENERATED_BODY|UCLASS|UINTERFACE|USTRUCT|UENUM|UPROPERTY|[A-Z][A-Z0-9_]*)\s*\("
    )
    for start, end, _block in extract_macro_blocks(masked, "UPROPERTY"):
        if end > member_offset:
            continue
        gap = masked[end:member_offset]
        if gap_macro_re.search(gap):
            continue
        stripped = re.sub(r"//[^\n]*", "", gap)
        stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.DOTALL)
        if not stripped.strip():
            return True
        if ";" not in stripped and not re.search(r"\b(class|struct|enum)\b", stripped):
            return True
    return False

UOBJECT_CONTAINER_MEMBER_RE = re.compile(
    r"\b(?:TArray|TMap|TSet)\s*<[^>]*(?:TObjectPtr\s*<\s*[^>]+>|[UA][A-Za-z_][A-Za-z0-9_<>]*\s*\*)[^>]*>"
    r"\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?:=\s*[^;]+)?;"
)

TOBJECTPTR_MEMBER_RE = re.compile(
    r"\bTObjectPtr\s*<\s*(?:const\s+)?[^>]+>\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?:=\s*[^;]+)?;"
)

RAW_NEW_UOBJECT_RE = re.compile(r"\bnew\s+(?:U|A)[A-Za-z_][A-Za-z0-9_]*\b")

UOBJECT_PTR_DECL_RE = re.compile(
    r"\b(?:U|A)[A-Za-z_][A-Za-z0-9_]*\s*\*\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\b"
)

DELETE_VAR_RE = re.compile(r"\bdelete\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*;")

BLUEPRINTPURE_NON_CONST_RE = re.compile(
    r"UFUNCTION\s*\([^)]*BlueprintPure[^)]*\)\s*\n\s*(?!.*\bconst\b)[^;]+?\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\(",
    re.MULTILINE,
)

def validate_uproperty_category_without_exposure(path: Path, text: str, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    if path.suffix.lower() not in CPP_HEADER_SUFFIXES:
        return findings
    masked = mask_comments_and_strings(text)
    exposure_specifiers = re.compile(
        r"\b(?:EditAnywhere|EditDefaultsOnly|EditInstanceOnly|"
        r"VisibleAnywhere|VisibleDefaultsOnly|VisibleInstanceOnly|"
        r"BlueprintReadOnly|BlueprintReadWrite|BlueprintAssignable|BlueprintCallable)\b"
    )
    for match in re.finditer(r"\bUPROPERTY\s*\(", masked):
        open_index = masked.find("(", match.start())
        close_index = find_balanced_parens(masked, open_index)
        if close_index < 0:
            continue
        specifiers = text[open_index + 1 : close_index]
        if not re.search(r"\bCategory\s*=", specifiers):
            continue
        if exposure_specifiers.search(specifiers):
            continue
        findings.append(
            Finding(
                "error",
                str(path.relative_to(root)),
                line_number(text, match.start()),
                "UPROPERTY_CATEGORY_WITHOUT_EXPOSURE",
                (
                    "UPROPERTY sets Category without an editor or Blueprint exposure specifier; "
                    "UHT -WarningsAsErrors rejects this. Remove Category for internal state, or add "
                    "an intentional Edit/Visible/Blueprint specifier."
                ),
            )
        )
    return findings

def validate_uobject_container_without_uproperty(path: Path, text: str, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    if path.suffix.lower() not in CPP_HEADER_SUFFIXES:
        return findings
    lines = text.splitlines()
    local_lines = lines_in_function_blocks(text)
    for index, line in enumerate(lines, start=1):
        if index in local_lines:
            continue
        if "(" in line and "TMap" not in line and "TSet" not in line and "TArray" not in line:
            continue
        match = UOBJECT_CONTAINER_MEMBER_RE.search(line)
        if not match or member_has_uproperty(text, text.find(line)):
            continue
        findings.append(
            Finding(
                "warning",
                str(path.relative_to(root)),
                index,
                "UOBJECT_CONTAINER_WITHOUT_UPROPERTY",
                f'UObject container member "{match.group("name")}" lacks UPROPERTY(); GC may collect retained objects.',
            )
        )
    return findings

def validate_tobjectptr_without_uproperty(path: Path, text: str, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    if path.suffix.lower() not in CPP_HEADER_SUFFIXES:
        return findings
    lines = text.splitlines()
    local_lines = lines_in_function_blocks(text)
    for index, line in enumerate(lines, start=1):
        if index in local_lines:
            continue
        match = TOBJECTPTR_MEMBER_RE.search(line)
        if not match or member_has_uproperty(text, text.find(line)):
            continue
        findings.append(
            Finding(
                "warning",
                str(path.relative_to(root)),
                index,
                "TOBJECTPTR_WITHOUT_UPROPERTY",
                f'TObjectPtr member "{match.group("name")}" lacks UPROPERTY(); GC tracking is incomplete.',
            )
        )
    return findings

def validate_raw_new_delete_uobject(path: Path, text: str, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    rel = str(path.relative_to(root))
    for match in RAW_NEW_UOBJECT_RE.finditer(text):
        findings.append(
            Finding(
                "warning",
                rel,
                line_number(text, match.start()),
                "RAW_NEW_DELETE_UOBJECT",
                "Avoid new on UObject-derived types; use NewObject<> with an explicit outer.",
            )
        )
    for header, start, body in iter_function_blocks(text):
        uobject_ptrs = {match.group("name") for match in UOBJECT_PTR_DECL_RE.finditer(body)}
        if not uobject_ptrs:
            continue
        for match in DELETE_VAR_RE.finditer(body):
            name = match.group("name")
            if name not in uobject_ptrs:
                continue
            findings.append(
                Finding(
                    "warning",
                    rel,
                    line_number(text, start + match.start()),
                    "RAW_NEW_DELETE_UOBJECT",
                    f"Avoid delete on UObject pointer '{name}'; let GC manage lifetime.",
                )
            )
    return findings

def validate_blueprintpure_missing_const(path: Path, text: str, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    if path.suffix.lower() not in CPP_HEADER_SUFFIXES:
        return findings
    for match in BLUEPRINTPURE_NON_CONST_RE.finditer(text):
        findings.append(
            Finding(
                "info",
                str(path.relative_to(root)),
                line_number(text, match.start()),
                "BLUEPRINTPURE_MISSING_CONST",
                f'BlueprintPure function "{match.group("name")}" should be const.',
            )
        )
    return findings

__all__ = [
    'REFLECTED_TYPE_RE',
    'REFLECTION_RE',
    'UE_DECLARATION_MACROS',
    'RAW_UOBJECT_MEMBER_RE',
    'validate_generated_h',
    '_uclass_line_index',
    'validate_reflected_namespace',
    'UHT_REFLECTION_MACROS',
    'UHT_REFLECTION_MACRO_LINE_RE',
    'UHT_CONDITION_ALLOWED_TOKENS',
    'INCLUDE_GUARD_MACRO_RE',
    'uht_condition_is_allowlisted',
    'validate_uht_macros_in_conditional_blocks',
    'validate_blueprint_native_event_declarations',
    'validate_raw_uobject_members',
    'validate_private_blueprint_access',
    'PROJECT_UOBJECT_REFERENCE_RE',
    'validate_project_uobject_type_visibility',
    'has_direct_uproperty_annotation',
    'member_has_uproperty',
    'UOBJECT_CONTAINER_MEMBER_RE',
    'TOBJECTPTR_MEMBER_RE',
    'RAW_NEW_UOBJECT_RE',
    'UOBJECT_PTR_DECL_RE',
    'DELETE_VAR_RE',
    'BLUEPRINTPURE_NON_CONST_RE',
    'validate_uproperty_category_without_exposure',
    'validate_uobject_container_without_uproperty',
    'validate_tobjectptr_without_uproperty',
    'validate_raw_new_delete_uobject',
    'validate_blueprintpure_missing_const',
]
