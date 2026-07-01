#!/usr/bin/env python
"""Ensure run_all_exports works when exec()'d without __file__ (Unreal headless path)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = ROOT / "tools" / "ue_export"


def test_run_all_exports_exec_without_file():
    source = (TOOLS_DIR / "run_all_exports.py").read_text(encoding="utf-8")
    namespace: dict = {}
    exec(f"_TOOLS_DIR = {str(TOOLS_DIR)!r}\n" + source, namespace)
    root = namespace["_tools_dir"]()
    assert Path(root) == TOOLS_DIR


def test_load_module_finds_export_common():
    source = (TOOLS_DIR / "run_all_exports.py").read_text(encoding="utf-8")
    namespace: dict = {}
    exec(f"_TOOLS_DIR = {str(TOOLS_DIR)!r}\n" + source, namespace)
    module = namespace["_load_module"]("export_fmod_metadata.py", str(TOOLS_DIR))
    assert callable(module["export_fmod_metadata"])
