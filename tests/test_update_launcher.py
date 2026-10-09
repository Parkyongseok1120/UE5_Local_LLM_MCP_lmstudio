"""Execute the Windows launcher in a disposable tree, with no installed MCPs."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows batch entry point")


@pytest.fixture
def launcher(tmp_path):
    source = tmp_path / "Release folder"
    (source / "scripts").mkdir(parents=True)
    shutil.copy2(ROOT / "UPDATE.bat", source / "UPDATE.bat")
    recorder = '''import json, os, sys
from pathlib import Path
entry = Path(__file__).name
with Path(os.environ["UPDATE_TEST_LOG"]).open("a", encoding="utf-8") as log:
    log.write(json.dumps({"entry": entry, "args": sys.argv[1:]}) + "\\n")
if os.environ.get("UPDATE_TEST_FAIL") == entry:
    raise SystemExit(1)
'''
    for name in ("install.py", "scripts/update_unity_mcp.py", "scripts/update_unreal_mcp.py"):
        (source / name).write_text(recorder, encoding="utf-8")
    home = tmp_path / "Selected LM Studio"
    home.mkdir()
    config = home / "mcp.json"
    config.write_text('{"mcpServers":{}}', encoding="utf-8")
    state = tmp_path / "Managed state"
    state.mkdir()
    (state / "runtime-python.path").write_text(sys.executable + "\n", encoding="utf-8")
    log = tmp_path / "calls.jsonl"
    env = {**os.environ, "UPDATE_NO_PAUSE": "1", "UPDATE_STATE_HOME": str(state),
           "UPDATE_TEST_LOG": str(log), "LMSTUDIO_HOME": str(tmp_path / "Wrong home")}
    env.pop("UPDATE_PYTHON", None)
    env.pop("UPDATE_TEST_FAIL", None)
    # Only managed Python is available to the launcher. Child Python processes
    # are real; all update/install entry points are inert recorders above.
    env["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
    return source, config, state, log, env


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("explicit_python", [False, True])
def test_update_reuses_python_and_targets_selected_home(launcher, dry_run, explicit_python):
    source, config, state, log, env = launcher
    if explicit_python:
        env["UPDATE_PYTHON"] = sys.executable
        (state / "runtime-python.path").unlink()
    command = [str(source / "UPDATE.bat"), *(["--dry-run"] if dry_run else []), str(config)]
    result = subprocess.run(subprocess.list2cmdline(command), shell=True, cwd=source,
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(calls) == (3 if dry_run else 5)
    assert all("--dry-run" in call["args"] for call in calls[:2])
    assert calls[-1]["entry"] == "install.py"
    args = calls[-1]["args"]
    assert Path(args[args.index("--lmstudio-home") + 1]).resolve() == config.parent.resolve()
    assert Path(args[args.index("--state-home") + 1]).resolve() == state.resolve()
    assert ("--dry-run" in args) == dry_run
    assert config.read_text(encoding="utf-8") == '{"mcpServers":{}}'


@pytest.mark.parametrize("failed", ["update_unity_mcp.py", "update_unreal_mcp.py"])
def test_binding_failure_stops_before_any_dependency_update(launcher, failed):
    source, config, _, log, env = launcher
    env["UPDATE_TEST_FAIL"] = failed
    result = subprocess.run(subprocess.list2cmdline([str(source / "UPDATE.bat"), str(config)]),
                            shell=True, cwd=source, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 1, result.stdout + result.stderr
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert calls and all("--dry-run" in call["args"] for call in calls)
    assert all(call["entry"] != "install.py" for call in calls)


def test_managed_python_with_korean_path_survives_legacy_codepage(launcher):
    source, config, state, log, env = launcher
    runtime = state / "관리 Python"
    venv.create(runtime, with_pip=False)
    python = runtime / "Scripts/python.exe"
    assert python.is_file()
    (state / "runtime-python.path").write_text(str(python) + "\n", encoding="utf-8")
    codepage = source / "codepage.txt"
    wrapper = source / "codepage-test.bat"
    wrapper.write_text(
        '@echo off\nsetlocal\n'
        'for /f "tokens=2 delims=:" %%C in (\'chcp\') do set "TEST_PREVIOUS_CP=%%C"\n'
        'chcp 949 >nul\n'
        f'call "{source / "UPDATE.bat"}" --dry-run "{config}"\n'
        'set "TEST_EXIT=%ERRORLEVEL%"\n'
        f'chcp >"{codepage}"\n'
        'chcp %TEST_PREVIOUS_CP% >nul\n'
        'exit /b %TEST_EXIT%\n', encoding="utf-8")
    result = subprocess.run(subprocess.list2cmdline([str(wrapper)]), shell=True, cwd=source,
                            env=env, capture_output=True, text=True, errors="replace", timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert b"949" in codepage.read_bytes(), "launcher must restore the original code page"
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(calls) == 3 and calls[-1]["entry"] == "install.py"
