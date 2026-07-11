from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from atomic_io import atomic_write_text, file_sha256  # noqa: E402


def test_atomic_write_preserves_content_on_reread(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    atomic_write_text(target, '{"ok": true}\n')
    assert target.read_text(encoding="utf-8") == '{"ok": true}\n'
    before = file_sha256(target)
    atomic_write_text(target, '{"ok": true, "v": 2}\n')
    after = file_sha256(target)
    assert before != after
