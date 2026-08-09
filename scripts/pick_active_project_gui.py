#!/usr/bin/env python3
"""Cross-platform GUI fallback for selecting the active Unreal project."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from project_controller import switch_active_project
from workspace_paths import load_shared_config


def choose_project() -> str:
    import tkinter as tk
    from tkinter import filedialog

    config = load_shared_config()
    initial = str(config.get("activeProject") or "").strip()
    initial_dir = str(Path(initial).parent) if initial else ""
    if not initial_dir:
        roots = [str(item) for item in config.get("projectSearchRoots") or [] if str(item)]
        initial_dir = roots[0] if roots else ""

    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
        return str(
            filedialog.askopenfilename(
                parent=root,
                title="Select Unreal project (.uproject)",
                initialdir=initial_dir or None,
                filetypes=[("Unreal Project", "*.uproject"), ("All files", "*")],
            )
            or ""
        )
    finally:
        root.destroy()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--prepare", action="store_true")
    args = parser.parse_args()
    try:
        selected = choose_project()
        if not selected:
            payload = {"ok": True, "cancelled": True, "message": "Selection cancelled."}
        else:
            payload = switch_active_project(
                args.workspace.resolve(),
                project_path=selected,
                prepare=args.prepare,
            )
    except Exception as exc:
        payload = {
            "ok": False,
            "error": str(exc),
            "fallback": "Call unreal_set_active_project with an absolute .uproject path.",
        }
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
