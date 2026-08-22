from __future__ import annotations

import io
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from direct_rag_contract import (  # noqa: E402
    DIRECT_RAG_TOOL_NAMES,
    direct_rag_tool_definitions,
)
from direct_rag_result import CapabilityResult, to_mcp_tool_result  # noqa: E402
from direct_rag_server import DirectRagServer, compose_handlers  # noqa: E402


def _requests(*payloads: dict) -> io.StringIO:
    return io.StringIO("".join(json.dumps(item) + "\n" for item in payloads))


def _responses(output: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in output.getvalue().splitlines() if line.strip()]


def test_direct_catalog_is_exactly_eight_capabilities() -> None:
    definitions = direct_rag_tool_definitions()

    assert tuple(tool["name"] for tool in definitions) == DIRECT_RAG_TOOL_NAMES
    assert len(definitions) == len(compose_handlers()) == 8
    serialized = json.dumps(definitions)
    assert "taskAuthorization" not in serialized
    assert "requiredNextTool" not in serialized
    assert "unreal_agent_plan" not in serialized
    assert "unreal_task_" not in serialized
    assert "continuationToken" not in serialized
    assert "sessionId" not in serialized


def test_project_switch_schema_has_no_resume_or_prepare_controller_inputs() -> None:
    tool = next(
        item
        for item in direct_rag_tool_definitions()
        if item["name"] == "unreal_set_active_project"
    )
    properties = tool["inputSchema"]["properties"]

    assert set(properties) == {"projectPath", "clear"}
    assert "resumeToken" not in properties
    assert "taskAuthorization" not in properties


def test_refresh_schema_defaults_to_source_and_discloses_editor_launch_gate() -> None:
    tool = next(
        item
        for item in direct_rag_tool_definitions()
        if item["name"] == "unreal_rag_refresh"
    )
    properties = tool["inputSchema"]["properties"]

    assert properties["scope"]["default"] == "project_source"
    assert properties["allowEditorLaunch"]["default"] is False
    assert "Unreal Editor subprocess" in properties["allowEditorLaunch"]["description"]


def test_stdio_catalog_ignores_stale_strict_environment(tmp_path: Path) -> None:
    environment = os.environ.copy()
    environment["MCP_EXECUTION_MODE"] = "strict"
    completed = subprocess.run(
        [sys.executable, str(SCRIPTS / "unreal_rag_direct.py"), "--index", str(tmp_path / "missing.sqlite")],
        input="\n".join(
            [
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {"protocolVersion": "2025-06-18"},
                    }
                ),
                json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
                "",
            ]
        ),
        text=True,
        capture_output=True,
        timeout=20,
        cwd=str(ROOT),
        env=environment,
    )

    assert completed.returncode == 0, completed.stderr
    messages = [json.loads(line) for line in completed.stdout.splitlines()]
    assert messages[0]["result"]["serverInfo"]["name"] == "unreal-rag-direct"
    names = tuple(item["name"] for item in messages[1]["result"]["tools"])
    assert names == DIRECT_RAG_TOOL_NAMES


@pytest.mark.parametrize(
    ("index_state", "expected_reason"),
    (
        ("missing", "RAG_INDEX_MISSING"),
        ("empty", "RAG_INDEX_EMPTY"),
        ("unreadable", "RAG_INDEX_UNREADABLE"),
    ),
)
def test_stdio_health_codes_are_observations_not_success_error_codes(
    tmp_path: Path,
    index_state: str,
    expected_reason: str,
) -> None:
    index = tmp_path / f"{index_state}.sqlite"
    if index_state == "empty":
        with sqlite3.connect(index) as conn:
            conn.execute("create table chunks (source text, layer text)")
    elif index_state == "unreadable":
        index.write_bytes(b"not a sqlite database")
    requests = "".join(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": {}},
            }
        )
        + "\n"
        for request_id, tool_name in (
            (1, "unreal_rag_health"),
            (2, "unreal_rag_rebuild_status"),
        )
    )
    completed = subprocess.run(
        [sys.executable, "-B", str(SCRIPTS / "unreal_rag_direct.py"), "--index", str(index)],
        cwd=str(ROOT),
        input=requests,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert completed.returncode == 0, completed.stderr
    for response in [json.loads(line) for line in completed.stdout.splitlines()]:
        result = response["result"]
        payload = result["structuredContent"]
        assert result.get("isError") is not True
        assert payload["ok"] is True
        assert "errorCode" not in payload
        assert payload["indexReasonCode"] == expected_reason
        if response["id"] == 2:
            serialized = json.dumps(payload, ensure_ascii=False)
            assert "recommendedCommand" not in serialized
            assert "recommendedDoctorCommand" not in serialized
            assert "availableActions" not in serialized


def test_default_import_closure_excludes_legacy_control_modules() -> None:
    code = """
import json, sys
sys.path.insert(0, 'scripts')
import unreal_rag_direct
import direct_rag_server
forbidden = [
    'unreal_rag_mcp', 'direct_model_mode', 'task_api', 'phase_tool_router',
    'route_recovery_policy', 'agent_orchestrator', 'architecture_reasoning',
    'code_sketch_claim_validate', 'wrapper_job_manager', 'control_runtime_identity',
    'rag_search', 'rag_semantic', 'rag_delivery', 'token_budget',
    'load_sampling_preset', 'read_query_history', 'state_root',
    'project_switch_invalidate', 'wrapper_evidence', 'unreal_static_validate',
    'index_staleness', 'on_active_project_changed', 'active_project_sync',
    'editor_metadata_status', 'install_editor_graph_plugin',
]
print(json.dumps({name: name in sys.modules for name in forbidden}, sort_keys=True))
"""
    completed = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", code],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=20,
        env={**os.environ, "MCP_EXECUTION_MODE": "strict"},
    )

    assert completed.returncode == 0, completed.stderr
    assert not any(json.loads(completed.stdout).values())


def test_direct_refresh_uses_factual_sync_without_legacy_search_or_controller(
    tmp_path: Path,
) -> None:
    shared = tmp_path / "shared.json"
    project = tmp_path / "Demo" / "Demo.uproject"
    project.parent.mkdir()
    project.write_text("{}", encoding="utf-8")
    shared.write_text(json.dumps({"activeProject": str(project)}), encoding="utf-8")
    code = f"""
import json, os, sys
from pathlib import Path
os.environ['SHARED_UNREAL_CONFIG'] = {str(shared)!r}
sys.path.insert(0, 'scripts')
import active_project_sync, sync_editor_metadata
observed = {{}}
def sync(**kwargs):
    observed['project'] = str(kwargs.get('project') or '')
    return {{'ok': True, 'steps': []}}
active_project_sync.sync_active_project = sync
sync_editor_metadata.refresh_editor_metadata = lambda **kwargs: {{'ok': True, 'project': kwargs.get('project_name')}}
from rag_refresh import refresh_active_project
result = refresh_active_project(workspace=Path.cwd())
forbidden = ['on_active_project_changed', 'warm_symbol_cache', 'rag_search', 'rag_semantic', 'token_budget', 'read_query_history', 'state_root', 'editor_export_runner']
print(json.dumps({{'ok': result.get('ok'), 'scope': result.get('scope'), 'editorLaunchAllowed': result.get('editorLaunchAllowed'), 'syncedProject': observed.get('project'), 'loaded': [name for name in forbidden if name in sys.modules]}}))
"""
    completed = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", code],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=20,
        env={**os.environ, "SHARED_UNREAL_CONFIG": str(shared)},
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "ok": True,
        "scope": "project_source",
        "editorLaunchAllowed": False,
        "syncedProject": str(project.resolve()),
        "loaded": [],
    }


def test_direct_refresh_projects_facts_and_removes_old_tool_directives(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import direct_rag_index
    import rag_refresh as refresh_module

    def fake_refresh(**kwargs):
        assert "direct" not in kwargs
        return {
            "ok": True,
            "scope": "editor_metadata",
            "project": "Demo.uproject",
            "editorMetadataSetup": {
                "ok": True,
                "projectName": "Demo",
                "metadataStatusAfter": {"needsEditorExport": False},
                "nextActions": ["call unreal_run_editor_export"],
                "agentWorkflow": [
                    "unreal_editor_metadata_status",
                    "unreal_asset_graph_lookup",
                ],
                "nested": {
                    "requiredReads": ["planner.md"],
                    "forbiddenActions": ["stop"],
                    "fact": 7,
                },
            },
            "cacheInvalidated": ["project_context", "direct_rag_freshness"],
        }

    monkeypatch.setattr(refresh_module, "refresh_active_project", fake_refresh)
    result = direct_rag_index.rag_refresh(
        SimpleNamespace(
            index=tmp_path / "rag.sqlite",
            workspace=tmp_path,
            notify=lambda *_args, **_kwargs: None,
        ),
        {"scope": "editor_metadata"},
    )
    serialized = json.dumps(result.payload, ensure_ascii=False)

    assert result.payload["editorMetadataSetup"]["nested"] == {"fact": 7}
    for forbidden in (
        "nextActions",
        "agentWorkflow",
        "requiredReads",
        "forbiddenActions",
        "unreal_run_editor_export",
        "unreal_editor_metadata_status",
        "unreal_asset_graph_lookup",
    ):
        assert forbidden not in serialized


def test_transport_dispatches_capability_without_lifecycle_state(tmp_path: Path) -> None:
    output = io.StringIO()
    server = DirectRagServer(
        tmp_path / "rag.sqlite",
        workspace=tmp_path,
        input_stream=_requests(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {"name": "unreal_rag_capabilities", "arguments": {}},
            }
        ),
        output_stream=output,
        error_stream=io.StringIO(),
    )

    server.run()

    payload = _responses(output)[0]["result"]
    assert payload["isError"] is False
    structured = payload["structuredContent"]
    assert structured["toolCount"] == 8
    assert "workflow lifecycle" in structured["boundaries"]["excluded"]
    assert not any(key.casefold().startswith("task") for key in structured)


def test_direct_project_switch_does_not_load_wrapper_or_validator_stack(tmp_path: Path) -> None:
    shared = tmp_path / "unreal-workspace.json"
    project = tmp_path / "Demo" / "Demo.uproject"
    project.parent.mkdir()
    project.write_text("{}", encoding="utf-8")
    shared.write_text('{"activeProject":null}', encoding="utf-8")
    code = f"""
import json, os, sys
from pathlib import Path
os.environ['SHARED_UNREAL_CONFIG'] = {str(shared)!r}
sys.path.insert(0, 'scripts')
from direct_rag_projects import set_active_project
result = set_active_project(type('Runtime', (), {{'workspace': Path.cwd()}})(), {{'projectPath': {str(project)!r}}})
forbidden = ['project_switch_invalidate', 'wrapper_evidence', 'unreal_static_validate', 'read_query_history', 'state_root']
print(json.dumps({{'ok': result.payload.get('ok'), 'loaded': [name for name in forbidden if name in sys.modules]}}))
"""
    completed = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", code],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=20,
        env={**os.environ, "SHARED_UNREAL_CONFIG": str(shared)},
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload == {"ok": True, "loaded": []}


def test_python_direct_clear_is_authoritative_for_node_direct(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from direct_rag_projects import set_active_project

    project = tmp_path / "Demo" / "Demo.uproject"
    project.parent.mkdir()
    project.write_text("{}", encoding="utf-8")
    shared = tmp_path / "shared.json"
    local = tmp_path / "local.json"
    shared.write_text(json.dumps({"activeProject": str(project)}), encoding="utf-8")
    local.write_text(json.dumps({"activeProject": str(project)}), encoding="utf-8")
    monkeypatch.setenv("SHARED_UNREAL_CONFIG", str(shared))

    result = set_active_project(SimpleNamespace(workspace=ROOT), {"clear": True})
    assert result.payload["ok"] is True
    assert json.loads(shared.read_text(encoding="utf-8"))["activeProject"] is None

    script = """
const {getActiveProject} = require('./lmstudio-unreal-agent-mcp/src/unreal-detect');
process.stdout.write(JSON.stringify({activeProject: getActiveProject(process.argv[1])}));
"""
    completed = subprocess.run(
        ["node", "-e", script, str(local)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=20,
        env={**os.environ, "SHARED_UNREAL_CONFIG": str(shared)},
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {"activeProject": None}
    assert json.loads(local.read_text(encoding="utf-8"))["activeProject"] == str(project)


def test_transport_rejects_unknown_and_extra_arguments(tmp_path: Path) -> None:
    output = io.StringIO()
    server = DirectRagServer(
        tmp_path / "rag.sqlite",
        workspace=tmp_path,
        input_stream=_requests(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "unreal_agent_plan", "arguments": {}},
            },
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "unreal_set_active_project",
                    "arguments": {"resumeToken": "legacy"},
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "unreal_rag_refresh",
                    "arguments": {"scope": "not-a-scope", "force": "yes"},
                },
            },
        ),
        output_stream=output,
        error_stream=io.StringIO(),
    )

    server.run()

    first, second, third = _responses(output)
    assert first["result"]["structuredContent"]["errorCode"] == "TOOL_NOT_CALLABLE"
    assert second["result"]["structuredContent"]["errorCode"] == "INVALID_TOOL_ARGUMENTS"
    assert third["result"]["structuredContent"]["errorCode"] == "INVALID_TOOL_ARGUMENTS"


def test_direct_result_strips_nested_task_and_route_fields() -> None:
    result = to_mcp_tool_result(
        CapabilityResult(
            {
                "ok": True,
                "evidence": {"value": 1, "taskAuthorization": {"secret": "x"}},
                "routeHash": "legacy",
                "requiredNextTool": "unreal_agent_plan",
            }
        ),
        tool_name="unreal_rag_search",
    )

    assert result["structuredContent"] == {"ok": True, "evidence": {"value": 1}}
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]


def test_direct_history_owns_receipts_and_rollback_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import direct_rag_history

    monkeypatch.setenv("DIRECT_RAG_HISTORY_PATH", str(tmp_path / "direct-history.json"))
    direct_rag_history._entries.clear()
    direct_rag_history._receipts.clear()
    direct_rag_history._order.clear()
    direct_rag_history._receipt_order.clear()
    index = tmp_path / "rag.sqlite"
    index.write_bytes(b"index")
    semantic, variant = direct_rag_history.query_keys(
        tool="unreal_rag_search",
        active_project="Demo",
        projects=["Demo"],
        query="Find ADemoActor",
        mode="api_lookup",
        scope="project",
        detail="compact",
        top_k=6,
        hybrid=False,
        index=index,
    )

    receipt = direct_rag_history.record(semantic, variant, "compact", 1)
    assert direct_rag_history.receipt_matches("", variant) is False
    assert direct_rag_history.receipt_matches(receipt, variant) is True
    index.write_bytes(b"changed-index-state")
    _, changed_variant = direct_rag_history.query_keys(
        tool="unreal_rag_search",
        active_project="Demo",
        projects=["Demo"],
        query="Find ADemoActor",
        mode="api_lookup",
        scope="project",
        detail="compact",
        top_k=6,
        hybrid=False,
        index=index,
    )
    assert changed_variant != variant
    assert direct_rag_history.receipt_matches(receipt, changed_variant) is False
    bounded = to_mcp_tool_result(
        CapabilityResult(
            {"ok": True, "evidence": "x" * 10_000},
            char_limit=2_000,
            rollback_delivery_key=variant,
        ),
        tool_name="unreal_rag_search",
    )
    assert bounded["structuredContent"]["errorCode"] == "OUTPUT_LIMIT_EXCEEDED"
    assert direct_rag_history.receipt_matches(receipt, variant) is False
    assert direct_rag_history.forget(variant) is False


def test_search_success_repeats_only_with_an_echoed_state_bound_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import direct_rag_history
    import direct_rag_search

    monkeypatch.setenv("DIRECT_RAG_HISTORY_PATH", str(tmp_path / "history.json"))
    direct_rag_history._entries.clear()
    direct_rag_history._receipts.clear()
    direct_rag_history._order.clear()
    direct_rag_history._receipt_order.clear()
    index = tmp_path / "rag.sqlite"
    index.write_bytes(b"index-v1")

    page = SimpleNamespace(
        rows=[{"chunk_id": "fact:1", "source": "unreal_symbol"}],
        context="factual evidence",
        resolved_scope="engine",
        detail_level="compact",
        freshness={},
        explicit_projects=[],
        selected_projects=[],
        stale_rows_suppressed=0,
        truncated=False,
    )
    monkeypatch.setattr(direct_rag_search, "retrieve", lambda *_args, **_kwargs: page)
    runtime = SimpleNamespace(index=index)
    arguments = {"query": "UActorComponent"}

    first = direct_rag_search.rag_search(runtime, dict(arguments)).payload
    second = direct_rag_search.rag_search(runtime, dict(arguments)).payload
    echoed = direct_rag_search.rag_search(
        runtime,
        {**arguments, "repeatReceipt": second["repeatReceipt"]},
    ).payload
    index.write_bytes(b"index-v2-with-different-size")
    changed = direct_rag_search.rag_search(
        runtime,
        {**arguments, "repeatReceipt": second["repeatReceipt"]},
    ).payload

    assert first["evidence"] == second["evidence"] == "factual evidence"
    assert first.get("duplicate") is not True
    assert second.get("duplicate") is not True
    assert first["repeatReceipt"] != second["repeatReceipt"]
    assert echoed == {
        "ok": True,
        "duplicate": True,
        "status": "no_new_information",
        "message": (
            "The supplied repeat receipt matches this query and current index state; "
            "the prior evidence is unchanged."
        ),
        "projects": [],
        "repeatReceipt": second["repeatReceipt"],
    }
    assert changed.get("duplicate") is not True
    assert changed["evidence"] == "factual evidence"
    assert changed["repeatReceipt"] != second["repeatReceipt"]


def test_search_exposes_only_actionable_next_detail_when_truncated(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import direct_rag_search

    index = tmp_path / "rag.sqlite"
    index.write_bytes(b"index")

    def page(truncated: bool) -> SimpleNamespace:
        return SimpleNamespace(
            rows=[{"chunk_id": "fact:1", "source": "unreal_symbol"}],
            context="factual evidence",
            resolved_scope="engine",
            detail_level="compact",
            freshness={},
            explicit_projects=[],
            selected_projects=[],
            stale_rows_suppressed=0,
            truncated=truncated,
        )

    monkeypatch.setattr(direct_rag_search, "deliver", lambda **_kwargs: {})
    monkeypatch.setattr(
        direct_rag_search,
        "retrieve",
        lambda *_args, **_kwargs: page(False),
    )
    complete = direct_rag_search.rag_search(
        SimpleNamespace(index=index), {"query": "UActorComponent"}
    ).payload
    monkeypatch.setattr(
        direct_rag_search,
        "retrieve",
        lambda *_args, **_kwargs: page(True),
    )
    truncated = direct_rag_search.rag_search(
        SimpleNamespace(index=index), {"query": "UActorComponent"}
    ).payload

    assert "continuationToken" not in complete
    assert "nextDetailLevel" not in complete
    assert "continuationToken" not in truncated
    assert truncated["nextDetailLevel"] == "medium"


def test_direct_module_size_limits_prevent_a_replacement_god_object() -> None:
    modules = sorted(SCRIPTS.glob("direct_rag_*.py"))
    assert modules
    counts = {
        path.name: len(path.read_text(encoding="utf-8").splitlines())
        for path in modules
    }

    assert max(counts.values()) <= 300, counts
    assert counts["direct_rag_server.py"] <= 225
    assert counts["direct_rag_runtime.py"] <= 60

    combined = "\n".join(path.read_text(encoding="utf-8") for path in modules)
    assert re.search(
        r"(?m)^\s*(?:from\s+rag_search\s+import|import\s+rag_search(?:\s|$))",
        combined,
    ) is None
    assert re.search(
        r"(?m)^\s*(?:from\s+rag_semantic\s+import|import\s+rag_semantic(?:\s|$))",
        combined,
    ) is None
    assert "from rag_delivery" not in combined
    assert "from token_budget" not in combined
    assert "load_sampling_preset" not in combined
    assert "read_query_history" not in combined
    assert "from state_root" not in combined
    assert "from index_staleness" not in combined
    assert "on_active_project_changed" not in combined


def test_direct_history_never_falls_back_to_agent_controller_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import direct_rag_history

    monkeypatch.delenv("DIRECT_RAG_HISTORY_PATH", raising=False)
    monkeypatch.delenv("DIRECT_RAG_STATE_ROOT", raising=False)
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "legacy-agent-state"))

    assert direct_rag_history._state_path() is None


def test_direct_search_is_factual_and_never_loads_legacy_search_stack(tmp_path: Path) -> None:
    from direct_rag_lexical import lexical_search
    from direct_rag_evidence import format_evidence_rows
    from rag_types import SearchOptions

    index = tmp_path / "rag.sqlite"
    columns = (
        "chunk_id text, source text, title text, locator text, chunk_index integer, "
        "text text, project text, relative_path text, extension text, layer text, "
        "doc_type text, genre text, symbol_name text, symbol_kind text, module_name text, "
        "error_code text, error_file text, path_only text"
    )
    with sqlite3.connect(index) as connection:
        connection.execute(f"create table chunks({columns})")
        connection.execute("create virtual table chunks_fts using fts5(title, text)")
        row = (
            "fact:1",
            "unreal_project_text",
            "DemoActor.cpp",
            "Source/Demo/DemoActor.cpp:12",
            0,
            "C1083 factual compiler diagnostic at this source location",
            "Demo",
            "Source/Demo/DemoActor.cpp",
            ".cpp",
            "project_text",
            "source",
            "",
            "ADemoActor",
            "class",
            "Demo",
            "C1083",
            "Source/Demo/DemoActor.cpp",
            "",
        )
        connection.execute("insert into chunks values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
        connection.execute(
            "insert into chunks_fts(rowid,title,text) values (1,?,?)",
            (row[2], row[5]),
        )

    rows = lexical_search(
        index,
        "C1083",
        4,
        SearchOptions(mode="module_fix", evidence_only=True),
    )
    evidence, _ = format_evidence_rows(rows, max_chars=8_000, max_chars_per_row=2_000)
    forbidden_markers = (
        "requiredReads",
        "allowedPatchTargets",
        "forbiddenActions",
        "softSteering",
        "Assembly rule:",
    )

    assert rows
    assert all(row.get("source") != "rag_sidecar" for row in rows)
    assert not any(marker in evidence for marker in forbidden_markers)


def test_engine_scope_does_not_report_active_project_or_project_staleness(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import direct_rag_retrieval
    import direct_rag_selection

    monkeypatch.setattr(direct_rag_selection, "active_project_names", lambda: ["Demo"])
    monkeypatch.setattr(
        direct_rag_selection,
        "resolve_active_project_path",
        lambda _workspace=None: tmp_path / "Demo.uproject",
    )
    monkeypatch.setattr(
        direct_rag_retrieval,
        "lexical_search",
        lambda *_args, **_kwargs: [],
    )
    page = direct_rag_retrieval.retrieve(
        tmp_path / "missing.sqlite",
        "UActorComponent",
        1,
        {"scope": "engine", "mode": "api_lookup"},
    )

    assert page.resolved_scope == "engine"
    assert page.selected_projects == []
    assert page.freshness["freshnessScope"] == "engine"
    assert page.freshness["refreshRecommended"] is False
    assert page.freshness["reason"] == "engine_scope"


def test_explicit_project_selectors_reach_search_freshness_and_history(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import direct_rag_search

    index = tmp_path / "rag.sqlite"
    index.write_bytes(b"fixture")
    observed: list[list[str]] = []

    def fake_delivery(**kwargs):
        observed.append(list(kwargs.get("projects") or []))
        if kwargs.get("rows") is None:
            return {"suppressed": False}
        return {
            "suppressed": False,
            "semanticQueryKey": "semantic",
            "deliveryVariantKey": "delivery",
            "repeatReceipt": "receipt",
        }

    def fake_retrieve(_index, _query, _top_k, arguments, **_kwargs):
        observed.append(list(arguments["project"]))
        return SimpleNamespace(
            rows=[],
            context="No evidence",
            resolved_scope="project_miss",
            detail_level="compact",
            freshness={},
            explicit_projects=list(arguments["project"]),
            selected_projects=list(arguments["project"]),
            stale_rows_suppressed=0,
            truncated=False,
        )

    monkeypatch.setattr(direct_rag_search, "deliver", fake_delivery)
    monkeypatch.setattr(direct_rag_search, "retrieve", fake_retrieve)
    monkeypatch.setattr(
        direct_rag_search,
        "resolve_request_index",
        lambda *_args, **_kwargs: {
            "ok": True,
            "index": str(index),
            "indexEngineVersion": "5.8",
        },
    )
    import direct_rag_corpus

    monkeypatch.setattr(
        direct_rag_corpus,
        "engine_corpus_error",
        lambda *_args, **_kwargs: None,
    )
    result = direct_rag_search.rag_search(
        SimpleNamespace(index=index),
        {"query": "Widget", "project": ["ProjectA", "ProjectB"]},
    )

    assert result.payload["projects"] == ["ProjectA", "ProjectB"]
    assert observed == [
        ["ProjectA", "ProjectB"],
        ["ProjectA", "ProjectB"],
        ["ProjectA", "ProjectB"],
    ]


def test_existing_exact_project_path_maps_to_index_identities(tmp_path: Path) -> None:
    from direct_rag_selection import indexed_project_filters

    descriptor = tmp_path / "WorktreeFolder" / "CanonicalGame.uproject"
    descriptor.parent.mkdir()
    descriptor.write_text("{}", encoding="utf-8")

    assert indexed_project_filters([str(descriptor)]) == ["CanonicalGame"]
    assert indexed_project_filters(["ExactIndexedName"]) == ["ExactIndexedName"]
