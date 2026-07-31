from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from automation_report_parser import parse_automation_report  # noqa: E402
from runtime_experiment_runner import (  # noqa: E402
    build_unreal_experiment_plan,
    run_unreal_experiment_plan,
)


def _argument_path(argv: list[str], prefix: str) -> Path:
    value = next(item[len(prefix) :] for item in argv if item.startswith(prefix))
    return Path(value)


def _summary_fields(
    *,
    succeeded: int,
    succeeded_with_warnings: int = 0,
    failed: int = 0,
    not_run: int = 0,
    in_process: int = 0,
) -> dict[str, int]:
    return {
        "succeeded": succeeded,
        "succeededWithWarnings": succeeded_with_warnings,
        "failed": failed,
        "notRun": not_run,
        "inProcess": in_process,
    }


def _write_report(
    argv: list[str],
    *,
    test_name: str = "Demo.Runtime.Reconnect",
    state: str = "Success",
    entries: list[dict] | None = None,
    extra_tests: list[dict] | None = None,
) -> Path:
    report_dir = _argument_path(argv, "-ReportExportPath=")
    report_dir.mkdir(parents=True, exist_ok=True)
    tests = [
        {
            "testDisplayName": test_name.rsplit(".", 1)[-1],
            "fullTestPath": test_name,
            "state": state,
            "deviceInstance": [
                {
                    "deviceName": "Test Device",
                    "instance": "Editor",
                    "platform": "Win64",
                }
            ],
            "entries": entries or [],
        }
    ]
    tests.extend(extra_tests or [])
    payload = {
        "reportCreatedOn": "2026-01-01T00:00:00Z",
        "succeeded": sum(test.get("state") == "Success" for test in tests),
        "succeededWithWarnings": 0,
        "failed": sum(test.get("state") == "Fail" for test in tests),
        "notRun": 0,
        "inProcess": 0,
        "totalDuration": 1.25,
        "tests": tests,
    }
    report_file = report_dir / "index.json"
    report_file.write_text(json.dumps(payload), encoding="utf-8")
    return report_file


def _passing_runner(argv: list[str], **_kwargs):
    _write_report(argv)
    return subprocess.CompletedProcess(argv, 0, stdout="Automation queue complete", stderr="")


def _passing_insights_runner(argv: list[str], **_kwargs):
    command = next(
        item
        for item in argv
        if item.startswith("-ExecOnAnalysisCompleteCmd=")
    )
    match = re.search(r'ExportTimerStatistics "([^"]+)"', command)
    assert match is not None
    csv_path = Path(match.group(1))
    name = csv_path.stem
    rows = {
        "GameThread": [("GameThread", 100, 2.0, 1.5)],
        "RenderThread": [("RenderThread", 100, 1.5, 1.0)],
        "RHIThread": [("RHIThread", 100, 1.0, 0.5)],
        "AllTimers": [
            ("CollectGarbageInternal", 2, 0.2, 0.15),
            ("FAsyncLoadingThread::TickAsyncLoading", 5, 0.4, 0.3),
            ("FMallocBinned3::Malloc", 50, 0.1, 0.08),
        ],
    }[name]
    csv_path.write_text(
        "Name,Count,C.Avg,Incl,I.Min,I.Max,I.Avg,I.Med,Excl\n"
        + "".join(
            f"{timer},{count},1,{inclusive},0,0,0,0,{exclusive}\n"
            for timer, count, inclusive, exclusive in rows
        ),
        encoding="utf-8",
    )
    return subprocess.CompletedProcess(
        argv,
        0,
        stdout="Exported timing statistics to file",
        stderr="",
    )


def test_experiment_plan_uses_argv_bounded_soak_and_fresh_artifact_contract(
    tmp_path: Path,
) -> None:
    plan = build_unreal_experiment_plan(
        editor_cmd="UnrealEditor-Cmd",
        project_file=str(tmp_path / "Demo.uproject"),
        automation_filter="Demo.Runtime.Reconnect",
        trace_channels=["cpu", "frame", "cpu"],
        trace_output="Saved/Profiling/reconnect.utrace",
        soak_iterations=500,
        map_name="Lobby",
        dedicated_server=True,
        unreal_insights_cmd="UnrealInsights",
    )

    assert plan["ok"] is True
    assert plan["soakIterations"] == 100
    assert plan["argv"][0] == "UnrealEditor-Cmd"
    assert plan["argv"][2] == "Lobby"
    assert any("Automation RunTest Demo.Runtime.Reconnect" in arg for arg in plan["argv"])
    assert "-trace=cpu,frame" in plan["argv"]
    assert "-server" in plan["argv"]
    assert plan["traceRequired"] is True
    assert plan["dedicatedMinDurationSec"] == 60


def test_plan_rejects_exec_command_injection_and_missing_required_trace(
    tmp_path: Path,
) -> None:
    plan = build_unreal_experiment_plan(
        editor_cmd="UnrealEditor-Cmd",
        project_file=str(tmp_path / "Demo.uproject"),
        automation_filter="Demo.Runtime; Quit",
        trace_channels=["cpu"],
    )

    assert plan["ok"] is False
    assert any("command separators" in issue for issue in plan["issues"])
    assert any("trace_output is required" in issue for issue in plan["issues"])

    comma_plan = build_unreal_experiment_plan(
        editor_cmd="UnrealEditor-Cmd",
        project_file=str(tmp_path / "Demo.uproject"),
        automation_filter="Demo.Runtime,Quit",
    )
    assert comma_plan["ok"] is False
    assert any("commas" in issue for issue in comma_plan["issues"])


def test_runner_revalidates_execution_bounds_for_untrusted_plan() -> None:
    result = run_unreal_experiment_plan(
        {
            "ok": True,
            "argv": ["UnrealEditor-Cmd", "Demo.uproject"],
            "automationFilter": "Demo.Runtime",
            "soakIterations": 101,
            "timeoutSeconds": 1800,
        }
    )

    assert result["ok"] is False
    assert "soakIterations" in result["error"]


def test_runner_accepts_realistic_ue_report_and_hashes_nonempty_trace(
    tmp_path: Path,
) -> None:
    trace_bytes = b"utrace-binary-evidence"

    def runner(argv: list[str], **_kwargs):
        _write_report(argv)
        trace_path = _argument_path(argv, "-tracefile=")
        trace_path.write_bytes(trace_bytes)
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=(
                "LogAutomationController: Display: Completed with 0 errors\n"
                "No fatal errors or crashes\n"
                "Fatal error count: 0"
            ),
            stderr="",
        )

    plan = build_unreal_experiment_plan(
        editor_cmd="UnrealEditor-Cmd",
        project_file=str(tmp_path / "Demo.uproject"),
        automation_filter="Demo.Runtime.Reconnect",
        trace_channels=["cpu"],
        trace_output="Saved/Profiling/reconnect.utrace",
        unreal_insights_cmd="UnrealInsights",
    )
    result = run_unreal_experiment_plan(
        plan,
        runner=runner,
        insights_runner=_passing_insights_runner,
        insights_platform_name="posix",
    )

    assert result["ok"] is True
    assert result["completedIterations"] == 1
    assert result["proofLevel"] == "RuntimeObserved"
    assert result["runs"][0]["automationReport"]["matchedTestCount"] == 1
    assert result["runs"][0]["trace"]["sizeBytes"] == len(trace_bytes)
    assert result["runs"][0]["trace"]["sha256"] == hashlib.sha256(
        trace_bytes
    ).hexdigest()
    assert result["oracleEvidence"]["errorCount"] == 0
    assert result["oracleEvidence"]["crashCount"] == 0
    metrics = result["runs"][0]["insightsAnalysis"]["metrics"]
    assert metrics["threads"]["GameThread"]["topTimer"] == "GameThread"
    assert metrics["timerGroups"]["gc"]["available"] is True
    assert metrics["timerGroups"]["asyncLoad"]["available"] is True
    assert metrics["timerGroups"]["allocation"]["available"] is True


def test_runner_fails_closed_when_report_is_missing(tmp_path: Path) -> None:
    def runner(argv: list[str], **_kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout="clean exit", stderr="")

    plan = build_unreal_experiment_plan(
        editor_cmd="UnrealEditor-Cmd",
        project_file=str(tmp_path / "Demo.uproject"),
        automation_filter="Demo.Runtime.Reconnect",
    )
    result = run_unreal_experiment_plan(plan, runner=runner)

    assert result["ok"] is False
    assert "does not execute" not in " ".join(result["runs"][0]["issues"])
    assert any(
        "no Automation report JSON" in issue for issue in result["runs"][0]["issues"]
    )


def test_runner_fails_closed_when_primary_report_is_malformed(tmp_path: Path) -> None:
    def runner(argv: list[str], **_kwargs):
        report_dir = _argument_path(argv, "-ReportExportPath=")
        (report_dir / "index.json").write_text("{not-json", encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    plan = build_unreal_experiment_plan(
        editor_cmd="UnrealEditor-Cmd",
        project_file=str(tmp_path / "Demo.uproject"),
        automation_filter="Demo.Runtime.Reconnect",
    )
    result = run_unreal_experiment_plan(plan, runner=runner)

    assert result["ok"] is False
    assert any("malformed" in issue for issue in result["runs"][0]["issues"])


def test_report_parser_supports_capitalized_mapping_layout(tmp_path: Path) -> None:
    report_dir = tmp_path / "Report"
    report_dir.mkdir()
    (report_dir / "Report.json").write_text(
        json.dumps(
            {
                **_summary_fields(succeeded=0, succeeded_with_warnings=1),
                "Results": {
                    "Tests": {
                        "Demo.Runtime.Reconnect": {
                            "Status": "SucceededWithWarnings",
                            "Entries": [],
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    result = parse_automation_report(
        report_dir,
        requested_filter="Demo.Runtime.Reconnect",
    )

    assert result["ok"] is True
    assert result["testCount"] == 1
    assert result["passedCount"] == 1


def test_report_parser_requires_each_requested_selector_to_execute(tmp_path: Path) -> None:
    report_dir = tmp_path / "Report"
    report_dir.mkdir()
    (report_dir / "index.json").write_text(
        json.dumps(
            {
                **_summary_fields(succeeded=1),
                "tests": [
                    {
                        "fullTestPath": "Demo.Runtime.Reconnect",
                        "state": "Success",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = parse_automation_report(
        report_dir,
        requested_filter="Demo.Runtime.Reconnect+Demo.Runtime.Travel",
    )

    assert result["ok"] is False
    assert any("Demo.Runtime.Travel" in issue for issue in result["issues"])


def test_report_parser_matches_unreal_substring_and_startswith_filter_semantics(
    tmp_path: Path,
) -> None:
    report_dir = tmp_path / "Report"
    report_dir.mkdir()
    (report_dir / "index.json").write_text(
        json.dumps(
            {
                **_summary_fields(succeeded=1),
                "tests": [
                    {
                        "fullTestPath": "Project.Network.Demo.Runtime.Reconnect",
                        "state": "Success",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    substring = parse_automation_report(
        report_dir,
        requested_filter="Demo.Runtime.Reconnect",
    )
    starts_with = parse_automation_report(
        report_dir,
        requested_filter="StartsWith:Project.Network",
    )
    group = parse_automation_report(
        report_dir,
        requested_filter="Group:Networking",
    )

    assert substring["ok"] is True
    assert starts_with["ok"] is True
    assert group["ok"] is False
    assert any("cannot be verified" in issue for issue in group["issues"])


def test_report_parser_rejects_interrupted_or_error_bearing_success_records(
    tmp_path: Path,
) -> None:
    report_dir = tmp_path / "Report"
    report_dir.mkdir()
    (report_dir / "index.json").write_text(
        json.dumps(
            {
                **_summary_fields(succeeded=1, failed=1),
                "tests": [
                    {
                        "fullTestPath": "Demo.Runtime.Reconnect",
                        "state": "Success",
                        "errors": 1,
                    },
                    {
                        "fullTestPath": "Demo.Runtime.Travel",
                        "state": "Interrupted",
                        "errors": 0,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    result = parse_automation_report(
        report_dir,
        requested_filter="Demo.Runtime",
    )

    assert result["ok"] is False
    assert result["failedCount"] == 1
    assert result["testErrorCount"] == 1


def test_report_parser_rejects_missing_or_malformed_required_summary(
    tmp_path: Path,
) -> None:
    report_dir = tmp_path / "Report"
    report_dir.mkdir()
    report = {
        **_summary_fields(succeeded=1),
        "failed": "garbage",
        "tests": [
            {
                "fullTestPath": "Demo.Runtime.Reconnect",
                "state": "Success",
            }
        ],
    }
    (report_dir / "index.json").write_text(json.dumps(report), encoding="utf-8")

    malformed = parse_automation_report(
        report_dir,
        requested_filter="Demo.Runtime.Reconnect",
    )
    del report["inProcess"]
    (report_dir / "index.json").write_text(json.dumps(report), encoding="utf-8")
    missing = parse_automation_report(
        report_dir,
        requested_filter="Demo.Runtime.Reconnect",
    )

    assert malformed["ok"] is False
    assert any("malformed" in issue for issue in malformed["issues"])
    assert missing["ok"] is False
    assert any("missing required field" in issue for issue in missing["issues"])


def test_runner_rejects_failed_unrelated_result_even_when_requested_test_passes(
    tmp_path: Path,
) -> None:
    def runner(argv: list[str], **_kwargs):
        _write_report(
            argv,
            extra_tests=[
                {
                    "fullTestPath": "Demo.Runtime.Other",
                    "state": "Fail",
                    "entries": [],
                }
            ],
        )
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    plan = build_unreal_experiment_plan(
        editor_cmd="UnrealEditor-Cmd",
        project_file=str(tmp_path / "Demo.uproject"),
        automation_filter="Demo.Runtime.Reconnect",
    )
    result = run_unreal_experiment_plan(plan, runner=runner)

    assert result["ok"] is False
    assert result["runs"][0]["automationReport"]["failedCount"] == 1


def test_runner_rejects_error_event_in_success_report(tmp_path: Path) -> None:
    def runner(argv: list[str], **_kwargs):
        _write_report(
            argv,
            entries=[
                {
                    "event": {
                        "type": "Error",
                        "message": "Replication invariant was violated",
                    }
                }
            ],
        )
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    plan = build_unreal_experiment_plan(
        editor_cmd="UnrealEditor-Cmd",
        project_file=str(tmp_path / "Demo.uproject"),
        automation_filter="Demo.Runtime.Reconnect",
    )
    result = run_unreal_experiment_plan(plan, runner=runner)

    assert result["ok"] is False
    assert result["runs"][0]["automationReport"]["errorEventCount"] == 1
    assert result["runs"][0]["diagnostics"]["errorCount"] >= 1


def test_runner_rejects_crash_and_structured_log_error_markers(
    tmp_path: Path,
) -> None:
    def runner(argv: list[str], **_kwargs):
        _write_report(argv)
        report_dir = _argument_path(argv, "-ReportExportPath=")
        (report_dir / "Editor.log").write_text(
            "LogNet: Error: Connection state corrupted\n"
            "Assertion failed: NetDriver [File:NetDriver.cpp] [Line: 42]\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout="Unhandled Exception: EXCEPTION_ACCESS_VIOLATION",
            stderr="",
        )

    plan = build_unreal_experiment_plan(
        editor_cmd="UnrealEditor-Cmd",
        project_file=str(tmp_path / "Demo.uproject"),
        automation_filter="Demo.Runtime.Reconnect",
    )
    result = run_unreal_experiment_plan(plan, runner=runner)

    assert result["ok"] is False
    assert result["oracleEvidence"]["errorCount"] >= 3
    assert result["oracleEvidence"]["crashCount"] >= 2


def test_runner_does_not_suppress_fatal_or_unmet_expected_error_markers(
    tmp_path: Path,
) -> None:
    def runner(argv: list[str], **_kwargs):
        _write_report(argv)
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=(
                "LogCore: Fatal: network invariant failed after 0 errors recovered\n"
                "LogAutomationTest: Error: Expected error marker was not found"
            ),
            stderr="",
        )

    plan = build_unreal_experiment_plan(
        editor_cmd="UnrealEditor-Cmd",
        project_file=str(tmp_path / "Demo.uproject"),
        automation_filter="Demo.Runtime.Reconnect",
    )
    result = run_unreal_experiment_plan(plan, runner=runner)

    assert result["ok"] is False
    assert result["oracleEvidence"]["errorCount"] >= 2
    assert result["oracleEvidence"]["crashCount"] >= 1


def test_runner_does_not_treat_zero_or_expected_error_summaries_as_failures(
    tmp_path: Path,
) -> None:
    def runner(argv: list[str], **_kwargs):
        _write_report(argv)
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=(
                "LogAutomationController: Display: Errors: 0\n"
                "LogTemp: Error: 0 errors\n"
                "LogTemp: Error: Expected error marker was found\n"
                "No crashes\n"
                "Expected Fatal error: marker was found\n"
                "Assertion failed count: 0"
            ),
            stderr="",
        )

    plan = build_unreal_experiment_plan(
        editor_cmd="UnrealEditor-Cmd",
        project_file=str(tmp_path / "Demo.uproject"),
        automation_filter="Demo.Runtime.Reconnect",
    )
    result = run_unreal_experiment_plan(plan, runner=runner)

    assert result["ok"] is True
    assert result["runs"][0]["diagnostics"]["errorCount"] == 0


def test_runner_requires_nonempty_trace_when_trace_is_required(tmp_path: Path) -> None:
    def runner(argv: list[str], **_kwargs):
        _write_report(argv)
        _argument_path(argv, "-tracefile=").write_bytes(b"")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    plan = build_unreal_experiment_plan(
        editor_cmd="UnrealEditor-Cmd",
        project_file=str(tmp_path / "Demo.uproject"),
        automation_filter="Demo.Runtime.Reconnect",
        trace_channels=["cpu"],
        trace_output="Saved/Profiling/reconnect.utrace",
        unreal_insights_cmd="UnrealInsights",
    )
    result = run_unreal_experiment_plan(plan, runner=runner)

    assert result["ok"] is False
    assert any("trace artifact is empty" in issue for issue in result["runs"][0]["issues"])


def test_runner_enforces_dedicated_server_minimum_duration(tmp_path: Path) -> None:
    times = iter([0.0, 0.0, 12.0, 12.0])

    plan = build_unreal_experiment_plan(
        editor_cmd="UnrealEditor-Cmd",
        project_file=str(tmp_path / "Demo.uproject"),
        automation_filter="Demo.Runtime.Reconnect",
        dedicated_server=True,
        dedicated_min_duration_seconds=60,
    )
    result = run_unreal_experiment_plan(
        plan,
        runner=_passing_runner,
        clock=lambda: next(times),
    )

    assert result["ok"] is False
    assert any(
        "ended before the required minimum duration" in issue
        for issue in result["runs"][0]["issues"]
    )


def test_runner_accepts_dedicated_server_after_minimum_duration(tmp_path: Path) -> None:
    times = iter([0.0, 0.0, 75.0, 75.0])

    plan = build_unreal_experiment_plan(
        editor_cmd="UnrealEditor-Cmd",
        project_file=str(tmp_path / "Demo.uproject"),
        automation_filter="Demo.Runtime.Reconnect",
        dedicated_server=True,
        dedicated_min_duration_seconds=60,
    )
    result = run_unreal_experiment_plan(
        plan,
        runner=_passing_runner,
        clock=lambda: next(times),
    )

    assert result["ok"] is True
    assert result["runs"][0]["durationSec"] == 75.0


def test_experiment_runner_stops_on_first_failed_artifact_iteration(
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []

    def runner(argv: list[str], **_kwargs):
        calls.append(argv)
        _write_report(argv, state="Success" if len(calls) == 1 else "Fail")
        return subprocess.CompletedProcess(argv, 0, stdout="run", stderr="")

    plan = build_unreal_experiment_plan(
        editor_cmd="UnrealEditor-Cmd",
        project_file=str(tmp_path / "Demo.uproject"),
        automation_filter="Demo.Runtime.Reconnect",
        soak_iterations=3,
    )
    result = run_unreal_experiment_plan(plan, runner=runner)

    assert result["ok"] is False
    assert result["completedIterations"] == 2
    assert result["proofLevel"] == "NeedsRuntimeProof"
