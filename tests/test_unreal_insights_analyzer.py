from __future__ import annotations

import csv
import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from unreal_insights_analyzer import (  # noqa: E402
    build_unreal_insights_analysis_plan,
    discover_unreal_insights,
    parse_timer_statistics_csv,
    run_unreal_insights_analysis,
)


def _csv_from_argv(argv: list[str]) -> Path:
    command = next(
        item
        for item in argv
        if item.startswith("-ExecOnAnalysisCompleteCmd=")
    )
    match = re.search(r'ExportTimerStatistics "([^"]+)"', command)
    assert match is not None
    return Path(match.group(1))


def _write_timer_csv(path: Path, rows: list[tuple[str, int, float, float]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "Name",
                "Count",
                "C.Avg",
                "Incl",
                "I.Min",
                "I.Max",
                "I.Avg",
                "I.Med",
                "Excl",
            ]
        )
        for name, count, inclusive, exclusive in rows:
            writer.writerow(
                [name, count, 1, inclusive, inclusive, inclusive, inclusive, inclusive, exclusive]
            )


def _passing_runner(argv: list[str], **_kwargs):
    csv_path = _csv_from_argv(argv)
    rows = {
        "GameThread": [("GameThread", 120, 3.0, 2.5)],
        "RenderThread": [("RenderThread", 120, 2.0, 1.5)],
        "RHIThread": [("RHIThread", 120, 1.0, 0.8)],
        "AllTimers": [
            ("CollectGarbageInternal", 2, 0.2, 0.1),
            ("FAsyncLoadingThread::TickAsyncLoading", 5, 0.4, 0.3),
            ("FMallocBinned3::Malloc", 100, 0.05, 0.04),
            ("Timer,WithComma", 1, 0.01, 0.01),
        ],
    }[csv_path.stem]
    _write_timer_csv(csv_path, rows)
    return subprocess.CompletedProcess(
        argv,
        0,
        stdout="Exported timing statistics to file",
        stderr="",
    )


def test_analysis_plan_matches_supported_unreal_insights_cli_and_uses_argv(
    tmp_path: Path,
) -> None:
    trace = tmp_path / "capture.utrace"
    plan = build_unreal_insights_analysis_plan(
        unreal_insights_cmd="UnrealInsights",
        trace_file=trace,
        output_dir=tmp_path / "exports",
    )

    assert plan["ok"] is True
    assert len(plan["exports"]) == 4
    game_argv = plan["exports"][0]["argv"]
    assert game_argv[0] == "UnrealInsights"
    assert game_argv[1] == f"-OpenTraceFile={trace}"
    assert "-unattended" in game_argv
    assert "-autoquit" in game_argv
    assert "-noui" in game_argv
    assert "-nullrhi" in game_argv
    assert any(
        "TimingInsights.ExportTimerStatistics" in item
        and '-threads="GameThread*"' in item
        for item in game_argv
    )


def test_analysis_plan_rejects_inner_command_separators(tmp_path: Path) -> None:
    plan = build_unreal_insights_analysis_plan(
        unreal_insights_cmd="UnrealInsights",
        trace_file=tmp_path / "unsafe;quit.utrace",
        output_dir=tmp_path / "exports",
    )

    assert plan["ok"] is False
    assert any("semicolons" in issue for issue in plan["issues"])


def test_parse_timer_statistics_csv_requires_real_rows_and_hashes_artifact(
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "timers.csv"
    _write_timer_csv(
        csv_path,
        [("Timer,WithComma", 3, 0.75, 0.5)],
    )

    result = parse_timer_statistics_csv(csv_path)

    assert result["ok"] is True
    assert result["rowCount"] == 1
    assert result["rows"][0]["name"] == "Timer,WithComma"
    assert result["rows"][0]["inclusiveTimeSec"] == 0.75
    assert result["sha256"] == hashlib.sha256(csv_path.read_bytes()).hexdigest()


def test_parse_timer_statistics_csv_fails_closed_for_header_only_or_bad_numbers(
    tmp_path: Path,
) -> None:
    header_only = tmp_path / "header.csv"
    header_only.write_text("Name,Count,Incl,Excl\n", encoding="utf-8")
    bad_number = tmp_path / "bad.csv"
    bad_number.write_text("Name,Count,Incl,Excl\nTimer,1,nan,0\n", encoding="utf-8")

    header_result = parse_timer_statistics_csv(header_only)
    bad_result = parse_timer_statistics_csv(bad_number)

    assert header_result["ok"] is False
    assert bad_result["ok"] is False


def test_unreal_insights_analysis_produces_thread_and_timer_group_metrics(
    tmp_path: Path,
) -> None:
    trace = tmp_path / "capture.utrace"
    trace.write_bytes(b"realistic-placeholder-trace")
    plan = build_unreal_insights_analysis_plan(
        unreal_insights_cmd="UnrealInsights",
        trace_file=trace,
        output_dir=tmp_path / "exports",
    )
    result = run_unreal_insights_analysis(
        plan,
        runner=_passing_runner,
        platform_name="posix",
    )

    assert result["ok"] is True
    assert len(result["runs"]) == 4
    assert result["traceSha256"] == hashlib.sha256(trace.read_bytes()).hexdigest()
    assert result["metrics"]["threads"]["GameThread"] == {
        "available": True,
        "timerCount": 1,
        "instanceCount": 120,
        "maxInclusiveTimeSec": 3.0,
        "topTimer": "GameThread",
    }
    assert result["metrics"]["threads"]["RenderThread"]["available"] is True
    assert result["metrics"]["threads"]["RHIThread"]["available"] is True
    assert result["metrics"]["timerGroups"]["gc"]["timerCount"] == 1
    assert result["metrics"]["timerGroups"]["asyncLoad"]["timerCount"] == 1
    assert result["metrics"]["timerGroups"]["allocation"]["timerCount"] == 1
    assert all(run["csv"]["sha256"] for run in result["runs"])
    assert "does not parse the binary format itself" in result["proofBoundary"]


def test_analysis_fails_closed_when_export_is_missing_and_stops(
    tmp_path: Path,
) -> None:
    trace = tmp_path / "capture.utrace"
    trace.write_bytes(b"trace")
    calls = 0

    def runner(argv: list[str], **_kwargs):
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    plan = build_unreal_insights_analysis_plan(
        unreal_insights_cmd="UnrealInsights",
        trace_file=trace,
        output_dir=tmp_path / "exports",
    )
    result = run_unreal_insights_analysis(
        plan,
        runner=runner,
        platform_name="posix",
    )

    assert result["ok"] is False
    assert calls == 1
    assert any("missing or malformed" in issue for issue in result["issues"])


def test_analysis_fails_closed_on_export_error_diagnostic_even_with_csv(
    tmp_path: Path,
) -> None:
    trace = tmp_path / "capture.utrace"
    trace.write_bytes(b"trace")

    def runner(argv: list[str], **_kwargs):
        _write_timer_csv(_csv_from_argv(argv), [("GameThread", 1, 0.1, 0.1)])
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout="Failed to export timing statistics!",
            stderr="",
        )

    plan = build_unreal_insights_analysis_plan(
        unreal_insights_cmd="UnrealInsights",
        trace_file=trace,
        output_dir=tmp_path / "exports",
    )
    result = run_unreal_insights_analysis(
        plan,
        runner=runner,
        platform_name="posix",
    )

    assert result["ok"] is False
    assert any("diagnostics report" in issue for issue in result["issues"])


def test_windows_raw_adapter_executes_export_and_keeps_memalloc_errors_nonblocking(
    tmp_path: Path,
) -> None:
    trace = tmp_path / "capture.utrace"
    trace.write_bytes(b"trace")
    invocations: list[str] = []

    def runner(command_line: str, **kwargs):
        assert isinstance(command_line, str)
        assert kwargs["shell"] is False
        assert kwargs["encoding"] == "utf-8"
        assert kwargs["errors"] == "replace"
        invocations.append(command_line)
        match = re.search(r"ExportTimerStatistics ([^\s]+\.csv)", command_line)
        assert match is not None
        csv_path = Path(match.group(1))
        _write_timer_csv(csv_path, [(csv_path.stem, 1, 0.1, 0.1)])
        return subprocess.CompletedProcess(
            command_line,
            0,
            stdout=(
                f"TimingInsights.ExportTimerStatistics {csv_path.name}\n"
                "LogTraceServices: Error: [MemAlloc] Invalid Tag on Thread 2\n"
                f'Exported timing statistics to file ("{csv_path.name}")'
            ),
            stderr="",
        )

    plan = build_unreal_insights_analysis_plan(
        unreal_insights_cmd=r"C:\UE 5.8\UnrealInsights.exe",
        trace_file=trace,
        output_dir=tmp_path / "exports",
    )
    result = run_unreal_insights_analysis(
        plan,
        runner=runner,
        platform_name="nt",
    )

    assert result["ok"] is True
    assert len(invocations) == 4
    assert all('-OpenTraceFile="' in command for command in invocations)
    assert all('-ExecOnAnalysisCompleteCmd="' in command for command in invocations)
    assert result["runs"][0]["commandObserved"] is True
    assert result["runs"][0]["analysisDiagnostics"]["count"] == 1
    assert result["runs"][0]["analysisDiagnostics"]["nonBlocking"] is True


def test_analysis_fails_closed_for_missing_trace_or_stale_output_directory(
    tmp_path: Path,
) -> None:
    missing_plan = build_unreal_insights_analysis_plan(
        unreal_insights_cmd="UnrealInsights",
        trace_file=tmp_path / "missing.utrace",
        output_dir=tmp_path / "missing-exports",
    )
    missing = run_unreal_insights_analysis(
        missing_plan,
        runner=_passing_runner,
        platform_name="posix",
    )

    trace = tmp_path / "capture.utrace"
    trace.write_bytes(b"trace")
    stale_dir = tmp_path / "stale"
    stale_dir.mkdir()
    stale_plan = build_unreal_insights_analysis_plan(
        unreal_insights_cmd="UnrealInsights",
        trace_file=trace,
        output_dir=stale_dir,
    )
    stale = run_unreal_insights_analysis(
        stale_plan,
        runner=_passing_runner,
        platform_name="posix",
    )

    assert missing["ok"] is False
    assert stale["ok"] is False
    assert any("could not be prepared" in issue for issue in stale["issues"])


def test_discover_unreal_insights_beside_editor(tmp_path: Path) -> None:
    editor = tmp_path / "UnrealEditor-Cmd.exe"
    insights = tmp_path / "UnrealInsights.exe"
    insights.write_bytes(b"binary")

    result = discover_unreal_insights(editor_cmd=str(editor))

    assert result == str(insights)
