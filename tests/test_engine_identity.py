"""Single Python engine identity reader: Unreal Build.version and Unity ProjectVersion.txt."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import engine_identity as ident  # noqa: E402
import direct_rag_project_engine as project_engine  # noqa: E402


def _engine(tmp_path: Path, name: str, build_version: dict | str | None) -> Path:
    root = tmp_path / name
    (root / "Engine" / "Source").mkdir(parents=True)
    if build_version is not None:
        (root / "Engine" / "Build").mkdir(parents=True)
        text = build_version if isinstance(build_version, str) else json.dumps(build_version)
        (root / "Engine" / "Build" / "Build.version").write_text(text, encoding="utf-8")
    return root


def test_build_version_is_observed_with_source_and_hash(tmp_path):
    root = _engine(tmp_path, "AnyName", {"MajorVersion": 5, "MinorVersion": 3, "PatchVersion": 2, "Changelist": 9})
    identity = ident.read_unreal_build_identity(root)
    assert identity["status"] == "observed" and identity["engine"] == "unreal"
    assert identity["version"] == "5.3.2" and (identity["major"], identity["minor"]) == (5, 3)
    assert identity["source"] == "build_version_file" and len(identity["sha256"]) == 64
    assert identity["hashScope"] == "file" and identity["engineRoot"] == str(root)


def test_missing_or_malformed_build_version_is_unknown_not_assumed(tmp_path):
    missing = ident.read_unreal_build_identity(_engine(tmp_path, "UE_5.9", None))
    assert missing["status"] == "unknown" and missing["reason"] and "version" not in missing
    partial = ident.read_unreal_build_identity(_engine(tmp_path, "UE_5.8", {"MajorVersion": 5}))
    assert partial["status"] == "unknown" and "version" not in partial
    broken = ident.read_unreal_build_identity(_engine(tmp_path, "Broken", "{not json"))
    assert broken["status"] == "unknown"


def test_folder_name_inference_is_labelled_and_never_observed(tmp_path):
    root = _engine(tmp_path, "UE_5.6", None)
    guess = ident.infer_unreal_version_from_folder(root)
    assert guess["status"] == "unknown" and guess["source"] == "folder_name_inferred"
    assert guess["version"] == "5.6" and (guess["major"], guess["minor"]) == (5, 6)
    described = ident.describe_unreal_engine_identity(root)
    assert described["status"] == "unknown" and described["inferred"]["source"] == "folder_name_inferred"
    nameless = ident.infer_unreal_version_from_folder(tmp_path / "Source")
    assert "version" not in nameless and nameless["reason"]


def test_observed_build_version_wins_over_misleading_folder_name(tmp_path):
    root = _engine(tmp_path, "UE_5.1", {"MajorVersion": 5, "MinorVersion": 7})
    described = ident.describe_unreal_engine_identity(root)
    assert described["status"] == "observed" and described["version"] == "5.7"
    assert project_engine.engine_root_version_with_source(root) == ("5.7", "build_version_file")
    assert project_engine.engine_root_version(root) == "5.7"


def test_string_only_engine_version_does_not_erase_unknown_provenance(tmp_path):
    root = _engine(tmp_path, "UE_9.2", None)
    assert project_engine.engine_root_version_with_source(root) == ("9.2", "folder_name_inferred")
    assert project_engine.engine_root_version(root) == ""


def test_installer_rejects_inferred_project_binding_before_creating_stage(tmp_path):
    import pytest
    from installer.direct_rag_build import create_direct_rag_build_plan
    root = _engine(tmp_path, "UE_9.2", None)
    descriptor = tmp_path / "Demo.uproject"
    descriptor.write_text('{"EngineAssociation":""}', encoding="utf-8")
    output = tmp_path / "index"
    with pytest.raises(ValueError, match="unverified"):
        create_direct_rag_build_plan(python_executable=Path(sys.executable), index_dir=output,
            tier="standard", project_roots=[descriptor], active_project=descriptor, engine_root=root, root=tmp_path)
    assert not output.exists()


def test_unity_project_version_file(tmp_path):
    (tmp_path / "ProjectSettings").mkdir()
    (tmp_path / "ProjectSettings" / "ProjectVersion.txt").write_text(
        "m_EditorVersion: 6000.2.8f1\nm_EditorVersionWithRevision: 6000.2.8f1 (abc)\n", encoding="utf-8")
    identity = ident.read_unity_project_version(tmp_path)
    assert identity["status"] == "observed" and identity["engine"] == "unity"
    assert identity["version"] == "6000.2.8f1" and (identity["major"], identity["minor"]) == (6000, 2)
    assert identity["source"] == "project_version_file"
    missing = ident.read_unity_project_version(tmp_path / "Other")
    assert missing["status"] == "unknown" and missing["source"] == "project_version_file" and missing["reason"]


def test_empty_association_reads_the_resolved_engine_and_never_defaults(tmp_path, monkeypatch):
    project = tmp_path / "Proj" / "Demo.uproject"
    project.parent.mkdir()
    project.write_text(json.dumps({"EngineAssociation": ""}), encoding="utf-8")
    observed = _engine(tmp_path, "SomeInstall", {"MajorVersion": 5, "MinorVersion": 4, "PatchVersion": 1})
    monkeypatch.setenv("UNREAL_ENGINE_ROOT", str(observed))
    binding = project_engine.project_engine_version(project, tmp_path)
    assert binding["ok"] and binding["engineVersion"] == "5.4"
    assert binding["engineVersionSource"] == "build_version_file"
    unreadable = _engine(tmp_path, "OpaqueInstall", None)
    monkeypatch.setenv("UNREAL_ENGINE_ROOT", str(unreadable))
    unknown = project_engine.project_engine_version(project, tmp_path)
    assert not unknown["ok"] and unknown["errorCode"] == "PROJECT_ENGINE_VERSION_UNRESOLVED"
    assert unknown["engineVersionStatus"] == "unknown"
    guessed = _engine(tmp_path, "UE_5.2", None)
    monkeypatch.setenv("UNREAL_ENGINE_ROOT", str(guessed))
    inferred = project_engine.project_engine_version(project, tmp_path)
    assert inferred["ok"] and inferred["engineVersionSource"] == "folder_name_inferred"
    assert inferred["engineVersionStatus"] == "unknown"


def test_version_tier_follows_the_validated_list_not_a_constant():
    import unreal_contracts as contracts
    data = {"validatedEngineVersions": ["7.1"]}
    assert contracts.engine_version_tier(data, "7.1.3") == "validated"
    assert contracts.engine_version_tier(data, "7.4") == "same_major_unvalidated"
    assert contracts.engine_version_tier(data, "6.9") == "other_major"
    assert contracts.engine_version_tier(data, None) == "unknown"
    assert contracts.engine_version_tier(data, "garbage") == "unknown"


def test_request_binding_does_not_promote_folder_guess_to_project_version(tmp_path):
    from direct_rag_request_binding import resolve_request_engine_binding

    project = tmp_path / "Demo.uproject"
    guessed = {"ok": True, "project": str(project), "engineAssociation": "", "engineVersion": "6.2",
               "engineVersionSource": "folder_name_inferred", "engineVersionStatus": "unknown"}
    result = resolve_request_engine_binding([project], tmp_path, lambda *_: guessed)
    assert result["ok"] is False
    assert result["errorCode"] == "PROJECT_ENGINE_VERSION_UNRESOLVED"
    assert result["engineVersionStatus"] == "unknown"
    assert result["engineVersionSource"] == "folder_name_inferred"
    assert "requestedVersion" not in result
    assert result["inferredEngineVersion"] == "6.2"


def test_request_binding_preserves_declared_and_observed_versions(tmp_path):
    from direct_rag_request_binding import resolve_request_engine_binding

    project = tmp_path / "Demo.uproject"
    for source in ("project_association", "build_version_file"):
        known = {"ok": True, "project": str(project), "engineAssociation": "6.2", "engineVersion": "6.2",
                 "engineVersionSource": source, "engineVersionStatus": "declared_or_observed"}
        result = resolve_request_engine_binding([project], tmp_path, lambda *_: known)
        assert result["ok"] is True and result["requestedVersion"] == "6.2"
