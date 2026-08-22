from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path

import unreal_rag_mcp as rag


INTERNAL_COMPATIBILITY_ALIASES = {
    "unreal_task_start": {
        "conversationId",
        "conversation_id",
        "plan_id",
        "project_file",
    },
    "unreal_task_checkpoint": {"ownerCapability", "taskSessionId"},
    "unreal_architecture_decision_approve": {"approval_token"},
    "unreal_agent_plan": {"latest_user_message", "userMessage"},
}


def _branch_names(test: ast.expr) -> set[str]:
    if not (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == "name"
        and len(test.comparators) == 1
    ):
        return set()
    value = test.comparators[0]
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return {value.value}
    if isinstance(value, (ast.Set, ast.Tuple, ast.List)):
        return {
            item.value
            for item in value.elts
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        }
    return set()


def _consumed_arguments(body: list[ast.stmt]) -> set[str]:
    tree = ast.Module(body=body, type_ignores=[])
    return {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "arguments"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }


def _legacy_dispatch_contract() -> tuple[set[str], dict[str, set[str]]]:
    source = textwrap.dedent(inspect.getsource(rag.McpServer.handle_tool_call))
    tree = ast.parse(source)
    dispatched: set[str] = set()
    consumed: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        names = _branch_names(node.test)
        if not names:
            continue
        branch_arguments = _consumed_arguments(node.body)
        for name in names:
            dispatched.add(name)
            consumed.setdefault(name, set()).update(branch_arguments)
    return dispatched, consumed


def test_every_public_tool_has_a_registry_or_legacy_dispatch_path(tmp_path: Path) -> None:
    server = rag.McpServer(tmp_path / "missing.sqlite")
    definitions = {item["name"] for item in server._all_tool_definitions_unfiltered()}
    legacy, _ = _legacy_dispatch_contract()
    dispatchable = legacy | set(rag._MCP_TOOL_REGISTRY.names())
    assert definitions <= dispatchable


def test_legacy_dispatch_arguments_are_public_or_explicit_internal_aliases(tmp_path: Path) -> None:
    server = rag.McpServer(tmp_path / "missing.sqlite")
    definitions = {
        item["name"]: set(item["inputSchema"].get("properties") or {})
        for item in server._all_tool_definitions_unfiltered()
    }
    _, consumed = _legacy_dispatch_contract()
    for name, argument_names in consumed.items():
        if name not in definitions:
            continue
        missing = argument_names - definitions[name]
        assert missing <= INTERNAL_COMPATIBILITY_ALIASES.get(name, set()), (
            name,
            sorted(missing),
        )


def test_all_public_required_fields_exist_in_their_schema(tmp_path: Path) -> None:
    server = rag.McpServer(tmp_path / "missing.sqlite")
    for definition in server._all_tool_definitions_unfiltered():
        schema = definition["inputSchema"]
        assert set(schema.get("required") or []) <= set(schema.get("properties") or {}), definition["name"]
