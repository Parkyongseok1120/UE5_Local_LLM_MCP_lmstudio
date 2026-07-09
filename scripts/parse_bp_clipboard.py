#!/usr/bin/env python
"""Parse Unreal Blueprint T3D clipboard text for basic node/pin extraction."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

BEGIN_OBJECT_RE = re.compile(r"^\s*Begin Object Class=(?P<class>[^\s]+)\s+Name=\"(?P<name>[^\"]+)\"", re.M)
LINKED_TO_RE = re.compile(r'LinkedTo=\((?P<links>[^)]*)\)')


def _parse_pin_line(line: str) -> dict[str, Any]:
    pin: dict[str, Any] = {"links": []}
    name_match = re.search(r'PinName="(?P<name>[^"]+)"', line)
    if name_match:
        pin["name"] = name_match.group("name")
    type_match = re.search(r'PinType\.PinCategory="(?P<cat>[^"]+)"', line)
    if type_match:
        pin["type"] = type_match.group("cat")
    link_match = LINKED_TO_RE.search(line)
    if link_match:
        raw = link_match.group("links")
        for part in raw.split(","):
            part = part.strip().strip('"')
            if not part:
                continue
            if " " in part:
                node_name, pin_name = part.split(" ", 1)
                pin["links"].append({"node": node_name, "pin": pin_name.strip()})
            else:
                pin["links"].append({"node": part, "pin": ""})
    return pin


def parse_bp_clipboard(text: str) -> dict[str, Any]:
    """Extract nodes, classes, and pins from Blueprint clipboard T3D text."""
    nodes: list[dict[str, Any]] = []
    for match in BEGIN_OBJECT_RE.finditer(text):
        node_class = match.group("class")
        node_name = match.group("name")
        start = match.end()
        end_match = re.search(r"^\s*End Object", text[start:], re.M)
        block = text[start : start + end_match.start()] if end_match else text[start : start + 4000]
        pins = [_parse_pin_line(line) for line in block.splitlines() if "CustomProperties Pin" in line]
        nodes.append(
            {
                "name": node_name,
                "class": node_class,
                "pins": [pin for pin in pins if pin.get("name")],
            }
        )

    pin_links: list[dict[str, Any]] = []
    for node in nodes:
        for pin in node.get("pins") or []:
            for link in pin.get("links") or []:
                pin_links.append(
                    {
                        "from_node": node["name"],
                        "from_pin": pin.get("name") or "",
                        "to_node": link.get("node") or "",
                        "to_pin": link.get("pin") or "",
                    }
                )

    return {
        "nodeCount": len(nodes),
        "linkCount": len(pin_links),
        "nodes": nodes,
        "pinLinks": pin_links,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Parse Blueprint T3D clipboard text.")
    parser.add_argument("--input", default="-", help="Input file path or '-' for stdin.")
    args = parser.parse_args()
    if args.input == "-":
        import sys

        text = sys.stdin.read()
    else:
        text = Path(args.input).read_text(encoding="utf-8", errors="replace")
    payload = parse_bp_clipboard(text)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
