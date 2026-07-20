from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_cline_template_still_has_placeholders() -> None:
    text = (ROOT / "config" / "cline_mcp_settings.template.json").read_text(encoding="utf-8")
    assert "{PYTHON_EXE}" in text
    assert "{REPO_ROOT}" in text


def test_install_path_helpers_has_cline_sync() -> None:
    helpers = (
        ROOT / "scripts" / "installer_support" / "Install-PathHelpers.ps1"
    ).read_text(encoding="utf-8")
    assert "Sync-ClineMcpSettings" in helpers
    assert "Test-ClineMcpHasUnresolvedPlaceholders" in helpers
