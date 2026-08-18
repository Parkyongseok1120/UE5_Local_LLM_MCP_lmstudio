from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from control_runtime_identity import (
    ControlRuntimeMismatch,
    assert_source_tree_matches_head,
    build_runtime_manifest,
    component_identity,
    verify_runtime_component,
)


ROOT = Path(__file__).resolve().parents[1]


def test_source_head_gate_ignores_untracked_files_but_rejects_tracked_drift(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init"], cwd=repository, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "runtime-test@example.invalid"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Runtime Test"],
        cwd=repository,
        check=True,
    )
    tracked = repository / "tracked.txt"
    tracked.write_text("committed\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-m", "fixture"], cwd=repository, check=True, capture_output=True)
    expected = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    (repository / "untracked.bin").write_bytes(b"build artifact")
    assert assert_source_tree_matches_head(repository) == expected
    packaged = repository / "extracted-package"
    packaged.mkdir()
    (packaged / "package-manifest.json").write_text(
        json.dumps({"sourceGitCommit": "sealed-package-commit"}),
        encoding="utf-8",
    )
    assert assert_source_tree_matches_head(packaged) == "sealed-package-commit"

    tracked.write_text("dirty\n", encoding="utf-8")
    with pytest.raises(
        ControlRuntimeMismatch,
        match="tracked source tree differs from HEAD",
    ) as captured:
        assert_source_tree_matches_head(repository)
    assert captured.value.error_code == "CONTROL_RUNTIME_SOURCE_HEAD_MISMATCH"


def test_runtime_manifest_covers_every_control_component() -> None:
    manifest = build_runtime_manifest(ROOT)

    assert manifest["protocolVersion"] == 2
    assert manifest["expectedSourceGitCommit"] == manifest["components"]["agent"]["gitCommit"]
    assert set(manifest["components"]) == {"agent", "rag", "compactor"}
    assert manifest["components"]["agent"]["componentVersion"] == "0.3.18"
    for name, identity in manifest["components"].items():
        assert identity["component"] == name
        assert len(identity["buildHash"]) == 64
        assert identity["componentVersion"]
        assert identity["protocolVersion"] == 2
        assert "gitCommit" in identity
        for field in (
            "transitionPolicyHash",
            "errorCatalogHash",
            "authorizationSchemaHash",
            "controlSchemaHash",
        ):
            assert len(identity[field]) == 64


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
    assert result["bundleIntegrityVerified"] is True
    assert result["installedGitCommit"] == result["expected"]["gitCommit"]
    assert result["expectedGitCommit"] == result["expected"]["gitCommit"]
    assert result["sourceHeadMatched"] is True
    assert result["runtimeStale"] is False
    assert result["runtimeVerified"] is True
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


def test_internally_consistent_stale_bundle_is_rejected_against_expected_source_head(
    tmp_path: Path,
) -> None:
    manifest = build_runtime_manifest(ROOT)
    manifest["expectedSourceGitCommit"] = "newer-source-head"
    manifest_path = tmp_path / "control-runtime.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(
        ControlRuntimeMismatch,
        match="CONTROL_RUNTIME_SOURCE_HEAD_MISMATCH",
    ) as captured:
        verify_runtime_component(
            "rag",
            manifest_path=manifest_path,
            repository_root=ROOT,
            required=True,
        )
    assert captured.value.error_code == "CONTROL_RUNTIME_SOURCE_HEAD_MISMATCH"


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
    for field in (
        "transitionPolicyHash",
        "errorCatalogHash",
        "authorizationSchemaHash",
        "controlSchemaHash",
    ):
        assert node_identity[field] == python_identity[field]


def test_protocol_schema_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    manifest = build_runtime_manifest(ROOT)
    manifest["components"]["rag"]["controlSchemaHash"] = "0" * 64
    manifest_path = tmp_path / "control-runtime.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ControlRuntimeMismatch, match="controlSchemaHash"):
        verify_runtime_component(
            "rag",
            manifest_path=manifest_path,
            repository_root=ROOT,
            required=True,
        )
