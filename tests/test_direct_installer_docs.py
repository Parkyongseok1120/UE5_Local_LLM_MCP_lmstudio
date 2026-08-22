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

    assert "Direct `--build-rag` indexing runs the managed Python collectors directly" in integrated
    assert "`install.py --build-rag` invokes its managed Python executable directly" in tiers
    assert "`install.py --build-rag` invokes the managed Python" in internal
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

    assert "The integrated installer does not copy or enable\nthis plugin" in editor
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
    assert "only in the development repository" in setup
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
