from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import report_eval_kpi  # noqa: E402


def _write_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    kpi = {
        "mode": "live",
        "passAt1Count": 1,
        "passAt1Rate": 0.333,
        "passCount": 2,
        "total": 3,
        "passRate": 0.667,
        "averageAttempts": 2.0,
        "failedCaseIds": ["case_c"],
        "sameErrorRepeatedCount": 1,
        "noOpEditCount": 0,
        "repeatedErrorCaseIds": ["case_b"],
        "noOpCaseIds": [],
    }
    baseline = {
        "mode": "live",
        "passAt1Rate": 0.2,
        "passRate": 0.5,
        "averageAttempts": 3.0,
        "sameErrorRepeatedCount": 2,
        "noOpEditCount": 1,
    }
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "retry_state.json").write_text(
        json.dumps(
            {
                "attempts": [
                    {"attempt": 1, "sameErrorRepeated": False, "noOpEdit": False},
                    {"attempt": 2, "sameErrorRepeated": True, "noOpEdit": True},
                ]
            }
        ),
        encoding="utf-8",
    )
    telemetry_rows = [
        {
            "query": "C1083 EnhancedInputComponent.h",
            "selectedMode": "module_fix",
            "sidecarCountsByType": {"module_resolver": 1, "error_route": 1},
            "topSources": {"rag_sidecar": 2, "project_guideline": 1},
            "suspectedModules": ["EnhancedInput"],
            "contextCharCount": 1234,
        },
        {
            "query": "ADemoActor",
            "selectedMode": "compile_fix",
            "sidecarCountsByType": {"symbol_graph": 1},
            "topSources": {"rag_sidecar": 1},
            "suspectedModules": [],
            "contextCharCount": 500,
        },
    ]
    (run_dir / "rag_telemetry.jsonl").write_text(
        "\n".join(json.dumps(row) for row in telemetry_rows) + "\n",
        encoding="utf-8",
    )
    kpi_path = tmp_path / "kpi.json"
    baseline_path = tmp_path / "baseline.json"
    kpi_path.write_text(json.dumps(kpi), encoding="utf-8")
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    return kpi_path, run_dir, baseline_path


def test_report_summary_aggregates_retry_and_rag_telemetry(tmp_path):
    kpi_path, run_dir, baseline_path = _write_fixture(tmp_path)

    summary = report_eval_kpi.build_summary(
        report_eval_kpi.load_json(kpi_path),
        report_eval_kpi.load_run_telemetry(run_dir),
        report_eval_kpi.load_json(baseline_path),
    )

    assert summary["telemetry"]["moduleResolverHintCount"] == 1
    assert summary["telemetry"]["errorRouteHintCount"] == 1
    assert summary["telemetry"]["symbolGraphUsageCount"] == 1
    assert summary["telemetry"]["suspectedModules"] == ["EnhancedInput"]
    assert summary["kpi"]["noOpEditCount"] == 1
    assert summary["baselineDelta"]["passRateDelta"] == 0.167


def test_report_markdown_mentions_observed_telemetry(tmp_path):
    kpi_path, run_dir, baseline_path = _write_fixture(tmp_path)
    summary = report_eval_kpi.build_summary(
        report_eval_kpi.load_json(kpi_path),
        report_eval_kpi.load_run_telemetry(run_dir),
        report_eval_kpi.load_json(baseline_path),
    )

    markdown = report_eval_kpi.render_markdown(
        summary,
        kpi_path=kpi_path,
        run_dir=run_dir,
        baseline_path=baseline_path,
    )

    assert "observed telemetry" in markdown.lower()
    assert "Module resolver hint count: 1" in markdown
    assert "Pass@K" in markdown


def test_missing_optional_telemetry_does_not_fail(tmp_path):
    kpi_path = tmp_path / "kpi.json"
    kpi_path.write_text(json.dumps({"mode": "metrics-only", "total": 0}), encoding="utf-8")

    summary = report_eval_kpi.build_summary(report_eval_kpi.load_json(kpi_path), report_eval_kpi.load_run_telemetry(None))

    assert summary["telemetry"]["telemetryRecords"] == 0


def test_report_labels_real_project_holdout_suite(tmp_path):
    kpi_path, run_dir, _baseline_path = _write_fixture(tmp_path)

    summary = report_eval_kpi.build_summary(
        report_eval_kpi.load_json(kpi_path),
        report_eval_kpi.load_run_telemetry(run_dir),
        suite_name="real-project-holdout-v0",
        suite_type="fixture-only",
    )
    markdown = report_eval_kpi.render_markdown(summary, kpi_path=kpi_path, run_dir=run_dir, baseline_path=None)

    assert summary["suite"] == {"name": "real-project-holdout-v0", "type": "fixture-only"}
    assert "Suite name: `real-project-holdout-v0`" in markdown
    assert "Suite type: `fixture-only`" in markdown
    assert "Suite label: Fixture-only holdout" in markdown
    assert "Fixture-only results do not prove live compile-fix success." in markdown


def test_report_labels_live_ubt_without_improvement_claim(tmp_path):
    kpi_path, run_dir, _baseline_path = _write_fixture(tmp_path)

    summary = report_eval_kpi.build_summary(
        report_eval_kpi.load_json(kpi_path),
        report_eval_kpi.load_run_telemetry(run_dir),
        suite_name="real-project-holdout-v0",
        suite_type="live-ubt",
    )
    markdown = report_eval_kpi.render_markdown(summary, kpi_path=kpi_path, run_dir=run_dir, baseline_path=None)

    assert "Suite label: Live UBT holdout" in markdown
    assert "single-run observations" in markdown
    assert "improved" not in markdown.lower()


def test_live_ubt_label_warns_when_kpi_mode_is_not_live(tmp_path):
    kpi_path = tmp_path / "kpi.json"
    kpi_path.write_text(json.dumps({"mode": "metrics-only", "total": 0}), encoding="utf-8")

    summary = report_eval_kpi.build_summary(
        report_eval_kpi.load_json(kpi_path),
        report_eval_kpi.load_run_telemetry(None),
        suite_name="real-project-holdout-v0",
        suite_type="live-ubt",
    )
    markdown = report_eval_kpi.render_markdown(summary, kpi_path=kpi_path, run_dir=None, baseline_path=None)

    assert "not from live mode" in markdown


def test_baseline_section_uses_observed_delta_wording(tmp_path):
    kpi_path, run_dir, baseline_path = _write_fixture(tmp_path)

    summary = report_eval_kpi.build_summary(
        report_eval_kpi.load_json(kpi_path),
        report_eval_kpi.load_run_telemetry(run_dir),
        report_eval_kpi.load_json(baseline_path),
        suite_name="real-project-holdout-v0",
        suite_type="live-ubt",
    )
    markdown = report_eval_kpi.render_markdown(
        summary,
        kpi_path=kpi_path,
        run_dir=run_dir,
        baseline_path=baseline_path,
    )

    assert "Baseline Delta Observed" in markdown
    assert "model improved" not in markdown.lower()


def test_report_cli_writes_markdown_and_json(tmp_path):
    kpi_path, run_dir, baseline_path = _write_fixture(tmp_path)
    out_md = tmp_path / "report.md"
    out_json = tmp_path / "report.json"

    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "report_eval_kpi.py"),
            str(kpi_path),
            "--run-dir",
            str(run_dir),
            "--baseline",
            str(baseline_path),
            "--out-md",
            str(out_md),
            "--out-json",
            str(out_json),
            "--suite-name",
            "real-project-holdout-v0",
            "--suite-type",
            "fixture-only",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=20,
    )

    assert proc.returncode == 0
    assert out_md.is_file()
    assert out_json.is_file()
    assert "Eval KPI Telemetry Report" in out_md.read_text(encoding="utf-8")
    assert json.loads(out_json.read_text(encoding="utf-8"))["suite"]["type"] == "fixture-only"
