from __future__ import annotations
import io
import json
import queue
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import direct_rag_operation as operation
from direct_rag_result import success
from direct_rag_server import DirectRagServer


@pytest.mark.parametrize("field,value", [
    ("source", ["ue_api_reference"]), ("layer", ["engine_api"]),
    ("doc_type", ["api_reference"]), ("genre", ["reference"]),
    ("extension", [".h"]), ("required_term", ["Actor"]),
    ("use_active_project", False),
])
def test_receipt_filter_changes_retrieve_again(monkeypatch, tmp_path, field, value):
    import direct_rag_search as search
    import direct_rag_corpus as corpus
    import direct_rag_history as history
    monkeypatch.setattr(history, "_state_path", lambda: None)
    for state in (history._entries, history._receipts, history._order, history._receipt_order):
        state.clear()
    index = tmp_path / "rag.sqlite"
    index.write_bytes(b"fixture")
    monkeypatch.setattr(search, "resolve_request_index", lambda *a, **k: {"ok": True, "index": str(index)})
    monkeypatch.setattr(corpus, "engine_corpus_error", lambda *a, **k: None)
    monkeypatch.setattr(search, "resolve_active_project_path", lambda *a: None)
    calls = []
    page = SimpleNamespace(rows=[], context="", resolved_scope="engine", detail_level="compact",
                           freshness={}, explicit_projects=[], selected_projects=[],
                           stale_rows_suppressed=0, truncated=False)
    monkeypatch.setattr(search, "retrieve", lambda *a, **k: calls.append(a) or page)
    runtime = SimpleNamespace(index=index, workspace=tmp_path)
    first = search.rag_search(runtime, {"query": "Actor"}).payload
    unchanged = search.rag_search(runtime, {"query": "Actor", "repeatReceipt": first["repeatReceipt"]}).payload
    changed = search.rag_search(runtime, {"query": "Actor", field: value, "repeatReceipt": first["repeatReceipt"]}).payload
    assert unchanged["duplicate"] is True
    assert changed.get("duplicate") is not True
    assert len(calls) == 2


def test_full_query_and_equivalent_filter_order_identity(tmp_path):
    from direct_rag_history import query_keys
    from direct_rag_selection import effective_search_filters
    kwargs = dict(tool="unreal_rag_search", active_project="", projects=[], mode="auto",
                  scope="auto", detail="compact", top_k=6, hybrid=False, index=tmp_path/"rag.sqlite")
    assert query_keys(query="x"*512+" one", **kwargs) != query_keys(query="x"*512+" two", **kwargs)
    assert effective_search_filters({"source": ["b", "a", "b"]}) == effective_search_filters({"source": ["a", "b"]})


def test_post_commit_cleanup_failure_remains_committed(monkeypatch, tmp_path):
    import direct_rag_generation_swap as swap
    from direct_rag_refresh_transaction import prepare_refresh_stage, commit_refresh_stage, recover_interrupted_refresh
    index = tmp_path / "index"
    index.mkdir()
    (index/"rag.sqlite").write_bytes(b"old")
    stage = prepare_refresh_stage(index)
    (stage/"rag.sqlite").write_bytes(b"new")
    real_clear = swap.clear_refresh_journal
    monkeypatch.setattr(swap, "clear_refresh_journal", lambda path: (_ for _ in ()).throw(OSError("cleanup")))
    result = commit_refresh_stage(stage, index, required_files=("rag.sqlite",))
    assert result["stageCommitted"] is True
    assert result["cleanup"]["status"] == "pending"
    assert (index/"rag.sqlite").read_bytes() == b"new"
    journal = Path(result["cleanup"]["journal"])
    assert json.loads(journal.read_text())["state"] == "committed"
    monkeypatch.setattr(swap, "clear_refresh_journal", real_clear)
    assert recover_interrupted_refresh(index)["reason"] == "committed_cleanup"
    assert not journal.exists()


def _project(tmp_path, name):
    project = tmp_path / name / (name+".uproject")
    project.parent.mkdir()
    project.write_text('{"EngineAssociation":"5.8"}')
    source = project.parent / "Source" / (name+".h")
    source.parent.mkdir()
    source.write_text("UCLASS()\nclass F"+name+"Actor { GENERATED_BODY() };\n")
    return project, source


def _build(stage, tmp_path):
    from build_rag_index import build, parse_args
    from index_inputs import existing_input_paths
    build(parse_args(["--out-dir", str(stage), "--workspace-root", str(tmp_path),
                      "--engine-version", "5.8", "--input", *map(str, existing_input_paths(stage))]))
    return stage/"rag.sqlite"


def test_other_project_rebuild_does_not_restamp_stale_collection(tmp_path):
    from direct_rag_project_collection import run_project_collectors
    from direct_rag_freshness import project_freshness
    from direct_rag_retrieval import retrieve
    a, source_a = _project(tmp_path, "Alpha")
    b, _ = _project(tmp_path, "Beta")
    stage = tmp_path/"stage"
    assert run_project_collectors(ROOT, a, stage, lambda _: None)[1] is None
    source_a.write_text("UCLASS()\nclass FAlphaChanged { GENERATED_BODY() };\n")
    assert run_project_collectors(ROOT, b, stage, lambda _: None)[1] is None
    index = _build(stage, tmp_path)
    facts = project_freshness(index, projects=[str(a), str(b)], workspace=tmp_path)
    assert [state["status"] for state in facts["projectStates"]] == ["stale", "fresh"]
    assert facts["projectSourceFresh"] is False
    page = retrieve(index, "class", 16, {"scope": "project", "project": [str(a), str(b)]}, workspace=tmp_path)
    assert not any(row["project"] == "Alpha" for row in page.rows)
    assert any(row["project"] == "Beta" for row in page.rows)


def test_source_add_delete_and_legacy_unknown(tmp_path):
    from direct_rag_source_snapshot import source_snapshot
    from direct_rag_freshness import project_freshness
    project, source = _project(tmp_path, "Legacy")
    before = source_snapshot(project)["fingerprint"]
    source.unlink()
    assert source_snapshot(project)["fingerprint"] != before
    source.write_text("class FLegacyActor {};\n")
    from build_rag_index import build, parse_args
    raw = tmp_path/"raw.jsonl"
    raw.write_text(json.dumps({"id": "legacy", "source": "unreal_symbol", "text": "class FLegacyActor",
                              "metadata": {"project": project.stem, "project_root": str(project.parent)}})+"\n")
    stage = tmp_path/"index"
    build(parse_args(["--input", str(raw), "--out-dir", str(stage), "--workspace-root", str(tmp_path)]))
    facts = project_freshness(stage/"rag.sqlite", projects=[str(project)], workspace=tmp_path)
    assert facts["freshnessStatus"] == "unknown"
    assert facts["projectSourceFresh"] is None
    assert facts["projectSymbolsFresh"] is None


def test_snapshot_covers_project_text_and_producer_specific_inputs(tmp_path):
    from direct_rag_source_snapshot import source_snapshot
    project, _source = _project(tmp_path, "Inputs")
    initial = source_snapshot(project)["fingerprint"]
    for relative in ("README.md", "Content/Dialog.txt", "Source/Probe.inl", ".private/Rules.Build.cs", "Config/Saved/Extra.ini"):
        path = project.parent/relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("original", encoding="utf-8")
        added = source_snapshot(project)["fingerprint"]
        assert added != initial, relative
        path.write_text("changed input", encoding="utf-8")
        assert source_snapshot(project)["fingerprint"] != added, relative
        path.unlink()
        assert source_snapshot(project)["fingerprint"] == initial, relative
    ignored = project.parent/"Saved"/"Ignored.txt"
    ignored.parent.mkdir()
    ignored.write_text("generated")
    assert source_snapshot(project)["fingerprint"] == initial


def test_package_failed_publish_restores_old_output(monkeypatch, tmp_path):
    import build_integrated_package as builder
    output, stage = tmp_path/"portable", tmp_path/".portable-staging-test"
    output.mkdir()
    (output/"old").write_text("good")
    stage.mkdir()
    real = Path.replace
    def replace(path, target):
        if path == stage:
            raise OSError("publish")
        return real(path, target)
    monkeypatch.setattr(Path, "replace", replace)
    with pytest.raises(OSError, match="publish"):
        builder._publish_package_output(stage, output)
    assert (output/"old").read_text() == "good"


def test_package_failed_restore_preserves_artifacts(monkeypatch, tmp_path):
    import build_integrated_package as builder
    output, stage = tmp_path/"portable", tmp_path/".portable-staging-test"
    output.mkdir()
    (output/"old").write_text("good")
    stage.mkdir()
    real = Path.replace
    def replace(path, target):
        if path == stage or path.name == "previous":
            raise OSError("fault")
        return real(path, target)
    monkeypatch.setattr(Path, "replace", replace)
    with pytest.raises(builder.PackagePublicationError, match="preserved backup"):
        builder._publish_package_output(stage, output)
    assert stage.exists()
    assert len(list(tmp_path.glob(".portable-backup-*/previous/old"))) == 1


def test_package_cleanup_failure_keeps_published_output(monkeypatch, tmp_path):
    import build_integrated_package as builder
    output, stage = tmp_path/"portable", tmp_path/".portable-staging-test"
    output.mkdir()
    stage.mkdir()
    (stage/"new").write_text("new")
    real = builder.shutil.rmtree
    def rmtree(path, *args, **kwargs):
        if Path(path).name.startswith(".portable-backup-"):
            raise OSError("cleanup")
        return real(path, *args, **kwargs)
    monkeypatch.setattr(builder.shutil, "rmtree", rmtree)
    result = builder._publish_package_output(stage, output)
    assert result["status"] == "pending"
    assert (output/"new").read_text() == "new"
    assert Path(result["backup"]).exists()


class LiveInput:
    def __init__(self):
        self.lines = queue.Queue()
    def __iter__(self):
        while (line := self.lines.get()) is not None:
            yield line
    def write(self, value):
        self.lines.put(json.dumps(value)+"\n")
    def close(self):
        self.lines.put(None)


def _call(identifier=1):
    return {"jsonrpc": "2.0", "id": identifier, "method": "tools/call",
            "params": {"name": "unreal_rag_refresh", "arguments": {"scope": "project_source"}}}


@pytest.mark.parametrize("stop", ["cancel", "eof"])
def test_running_collector_cancel_and_eof_stop_process(tmp_path, stop):
    stream, output = LiveInput(), io.StringIO()
    server = DirectRagServer(tmp_path/"rag.sqlite", workspace=tmp_path, input_stream=stream, output_stream=output)
    started = threading.Event()
    pid_file = tmp_path/"pid"
    def handler(runtime, arguments):
        started.set()
        operation.run_collector([sys.executable, "-c",
            "import os,time,pathlib; pathlib.Path(r'"+str(pid_file)+"').write_text(str(os.getpid())); time.sleep(30)"],
            cwd=tmp_path, scratch=tmp_path)
        return success()
    server._handlers["unreal_rag_refresh"] = handler
    worker = threading.Thread(target=server.run)
    worker.start()
    stream.write(_call())
    assert started.wait(2)
    until = time.monotonic()+3
    while not pid_file.exists() and time.monotonic() < until:
        time.sleep(.02)
    assert pid_file.exists()
    if stop == "cancel":
        stream.write({"method": "notifications/cancelled", "params": {"requestId": 1}})
    else:
        stream.close()
    until = time.monotonic()+6
    while '"RAG_OPERATION_CANCELLED"' not in output.getvalue() and time.monotonic() < until:
        time.sleep(.02)
    assert "RAG_OPERATION_CANCELLED" in output.getvalue()
    if stop == "cancel":
        stream.close()
    worker.join(2)
    assert not worker.is_alive()
    result = next(json.loads(line)["result"]["structuredContent"] for line in output.getvalue().splitlines()
                  if json.loads(line).get("id") == 1)
    assert result["terminationConfirmed"] is True
    assert result["terminationScope"] == "collector_process"


def test_eof_does_not_dispatch_queued_mutation_and_invalid_id_is_safe(tmp_path):
    from direct_rag_contract import DIRECT_RAG_MUTATING_TOOL_NAMES
    from direct_rag_stdio_requests import RequestInbox, normalize_line
    requests = [
        {"id": {"bad": 1}, "method": "ping"},
        {"id": [], "method": "tools/list"}, _call(3), {"id": 4, "method": "ping"}]
    stream = io.StringIO("".join(json.dumps(item)+"\n" for item in requests))
    output = io.StringIO()
    server = DirectRagServer(tmp_path/"rag.sqlite", input_stream=stream, output_stream=output)
    calls = []
    server._handlers["unreal_rag_refresh"] = lambda *args: calls.append(args) or success()
    inbox = RequestInbox(stream, server.send_error,
                         is_mutating=lambda name: name in DIRECT_RAG_MUTATING_TOOL_NAMES)
    # Finish the reader before dispatch so this specifically covers queued EOF,
    # independent of whether the scheduler starts a handler before reading EOF.
    queued = list(inbox)
    for raw, context in queued:
        if context is None:
            server.handle_message(json.loads(normalize_line(raw)))
        else:
            with operation.operation_scope(context):
                server.handle_message(json.loads(normalize_line(raw)))
    assert calls == []
    responses = [json.loads(line) for line in output.getvalue().splitlines()]
    assert sum(response.get("error", {}).get("code") == -32600 for response in responses) == 2
    assert any(response.get("id") == 4 and response.get("result") == {} for response in responses)


def test_bom_normalization_and_invalid_tool_name_do_not_escape_registry(tmp_path):
    output = io.StringIO()
    malformed = {"id": 1, "method": "tools/call", "params": {"name": ["bad"]}}
    stream = io.StringIO(" \ufeff" + json.dumps(_call(2)) + "\n" + json.dumps(malformed) + "\n")
    server = DirectRagServer(tmp_path/"rag.sqlite", input_stream=stream, output_stream=output)
    calls = []
    server._handlers["unreal_rag_refresh"] = lambda *args: calls.append(args) or success()
    from direct_rag_stdio_requests import RequestInbox
    inbox = RequestInbox(stream, server.send_error, is_mutating=lambda name: True)
    queued = list(inbox)
    for raw, context in queued:
        assert context is not None and context.cancelled.is_set()
        from direct_rag_stdio_requests import normalize_line
        with operation.operation_scope(context):
            server.handle_message(json.loads(normalize_line(raw)))
    assert calls == []
    responses = [json.loads(line) for line in output.getvalue().splitlines()]
    assert len(responses) == 2
    assert responses[1]["result"]["structuredContent"]["errorCode"] == "TOOL_NOT_CALLABLE"


def test_collector_timeout_output_cap_and_unconfirmed_reservation(monkeypatch, tmp_path):
    # A collector owns no transport input: its stdin is EOF, even while the
    # server's reader waits for another JSON-RPC request on a live connection.
    assert operation.run_collector([sys.executable, "-c", "import sys; assert sys.stdin.read() == ''"], cwd=tmp_path)["ok"]
    with pytest.raises(operation.RagOperationStopped, match="RAG_COLLECTOR_TIMEOUT"):
        operation.run_collector([sys.executable, "-c", "import time; time.sleep(30)"], cwd=tmp_path, timeout=.1)
    with pytest.raises(operation.RagOperationStopped, match="RAG_COLLECTOR_OUTPUT_LIMIT"):
        operation.run_collector([sys.executable, "-c", "print('x'*50000)"], cwd=tmp_path, max_output_bytes=1000)
    from direct_rag_refresh_transaction import discard_refresh_stage
    stage = tmp_path/"stage"
    stage.mkdir()
    process = SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(operation, "_PENDING", [(process, stage.resolve())])
    discard_refresh_stage(stage)
    assert stage.exists()
    with pytest.raises(operation.RagOperationStopped, match="SHUTDOWN_PENDING"):
        operation.run_collector([sys.executable, "-c", "pass"], cwd=tmp_path)
    process.poll = lambda: 0
    assert operation.run_collector([sys.executable, "-c", "pass"], cwd=tmp_path)["ok"]

