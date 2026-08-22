#!/usr/bin/env python
"""Registry-backed dispatch for selected unreal-rag MCP tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

HandlerFn = Callable[[Any, Any, dict[str, Any]], None]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    schema_dict: dict[str, Any]
    handler: HandlerFn | str


class McpToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Duplicate tool registration: {spec.name}")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def names(self) -> frozenset[str]:
        return frozenset(self._tools)

    def dispatch(self, server: Any, message_id: Any, name: str, arguments: dict[str, Any]) -> bool:
        spec = self._tools.get(name)
        if spec is None:
            return False
        handler = spec.handler
        if isinstance(handler, str):
            bound = getattr(server, handler)
            bound(message_id, arguments)
        else:
            handler(server, message_id, arguments)
        return True
