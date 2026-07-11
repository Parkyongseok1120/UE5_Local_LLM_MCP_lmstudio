#!/usr/bin/env python
"""Resolve project symbol → include path for component registration and complete-type usage."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from symbol_graph import load_symbol_graph, lookup_symbol

UsageKind = Literal[
    "create_default_subobject",
    "new_object",
    "member_call",
    "declaration",
    "sizeof",
    "static_class",
    "unknown",
]

COMPLETE_TYPE_USAGES = frozenset(
    {
        "create_default_subobject",
        "new_object",
        "member_call",
        "sizeof",
        "static_class",
        "unknown",
    }
)


@dataclass
class IncludeResolution:
    symbol: str
    declaring_file: str
    preferred_include: str
    target_file: str
    requires_complete_type: bool
    forward_declaration_sufficient: bool
    owner_module: str
    consumer_module: str
    build_cs_required: bool
    confidence: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _module_name_from_path(path: Path, project_root: Path) -> str:
    try:
        parts = path.resolve().relative_to(project_root.resolve()).parts
    except ValueError:
        return ""
    if len(parts) >= 2 and parts[0].lower() == "source":
        return parts[1]
    if len(parts) >= 4 and parts[0].lower() == "plugins" and parts[2].lower() == "source":
        return parts[3]
    return ""


def project_relative_include(declaring_file: Path, project_root: Path) -> str:
    """Map Source/Module/Public/Foo/Bar.h → Foo/Bar.h style include."""
    try:
        rel = declaring_file.relative_to(project_root)
    except ValueError:
        return declaring_file.name
    parts = list(rel.parts)
    if "Source" in parts:
        idx = parts.index("Source")
        parts = parts[idx + 1 :]
    if parts and parts[0] not in {"Public", "Private", "Classes"}:
        # ModuleName/Public/...
        if len(parts) >= 2 and parts[1] in {"Public", "Private", "Classes"}:
            parts = parts[2:]
        else:
            parts = parts[1:]
    elif parts and parts[0] in {"Public", "Private", "Classes"}:
        parts = parts[1:]
    return "/".join(parts).replace("\\", "/")


def _declaring_scan_roots(root: Path) -> list[Path]:
    try:
        from plugin_project_context import resolve_scan_roots

        return resolve_scan_roots(root)
    except Exception:
        source = root / "Source"
        return [source] if source.is_dir() else [root]


def _scan_declaring_file(scan_roots: list[Path], symbol: str) -> Path | None:
    class_pattern = re.compile(
        rf"\bclass\s+(?:[A-Z0-9_]+_API\s+)?{re.escape(symbol)}\b[^;{{]*\{{",
        re.MULTILINE,
    )
    uclass_pattern = re.compile(r"\bUCLASS\b")
    for scan_root in scan_roots:
        if not scan_root.is_dir():
            continue
        for path in scan_root.rglob("*.h"):
            if path.suffix.lower() not in {".h", ".hpp"}:
                continue
            if "Intermediate" in path.parts or "ThirdParty" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                continue
            if class_pattern.search(text) and uclass_pattern.search(text):
                return path
    return None


def _resolve_declaring_file(root: Path, symbol: str) -> tuple[Path | None, float]:
    graph = load_symbol_graph(root)
    for row in lookup_symbol(symbol, graph, limit=5):
        file_path = str(row.get("file_path") or "")
        if file_path:
            candidate = Path(file_path)
            if not candidate.is_file():
                candidate = root / file_path
            if candidate.is_file():
                return candidate.resolve(), 0.9
    scanned = _scan_declaring_file(_declaring_scan_roots(root), symbol)
    if scanned:
        return scanned.resolve(), 0.75
    return None, 0.0


def infer_usage_kind(text: str, symbol: str, offset: int) -> UsageKind:
    window = text[max(0, offset - 80) : offset + 80]
    if f"CreateDefaultSubobject<{symbol}" in window or f"CreateDefaultSubobject< {symbol}" in window:
        return "create_default_subobject"
    if f"NewObject<{symbol}" in window or f"NewObject< {symbol}" in window:
        return "new_object"
    if f"sizeof({symbol}" in window or f"sizeof( {symbol}" in window:
        return "sizeof"
    if f"{symbol}::StaticClass" in window:
        return "static_class"
    if re.search(rf"\b{re.escape(symbol)}\s*[\.\->]", window):
        return "member_call"
    return "declaration"


def resolve_project_symbol_include(
    root: Path,
    symbol: str,
    referencing_file: Path,
    usage_kind: UsageKind | str = "unknown",
) -> IncludeResolution | None:
    root = root.resolve()
    symbol = str(symbol or "").strip()
    if not symbol or not symbol.startswith("U"):
        return None

    from unreal_static_validate import CPP_SYMBOL_INCLUDES

    if symbol in CPP_SYMBOL_INCLUDES:
        return None

    referencing_file = referencing_file.resolve() if referencing_file.exists() else referencing_file

    declaring, confidence = _resolve_declaring_file(root, symbol)
    if not declaring:
        return None

    project_root = root
    if (root / "Source").is_dir():
        project_root = root
    elif root.suffix.lower() == ".uproject":
        project_root = root.parent
    project_root = project_root.resolve()

    preferred = project_relative_include(declaring, project_root)
    try:
        ref_rel = str(referencing_file.relative_to(project_root)).replace("\\", "/")
    except ValueError:
        ref_rel = str(referencing_file).replace("\\", "/")
    owner_module = _module_name_from_path(declaring, project_root)
    consumer_module = _module_name_from_path(referencing_file, project_root) if referencing_file.is_file() else ""
    build_cs_required = bool(owner_module and consumer_module and owner_module != consumer_module)

    kind = usage_kind if usage_kind in COMPLETE_TYPE_USAGES else "unknown"
    requires_complete = kind in {
        "create_default_subobject",
        "new_object",
        "member_call",
        "sizeof",
        "static_class",
    }
    forward_ok = not requires_complete and kind == "declaration"

    reason = "Complete type required at use site."
    if kind == "create_default_subobject":
        reason = "CreateDefaultSubobject requires the complete component type."
    elif kind == "new_object":
        reason = "NewObject requires the complete type."

    return IncludeResolution(
        symbol=symbol,
        declaring_file=str(declaring.relative_to(project_root)).replace("\\", "/"),
        preferred_include=preferred,
        target_file=ref_rel,
        requires_complete_type=requires_complete,
        forward_declaration_sufficient=forward_ok,
        owner_module=owner_module,
        consumer_module=consumer_module,
        build_cs_required=build_cs_required,
        confidence=confidence,
        reason=reason,
    )


def format_include_feedback(resolution: IncludeResolution) -> str:
    lines = [
        f"Missing include for project component {resolution.symbol}.",
        f'Add: #include "{resolution.preferred_include}"',
        f"To: {resolution.target_file}",
    ]
    if not resolution.build_cs_required:
        lines.append("Do not modify Build.cs; the symbol belongs to the same module.")
    else:
        lines.append(
            f"Cross-module symbol: verify {resolution.owner_module} is in consumer Build.cs dependencies."
        )
    lines.append(resolution.reason)
    return "\n".join(lines)
