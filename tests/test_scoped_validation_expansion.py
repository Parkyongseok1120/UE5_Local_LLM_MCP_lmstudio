from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from domain_validation_context import expand_domain_validation_scope  # noqa: E402


def test_cpp_scope_expands_paired_header(tmp_path: Path) -> None:
    root = tmp_path / "Demo"
    module = root / "Source" / "Demo"
    pub = module / "Public"
    priv = module / "Private"
    pub.mkdir(parents=True)
    priv.mkdir(parents=True)
    (root / "Demo.uproject").write_text('{"Modules":[{"Name":"Demo","Type":"Runtime"}]}', encoding="utf-8")
    header = pub / "DemoActor.h"
    cpp = priv / "DemoActor.cpp"
    header.write_text("class ADemoActor : public AActor {};\n", encoding="utf-8")
    cpp.write_text("ADemoActor::ADemoActor() {}\n", encoding="utf-8")
    result = expand_domain_validation_scope(root, [cpp])
    expanded = set(result["expandedScope"])
    assert "Source/Demo/Public/DemoActor.h" in expanded


def test_header_scope_expands_implementation_by_class_name(tmp_path: Path) -> None:
    root = tmp_path / "Demo"
    module = root / "Source" / "Demo"
    pub = module / "Public"
    priv = module / "Private"
    pub.mkdir(parents=True)
    priv.mkdir(parents=True)
    (root / "Demo.uproject").write_text('{"Modules":[{"Name":"Demo","Type":"Runtime"}]}', encoding="utf-8")
    header = pub / "DemoActor.h"
    cpp = priv / "DemoActor.cpp"
    header.write_text("class ADemoActor : public AActor {};\n", encoding="utf-8")
    cpp.write_text("ADemoActor::ADemoActor() {}\n", encoding="utf-8")
    result = expand_domain_validation_scope(root, [header])
    expanded = set(result["expandedScope"])
    assert "Source/Demo/Private/DemoActor.cpp" in expanded
    assert "Demo::ADemoActor" not in result["unresolved"]
