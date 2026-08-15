from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from control_runtime_identity import (
    ControlRuntimeMismatch,
    build_runtime_manifest,
    component_identity,
    verify_runtime_component,
)


ROOT = Path(__file__).resolve().parents[1]


def test_runtime_manifest_covers_every_control_component() -> None:
    manifest = build_runtime_manifest(ROOT)

    assert manifest["protocolVersion"] == 2
    assert set(manifest["components"]) == {"agent", "rag", "compactor"}
    assert manifest["components"]["agent"]["componentVersion"] == "0.3.16"
    for name, identity in manifest["components"].items():
        assert identity["component"] == name
        assert len(identity["buildHash"]) == 64
        assert identity["componentVersion"]
        assert identity["protocolVersion"] == 2
        assert "gitCommit" in identity


@pytest.mark.parametrize("component", ["agent", "rag", "compactor"])
def test_matching_runtime_manifest_verifies(component: str, tmp_path: Path) -> None:
    manifest_path = tmp_path / "control-runtime.json"
    manifest_path.write_text(
        json.dumps(build_runtime_manifest(ROOT)),
        encoding="utf-8",
    )

    result = verify_runtime_component(
        component,
        manifest_path=manifest_path,
        repository_root=ROOT,
        required=True,
    )

    assert result["verified"] is True
    assert result["running"]["buildHash"] == result["expected"]["buildHash"]


def test_tampered_runtime_fails_closed(tmp_path: Path) -> None:
    copied_root = tmp_path / "repository"
    copied_agent = copied_root / "lmstudio-unreal-agent-mcp"
    shutil.copytree(ROOT / "lmstudio-unreal-agent-mcp" / "src", copied_agent / "src")
    shutil.copy2(
        ROOT / "lmstudio-unreal-agent-mcp" / "package.json",
        copied_agent / "package.json",
    )
    expected = component_identity("agent", repository_root=copied_root)
    manifest_path = tmp_path / "control-runtime.json"
    manifest_path.write_text(
        json.dumps({"components": {"agent": expected}}),
        encoding="utf-8",
    )
    (copied_agent / "src" / "control-envelope.js").write_text(
        "// stale process image\n",
        encoding="utf-8",
    )

    with pytest.raises(ControlRuntimeMismatch, match="CONTROL_RUNTIME_VERSION_MISMATCH"):
        verify_runtime_component(
            "agent",
            manifest_path=manifest_path,
            repository_root=copied_root,
            required=True,
        )


def test_runtime_commit_mismatch_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = build_runtime_manifest(ROOT)
    manifest["components"]["rag"]["gitCommit"] = "different-commit"
    manifest_path = tmp_path / "control-runtime.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setenv("CONTROL_RUNTIME_GIT_COMMIT", "installed-commit")

    with pytest.raises(ControlRuntimeMismatch, match="gitCommit"):
        verify_runtime_component(
            "rag",
            manifest_path=manifest_path,
            repository_root=ROOT,
            required=True,
        )


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
@pytest.mark.parametrize(
    ("component", "module_path"),
    [
        ("agent", "./lmstudio-unreal-agent-mcp/src/runtime-identity.js"),
        ("compactor", "./lmstudio-unreal-agent-mcp/src/runtime-identity.js"),
    ],
)
def test_python_and_node_build_hashes_are_platform_neutral(
    component: str,
    module_path: str,
) -> None:
    command = (
        f"const r=require('{module_path}');"
        f"process.stdout.write(JSON.stringify(r.componentIdentity('{component}', process.cwd())))"
    )
    completed = subprocess.run(
        ["node", "-e", command],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    node_identity = json.loads(completed.stdout)
    python_identity = component_identity(component, repository_root=ROOT)

    assert node_identity["buildHash"] == python_identity["buildHash"]
    assert node_identity["componentVersion"] == python_identity["componentVersion"]
    assert node_identity["protocolVersion"] == python_identity["protocolVersion"]
