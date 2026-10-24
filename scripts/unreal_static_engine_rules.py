#!/usr/bin/env python
"""Engine-backed Unreal validators: rules that read the installed engine's public headers.

Every rule takes an ``EngineHeaders`` (``unreal_static_engine``) or None and reports nothing
without a readable engine. Findings carry ``analysisKind="engine_header_lexical"``, a confidence
and engine evidence. ``validate_engine_aware`` runs them after the lexical validators.
"""

from __future__ import annotations

import re
from pathlib import Path

from cpp_parse_utils import (
    mask_comments_and_strings,
)
from unreal_static_crossfile import (
    validate_duplicate_reflected_type_names,
)
from unreal_static_engine import (
    EngineHeaders,
    EngineMember,
    engine_base_chain,
    normalize_parameter_type,
    normalize_return_type,
    parse_class_members,
    split_parameters,
    validate_known_invalid_engine_symbols,
)
from unreal_static_model import (
    CPP_HEADER_SUFFIXES,
    CPP_SOURCE_SUFFIXES,
    SOURCE_ONLY_SUFFIXES,
    Finding,
)
from unreal_static_registry import _append_validator_internal_error
from unreal_static_scan import (
    build_source_include_index,
    iter_class_method_blocks,
    iter_source_files,
    line_number,
    read_text,
)
from unreal_static_visibility import (
    QUOTED_INCLUDE_RE,
    _resolve_project_include,
    visible_includes,
)


class ProjectTypes:
    """Project-defined types: first base, base count, defining header text and every type name."""

    _DEFINITION = re.compile(
        r"\b(?:class|struct)\s+(?:[A-Z0-9_]+_API\s+)?([A-Za-z_]\w*)\s*(?:final\s*)?(?::([^;{]*))?\{")

    def __init__(self, texts: dict[Path, str]) -> None:
        self.texts = texts
        self.bases: dict[str, str] = {}
        self.base_counts: dict[str, int] = {}
        self.class_text: dict[str, str] = {}
        self.type_names: set[str] = set()
        for path, text in texts.items():
            masked = mask_comments_and_strings(text)
            for match in re.finditer(r"\b(?:enum\s+(?:class\s+)?|using\s+|typedef\s+[^;]*\s)([A-Za-z_]\w*)", masked):
                self.type_names.add(match.group(1))
            if path.suffix.lower() not in CPP_HEADER_SUFFIXES:
                continue
            for match in self._DEFINITION.finditer(masked):
                name = match.group(1)
                self.type_names.add(name)
                self.class_text.setdefault(name, text)
                clause = match.group(2) or ""
                parents = re.findall(r"(?:\b(?:public|protected|private)\s+)?(?:virtual\s+)?([A-Za-z_]\w*)\s*(?:<[^,{]*>)?\s*(?:,|$)",
                                     clause.strip())
                if parents:
                    self.bases.setdefault(name, parents[0])
                    self.base_counts.setdefault(name, len(parents))

    def engine_chain(self, class_name: str) -> list[str] | None:
        return engine_base_chain(class_name, self.bases, self.type_names)

    def project_members(self, chain: list[str]) -> dict[str, list[EngineMember]]:
        """Members declared by the project classes on ``chain`` (engine base excluded)."""
        members: dict[str, list[EngineMember]] = {}
        for name in chain[:-1]:
            parsed = parse_class_members(self.class_text.get(name, ""), name)
            for key, values in (parsed[1] if parsed else {}).items():
                members.setdefault(key, []).extend(values)
        return members


def _engine_location(engine: EngineHeaders, lookup) -> dict:
    member = lookup.members[0] if lookup.members else None
    return {"engineUri": engine.uri(lookup.header), "line": member.line if member else None,
            "declaredIn": lookup.declared_in}


def _signature_text(member: EngineMember, name: str) -> str:
    return f"{member.return_type or 'void'} {name}({', '.join(member.params)}){' const' if member.const else ''}"


def _signatures_match(project: EngineMember, engine: EngineMember) -> bool:
    if [normalize_parameter_type(p) for p in project.params] != [normalize_parameter_type(p) for p in engine.params]:
        return False
    if project.const != engine.const:
        return False
    ours, theirs = normalize_return_type(project.return_type), normalize_return_type(engine.return_type)
    # Covariant pointer returns are legal overrides.
    return ours == theirs or (ours.endswith("*") and theirs.endswith("*"))


def validate_engine_override_signatures(path: Path, text: str, root: Path, project: ProjectTypes,
                                        engine: EngineHeaders | None) -> list[Finding]:
    """``override`` and ``_Implementation`` declarations whose engine base declares a different signature."""
    if engine is None or path.suffix.lower() not in CPP_HEADER_SUFFIXES:
        return []
    findings: list[Finding] = []
    rel = str(path.relative_to(root)).replace("\\", "/")
    for match in ProjectTypes._DEFINITION.finditer(mask_comments_and_strings(text)):
        class_name = match.group(1)
        chain = project.engine_chain(class_name)
        if not chain or project.base_counts.get(class_name, 1) != 1:
            continue
        parsed = parse_class_members(text, class_name)
        if not parsed:
            continue
        inherited = project.project_members(chain[1:])
        for name, members in parsed[1].items():
            for member in members:
                implementation = name.endswith("_Implementation")
                if member.kind != "method" or not (member.override or implementation) or name in inherited:
                    continue
                lookup = engine.lookup_member(chain[-1], name)
                base_name = name
                if lookup.status != "found" and implementation:
                    base_name = name[: -len("_Implementation")]
                    lookup = engine.lookup_member(chain[-1], base_name)
                    if lookup.status == "found" and not any(m.reflected == "UFUNCTION" for m in lookup.members):
                        continue
                if lookup.status == "unknown":
                    continue
                candidates = [m for m in lookup.members if m.kind == "method"]
                if lookup.status == "absent":
                    if implementation or not member.override:
                        continue
                    message = (f"{class_name}::{name} is marked override, but no base on {' -> '.join(lookup.chain[:6])} "
                               "declares it (C3668). Check the engine declaration or drop the override.")
                elif any(_signatures_match(member, candidate) for candidate in candidates):
                    continue
                else:
                    expected = "; ".join(_signature_text(c, name) for c in candidates[:3])
                    message = (f"{class_name}::{name} does not match the {lookup.declared_in} declaration it overrides "
                               f"(got {_signature_text(member, name)}). Engine declares: {expected}.")
                findings.append(Finding(
                    "error", rel, member.line, "ENGINE_OVERRIDE_SIGNATURE_MISMATCH", message,
                    metadata=engine.metadata("ENGINE_OVERRIDE_SIGNATURE_MISMATCH", "high",
                                             [_engine_location(engine, lookup)] if lookup.members else
                                             [{"inheritancePath": lookup.chain}],
                                             ["only the first base class is followed"])))
    return findings


_WRAPPER_TYPES = frozenset({"TSoftObjectPtr", "TWeakObjectPtr", "TSoftClassPtr", "TSubclassOf"})
_GENERATED_MEMBERS = frozenset({"StaticClass", "StaticStruct", "Super", "ThisClass", "GetPrivateStaticClass",
                                "StaticRegisterNatives", "StaticPackage", "GetDefaultObject"})
_CPP_KEYWORDS = frozenset({"return", "if", "else", "for", "while", "switch", "case", "this", "auto", "const",
                           "delete", "new", "true", "false", "nullptr", "Super", "break", "continue", "do", "sizeof"})


def _receiver(type_text: str) -> dict[str, str]:
    """Owner type per access operator (``->`` / ``.``) for a declared variable type."""
    value = re.sub(r"\b(?:const|class|struct)\s+", "", type_text).strip()
    match = re.fullmatch(r"(TObjectPtr|TWeakObjectPtr|TSoftObjectPtr)\s*<\s*([A-Za-z_]\w*)\s*>", value)
    if match:
        # ``->`` reaches the pointee; ``.`` the wrapper's own API (TObjectPtr's is not checked).
        return {"->": match.group(2), **({"." : match.group(1)} if match.group(1) in _WRAPPER_TYPES else {})}
    match = re.fullmatch(r"([A-Za-z_]\w*)\s*\*", value)
    if match:
        return {"->": match.group(1)}
    match = re.fullmatch(r"([A-Za-z_]\w*)\s*(?:<[^<>]*>)?", value)
    if match and match.group(1) in _WRAPPER_TYPES | {"FHitResult"}:
        return {".": match.group(1)}
    return {}


def _declared_receivers(body: str, end: int) -> dict[str, dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    for match in re.finditer(r"\b([AUFI][A-Za-z0-9_]*)\s*\*\s*(?:const\s+)?([A-Za-z_]\w*)\s*(?:=|\(|;|\))", body[:end]):
        found[match.group(2)] = {"->": match.group(1)}
    for match in re.finditer(r"\bauto\s*\*?\s*([A-Za-z_]\w*)\s*=[^;]*?\b(?:Cast|CastChecked|NewObject|SpawnActor|"
                             r"CreateDefaultSubobject|GetSubsystem)\s*<\s*([A-Za-z_]\w*)\s*>", body[:end]):
        found[match.group(1)] = {"->": match.group(2)}
    return found


def validate_engine_member_access(path: Path, text: str, root: Path, project: ProjectTypes,
                                  engine: EngineHeaders | None, hints: list[Path] | tuple[Path, ...] = ()) -> list[Finding]:
    """Member access on an engine-typed receiver, and unqualified assignments in engine-derived
    class methods, that name no member of the engine type or its bases (C2039 / C2065)."""
    if engine is None or path.suffix.lower() not in CPP_SOURCE_SUFFIXES:
        return []
    findings: list[Finding] = []
    rel = str(path.relative_to(root)).replace("\\", "/")
    masked = mask_comments_and_strings(text)
    reported: set[tuple[str, str]] = set()

    def report(offset: int, owner: str, member: str, lookup, how: str) -> None:
        if (owner, member) in reported:
            return
        reported.add((owner, member))
        findings.append(Finding(
            "error", rel, line_number(text, offset), "ENGINE_MEMBER_NOT_FOUND",
            f"{owner} has no member '{member}' ({how}); searched {' -> '.join(lookup.chain[:8])} in the installed engine headers.",
            metadata=engine.metadata("ENGINE_MEMBER_NOT_FOUND", "medium", [{"inheritancePath": lookup.chain[:16]}],
                                     ["the receiver type is inferred from a local declaration or a class member"])))

    other_texts = [value for key, value in project.texts.items() if key != path]
    for class_name, method, start, body in iter_class_method_blocks(masked):
        body_start = masked.find(body, start)
        if body_start < 0:
            continue
        chain = project.engine_chain(class_name)
        fields: dict[str, dict[str, str]] = {}
        class_members = project.project_members(chain) if chain else {}
        for name, members in class_members.items():
            for member in members:
                if member.kind == "field" and member.value_type and _receiver(member.value_type):
                    fields[name] = _receiver(member.value_type)
        for access in re.finditer(r"\b([A-Za-z_]\w*)\s*(->|\.)\s*([A-Za-z_]\w*)", body):
            variable, operator, member = access.groups()
            receiver = _declared_receivers(body, access.start()).get(variable) or fields.get(variable) or {}
            owner = receiver.get(operator)
            if not owner or member in _GENERATED_MEMBERS:
                continue
            if owner in project.type_names or (owner.startswith("T") and owner not in _WRAPPER_TYPES):
                continue
            lookup = engine.lookup_member(owner, member, hints)
            if lookup.status == "absent":
                report(body_start + access.start(3), owner, member, lookup, f"{variable}{operator}{member}")
        if not chain:
            continue
        signature = masked[start:body_start]
        for assignment in re.finditer(r"(?:^|[;{}])\s*([A-Za-z_]\w*)\s*(?:=(?!=)|\+=|-=|\|=)", body, re.M):
            name = assignment.group(1)
            if name in _CPP_KEYWORDS or name.isupper() or name in class_members:
                continue
            declared = re.compile(r"[\w>*&]\s+" + re.escape(name) + r"\s*[=;,(\[{)]")
            if declared.search(body) or declared.search(signature):
                continue
            if any(re.search(r"\b" + re.escape(name) + r"\b", other) for other in other_texts):
                continue
            lookup = engine.lookup_member(chain[-1], name, hints)
            if lookup.status == "absent":
                report(body_start + assignment.start(1), chain[-1], name, lookup, f"assigned in {class_name}::{method}")
    return findings


def validate_parameter_shadowing(path: Path, text: str, root: Path, project: ProjectTypes,
                                 engine: EngineHeaders | None, hints: list[Path] | tuple[Path, ...] = ()) -> list[Finding]:
    """Out-of-line definitions whose parameter hides an engine base data member (C4458, an error in UE builds)."""
    if engine is None or path.suffix.lower() not in CPP_SOURCE_SUFFIXES:
        return []
    findings: list[Finding] = []
    rel = str(path.relative_to(root)).replace("\\", "/")
    masked = mask_comments_and_strings(text)
    definition = re.compile(r"\b([A-Za-z_]\w*)::(~?[A-Za-z_]\w*)\s*\(([^;{}()]*(?:\([^()]*\)[^;{}()]*)*)\)\s*(?:const\s*)?\{")
    for match in definition.finditer(masked):
        class_name, method, params = match.groups()
        chain = project.engine_chain(class_name)
        if not chain:
            continue
        offset = match.start(3)
        for param in split_parameters(params):
            name_match = re.search(r"([A-Za-z_]\w*)\s*(?:=.*)?$", param)
            if not name_match or not re.search(r"[\w>*&]\s*[*&]?\s*" + re.escape(name_match.group(1)) + r"\s*(?:=.*)?$", param):
                continue
            name = name_match.group(1)
            lookup = engine.lookup_member(chain[-1], name, hints)
            if lookup.status != "found" or not any(m.kind == "field" for m in lookup.members):
                continue
            position = masked.find(param, offset)
            findings.append(Finding(
                "error", rel, line_number(text, position if position >= 0 else match.start()),
                "PARAMETER_SHADOWS_ENGINE_MEMBER",
                f"Parameter '{name}' of {class_name}::{method} hides {lookup.declared_in}::{name} (C4458 is an error in "
                f"Unreal builds). Rename the parameter, e.g. In{name}.",
                metadata=engine.metadata("PARAMETER_SHADOWS_ENGINE_MEMBER", "medium", [_engine_location(engine, lookup)])))
    return findings


REPLICATED_USING_RE = re.compile(r"\bUPROPERTY\s*\((?:[^()]|\([^()]*\))*?\bReplicatedUsing\s*=\s*([A-Za-z_]\w*)")


def validate_reflected_member_conflicts(path: Path, text: str, root: Path, project, engine) -> list[Finding]:
    """UHT errors that depend on what the engine base class reflects.

    - a UPROPERTY whose name an engine base already declares as a UPROPERTY (shadowing is not allowed);
    - a UFUNCTION() declaration of a function an engine base already declares as a UFUNCTION;
    - ReplicatedUsing naming a function that is neither a UFUNCTION here nor inherited as one.

    ``project`` supplies ``engine_chain`` / ``project_members``; ``engine`` is an ``EngineHeaders``
    or None, in which case nothing is reported.
    """
    if engine is None or path.suffix.lower() not in CPP_HEADER_SUFFIXES:
        return []
    findings: list[Finding] = []
    rel = str(path.relative_to(root)).replace("\\", "/")
    masked = mask_comments_and_strings(text)
    for match in re.finditer(r"\bUCLASS\s*\((?:[^()]|\([^()]*\))*\)\s*class\s+(?:[A-Z0-9_]+_API\s+)?([A-Za-z_]\w*)", masked):
        class_name = match.group(1)
        chain = project.engine_chain(class_name)
        parsed = parse_class_members(text, class_name) if chain else None
        if not parsed:
            continue
        engine_base = chain[-1]
        members = parsed[1]
        for name, declared in members.items():
            for member in declared:
                if member.reflected not in {"UPROPERTY", "UFUNCTION"}:
                    continue
                lookup = engine.lookup_member(engine_base, name)
                if lookup.status != "found":
                    continue
                inherited = [m for m in lookup.members if m.reflected == member.reflected]
                if not inherited:
                    continue
                evidence = [{"engineUri": engine.uri(lookup.header), "line": inherited[0].line, "declaredIn": lookup.declared_in}]
                if member.kind == "field" and member.reflected == "UPROPERTY":
                    findings.append(Finding(
                        "error", rel, member.line, "UPROPERTY_SHADOWS_BASE_MEMBER",
                        f"UPROPERTY '{name}' in {class_name} shadows {lookup.declared_in}::{name}; UnrealHeaderTool does not "
                        f"allow shadowing. Use the inherited {name} (set it in the constructor) or rename this property.",
                        metadata=engine.metadata("UPROPERTY_SHADOWS_BASE_MEMBER", "high", evidence)))
                elif member.kind == "method" and member.reflected == "UFUNCTION":
                    findings.append(Finding(
                        "error", rel, member.line, "UFUNCTION_OVERRIDE_REDECLARED",
                        f"{name} is already a UFUNCTION in {lookup.declared_in}; an override must not repeat UFUNCTION(). "
                        f"Declare it as 'virtual ... {name}(...) override;' without the macro.",
                        metadata=engine.metadata("UFUNCTION_OVERRIDE_REDECLARED", "high", evidence)))
        inherited_project = project.project_members(chain[1:])
        for usage in REPLICATED_USING_RE.finditer(masked, match.end()):
            if _enclosing_class(masked, usage.start()) != class_name:
                continue
            function = usage.group(1)
            local = [m for m in members.get(function, []) + inherited_project.get(function, []) if m.kind == "method"]
            if any(m.reflected == "UFUNCTION" for m in local):
                continue
            lookup = engine.lookup_member(engine_base, function)
            if lookup.status == "unknown" or any(m.reflected == "UFUNCTION" for m in lookup.members):
                continue
            state = "is declared without UFUNCTION()" if local else "is not declared"
            findings.append(Finding(
                "error", rel, line_number(text, usage.start(1)), "REPLICATED_USING_FUNCTION_MISSING",
                f"ReplicatedUsing={function} in {class_name}: {function} {state}, and no base on "
                f"{' -> '.join(chain)} declares it as a UFUNCTION. UnrealHeaderTool reports 'Replication notification "
                f"function not found'; declare 'UFUNCTION() void {function}();'.",
                metadata=engine.metadata("REPLICATED_USING_FUNCTION_MISSING", "high" if lookup.status == "absent" else "medium",
                                         [{"inheritancePath": chain + lookup.chain[1:8]}])))
    return findings


def _enclosing_class(masked: str, offset: int) -> str | None:
    """Name of the innermost class/struct body containing ``offset``."""
    best = None
    for match in re.finditer(r"\b(?:class|struct)\s+(?:[A-Z0-9_]+_API\s+)?([A-Za-z_]\w*)[^;{()]*\{", masked[:offset]):
        depth, index = 1, match.end()
        while index < offset and depth:
            depth += (masked[index] == "{") - (masked[index] == "}")
            index += 1
        if depth:
            best = match.group(1)
    return best


def validate_engine_include_paths(path: Path, text: str, root: Path, include_index: dict[str, list[str]],
                                  engine) -> list[Finding]:
    """Quoted includes that neither the project nor the installed engine's public headers provide.

    ``wrong_path``: the file exists in the engine under another include path (an error, with the
    include path that works). ``not_found``: no public engine header has that file name (a warning:
    third-party and private headers are not catalogued). Without an engine nothing is reported.
    """
    if engine is None:
        return []
    findings: list[Finding] = []
    rel = str(path.relative_to(root)).replace("\\", "/")
    for match in QUOTED_INCLUDE_RE.finditer(text):
        include = match.group(1).replace("\\", "/")
        name = Path(include).name
        if Path(include).suffix.lower() not in CPP_HEADER_SUFFIXES or include.endswith(".generated.h"):
            continue
        if _resolve_project_include(include, path, include_index) or include_index.get(name):
            continue
        if "thirdparty" in include.casefold():
            continue
        resolution = engine.resolve_include(include)
        status = resolution.get("status")
        if status not in {"wrong_path", "not_found"}:
            continue
        suggestions = resolution.get("suggestions") or []
        if status == "wrong_path":
            message = (f'"{include}" is not an include path in the installed engine; the header exists as '
                       f'"{suggestions[0]}". Use #include "{suggestions[0]}".')
            evidence = [{"engineUri": engine.uri(item)} for item in resolution.get("matches", [])[:3]]
        else:
            hint = f" Closest engine headers: {', '.join(suggestions)}." if suggestions else ""
            message = (f'No public engine or project header named "{name}" exists, so "{include}" cannot be opened (C1083).'
                       f"{hint} Verify the header with an engine lookup before including it.")
            evidence = [{"catalog": "public Runtime/Editor/Plugins headers", "complete": engine.index_complete}]
        findings.append(Finding(
            "error" if status == "wrong_path" else "warning", rel, line_number(text, match.start(1)),
            "ENGINE_INCLUDE_NOT_FOUND", message,
            metadata=engine.metadata("ENGINE_INCLUDE_NOT_FOUND", "high" if status == "wrong_path" else "medium", evidence,
                                     ["third-party, private and Developer-only headers are not proof of absence"])))
    return findings


def validate_engine_aware(
    root: Path,
    identity: dict | None,
    *,
    scope_paths: list[Path] | None = None,
    skip_include_path_checks: bool = False,
) -> list[Finding]:
    """Rules that read the installed engine's headers (bounded), plus project-wide reflected names.

    Without a readable engine (unknown identity, missing root) the engine-backed rules report
    nothing; the lexical ones here still run.
    """
    engine = EngineHeaders.from_identity(identity)
    all_paths = [path for path in iter_source_files(root) if path.suffix.lower() in SOURCE_ONLY_SUFFIXES]
    texts = {path: read_text(path) for path in all_paths}
    if scope_paths is None:
        targets = all_paths
    else:
        targets = [path for path in scope_paths if path.is_file() and path.suffix.lower() in SOURCE_ONLY_SUFFIXES]
    findings = validate_duplicate_reflected_type_names(root, texts, None if scope_paths is None else targets)
    project = ProjectTypes(texts)
    include_index = build_source_include_index(root)
    for path in targets:
        text = texts.get(path)
        if text is None:
            text = read_text(path)
        hints = engine.include_paths(sorted(visible_includes(path, text, include_index))) if engine else []
        validators = [
            ("validate_known_invalid_engine_symbols",
             lambda: validate_known_invalid_engine_symbols(path, text, root, engine, hints)),
            ("validate_reflected_member_conflicts",
             lambda: validate_reflected_member_conflicts(path, text, root, project, engine)),
            ("validate_engine_override_signatures",
             lambda: validate_engine_override_signatures(path, text, root, project, engine)),
            ("validate_engine_member_access",
             lambda: validate_engine_member_access(path, text, root, project, engine, hints)),
            ("validate_parameter_shadowing",
             lambda: validate_parameter_shadowing(path, text, root, project, engine, hints)),
        ]
        if not skip_include_path_checks:
            validators.append(("validate_engine_include_paths",
                               lambda: validate_engine_include_paths(path, text, root, include_index, engine)))
        for name, run in validators:
            try:
                findings.extend(run())
            except Exception as exc:  # one rule must not hide the others
                _append_validator_internal_error(findings, path, root, name, exc)
    return findings


__all__ = [
    "ProjectTypes",
    "REPLICATED_USING_RE",
    "validate_engine_aware",
    "validate_engine_include_paths",
    "validate_engine_member_access",
    "validate_engine_override_signatures",
    "validate_parameter_shadowing",
    "validate_reflected_member_conflicts",
]
