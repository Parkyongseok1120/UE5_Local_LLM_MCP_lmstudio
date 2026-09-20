"""The Unity beta must be usable from a portable package, including common I/O."""
from pathlib import Path

import build_integrated_package as builder

ROOT = Path(__file__).resolve().parents[1]


def test_unity_portable_runtime_is_complete_and_has_no_editor_state(tmp_path):
    output = tmp_path / "portable"
    builder.build(ROOT, output, None, include_index=False, require_clean_source=False)
    required = [
        *ROOT.glob("lmstudio-unity-mcp/src/*.js"),
        *ROOT.glob("shared-tool-core/*.js"),
        *ROOT.glob("unity-editor-bridge/Editor/*.cs"),
        *ROOT.glob("unity-editor-bridge/Editor/*.asmdef"),
        *ROOT.glob("unity-editor-bridge/Runtime/**/*.*"),
        *ROOT.glob("unity-editor-bridge/Adapters/**/*.*"),
        *ROOT.glob("unity-symbol-worker/*.cs"),
        *ROOT.glob("unity-symbol-worker/*.csproj"),
    ]
    for source in required:
        if source.suffix not in {".js", ".cs", ".asmdef", ".meta", ".csproj"} or source.name.startswith("."):
            continue
        packaged = output / source.relative_to(ROOT)
        assert packaged.is_file(), source.relative_to(ROOT)
        assert packaged.read_bytes() == source.read_bytes()
    assert (output / "lmstudio-unity-mcp/pnpm-lock.yaml").is_file()
    assert (output / "scripts/update_unity_mcp.py").is_file()
    assert (output / "scripts/update_unreal_mcp.py").is_file()
    assert (output / "lmstudio-unreal-agent-mcp/src/write-locks.js").is_file()
    assert (output / "lmstudio-unreal-agent-mcp/src/direct-file-snapshot.js").is_file()
    assert not (output / "unity-editor-bridge/Tests~").exists()
    assert not (output / "lmstudio-unity-mcp/node_modules").exists()
    assert not list(output.rglob("bridge.json"))
