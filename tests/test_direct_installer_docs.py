from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8-sig")


def test_current_installer_docs_describe_python_only_rag_build() -> None:
    integrated = _read("docs/Integrated_Installer.md")
    tiers = _read("docs/Indexing_Tiers.md")
    internal = _read("installer/README.md")
    combined = "\n".join((integrated, tiers, internal))

    assert "`--build-rag`는 설치기가 관리하는 Python으로 수집과 검색 자료 생성을 직접 실행합니다" in integrated
    assert "`install.py --build-rag`는 설치기가 관리하는 Python으로 수집과 검색 자료 생성을 직접 실행합니다" in tiers
    assert "`install.py --build-rag`는 설치기가 관리하는 Python" in internal
    assert "INSTALL-*-BUILD-RAG.bat" not in combined
    assert "opt-in indexing requires PowerShell" not in combined
    assert "PowerShell 7 (`pwsh`) only when `--build-rag`" not in combined
    assert "PowerShell pipeline" not in combined
    assert "pwsh -NoProfile -File ./rag.ps1 refresh" in combined


def test_current_docs_require_explicit_editor_metadata_side_effects() -> None:
    integrated = _read("docs/Integrated_Installer.md")
    tiers = _read("docs/Indexing_Tiers.md")
    editor = _read("docs/Editor_Metadata_Export.md")
    combined = "\n".join((integrated, tiers, editor))

    assert "통합 설치기는 이 플러그인을 복사하거나 활성화하지 않습니다" in editor
    assert "allowEditorLaunch=false" in editor
    assert "-RefreshScope editor_metadata -AllowEditorLaunch" in editor
    assert "tools/ue_export/run_all_exports.py" in editor
    assert "taxonomy_text_lines" in editor
    assert "graph_lookup_guidance" not in editor
    assert "unreal_asset_graph_lookup" not in editor
    assert "installer asks whether to enable automatic Editor export" not in combined
    assert "integrated installer's explicit plugin prompt" not in combined
    assert "Automatic Editor metadata export" not in combined


def test_packaged_setup_docs_use_existing_installer_and_doctor_commands() -> None:
    setup = _read("docs/LMStudio_Unreal_Agent_Setup.md")
    discipline = _read("docs/LMStudio_MCP_Tool_Discipline.md")
    integrated = _read("docs/Integrated_Installer.md")
    combined = "\n".join((setup, discipline, integrated))

    assert "python install.py --profile" in combined
    assert ".\\rag.ps1 doctor" in setup
    assert "./rag.ps1 doctor" in combined
    assert "unreal_rag_health" in combined
    assert "get_workspace_info" in combined
    assert "개발 저장소에만 있습니다" in setup
    assert "patch_mcp_runtime_paths.ps1" not in combined
    assert "scripts/patch_mcp_config.py" not in combined
    for source_checkout_only in (
        "manage_runtime_manifest.py",
        "build_integrated_package.py",
        "eval_evidence_first_benchmark.py",
        "load_sampling_preset.py",
        "bench_lmstudio_mcp.py",
    ):
        assert source_checkout_only not in combined


def test_current_docs_keep_host_compactor_off_by_default_and_use_one_activation_switch() -> None:
    current_docs = (
        "README.md",
        "README.portable.md",
        "installer/README.md",
        "lmstudio-context-compactor-plugin/README.md",
        "docs/Integrated_Installer.md",
        "docs/LMStudio_Unreal_Agent_Setup.md",
        "docs/LMStudio_MCP_Tool_Discipline.md",
        "docs/Troubleshooting.md",
        "docs/Cline_Rider_Unreal_Agent_Setup.md",
        "docs/ARCHITECTURE.md",
        "docs/Model_Profiles.md",
    )
    documents = {relative: _read(relative) for relative in current_docs}
    combined = "\n".join(documents.values())

    for relative, document in documents.items():
        assert "OFF" in document, relative
    assert "Enable transparent compaction" not in combined
    assert "two-switch" not in combined.casefold()
    assert "both switches" not in combined.casefold()
    assert "nested compaction opt-in" not in combined.casefold()
    assert "단일 스위치" in combined
    assert "단일" in documents["README.md"]
    assert "설치는 사용 가능한 파일을 준비할 뿐 채팅에서 활성화하지 않습니다" in combined
    assert "New chats start OFF" not in combined
    assert "New chats start with no chat plugins enabled" not in combined
    assert "새 채팅은 OFF로 시작" not in combined
