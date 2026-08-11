from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ROOTS = (
    ROOT / "scripts",
    ROOT / "lmstudio-unreal-agent-mcp" / "src",
    ROOT / "lmstudio-context-compactor-plugin" / "src",
    ROOT / "tools" / "ue_export",
    ROOT / "tools" / "ue_plugins",
    ROOT / "config",
    ROOT / "docs",
)
TEXT_SUFFIXES = frozenset(
    {".py", ".js", ".ts", ".json", ".md", ".ps1", ".cpp", ".h", ".cs", ".sh", ".bat", ".yml", ".yaml", ".toml"}
)
MOJIBAKE_MARKERS = ("�", "Ã", "Â", "â€", "â€™", "â€œ", "â€�", "ðŸ", "ì„", "í•", "ë‹", "ë¥")


def _production_text_files() -> list[Path]:
    return [
        path
        for root in PRODUCTION_ROOTS
        for path in root.rglob("*")
        if path.is_file() and path.suffix.casefold() in TEXT_SUFFIXES
    ]


def test_production_text_is_strict_utf8_without_common_mojibake() -> None:
    failures = []
    for path in _production_text_files():
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeError as exc:
            failures.append(f"{path.relative_to(ROOT)}: {exc}")
            continue
        markers = [marker for marker in MOJIBAKE_MARKERS if marker in text]
        if markers:
            failures.append(f"{path.relative_to(ROOT)}: mojibake markers {markers}")
    assert failures == []
