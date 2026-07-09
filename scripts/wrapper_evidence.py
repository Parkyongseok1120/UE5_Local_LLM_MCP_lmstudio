#!/usr/bin/env python3
"""Project state summaries and focused evidence builders for the LM Studio wrapper."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import token_budget
from unreal_static_validate import iter_source_files, read_text, should_ignore_project_path

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

_PROJECT_SNAPSHOT_CACHE: dict[str, dict[str, tuple[float, str]]] = {}
_REFACTOR_SURFACE_CACHE: dict[str, str] = {}

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
CLASS_DECL_RE = re.compile(r"\bclass\s+(?:[A-Z0-9_]+_API\s+)?(?P<name>[AUFSI][A-Za-z0-9_]*)\b")
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
PROJECT_PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/][^\s:'\"]+|(?:Source|Plugins|Config)[\\/][^\s:'\"]+)"
    r"\.(?:h|hpp|cpp|c|cc|cs|ini|json|uproject|uplugin)",
    re.I,
)
SOURCE_PAIR_SUFFIXES = {".h", ".hpp", ".cpp", ".c", ".cc"}


def project_relative_path(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def is_text_project_file(path: Path) -> bool:
    if not path.is_file() or path.suffix.lower() not in ALLOWED_SUFFIXES:
        return False
    return not should_ignore_project_path(path)


def clear_all_project_snapshot_caches() -> None:
    _PROJECT_SNAPSHOT_CACHE.clear()


def invalidate_project_snapshot_cache(root: Path, *, relative_paths: list[str] | None = None) -> None:
    root_key = str(root.resolve())
    cache = _PROJECT_SNAPSHOT_CACHE.get(root_key)
    if not cache:
        return
    if relative_paths:
        for relative in relative_paths:
            cache.pop(relative.replace("\\", "/"), None)
    else:
        _PROJECT_SNAPSHOT_CACHE.pop(root_key, None)


def snapshot_project_files(root: Path) -> dict[str, str]:
    root_key = str(root.resolve())
    cache = _PROJECT_SNAPSHOT_CACHE.setdefault(root_key, {})
    snapshot: dict[str, str] = {}
    seen: set[str] = set()
    for path in sorted(iter_source_files(root)):
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


def _resolve_focus_path(root: Path, raw: str) -> Path | None:
    candidate_text = str(raw or "").strip().strip(".,);]")
    if not candidate_text:
        return None
    candidate = Path(candidate_text)
    if not candidate.is_absolute():
        candidate = root / candidate_text.replace("\\", "/")
    try:
        resolved = candidate.resolve()
    except OSError:
        return None
    root_resolved = root.resolve()
    if resolved.is_file() and (root_resolved in resolved.parents or resolved.parent == root_resolved):
        return resolved
    return None


def project_summary_focus_paths(root: Path, focus_text: str, snapshot: dict[str, str] | None = None) -> list[str]:
    snapshot = snapshot if snapshot is not None else snapshot_project_files(root)
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

    for match in PROJECT_PATH_RE.finditer(focus_text or ""):
        resolved = _resolve_focus_path(root, match.group(0))
        if not resolved:
            continue
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
        build_entries = sorted((rel, txt) for rel, txt in snapshot.items() if rel.lower().endswith(".build.cs"))
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
    for match in PROJECT_PATH_RE.finditer(text or ""):
        resolved = _resolve_focus_path(root, match.group(0))
        if resolved:
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
        Path(path).stem: path for path in before if Path(path).suffix.lower() in {".cpp", ".c", ".cc"}
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
        before_definitions = {(item["class"], item["name"]) for item in _cpp_member_definitions(before_cpp, class_names)}
        after_definitions = {(item["class"], item["name"]) for item in _cpp_member_definitions(after_cpp, class_names)}
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
            if path.suffix.lower() in {".h", ".hpp"}
            and any(part in path.parts for part in ("Source", "Plugins", "Config"))
        ]
        by_stem = {
            path.stem: path
            for path in iter_source_files(root)
            if path.suffix.lower() in {".cpp", ".c", ".cc"}
            and any(part in path.parts for part in ("Source", "Plugins", "Config"))
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


def clear_refactor_surface_cache() -> None:
    _REFACTOR_SURFACE_CACHE.clear()


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

    cache_key = hashlib.sha1(
        f"{root.resolve()}|{max_files}|{max_lines_per_file}|{max_chars}|{'|'.join(terms)}".encode("utf-8")
    ).hexdigest()
    cached = _REFACTOR_SURFACE_CACHE.get(cache_key)
    if cached is not None:
        return cached

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
    result = "\n".join(lines)[:max_chars] if files_added else ""
    _REFACTOR_SURFACE_CACHE[cache_key] = result
    return result


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


def _should_include_refactor_surface(mode: str) -> bool:
    normalized = str(mode or "").strip().lower()
    return normalized == "multifile_refactor" or normalized.startswith("refactor_")


def callback_registration_evidence(root: Path, focus_text: str, *, max_chars: int = 1200) -> str:
    lower = str(focus_text or "").lower()
    if not any(term in lower for term in ("callback", "registration", "parameter list")):
        return ""
    lines = ["Callback registration evidence:"]
    added = False
    for path in iter_source_files(root):
        if path.suffix.lower() not in {".cpp", ".c", ".cc"}:
            continue
        text = read_text(path)
        if "using " not in text or "&" not in text:
            continue
        rel = str(path.relative_to(root)).replace("\\", "/")
        file_lines: list[str] = []
        for line_no, raw_line in enumerate(text.splitlines(), start=1):
            compact = re.sub(r"\s+", " ", raw_line).strip()
            if re.search(r"using\s+\w+\s*=\s*.*\(\*\)", compact) or re.search(r"&\w+::\w+", compact):
                file_lines.append(f"- line {line_no}: {compact}")
        if file_lines:
            lines.append(f"## {rel}")
            lines.extend(file_lines)
            added = True
        if len("\n".join(lines)) >= max_chars:
            break
    return "\n".join(lines)[:max_chars] if added else ""


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
    registration_evidence = callback_registration_evidence(root, focus_text)
    pair_context = "" if (declaration_evidence or refactor_evidence or registration_evidence) else focused_source_pair_context(root, focus_text)
    return "\n".join(
        part for part in (declaration_evidence, refactor_evidence, registration_evidence, pair_context) if part.strip()
    )[:max_chars]


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


def multifile_pattern_hint(request: str) -> str:
    lower = str(request or "").lower()
    hints: list[str] = []
    if "interface" in lower and ("return type" in lower or "implementer" in lower):
        hints.append("Pattern: align implementer header + cpp return type/signature with the interface declaration in one patch.")
    if "subsystem" in lower and ("applydata" in lower or "api move" in lower or "consumer" in lower):
        hints.append("Pattern: rename cpp definition and consumer callsites to the header-declared method name.")
    if "callback" in lower or "registration" in lower or "parameter list" in lower:
        hints.append("Pattern: expand handler header/cpp params to match the callback typedef, then keep registration assignment.")
    if any(term in lower for term in ("uproperty", "ufunction", "reflected score", "type migration")):
        hints.append(
            "Pattern: when the header UFUNCTION return type is already authoritative, fix only the .cpp "
            "definition return type and cast the stored member value."
        )
    if "prepare" in lower and "commit" in lower:
        hints.append("Pattern: replace stale combined method with split header methods and update consumer callsites.")
    if "split" in lower and "callsite" in lower:
        hints.append("Pattern: update cpp definitions and consumer callsites together; do not reintroduce removed combined methods.")
    return "\n".join(hints)


def multifile_required_surface_hint(mode: str, request: str, root: Path) -> str:
    if str(mode or "") != "multifile_refactor" and not multifile_surface_enforced(mode, request):
        return ""
    surfaces: list[str] = []
    lower = request.lower()
    if any(term in lower for term in ("delegate", "signature", "declaration", "definition")):
        surfaces.extend(["matching header declaration", "matching .cpp definition"])
    if "interface" in lower:
        surfaces.append("interface + implementer header/cpp")
    if "callsite" in lower or "call site" in lower or "consumer" in lower:
        surfaces.append("consumer/callsite .cpp")
    if "callback" in lower or "registration" in lower:
        surfaces.append("callback handler header + .cpp definition (registration typedef unchanged if already correct)")
    if any(term in lower for term in ("uproperty", "ufunction", "reflected score", "type migration")):
        surfaces.append("authoritative header declaration + matching .cpp return type")
    evidence = refactor_surface_evidence(root, request, max_files=6, max_lines_per_file=3, max_chars=1200)
    if evidence:
        surfaces.append("refactor surface evidence below")
    if not surfaces:
        return ""
    pattern_hint = multifile_pattern_hint(request)
    parts = ["Required multifile surfaces: " + "; ".join(dict.fromkeys(surfaces))]
    if pattern_hint:
        parts.append(pattern_hint)
    return "\n".join(parts)
