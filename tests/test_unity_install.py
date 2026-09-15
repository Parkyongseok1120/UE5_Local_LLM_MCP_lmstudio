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
