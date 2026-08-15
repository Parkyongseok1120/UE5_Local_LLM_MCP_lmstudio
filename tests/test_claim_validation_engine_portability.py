#!/usr/bin/env python
"""Regression tests for project-bound engine header claim validation."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))


def _load_rag_mcp_module():
    path = SCRIPTS / "unreal_rag_mcp.py"
    spec = importlib.util.spec_from_file_location("unreal_rag_mcp_engine_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _engine_root(parent: Path, name: str) -> Path:
    root = parent / name
    (root / "Engine" / "Build").mkdir(parents=True)
    return root.resolve()


def _project(parent: Path, association: str | None) -> Path:
    project = parent / "다른 프로젝트" / "PortableGame.uproject"
    project.parent.mkdir(parents=True)
    descriptor: dict[str, object] = {"FileVersion": 3}
    if association is not None:
        descriptor["EngineAssociation"] = association
    project.write_text(json.dumps(descriptor, ensure_ascii=False), encoding="utf-8")
    return project.resolve()


def test_claim_validation_rejects_stale_installer_engine_for_custom_project(
    monkeypatch,
    tmp_path: Path,
) -> None:
    mod = _load_rag_mcp_module()
    association_a = "{INSTALLER-PROJECT-A}"
    association_b = "{ACTIVE-PROJECT-B-UNREGISTERED}"
    engine_a = _engine_root(tmp_path, "Engine A")
    project_b = _project(tmp_path, association_b)
    (project_b.parent / "Source" / "PortableGame").mkdir(parents=True)
    shared = tmp_path / "unreal workspace.json"
    shared.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("SHARED_UNREAL_CONFIG", str(shared))
    monkeypatch.setenv("UNREAL_ENGINE_ROOT", str(engine_a))
    monkeypatch.setenv("UNREAL_ENGINE_ROOT_ASSOCIATION", association_a)

    server = mod.McpServer(tmp_path / "missing.sqlite")
    server.workspace = tmp_path
    server.architecture_graph = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("unresolved project engine must block before graph/header validation")
    )
    sent: list[dict] = []
    server.send = sent.append

    mod._handle_unreal_code_sketch_claim_validate(
        server,
        91,
        {
            "sketch": "AActor* Actor = nullptr;",
            "projectRoot": str(project_b),
            "targetFiles": ["Source/PortableGame/NewActor.h"],
            "changeKind": "new_file",
        },
    )

    payload = sent[-1]["result"]["structuredContent"]
    assert payload["ok"] is False
    assert payload["errorCode"] == "ENGINE_ASSOCIATION_UNRESOLVED"
    assert payload["gatePassed"] is False
    assert payload["writeGateClosed"] is True
    assert payload["engineResolution"]["engineRoot"] == ""
    assert payload["engineResolution"]["requestedEngineAssociation"] == association_b
    assert payload["engineResolution"]["projectFile"] == str(project_b)


def test_claim_validation_explicit_engine_is_intentional_and_invalid_never_falls_back(
    monkeypatch,
    tmp_path: Path,
) -> None:
    mod = _load_rag_mcp_module()
    association_a = "{INSTALLER-PROJECT-A}"
    association_b = "{ACTIVE-PROJECT-B}"
    engine_a = _engine_root(tmp_path, "Engine A")
    engine_b = _engine_root(tmp_path, "Engine B 비ASCII")
    project_b = _project(tmp_path, association_b)
    shared = tmp_path / "unreal workspace.json"
    shared.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("SHARED_UNREAL_CONFIG", str(shared))
    monkeypatch.setenv("UNREAL_ENGINE_ROOT", str(engine_a))
    monkeypatch.setenv("UNREAL_ENGINE_ROOT_ASSOCIATION", association_a)

    explicit = mod._resolve_claim_validation_engine(
        str(project_b),
        str(engine_b),
        tmp_path,
    )
    assert explicit["ok"] is True
    assert explicit["source"] == "argument"
    assert Path(str(explicit["engineRoot"])) == engine_b
    assert explicit["requestedEngineAssociation"] == association_b

    association_free = _project(tmp_path / "association-free", None)
    invalid = mod._resolve_claim_validation_engine(
        str(association_free),
        str(tmp_path / "missing explicit engine"),
        tmp_path,
    )
    assert invalid["ok"] is False
    assert invalid["errorCode"] == "EXPLICIT_ENGINE_ROOT_INVALID"
    assert invalid["engineRoot"] == ""


def test_claim_validation_passes_exact_mapped_project_engine_to_both_header_checks(
    monkeypatch,
    tmp_path: Path,
) -> None:
    mod = _load_rag_mcp_module()
    association_a = "{INSTALLER-PROJECT-A}"
    association_b = "{ACTIVE-PROJECT-B-MAPPED}"
    engine_a = _engine_root(tmp_path, "Engine A")
    engine_b = _engine_root(tmp_path, "Engine B mapped")
    project_b = _project(tmp_path, association_b)
    target = project_b.parent / "Source" / "PortableGame" / "Worker.cpp"
    target.parent.mkdir(parents=True)
    target.write_text("void Run() {}\n", encoding="utf-8")
    shared = tmp_path / "unreal workspace.json"
    shared.write_text(
        json.dumps(
            {"engineRootsByAssociation": {association_b: str(engine_b)}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SHARED_UNREAL_CONFIG", str(shared))
    monkeypatch.setenv("UNREAL_ENGINE_ROOT", str(engine_a))
    monkeypatch.setenv("UNREAL_ENGINE_ROOT_ASSOCIATION", association_a)

    captured: dict[str, object] = {}
    import code_sketch_pipeline

    monkeypatch.setattr(
        mod,
        "build_generation_contract",
        lambda *_args, **_kwargs: {
            "ok": True,
            "targets": [],
            "issues": [],
            "writeGate": {"writesAllowed": True},
        },
    )

    def validate_surface(*_args, **kwargs):
        captured["surfaceEngineRoot"] = kwargs.get("engine_root")
        return {
            "ok": True,
            "targets": [],
            "issues": [],
            "writeGate": {"writesAllowed": True},
        }

    def validate_claims(*_args, **kwargs):
        captured["claimEngineRoot"] = kwargs.get("engine_root")
        return {
            "ok": True,
            "results": [],
            "verdictSummary": "claim validation passed",
            "compilerProofRequired": False,
        }

    monkeypatch.setattr(code_sketch_pipeline, "validate_active_slice_surface", validate_surface)
    monkeypatch.setattr(code_sketch_pipeline, "load_declaration_context", lambda _contract: ("", []))
    monkeypatch.setattr(code_sketch_pipeline, "proposed_code_surface", lambda sketch: (sketch, {}))
    monkeypatch.setattr(mod, "validate_sketch", validate_claims)

    server = mod.McpServer(tmp_path / "missing.sqlite")
    server.workspace = tmp_path
    server.architecture_graph = lambda *_args, **_kwargs: (
        {"symbols": []},
        "test_graph",
        0.0,
    )
    sent: list[dict] = []
    server.send = sent.append
    mod._handle_unreal_code_sketch_claim_validate(
        server,
        92,
        {
            "sketch": "void Run() {}",
            "projectRoot": str(project_b),
            "targetFiles": ["Source/PortableGame/Worker.cpp"],
            "changeKind": "modify_existing",
        },
    )

    payload = sent[-1]["result"]["structuredContent"]
    assert captured == {
        "surfaceEngineRoot": str(engine_b),
        "claimEngineRoot": str(engine_b),
    }
    assert payload["engineResolution"]["engineRoot"] == str(engine_b)
    assert payload["engineResolution"]["source"] == "config.engineRootsByAssociation"
    assert payload["engineResolution"]["requestedEngineAssociation"] == association_b
