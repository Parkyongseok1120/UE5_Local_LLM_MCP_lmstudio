"""Pure active-slice contract checks for code-sketch orchestration."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

SOURCE_SUFFIXES = frozenset({".h", ".hpp", ".hh", ".inl", ".cpp", ".cc", ".cxx"})


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
