"""Installation/CLI regressions use isolated fixture roots; never a real VM or model."""
from argparse import Namespace
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def fixture(tmp_path):
    install = load("unity_install_entry_test", ROOT / "install.py")
    from installer import unity_install
    project = tmp_path.resolve() / "Different Project"
    for d in ["Assets", "Packages", "ProjectSettings"]:
        (project / d).mkdir(parents=True)
    (project / "Packages/manifest.json").write_text('{"dependencies":{"keep":"1"}}')
    (project / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.0f1")
    args = Namespace(unity_project=project, active_project=None, unity_replace_bridge=False,
                     state_home=tmp_path.resolve() / "state", unity_mcp_config=None,
                     runtime_node=shutil.which("node"), runtime_python=sys.executable,
                     dry_run=True, skip_deps=True, unity_editor=None,
                     unity_dotnet=None, unity_symbol_worker=None)
    return unity_install, install, project, args


def test_unity_dry_run_has_no_writes_and_no_model_ready_claim(fixture):
    u, i, project, args = fixture
    before = (project / "Packages/manifest.json").read_bytes()
    report = u.install_unity(args, ROOT, i.Transaction, i.InstallLock)
    assert report["ok"] and not report["ready"]
    assert report["sameHostRequired"] and report["modelConnection"] == "not_verified"
    assert not args.state_home.exists()
    assert (project / "Packages/manifest.json").read_bytes() == before


def test_interactive_unity_selection_reaches_installer(fixture, monkeypatch, capsys):
    _, i, project, original = fixture
    args = i.build_parser().parse_args(["--profile", "custom", "--components", "unity", "--dry-run", "--state-home", str(original.state_home)])
    responses = iter(["n", "y", "n", "n", "", "y"])
    monkeypatch.setattr(i.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(responses))
    picks = []
    def pick(kind, initial):
        picks.append(kind)
        return project
    monkeypatch.setattr(i, "_pick_indexing_target", pick)
    resolved = i._resolve_components(args)
    report = i.install(args, resolved_components=resolved)
    assert resolved[1] == {"unity"}
    assert picks == ["unity"]
    assert args.unity_project == project
    assert report["ok"] and report["engine"] == "unity" and not report["ready"]
    assert report["project"] == str(project)
    assert not args.state_home.exists()
    output = capsys.readouterr().out
    assert "Unity project setup" in output and str(project) in output
    assert "compactor will be installed" not in output
    assert "5. UNITY" not in output


def test_unity_setup_preserves_explicit_paths(fixture, monkeypatch):
    _, i, project, _ = fixture
    args = i.build_parser().parse_args(["--profile", "custom", "--components", "unity", "--unity-project", str(project),
                                       "--unity-editor", sys.executable])
    monkeypatch.setattr(i.sys.stdin, "isatty", lambda: True)
    prompts = []
    def answer(prompt):
        prompts.append(prompt)
        return "y" if "Unity MCP" in prompt or "Continue" in prompt else "n"
    monkeypatch.setattr("builtins.input", answer)
    monkeypatch.setattr(i, "_pick_indexing_target", lambda *a: pytest.fail("explicit project must be preserved"))
    assert i._resolve_components(args) == ("custom", {"unity"})
    assert args.unity_project == project
    assert args.unity_editor == Path(sys.executable)
    assert len(prompts) == 5 and "Continue" in prompts[-1]


def test_unity_picker_rejects_non_project_and_can_retry(fixture, monkeypatch):
    _, i, project, _ = fixture
    args = i.build_parser().parse_args(["--profile", "custom", "--components", "unity", "--unity-editor", sys.executable])
    args.workspace_root = [project.parent]
    selections = iter([str(project.parent), str(project)])
    monkeypatch.setattr(i, "_pick_with_tkinter", lambda *a: next(selections))
    monkeypatch.setattr(i, "_pick_with_osascript", lambda *a: next(selections))
    responses = iter(["", "y"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(responses))
    i._interactive_unity_setup(args)
    assert args.unity_project == project


def test_unity_picker_cancel_does_not_select_default_project(fixture, monkeypatch):
    _, i, _, _ = fixture
    args = i.build_parser().parse_args(["--profile", "custom", "--components", "unity"])
    args.workspace_root = []
    monkeypatch.setattr(i, "_pick_indexing_target", lambda *a: None)
    responses = iter(["", "n"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(responses))
    with pytest.raises(RuntimeError, match="cancelled"):
        i._interactive_unity_setup(args)
    assert args.unity_project is None


def test_unity_picker_unavailable_accepts_quoted_path(fixture, monkeypatch):
    _, i, project, _ = fixture
    args = i.build_parser().parse_args(["--profile", "custom", "--components", "unity"])
    args.workspace_root = []
    monkeypatch.setattr(i, "_pick_indexing_target", lambda *a: None)
    responses = iter([f'"{project}"', f'"{sys.executable}"'])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(responses))
    i._interactive_unity_setup(args)
    assert args.unity_project == project
    assert args.unity_editor == Path(sys.executable).resolve()


def test_unity_profile_yes_never_prompts(fixture, monkeypatch):
    _, i, project, _ = fixture
    args = i.build_parser().parse_args(["--profile", "custom", "--components", "unity", "--yes", "--unity-project", str(project)])
    monkeypatch.setattr(i.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("--yes must not prompt"))
    assert i._resolve_components(args) == ("custom", {"unity"})


def test_unity_mixed_components_accepted(fixture):
    _, i, _, _ = fixture
    args = i.build_parser().parse_args(["--profile", "custom", "--components", "unity,unreal", "--yes"])
    assert i._resolve_components(args)[1] == {"unity", "unreal", "context_compactor"}


@pytest.mark.parametrize("unreal,unity", [(True, True), (True, False), (False, True), (False, False)])
def test_engine_choices_precede_project_setup_in_unreal_unity_order(fixture, monkeypatch, capsys, unreal, unity):
    _, i, project, _ = fixture
    args = i.build_parser().parse_args(["--profile", "standard"])
    events = []
    monkeypatch.setattr(i.sys.stdin, "isatty", lambda: True)
    def answer(prompt):
        if "Unreal MCP" in prompt:
            events.append("choose-unreal")
            return "y" if unreal else "n"
        if "Unity MCP" in prompt:
            events.append("choose-unity")
            return "y" if unity else "n"
        return "n"
    monkeypatch.setattr("builtins.input", answer)
    monkeypatch.setattr(i, "_interactive_project_indexing", lambda a: events.append("unreal-project"))
    monkeypatch.setattr(i, "_interactive_engine_selection", lambda a: None)
    monkeypatch.setattr(i, "_interactive_rag_indexing", lambda a: None)
    monkeypatch.setattr(i, "_interactive_agent_authority", lambda: False)
    def unity_setup(a):
        events.append("unity-project")
        assert a.active_project is None
        a.unity_project = project
    monkeypatch.setattr(i, "_interactive_unity_setup", unity_setup)
    monkeypatch.setattr(i, "_confirm_interactive_install", lambda *a: events.append("summary"))
    _, components = i._resolve_components(args)
    assert ("unreal" in components) == unreal
    assert ("unity" in components) == unity
    assert events == ["choose-unreal", "choose-unity"] + (["unreal-project"] if unreal else []) + (["unity-project"] if unity else []) + ["summary"]
    assert "5. UNITY" not in capsys.readouterr().out


def test_unity_setup_does_not_reuse_unreal_project(fixture, monkeypatch):
    _, i, project, _ = fixture
    args = i.build_parser().parse_args(["--unity-editor", sys.executable])
    unreal = project.parent / "Unreal.uproject"
    unreal.write_text("{}")
    args.active_project = unreal
    args.workspace_root = [project.parent]
    monkeypatch.setattr(i, "_pick_indexing_target", lambda *a: project)
    i._interactive_unity_setup(args)
    assert args.active_project == unreal and args.unity_project == project


def test_profile_menu_has_no_separate_unity_choice(fixture, monkeypatch, capsys):
    _, i, _, _ = fixture
    monkeypatch.setattr("builtins.input", lambda *a: "2")
    assert i._interactive_profile() == "standard"
    assert "5. UNITY" not in capsys.readouterr().out


@pytest.fixture
def combined_install(fixture, monkeypatch):
    u, i, project, original = fixture
    base = project.parent
    unreal = base / "Unreal Game" / "Game.uproject"
    unreal.parent.mkdir()
    unreal.write_text("{}")
    args = i.build_parser().parse_args([
        "--profile", "custom", "--components", "unreal,unity", "--yes", "--skip-deps",
        "--skip-context-compactor", "--allow-skip-context-compactor",
        "--state-home", str(original.state_home), "--lmstudio-home", str(base / "lmstudio"),
        "--codex-home", str(base / "codex"), "--workspace-root", str(unreal.parent),
        "--active-project", str(unreal), "--unity-project", str(project),
    ])
    monkeypatch.delenv("UNREAL_ENGINE_ROOT", raising=False)
    monkeypatch.setattr(i, "_detect_engine_root", lambda *a: None)
    monkeypatch.setattr(i, "_verify_unreal_agent_dependency", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(i, "_live_server_status", lambda *a: {"reachable": False})
    def run(command, **kwargs):
        if "check_unity_install.js" in " ".join(command):
            return subprocess.CompletedProcess(command, 0, '{"connection":"disconnected","workerConfigured":true}', "")
        return subprocess.CompletedProcess(command, 0, "v24.0.0", "")
    monkeypatch.setattr(u, "run", run)
    def worker(*worker_args):
        worker_args[-1].append("dotnet-publish-symbol-worker")
        return Path(sys.executable), base / "worker.dll"
    monkeypatch.setattr(u, "worker", worker)
    config = args.lmstudio_home / "mcp.json"
    config.parent.mkdir()
    config.write_bytes(b'{"mcpServers":{"keep":{"command":"untouched"}}}')
    return u, i, project, unreal, args, config


@pytest.mark.parametrize("agent_mode", [False, True])
def test_combined_install_registers_both_and_rolls_back_one_journal(combined_install, agent_mode):
    _, i, project, unreal, args, config = combined_install
    manifest = project / "Packages/manifest.json"
    before = manifest.read_bytes(), config.read_bytes()
    args.enable_agent_mode = args.accept_agent_risk = agent_mode
    report = i.install(args)
    assert report["ok"] and set(report["components"]) == {"unreal", "unity"}
    assert report["activeProject"] == str(unreal)
    assert report["unity"]["project"] == str(project)
    assert report["unity"]["ok"] and not report["unity"]["ready"]
    assert report["unity"]["mcpConfig"] == str(config)
    assert "journal" not in report["unity"]
    assert "unity-dotnet-publish-symbol-worker" in report["externalActions"]
    servers = json.loads(config.read_text())["mcpServers"]
    assert set(servers) == {"keep", "unreal-agent", "unreal-rag", "unity-tools"}
    assert servers["unity-tools"]["env"]["UNITY_PROJECT_ROOT"] == str(project)
    assert servers["unity-tools"]["env"]["ALLOW_WRITE"] == "0"
    assert servers["unity-tools"]["env"]["ALLOW_COMMANDS"] == "0"
    assert servers["unreal-agent"]["env"]["ALLOW_WRITE"] == ("1" if agent_mode else "0")
    journal = json.loads(Path(report["journal"]).read_text())
    assert any(a["target"] == str(manifest) for a in journal["actions"])
    i.rollback_last_install(args.state_home)
    assert (manifest.read_bytes(), config.read_bytes()) == before
    assert not (args.state_home / "runtime-python.path").exists()


def test_combined_unity_failure_restores_both_engines(combined_install, monkeypatch):
    u, i, project, _, args, config = combined_install
    manifest = project / "Packages/manifest.json"
    before = manifest.read_bytes(), config.read_bytes()
    original_run = u.run
    def fail(command, **kwargs):
        if "check_unity_install.js" in " ".join(command):
            raise RuntimeError("Unity connection check failed")
        return original_run(command, **kwargs)
    monkeypatch.setattr(u, "run", fail)
    with pytest.raises(RuntimeError, match="unity-dotnet-publish-symbol-worker"):
        i.install(args)
    assert (manifest.read_bytes(), config.read_bytes()) == before
    assert not (args.lmstudio_home / "config/unreal-workspace.json").exists()
    assert not (args.state_home / "install-journal.json").exists()
    assert not (args.state_home / "install.lock").exists()


def test_combined_install_requires_separate_unity_project_before_writes(combined_install):
    _, i, _, _, args, config = combined_install
    args.unity_project = None
    before = config.read_bytes()
    with pytest.raises(ValueError, match="--unity-project"):
        i.install(args)
    assert config.read_bytes() == before
    assert not args.state_home.exists()


def test_combined_dry_run_keeps_both_projects_unchanged(combined_install):
    _, i, project, _, args, config = combined_install
    args.dry_run = True
    manifest = project / "Packages/manifest.json"
    before = manifest.read_bytes(), config.read_bytes()
    report = i.install(args)
    assert report["ok"] and report["unity"]["verification"] == "not_run"
    assert (manifest.read_bytes(), config.read_bytes()) == before
    assert not args.state_home.exists()


def test_combined_install_honors_explicit_unity_config(combined_install):
    _, i, project, _, args, config = combined_install
    separate = project.parent / "separate-host" / "mcp.json"
    args.unity_mcp_config = separate
    report = i.install(args)
    assert report["unity"]["mcpConfig"] == str(separate)
    assert "unity-tools" not in json.loads(config.read_text())["mcpServers"]
    assert "unity-tools" in json.loads(separate.read_text())["mcpServers"]
    i.rollback_last_install(args.state_home)
    assert not separate.exists()


@pytest.mark.skipif(shutil.which("bash") is None, reason="Bash is required for the Intel Mac launcher")
def test_intel_unity_dry_run_keeps_mcp_on_editor_host(fixture):
    _, _, project, _ = fixture
    r = subprocess.run(["bash", str(ROOT / "install-intel-mac.sh"), "--engine", "unity", "--project", str(project), "--vm-name", "custom-unity-vm", "--model", "custom/model", "--dry-run"], env={**os.environ, "INTEL_MAC_INSTALLER_TEST": "1"}, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "custom-unity-vm" in r.stdout and "custom/model" in r.stdout
    assert "local" in r.stdout.lower()


@pytest.mark.skipif(sys.platform != "darwin", reason="Intel installer uses macOS tar; all VM operations below are mocked")
def test_mock_intel_installer_generated_launcher_preserves_selection(tmp_path):
    kit = tmp_path / "kit"
    (kit / "scripts").mkdir(parents=True)
    shutil.copyfile(ROOT / "install-intel-mac.sh", kit / "install-intel-mac.sh")
    shutil.copyfile(ROOT / "scripts/lmstudio-headless-cli.sh", kit / "scripts/lmstudio-headless-cli.sh")
    project = tmp_path / "Game"; project.mkdir(); (project / "Game.uproject").write_text("{}")
    engine = tmp_path / "Engine"; engine.mkdir()
    fake = tmp_path / "limactl"; log = tmp_path / "calls.jsonl"
    fake.write_text(f'''#!{sys.executable}
import sys,json
from pathlib import Path
with Path({str(log)!r}).open("a") as f: f.write(json.dumps(sys.argv[1:])+"\\n")
c=" ".join(sys.argv[1:])
if sys.argv[1] == "list": print(sys.argv[-1])
elif "tar -xf" in c: sys.stdin.buffer.read()
elif "link status" in c: print("Status: Online\\nStatus: connected")
elif "--list-tools" in c: print("evidence-first\\tread_file\\nunreal-rag\\tunreal_rag_health\\nunreal-agent\\tunreal_project_info")
elif "--verify-install" in c: print('{{"ready":true,"mcpHealthVerified":true,"modelToolRequestVerified":true}}')
''')
    fake.chmod(0o755)
    env = {**os.environ, "LIMACTL_BIN": str(fake), "INTEL_MAC_INSTALLER_TEST": "1"}
    env.pop("LMSTUDIO_VM", None); env.pop("LMSTUDIO_MODEL", None)
    endpoint = "http://127.0.0.1:2345/v1/chat/completions"
    r = subprocess.run(["bash", str(kit / "install-intel-mac.sh"), "--project", str(project), "--engine-root", str(engine), "--vm-name", "configured-vm", "--model", "configured/model", "--model-endpoint", endpoint, "--skip-rag-build", "--skip-login"], env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stdout + r.stderr
    before = len(log.read_text().splitlines())
    c = subprocess.run(["bash", str(project / "lmstudio-cli.sh"), "hello"], env=env, capture_output=True, text=True)
    assert c.returncode == 0, c.stderr
    calls = [json.loads(s) for s in log.read_text().splitlines()]
    assert any("--verify-install unreal_rag_health" in " ".join(row) for row in calls[:before])
    assert calls[before][1] == "configured-vm" and "configured/model" in calls[before]
    assert endpoint in calls[before]


def test_unity_binding_requires_explicit_replacement(fixture):
    u, i, project, args = fixture
    (project / "Packages/manifest.json").write_text('{"dependencies":{"com.evidencefirst.unity-bridge":"file:elsewhere"}}')
    with pytest.raises(ValueError, match="Another Bridge"):
        u.install_unity(args, ROOT, i.Transaction, i.InstallLock)
    args.unity_replace_bridge = True
    assert u.install_unity(args, ROOT, i.Transaction, i.InstallLock)["ok"]


def test_unity_failed_preflight_does_not_bind_project(fixture, monkeypatch):
    u, i, project, args = fixture
    args.dry_run = False
    before = (project / "Packages/manifest.json").read_bytes()
    def failed(*a, **k):
        raise RuntimeError("runtime unavailable")
    monkeypatch.setattr(u, "run", failed)
    with pytest.raises(RuntimeError, match="runtime unavailable"):
        u.install_unity(args, ROOT, i.Transaction, i.InstallLock)
    assert (project / "Packages/manifest.json").read_bytes() == before


def test_unity_postwrite_failure_rolls_back_project_and_config(fixture, monkeypatch):
    u, i, project, args = fixture
    args.dry_run = False
    args.unity_mcp_config = args.state_home / "mcp.json"
    args.state_home.mkdir()
    original = b'{"mcpServers":{"keep":{"command":"keep"}}}'
    args.unity_mcp_config.write_bytes(original)
    manifest = (project / "Packages/manifest.json").read_bytes()
    def run(command, **kw):
        if "check_unity_install.js" in " ".join(command):
            raise RuntimeError("verification failed")
        return subprocess.CompletedProcess(command, 0, "v24.0.0", "")
    monkeypatch.setattr(u, "run", run)
    monkeypatch.setattr(u, "worker", lambda *a: (Path(sys.executable), ROOT / "fake-worker.dll"))
    with pytest.raises(RuntimeError, match="verification failed"):
        u.install_unity(args, ROOT, i.Transaction, i.InstallLock)
    assert args.unity_mcp_config.read_bytes() == original
    assert (project / "Packages/manifest.json").read_bytes() == manifest
    assert not (args.state_home / "runtime-python.path").exists()


@pytest.mark.parametrize("health", [{"ready": False}, {"ready": "true"}, {"ready": True, "okForChat": False}])
def test_health_false_is_not_install_success(health):
    chat = load("headless_health_false_test", ROOT / "scripts/headless_mcp_chat.py")
    owner = Namespace(call=lambda *_: {"structuredContent": health})
    with pytest.raises(chat.McpError, match="not ready"):
        chat.verify_install("unreal_rag_health", {"unreal_rag_health": owner}, [], "model", "unused", 1)


def test_model_plaintext_is_not_install_success(monkeypatch):
    chat = load("headless_model_probe_test", ROOT / "scripts/headless_mcp_chat.py")
    owner = Namespace(call=lambda *_: {"structuredContent": {"ready": True}})
    monkeypatch.setattr(chat, "post_chat", lambda *_: {"choices": [{"message": {"content": "false"}}]})
    with pytest.raises(chat.McpError, match="plain-text"):
        chat.verify_install("unreal_rag_health", {"unreal_rag_health": owner}, [], "model", "unused", 1)
    monkeypatch.setattr(chat, "post_chat", lambda *_: {"choices": [{"message": {"tool_calls": [{"function": {"name": "unreal_rag_health", "arguments": "{}"}}]}}]})
    assert chat.verify_install("unreal_rag_health", {"unreal_rag_health": owner}, [], "model", "unused", 1)["ready"]


@pytest.mark.skipif(os.name == "nt", reason="Bash launcher")
def test_launcher_preserves_vm_model_and_does_not_execute_config(tmp_path):
    cli = tmp_path / "lmstudio-cli.sh"
    shutil.copyfile(ROOT / "scripts/lmstudio-headless-cli.sh", cli)
    fake = tmp_path / "limactl"
    log = tmp_path / "calls.json"
    fake.write_text(f"#!{sys.executable}\nimport json,sys\nfrom pathlib import Path\nPath({str(log)!r}).write_text(json.dumps(sys.argv[1:]))\n")
    fake.chmod(0o755)
    injected = tmp_path / "must-not-exist"
    model = f"custom/model$(touch {injected})"
    (tmp_path / "lmstudio-cli.conf").write_text(f"VM=custom-vm\nMODEL={model}\nLIMACTL={fake}\nENGINE=unreal\n")
    env = {k: v for k, v in os.environ.items() if k not in {"LIMACTL_BIN", "LMSTUDIO_VM", "LMSTUDIO_MODEL"}}
    completed = subprocess.run(["bash", str(cli), "hello"], env=env, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    call = json.loads(log.read_text())
    assert call[1] == "custom-vm" and model in call
    assert "runtime-python.path" in " ".join(call) and "3.12.13" not in " ".join(call)
    assert not injected.exists()
