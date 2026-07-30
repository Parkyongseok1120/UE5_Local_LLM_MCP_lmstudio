from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from collect_build_logs import extract_error  # noqa: E402


def test_extract_error_records_translation_unit_and_include_stack(tmp_path: Path) -> None:
    log_path = tmp_path / "ubt.log"
    log_path.write_text("", encoding="utf-8")
    lines = [
        "[1/2] Compile [x64] Source/Demo/Private/DemoActor.cpp",
        "Note: including file: C:\\UE\\Engine\\Source\\Runtime\\Engine\\Classes\\GameFramework\\Actor.h",
        "C:\\Project\\Source\\Demo\\Private\\DemoActor.cpp(17): error C2065: 'MissingValue': undeclared identifier",
    ]

    record = extract_error(log_path, tmp_path, lines, 2, 2)

    assert record is not None
    metadata = record["metadata"]
    assert metadata["diagnostic_order"] == 2
    assert metadata["translation_unit"].endswith("Source/Demo/Private/DemoActor.cpp")
    assert metadata["include_stack"] == [
        "C:\\UE\\Engine\\Source\\Runtime\\Engine\\Classes\\GameFramework\\Actor.h"
    ]
