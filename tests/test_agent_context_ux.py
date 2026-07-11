from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "lmstudio-unreal-agent-mcp" / "src" / "context-ux.js"


def run_node(expression: str) -> object:
    script = (
        f"const ux = require({json.dumps(str(MODULE))});"
        f"const result = ({expression});"
        "process.stdout.write(JSON.stringify(result));"
    )
    proc = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert proc.stdout, "node produced no stdout"
    return json.loads(proc.stdout)


def test_build_response_is_compact_by_default_and_caps_likely_errors() -> None:
    errors = "\n".join(f"Foo.cpp(1): error C{i:04d}: bad" for i in range(100))
    payload = run_node(
        "ux.buildResponsePayload({"
        f"result: {{ok:false, exitCode:1, stdout:{json.dumps(errors)}, stderr:'', error:''}},"
        "build:{target:'GameEditor',platform:'Win64',configuration:'Development',allTargets:['GameEditor']},"
        "planResult:{selectionReason:'active'},projectPath:'C:/Game/Game.uproject',"
        "command:'Build.bat',logPath:'.agent/logs/latest-build.log',verbose:false})"
    )

    assert payload["responseMode"] == "compact"
    assert len(payload["likelyErrors"]) == 20
    assert "stdout" not in payload
    assert "stderr" not in payload
    assert payload["fullLogPath"] == ".agent/logs/latest-build.log"
    assert payload["summary"].startswith("BUILD FAILED")


def test_build_response_verbose_is_opt_in() -> None:
    payload = run_node(
        "ux.buildResponsePayload({"
        "result:{ok:true,exitCode:0,stdout:'build output',stderr:'',error:''},"
        "build:{target:'GameEditor',platform:'Win64',configuration:'Development'},"
        "planResult:{selectionReason:'active'},projectPath:'C:/Game/Game.uproject',"
        "command:'Build.bat',logPath:'.agent/logs/latest-build.log',verbose:true})"
    )
    assert payload["responseMode"] == "verbose"
    assert payload["stdout"] == "build output"


def test_global_result_cap_preserves_valid_json() -> None:
    payload = run_node(
        "JSON.parse(ux.compactMcpContent(JSON.stringify({"
        "summary:'SEARCH READY',ok:true,results:Array(500).fill('x'.repeat(100))"
        "}), 4000))"
    )
    assert payload["summary"] == "SEARCH READY"
    assert payload["truncated"] is True
    assert payload["originalChars"] > 4000
    assert payload["nextSteps"]


def test_global_result_cap_honors_limit_when_only_error_and_steps_are_huge() -> None:
    limit = 1200
    source = {
        "summary": "BUILD FAILED",
        "ok": False,
        "error": "x" * 5000,
        "nextSteps": ["y" * 400 for _ in range(20)],
        "suggestedToolCalls": [
            {"tool": "read_file", "args": {"path": "z" * 300}}
            for _ in range(10)
        ],
    }
    result = run_node(
        f"ux.compactMcpContent({json.dumps(json.dumps(source))}, {limit})"
    )
    assert isinstance(result, str)
    assert len(result) <= limit
    parsed = json.loads(result)
    assert parsed["summary"] == "BUILD FAILED"
    assert parsed["truncated"] is True


def test_log_compaction_prefers_first_error_cluster() -> None:
    payload = run_node(
        "ux.compactLogPayload({summary:'LOGS READY',ok:true,logs:[{"
        "file:'Game.log',lineCount:1000,lines:["
        "...Array(20).fill('noise'),"
        "'Foo.cpp(10): error C2039: DisableGravity is not a member',"
        "...Array(100).fill('after')]}]}, 1000)"
    )
    lines = payload["logs"][0]["lines"]
    assert any("C2039" in line for line in lines)
    assert payload["truncated"] is True


def test_handoff_is_short_and_has_fixed_resume_path() -> None:
    handoff = run_node(
        "ux.formatSessionHandoff({summary:'Fixed two files',"
        "changedFiles:['Source/A.cpp','Source/B.h'],"
        "openErrors:['C2039'],nextSteps:['fix first error','build'],"
        "avoidRepeating:['do not retry same write']})"
    )
    assert ".agent/handoff/latest.md" in handoff
    assert len(handoff.strip().splitlines()) <= 10


def test_parse_build_execution_summary_detects_up_to_date_and_actions() -> None:
    summary = run_node(
        "ux.parseBuildExecutionSummary("
        "'Target is up to date\\n------ Building 1 action(s) ------', '')"
    )
    assert summary["upToDate"] is True
    assert summary["actionsExecuted"] == 1

    zero_actions = run_node(
        "ux.parseBuildExecutionSummary('Total execution time: 0.52 seconds\\nrun 0 action(s)', '')"
    )
    assert zero_actions["actionsExecuted"] == 0


def test_build_response_marks_up_to_date_as_built_stale() -> None:
    payload = run_node(
        "ux.buildResponsePayload({"
        "result:{ok:true,exitCode:0,stdout:'Target is up to date\\nrun 0 action(s)',stderr:'',error:''},"
        "build:{target:'GameEditor',platform:'Win64',configuration:'Development'},"
        "planResult:{selectionReason:'active'},projectPath:'C:/Game/Game.uproject',"
        "command:'Build.bat',logPath:'.agent/logs/latest-build.log',verbose:false})"
    )
    assert payload["upToDate"] is True
    assert payload["actionsExecuted"] == 0
    assert payload["proofLevel"] == "BuiltStale"
    assert "up to date" in payload["summary"].lower()
    assert any("upToDate=true" in step for step in payload["nextSteps"])


def test_build_response_normal_success_is_built_proof_level() -> None:
    payload = run_node(
        "ux.buildResponsePayload({"
        "result:{ok:true,exitCode:0,stdout:'run 12 action(s)\\nBUILD SUCCEEDED',stderr:'',error:''},"
        "build:{target:'GameEditor',platform:'Win64',configuration:'Development'},"
        "planResult:{selectionReason:'active'},projectPath:'C:/Game/Game.uproject',"
        "command:'Build.bat',logPath:'.agent/logs/latest-build.log',verbose:false})"
    )
    assert payload["upToDate"] is False
    assert payload["actionsExecuted"] == 12
    assert payload["proofLevel"] == "Built"


def test_build_response_up_to_date_with_actions_is_built() -> None:
    payload = run_node(
        "ux.buildResponsePayload({"
        "result:{ok:true,exitCode:0,stdout:'Target is up to date\\n------ Building 1 action(s) ------',stderr:'',error:''},"
        "build:{target:'GameEditor',platform:'Win64',configuration:'Development'},"
        "planResult:{selectionReason:'active'},projectPath:'C:/Game/Game.uproject',"
        "command:'Build.bat',logPath:'.agent/logs/latest-build.log',verbose:false})"
    )
    assert payload["upToDate"] is True
    assert payload["actionsExecuted"] == 1
    assert payload["proofLevel"] == "Built"
    assert "0 files recompiled" not in payload["summary"].lower()


def test_build_response_failure_is_not_success_proof_level() -> None:
    payload = run_node(
        "ux.buildResponsePayload({"
        "result:{ok:false,exitCode:1,stdout:'error C2039',stderr:'',error:''},"
        "build:{target:'GameEditor',platform:'Win64',configuration:'Development'},"
        "planResult:{selectionReason:'active'},projectPath:'C:/Game/Game.uproject',"
        "command:'Build.bat',logPath:'.agent/logs/latest-build.log',verbose:false})"
    )
    assert payload["proofLevel"] == "Failed"
    assert payload["ok"] is False


def test_build_response_unknown_action_count_is_built_unverified() -> None:
    payload = run_node(
        "ux.buildResponsePayload({"
        "result:{ok:true,exitCode:0,stdout:'BUILD SUCCEEDED',stderr:'',error:''},"
        "build:{target:'GameEditor',platform:'Win64',configuration:'Development'},"
        "planResult:{selectionReason:'active'},projectPath:'C:/Game/Game.uproject',"
        "command:'Build.bat',logPath:'.agent/logs/latest-build.log',verbose:false})"
    )
    assert payload["proofLevel"] == "BuiltUnverified"
    assert "unverified" in payload["summary"].lower()


def test_parse_build_execution_summary_variants() -> None:
    twelve = run_node(
        "ux.parseBuildExecutionSummary('------ Building 12 action(s) started ------', '')"
    )
    assert twelve["actionsExecuted"] == 12

    with_processes = run_node(
        "ux.parseBuildExecutionSummary('Building 12 action(s) with 24 processes...', '')"
    )
    assert with_processes["actionsExecuted"] == 12

    parallel_capacity = run_node(
        "ux.parseBuildExecutionSummary('Executing up to 24 actions, one per physical core', '')"
    )
    assert parallel_capacity["actionsExecuted"] is None


def test_slim_write_success_payload_includes_replacements() -> None:
    payload = run_node(
        "ux.slimWriteSuccessPayload('patched', {findings:[], ok:true}, "
        "{operation:'replace', replacements:1, path:'Source/A.cpp'})"
    )
    assert payload["operation"] == "replace"
    assert payload["replacements"] == 1
    assert payload["validationSummary"] is not None


def test_slim_write_success_payload_includes_scan_mode_and_elapsed() -> None:
    payload = run_node(
        "ux.slimWriteSuccessPayload('patched', "
        "{findings:[], ok:true, scanMode:'scoped', elapsedMs:318}, "
        "{operation:'replace', path:'Source/A.cpp'})"
    )
    assert payload["validationSummary"]["scanMode"] == "scoped"
    assert payload["validationSummary"]["elapsedMs"] == 318



def test_write_discipline_options_for_existing_paths() -> None:
    payload = run_node("ux.writeDisciplineOptions(true)")
    assert payload["writeToolPolicy"] == "create_only"
    assert payload["requiredNextTool"] == "replace_in_file"
    assert payload["doNotRetry"] == "write_file"


def test_compact_validation_payload_groups_and_caps_findings() -> None:
    findings = [
        {"severity": "warning", "code": "TOBJECTPTR_WITHOUT_UPROPERTY", "path": "A.h", "line": 1, "message": "m1"},
        {"severity": "warning", "code": "TOBJECTPTR_WITHOUT_UPROPERTY", "path": "A.h", "line": 1, "message": "dup"},
        {"severity": "warning", "code": "DELEGATE_BIND_WITHOUT_UNBIND", "path": "B.cpp", "line": 2, "message": "m2"},
    ]
    payload = run_node(f"ux.compactValidationPayload({json.dumps({'findings': findings, 'ok': True})})")
    assert payload["advisoryOnly"] is True
    assert payload["findingCount"] == 2
    assert len(payload["findings"]) == 2
    assert payload["findings"][0]["group"] == "GC/Ownership"
    assert payload["findings"][0]["fixHint"]


def test_compact_validation_payload_advisory_only_false_with_errors() -> None:
    findings = [
        {"severity": "error", "code": "GENERATED_H_NOT_LAST", "path": "A.h", "line": 1, "message": "bad"},
        {"severity": "warning", "code": "DELEGATE_BIND_WITHOUT_UNBIND", "path": "B.cpp", "line": 2, "message": "warn"},
    ]
    payload = run_node(f"ux.compactValidationPayload({json.dumps({'findings': findings, 'ok': False})})")
    assert payload["advisoryOnly"] is False
    assert payload["blockingErrorCount"] == 1
    assert payload["warningCount"] == 1


def test_validation_finding_meta_replication_before_uproperty() -> None:
    replication = run_node("ux.validationFindingMeta('REPLICATED_UPROPERTY_WITHOUT_DOREPLIFETIME')")
    assert replication["group"] == "Networking"
