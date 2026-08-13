"""Pure active-slice contract checks for code-sketch orchestration."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

SOURCE_SUFFIXES = frozenset({".h", ".hpp", ".hh", ".inl", ".cpp", ".cc", ".cxx"})


_QUALIFIED_DEFINITION_RE = re.compile(
    r"(?ms)\b(?P<owner>[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*)::"
    r"(?P<member>~?[A-Za-z_]\w*|operator\s*[^\s(]+)\s*"
    r"\([^;{}]*\)\s*"
    r"(?:const\b\s*)?(?:noexcept(?:\s*\([^)]*\))?\s*)?"
    r"(?:override\b\s*)?(?:final\b\s*)?(?:->\s*[^\n{]+\s*)?"
    r"(?=\{|:\s*[A-Za-z_])"
)


def _surface_path(value: Any) -> str:
    return str(value or "").replace("\\", "/").strip("/").casefold()


def _path_in_surface(path: Any, surface_paths: set[str]) -> bool:
    candidate = _surface_path(path)
    return bool(
        candidate
        and any(
            candidate == allowed
            or candidate.endswith("/" + allowed)
            or allowed.endswith("/" + candidate)
            for allowed in surface_paths
            if allowed
        )
    )


def _declared_owners(
    text: str,
    *,
    include_qualified_definitions: bool = True,
) -> set[str]:
    owners = {
        match.group(1).casefold()
        for match in re.finditer(
            r"\b(?:class|struct|union)\s+"
            r"(?:(?:[A-Z][A-Z0-9_]*_API|alignas\s*\([^)]*\))\s+)?"
            r"([A-Za-z_]\w*)",
            text,
        )
    }
    owners.update(
        match.group(1).casefold()
        for match in re.finditer(r"\bnamespace\s+([A-Za-z_]\w*)", text)
    )
    if include_qualified_definitions:
        owners.update(
            match.group("owner").rsplit("::", 1)[-1].casefold()
            for match in _QUALIFIED_DEFINITION_RE.finditer(text)
        )
    return owners


def _definition_claims(text: str) -> list[dict[str, str]]:
    return [
        {
            "owner": match.group("owner"),
            "ownerLeaf": match.group("owner").rsplit("::", 1)[-1],
            "member": re.sub(r"\s+", " ", match.group("member")).strip(),
        }
        for match in _QUALIFIED_DEFINITION_RE.finditer(text)
    ]


def _strip_cpp_comments(text: str) -> str:
    """Remove comments while preserving strings and character literals.

    Material-delta checks must not treat a rewritten explanation as code.  A
    small scanner is preferable to a regular expression here because URLs,
    escaped quotes, and comment markers inside literals are common in Unreal
    source.
    """

    output: list[str] = []
    index = 0
    state = "code"
    while index < len(text):
        current = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if state == "line_comment":
            if current == "\n":
                output.append(current)
                state = "code"
            index += 1
            continue
        if state == "block_comment":
            if current == "*" and following == "/":
                index += 2
                state = "code"
            else:
                if current == "\n":
                    output.append(current)
                index += 1
            continue
        if state in {"string", "character"}:
            output.append(current)
            if current == "\\" and following:
                output.append(following)
                index += 2
                continue
            terminator = '"' if state == "string" else "'"
            if current == terminator:
                state = "code"
            index += 1
            continue
        if current == "/" and following == "/":
            state = "line_comment"
            index += 2
            continue
        if current == "/" and following == "*":
            state = "block_comment"
            index += 2
            continue
        output.append(current)
        if current == '"':
            state = "string"
        elif current == "'":
            state = "character"
        index += 1
    return "".join(output)


def _normalized_code(text: str) -> str:
    return re.sub(r"\s+", "", _strip_cpp_comments(text))


def _balanced_body(text: str, start: int) -> tuple[str, int] | None:
    """Return a definition body starting at or after *start*.

    The definition matcher stops before the opening brace.  This scanner skips
    braces in comments/literals and therefore works for ordinary UE method
    bodies without pretending to be a full C++ parser.
    """

    open_index = text.find("{", start)
    if open_index < 0:
        return None
    depth = 0
    index = open_index
    state = "code"
    while index < len(text):
        current = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if state == "line_comment":
            if current == "\n":
                state = "code"
            index += 1
            continue
        if state == "block_comment":
            if current == "*" and following == "/":
                index += 2
                state = "code"
            else:
                index += 1
            continue
        if state in {"string", "character"}:
            if current == "\\" and following:
                index += 2
                continue
            terminator = '"' if state == "string" else "'"
            if current == terminator:
                state = "code"
            index += 1
            continue
        if current == "/" and following == "/":
            state = "line_comment"
            index += 2
            continue
        if current == "/" and following == "*":
            state = "block_comment"
            index += 2
            continue
        if current == '"':
            state = "string"
        elif current == "'":
            state = "character"
        elif current == "{":
            depth += 1
        elif current == "}":
            depth -= 1
            if depth == 0:
                return text[open_index + 1 : index], index + 1
        index += 1
    return None


def _definition_bodies(text: str) -> list[dict[str, str]]:
    definitions: list[dict[str, str]] = []
    for match in _QUALIFIED_DEFINITION_RE.finditer(text):
        balanced = _balanced_body(text, match.end())
        if balanced is None:
            continue
        body, _ = balanced
        definitions.append(
            {
                "owner": match.group("owner"),
                "member": re.sub(r"\s+", " ", match.group("member")).strip(),
                "body": body,
                "normalizedBody": _normalized_code(body),
            }
        )
    return definitions


def _material_delta_contract(sketch: str, existing_target_text: str) -> dict[str, Any]:
    """Prove that an existing-file sketch contains at least one code delta.

    This is intentionally a narrow proof.  It catches exact restatements and a
    common semantic no-op where a one-line delegation is replaced by the
    already-existing callee body.  It does not claim that every remaining delta
    is behaviorally correct; API checks, compilation, and tests remain required.
    """

    existing_definitions = _definition_bodies(existing_target_text)
    proposed_definitions = _definition_bodies(sketch)
    by_key: dict[tuple[str, str], list[dict[str, str]]] = {}
    for definition in existing_definitions:
        key = (definition["owner"].casefold(), definition["member"].casefold())
        by_key.setdefault(key, []).append(definition)

    definition_deltas: list[dict[str, str]] = []
    material_definition_count = 0
    for proposed in proposed_definitions:
        key = (proposed["owner"].casefold(), proposed["member"].casefold())
        current = by_key.get(key, [])
        status = "new_definition" if not current else "material_change"
        if any(
            item["normalizedBody"] == proposed["normalizedBody"] for item in current
        ):
            status = "existing_restatement"
        elif len(current) == 1:
            delegated = re.fullmatch(
                r"(?:return)?(?P<callee>[A-Za-z_]\w*)\([^;{}]*\);",
                current[0]["normalizedBody"],
            )
            if delegated:
                callee_key = (key[0], delegated.group("callee").casefold())
                if any(
                    item["normalizedBody"] == proposed["normalizedBody"]
                    for item in by_key.get(callee_key, [])
                ):
                    status = "equivalent_delegate_inline"
        if status in {"new_definition", "material_change"}:
            material_definition_count += 1
        definition_deltas.append(
            {
                "owner": proposed["owner"],
                "member": proposed["member"],
                "status": status,
            }
        )

    existing_lines = {
        re.sub(r"\s+", "", line)
        for line in _strip_cpp_comments(existing_target_text).splitlines()
        if re.sub(r"\s+", "", line) not in {"", "{", "}"}
    }
    novel_lines = [
        line.strip()[:220]
        for line in _strip_cpp_comments(sketch).splitlines()
        if re.sub(r"\s+", "", line) not in {"", "{", "}"}
        and re.sub(r"\s+", "", line) not in existing_lines
    ]
    explicit_diff = bool(
        re.search(r"(?m)^(?:\+|-)(?!\+\+|--|\s*$).+", sketch)
    )
    material = bool(
        explicit_diff
        or material_definition_count
        or (not proposed_definitions and novel_lines)
    )
    return {
        "required": True,
        "ok": material,
        "status": "material_delta" if material else "no_material_delta",
        "definitionDeltas": definition_deltas[:16],
        "novelCodeLineCount": len(novel_lines),
        "novelCodeLines": novel_lines[:8],
        "explicitDiff": explicit_diff,
        "proofBoundary": (
            "A source delta was found; behavioral correctness still requires build/test proof."
            if material
            else "The sketch only restates existing source or inlines an existing delegate."
        ),
    }


def _owner_surface_binding(
    sketch: str,
    *,
    target_files: list[str],
    contract: dict[str, Any],
    graph: dict[str, Any] | None,
    existing_target_text: str,
) -> dict[str, Any]:
    """Bind qualified method definitions to the declaration/definition slice.

    API validation alone can prove that every token in a sketch exists while the
    sketch still implements a different class than the server-owned target
    files.  This check treats target files and their paired declaration reads as
    one source surface and rejects definitions owned exclusively elsewhere.
    """

    claims = _definition_claims(sketch)
    target_paths = {_surface_path(item) for item in target_files if str(item).strip()}
    declaration_paths = set(target_paths)
    declaration_text = existing_target_text
    for evidence in contract.get("requiredReads") or []:
        if not isinstance(evidence, dict):
            continue
        raw_path = str(evidence.get("filePath") or "").strip()
        if not raw_path:
            continue
        declaration_paths.add(_surface_path(raw_path))
        try:
            declaration_text += "\n" + Path(raw_path).read_text(
                encoding="utf-8-sig",
                errors="replace",
            )
        except OSError:
            continue

    allowed_owners = _declared_owners(declaration_text)
    sketch_declared_owners = _declared_owners(
        sketch,
        include_qualified_definitions=False,
    )
    target_stems = {Path(item).stem.casefold() for item in target_files}
    graph_locations: dict[str, set[str]] = {}
    for row in (graph or {}).get("symbols") or []:
        if not isinstance(row, dict):
            continue
        file_path = str(row.get("file_path") or "").strip()
        qualified = str(row.get("qualified_name") or "").strip()
        symbol_name = str(row.get("symbol_name") or "").strip()
        row_owners: set[str] = set()
        if "::" in qualified:
            row_owners.add(qualified.rsplit("::", 1)[0].rsplit("::", 1)[-1])
        if str(row.get("symbol_kind") or "") in {
            "class",
            "struct",
            "interface",
            "namespace",
        } and symbol_name:
            row_owners.add(symbol_name.rsplit("::", 1)[-1])
        for owner in row_owners:
            key = owner.casefold()
            if file_path:
                graph_locations.setdefault(key, set()).add(file_path)
            if _path_in_surface(file_path, declaration_paths):
                allowed_owners.add(key)

    outside: list[dict[str, Any]] = []
    for claim in claims:
        owner_leaf = claim["ownerLeaf"]
        owner_key = owner_leaf.casefold()
        canonical = owner_leaf[1:] if owner_leaf[:1] in {"A", "F", "I", "S", "T", "U", "W"} else owner_leaf
        canonical_matches_target = canonical.casefold() in target_stems
        locations = sorted(graph_locations.get(owner_key, set()))
        owned_in_surface = any(_path_in_surface(path, declaration_paths) for path in locations)
        declared_only_in_sketch = owner_key in sketch_declared_owners and not locations
        if (
            owner_key in allowed_owners
            or canonical_matches_target
            or owned_in_surface
            or declared_only_in_sketch
        ):
            continue
        outside.append(
            {
                **claim,
                "knownOwnerFiles": locations[:8],
                "reason": (
                    "owner is declared only outside the active target/declaration surface"
                    if locations
                    else "owner is not established by the active target/declaration surface"
                ),
            }
        )

    return {
        "definitionClaims": claims,
        "allowedOwners": sorted(allowed_owners),
        "targetFiles": list(target_files),
        "declarationFileCount": len(declaration_paths),
        "outsideDefinitionOwners": outside,
        "ok": not outside,
    }


def _close_gate(contract: dict[str, Any], issue: str, reason: str) -> None:
    contract.setdefault("issues", []).insert(0, issue)
    contract["ok"] = False
    contract.setdefault("writeGate", {})["writesAllowed"] = False
    contract["writeGate"]["reason"] = reason


def validate_active_slice_surface(
    sketch: str,
    *,
    target_files: list[str],
    generation_contract: dict[str, Any],
    graph: dict[str, Any] | None,
    require_material_delta: bool = False,
) -> dict[str, Any]:
    """Reject code surfaces that exceed or evade the server-bound source slice."""

    contract = generation_contract
    labeled_files = re.findall(
        r"(?mi)^\s*(?://|/\*)\s*(?:file\s*:\s*)?"
        r"([A-Za-z0-9_.\\/\-]+\.(?:h|hpp|hh|inl|cpp|cc|cxx|cs))"
        r"(?=\s*(?:[-:]|(?:\*/)?\s*$))",
        sketch,
    )
    allowed_paths = {str(Path(item).as_posix()).casefold() for item in target_files}
    allowed_names = {Path(item).name.casefold() for item in target_files}
    outside_labels = [
        item
        for item in dict.fromkeys(labeled_files)
        if str(Path(item).as_posix()).casefold() not in allowed_paths
        and Path(item).name.casefold() not in allowed_names
    ]
    if outside_labels:
        _close_gate(
            contract,
            "sketch contains labeled source sections outside targetFiles: "
            + ", ".join(outside_labels[:6]),
            "sketch exceeds active targetFiles slice",
        )

    reflected_classes = re.findall(
        r"(?ms)\bUCLASS\s*(?:\([^)]*\))?\s*class\s+"
        r"(?:[A-Z][A-Z0-9_]*_API\s+)?([A-Za-z_]\w*)",
        sketch,
    )
    target_stems = {Path(item).stem.casefold() for item in target_files}
    existing_target_text = ""
    for target in contract.get("targets") or []:
        if not isinstance(target, dict) or not target.get("exists"):
            continue
        try:
            existing_target_text += "\n" + Path(
                str(target.get("absolutePath") or "")
            ).read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue

    owner_binding = _owner_surface_binding(
        sketch,
        target_files=target_files,
        contract=contract,
        graph=graph,
        existing_target_text=existing_target_text,
    )
    contract["surfaceBinding"] = owner_binding
    outside_definitions = owner_binding["outsideDefinitionOwners"]
    if outside_definitions:
        first = outside_definitions[0]
        known_files = ", ".join(first.get("knownOwnerFiles") or [])
        suffix = f" (owned by {known_files})" if known_files else ""
        _close_gate(
            contract,
            "sketch defines "
            f"{first['owner']}::{first['member']} outside targetFiles; "
            "qualified definitions must belong to the active target/declaration surface"
            + suffix,
            "sketch definition owner is outside active targetFiles",
        )
    outside_reflected_classes: list[str] = []
    invalid_reflected_prefixes: list[str] = []
    for class_name in dict.fromkeys(reflected_classes):
        if not class_name.startswith(("A", "U")):
            invalid_reflected_prefixes.append(class_name)
        canonical = class_name[1:] if class_name[:1] in {"A", "U", "W"} else class_name
        matches_target = canonical.casefold() in target_stems
        already_owned_by_target = bool(
            re.search(
                rf"\bclass\s+(?:[A-Z][A-Z0-9_]*_API\s+)?{re.escape(class_name)}\b",
                existing_target_text,
            )
        )
        if not matches_target and not already_owned_by_target:
            outside_reflected_classes.append(class_name)
    if outside_reflected_classes:
        _close_gate(
            contract,
            "sketch declares reflected UCLASS types outside targetFiles: "
            + ", ".join(outside_reflected_classes[:6]),
            "reflected class exceeds active targetFiles slice",
        )
    if invalid_reflected_prefixes:
        _close_gate(
            contract,
            "UCLASS names must use an Unreal A/U prefix: "
            + ", ".join(invalid_reflected_prefixes[:6]),
            "reflected class exceeds active targetFiles slice",
        )

    reflected_headers: dict[str, str] = {}
    for row in (graph or {}).get("symbols") or []:
        if not isinstance(row, dict) or row.get("is_reflected") is not True:
            continue
        if row.get("symbol_kind") not in {"class", "interface"}:
            continue
        symbol_name = str(row.get("symbol_name") or "").strip()
        file_name = Path(str(row.get("file_path") or "")).name
        if symbol_name and file_name:
            reflected_headers[symbol_name.casefold()] = file_name
            if len(symbol_name) > 1 and symbol_name[:1] in {"A", "U", "I", "F"}:
                reflected_headers[symbol_name[1:].casefold()] = file_name
    wrong_reflected_includes: list[str] = []
    for include_path in re.findall(r'(?mi)^\s*#\s*include\s*"([^"]+)"', sketch):
        include_name = Path(include_path).name
        reflected_type = Path(include_name).stem
        actual_header = reflected_headers.get(reflected_type.casefold(), "")
        if actual_header and include_name.casefold() != actual_header.casefold():
            wrong_reflected_includes.append(
                f"{include_path} names {reflected_type}, but project source declares it in {actual_header}"
            )
    if wrong_reflected_includes:
        _close_gate(
            contract,
            "sketch uses a guessed reflected-type header: " + wrong_reflected_includes[0],
            "reflected include path is not source-backed",
        )

    modifies_existing = any(
        isinstance(target, dict) and bool(target.get("exists"))
        for target in contract.get("targets") or []
    )
    concrete_source = bool(
        re.search(
            r"(?m)(?:;|[{}]|^\s*#\s*(?:include|define|if|pragma)\b|"
            r"\b(?:UCLASS|USTRUCT|UENUM|UPROPERTY|UFUNCTION|GENERATED_BODY)\s*\()",
            sketch,
        )
    )
    if modifies_existing and not concrete_source:
        _close_gate(
            contract,
            "existing-file validation requires a concrete code snippet; "
            "a prose API/implementation summary cannot open the write gate",
            "sketch is prose, not proposed code",
        )
    behavior_placeholders = re.findall(
        r"(?mi)^\s*//[^\n]*\b(?:TODO|FIXME|implement|place|handle|execute|apply)\b"
        r"[^\n]*\bhere\b[^\n]*$",
        sketch,
    )
    if behavior_placeholders:
        _close_gate(
            contract,
            "sketch leaves requested behavior as an implementation placeholder: "
            + behavior_placeholders[0].strip()[:220],
            "requested behavior is still a placeholder",
        )
    if modifies_existing and require_material_delta:
        material_delta = _material_delta_contract(sketch, existing_target_text)
        contract["materialDelta"] = material_delta
        if not material_delta["ok"]:
            _close_gate(
                contract,
                "sketch has no material source delta; it only restates existing definitions "
                "or inlines an already-existing delegate body",
                "sketch does not change existing behavior",
            )
    return contract


def load_declaration_context(
    generation_contract: dict[str, Any],
    *,
    max_files: int = 4,
    max_chars: int = 96_000,
) -> tuple[str, list[str]]:
    chunks: list[str] = []
    files: list[str] = []
    char_count = 0
    for evidence in generation_contract.get("requiredReads") or []:
        if not isinstance(evidence, dict):
            continue
        raw_path = str(evidence.get("filePath") or "").strip()
        if not raw_path or raw_path in files:
            continue
        path = Path(raw_path)
        if path.suffix.casefold() not in SOURCE_SUFFIXES:
            continue
        try:
            source_text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        remaining = max_chars - char_count
        if remaining <= 0 or len(files) >= max_files:
            break
        source_text = source_text[:remaining]
        chunks.append(source_text)
        char_count += len(source_text)
        files.append(raw_path)
    return "\n".join(chunks), files


__all__ = ["load_declaration_context", "validate_active_slice_surface"]
