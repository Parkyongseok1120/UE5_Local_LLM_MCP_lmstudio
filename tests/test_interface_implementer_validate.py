from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from unreal_static_validate import validate_interface_implementer_drift  # noqa: E402


def test_interface_implementer_drift_detected(tmp_path: Path) -> None:
    (tmp_path / "HoldoutActionInterface.h").write_text(
        "#pragma once\nclass IHoldoutActionInterface { public: virtual void ApplyInteraction(float Strength) = 0; };\n",
        encoding="utf-8",
    )
    (tmp_path / "HoldoutActionImplementer.h").write_text(
        '#pragma once\n#include "HoldoutActionInterface.h"\n'
        "class FHoldoutActionImplementer : public IHoldoutActionInterface { public: void ApplyInteraction(int32 Strength) override; };\n",
        encoding="utf-8",
    )
    findings = validate_interface_implementer_drift(tmp_path)
    assert any(f.code == "INTERFACE_IMPLEMENTER_SIGNATURE_MISMATCH" for f in findings)
