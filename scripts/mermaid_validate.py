#!/usr/bin/env python
"""Safe Mermaid subset validation and ASCII fallback generation."""

from __future__ import annotations

import re
from typing import Any

ALLOWED_DIAGRAM_TYPES = frozenset({"flowchart", "graph", "sequenceDiagram", "classDiagram"})
PROHIBITED_DIRECTIVES = re.compile(
    r"\b(click|style|classDef|linkStyle|subgraph\s+id|%%\{init)",
    re.IGNORECASE,
)
RESERVED_IDS = frozenset({"end", "subgraph", "graph", "flowchart"})


def _extract_mermaid_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    pattern = re.compile(r"```\s*mermaid\s*\n(.*?)\n```", re.IGNORECASE | re.DOTALL)
    for match in pattern.finditer(text or ""):
        blocks.append(match.group(1))
    return blocks


def validate_mermaid_block(source: str) -> dict[str, Any]:
    diagram = (source or "").strip()
    issues: list[str] = []
    if not diagram:
        return {
            "valid": False,
            "safeToRender": False,
            "diagramType": "",
            "issues": ["empty diagram"],
            "normalizedMermaid": "",
            "asciiFallback": "Empty -> NoOutput",
            "requiredAction": "use_ascii_only",
        }

    first_line = diagram.splitlines()[0].strip()
    diagram_type = first_line.split()[0] if first_line else ""
    if diagram_type not in ALLOWED_DIAGRAM_TYPES:
        issues.append(f"unsupported diagram type: {diagram_type or 'missing'}")

    if PROHIBITED_DIRECTIVES.search(diagram):
        issues.append("prohibited directive detected")

    for opener, closer in (("(", ")"), ("[", "]"), ("{", "}")):
        if diagram.count(opener) != diagram.count(closer):
            issues.append(f"unbalanced {opener}{closer}")

    node_count = len(re.findall(r"-->|---|->>|-->>", diagram))
    if node_count > 24:
        issues.append("edge count exceeds limit")

    valid = not issues
    ascii_fallback = _ascii_fallback_from_diagram(diagram)
    return {
        "valid": valid,
        "safeToRender": valid,
        "diagramType": diagram_type,
        "issues": issues,
        "normalizedMermaid": diagram if valid else "",
        "asciiFallback": ascii_fallback,
        "requiredAction": "render_mermaid" if valid else "use_ascii_only",
    }


def _ascii_fallback_from_diagram(diagram: str) -> str:
    edges = re.findall(r"([A-Za-z0-9_]+)\s*[-=.]+>\s*([A-Za-z0-9_]+)", diagram)
    if not edges:
        return "Diagram -> (see text)"
    return " -> ".join([edges[0][0], * [right for _, right in edges[:6]]])


def sanitize_report_markdown(text: str, *, mode: str = "sanitize") -> dict[str, Any]:
    blocks = _extract_mermaid_blocks(text)
    degraded = False
    output = text or ""
    validations: list[dict[str, Any]] = []
    for block in blocks:
        result = validate_mermaid_block(block)
        validations.append(result)
        fence = f"```mermaid\n{block}\n```"
        if result["safeToRender"] or mode == "passthrough":
            continue
        if mode == "strict":
            degraded = True
            output = output.replace(fence, f"```\n{result['asciiFallback']}\n```")
        else:
            degraded = True
            output = output.replace(fence, result["asciiFallback"])
    if mode == "strict" and degraded:
        return {"ok": False, "text": output, "degraded": True, "validations": validations}
    return {"ok": True, "text": output, "degraded": degraded, "validations": validations}
