from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import analyze_failure_attempts as analyzer  # noqa: E402


def test_analyze_failure_attempts_classifies_cpp_only_multifile_attempt(tmp_path):
    artifact = tmp_path / "artifacts" / "case_multifile" / "wrapper_run" / "attempt_1"
    artifact.mkdir(parents=True)
    (artifact / "model_response.json").write_text(
        json.dumps(
            {
                "answer": "patched cpp only",
                "patches": [
                    {
                        "path": "Source/HoldoutFixture/Private/HoldoutDelegateOwner.cpp",
                        "oldText": "void HandleScoreChanged()",
                        "newText": "void HandleScoreChanged(int32 Score)",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    cases = {
        "case_multifile": {
            "id": "case_multifile",
            "evalTier": "multifile_refactor",
            "expectedPatchTargets": ["matching cpp/header"],
        }
    }

    report = analyzer.analyze_artifacts(tmp_path / "artifacts", cases)

    assert report["caseCount"] == 1
    assert report["patternCounts"]["cpp_only_no_header"] == 1
    assert report["patternCounts"]["partial_coverage"] == 1


def test_analyze_failure_attempts_can_attach_report_to_kpi(tmp_path):
    kpi = tmp_path / "kpi.json"
    kpi.write_text(json.dumps({"total": 1}), encoding="utf-8")
    report = {"caseCount": 1, "patternCounts": {"patch_application_failed": 1}, "cases": []}

    analyzer.attach_to_kpi(kpi, report)

    data = json.loads(kpi.read_text(encoding="utf-8"))
    assert data["failureAnalysis"]["patternCounts"]["patch_application_failed"] == 1
