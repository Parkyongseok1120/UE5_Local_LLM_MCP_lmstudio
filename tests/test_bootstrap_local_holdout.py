from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bootstrap_local_holdout  # noqa: E402


EXAMPLE = ROOT / "config" / "rag_eval_real_project_holdout_cases.local.example.json"


def test_bootstrap_creates_local_config_from_example(tmp_path):
    output = tmp_path / "rag_eval_real_project_holdout_cases.local.json"

    data = bootstrap_local_holdout.write_local_config(example_path=EXAMPLE, output_path=output)

    assert output.is_file()
    loaded = json.loads(output.read_text(encoding="utf-8"))
    assert loaded["suite"] == "real-project-holdout-local-v0"
    assert len(loaded["cases"]) == 5
    assert data == loaded


def test_bootstrap_does_not_overwrite_without_force(tmp_path):
    output = tmp_path / "local.json"
    output.write_text('{"sentinel": true}', encoding="utf-8")

    try:
        bootstrap_local_holdout.write_local_config(example_path=EXAMPLE, output_path=output)
    except FileExistsError:
        pass
    else:
        raise AssertionError("expected FileExistsError")

    assert json.loads(output.read_text(encoding="utf-8")) == {"sentinel": True}


def test_bootstrap_fills_project_file_and_fixture_root(tmp_path):
    output = tmp_path / "local.json"

    data = bootstrap_local_holdout.write_local_config(
        example_path=EXAMPLE,
        output_path=output,
        project_file="<PATH_TO_PROJECT>.uproject",
        fixture_root="<PATH_TO_FIXTURE_ROOT>",
    )

    for case in data["cases"]:
        assert case["projectFile"] == "<PATH_TO_PROJECT>.uproject"
        assert case["fixtureDir"] == f"<PATH_TO_FIXTURE_ROOT>/{case['id']}"


def test_next_step_text_includes_required_commands_and_model():
    text = bootstrap_local_holdout.next_step_text(Path("config/rag_eval_real_project_holdout_cases.local.json"))

    assert "validate_holdout_cases.py" in text
    assert "build_symbol_graph.py" in text
    assert "eval_pass_at_k.py --metrics-only" in text
    assert "eval_pass_at_k.py --live --require-live" in text
    assert "qwen3.6-27b-heretic-uncensored-finetune-neo-code-di-imatrix-max" in text


def test_bootstrap_cli_writes_temp_config_and_prints_next_steps(tmp_path):
    output = tmp_path / "local.json"

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "bootstrap_local_holdout.py"),
            "--example-config",
            str(EXAMPLE),
            "--output-config",
            str(output),
            "--project-file",
            "<PATH_TO_PROJECT>.uproject",
            "--fixture-root",
            "<PATH_TO_FIXTURE_ROOT>",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=20,
    )

    assert proc.returncode == 0, proc.stderr
    assert output.is_file()
    assert "validate_holdout_cases.py" in proc.stdout
    assert "eval_pass_at_k.py --live --require-live" in proc.stdout
    assert "qwen3.6-27b-heretic-uncensored-finetune-neo-code-di-imatrix-max" in proc.stdout


def test_tracked_bootstrap_files_do_not_contain_user_paths():
    paths = [
        ROOT / "scripts" / "bootstrap_local_holdout.py",
        ROOT / "config" / "rag_eval_real_project_holdout_cases.local.example.json",
    ]

    text = "\n".join(path.read_text(encoding="utf-8", errors="replace") for path in paths)
    assert "C:/Users/" not in text
    assert "C:\\Users\\" not in text
    assert "/Users/" not in text
    assert "/home/" not in text


def test_bootstrap_detects_only_ue58_ubt_candidates():
    text = (ROOT / "scripts" / "bootstrap_local_holdout.py").read_text(encoding="utf-8")

    assert "UE_5.8" in text
    assert "UE_5.7" not in text
    assert "UE_5.6" not in text
