#!/usr/bin/env python
"""Create an ignored local live holdout config from the public-safe template."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXAMPLE = ROOT / "config" / "rag_eval_real_project_holdout_cases.local.example.json"
DEFAULT_OUTPUT = ROOT / "config" / "rag_eval_real_project_holdout_cases.local.json"
DEFAULT_SUITE_NAME = "real-project-holdout-local-v0"
DEFAULT_MODEL = "qwen3.6-27b-heretic-uncensored-finetune-neo-code-di-imatrix-max"


def detect_ubt_path() -> Path | None:
    """Return the UE 5.8 UBT path if present; never fail if absent."""
    base = Path("C:/") / "Program Files" / "Epic Games"
    candidate = base / "UE_5.8" / "Engine" / "Binaries" / "DotNET" / "UnrealBuildTool" / "UnrealBuildTool.exe"
    if candidate.is_file():
        return candidate
    return None


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def update_config(
    data: dict[str, Any],
    *,
    project_file: str = "",
    fixture_root: str = "",
    suite_name: str = DEFAULT_SUITE_NAME,
) -> dict[str, Any]:
    out = json.loads(json.dumps(data))
    out["suite"] = suite_name
    fixture_root = fixture_root.rstrip("/\\")
    for case in out.get("cases") or []:
        if project_file:
            case["projectFile"] = project_file
        if fixture_root:
            case["fixtureDir"] = f"{fixture_root}/{case.get('id')}"
    return out


def write_local_config(
    *,
    example_path: Path = DEFAULT_EXAMPLE,
    output_path: Path = DEFAULT_OUTPUT,
    project_file: str = "",
    fixture_root: str = "",
    suite_name: str = DEFAULT_SUITE_NAME,
    force: bool = False,
) -> dict[str, Any]:
    if output_path.exists() and not force:
        raise FileExistsError(f"{output_path} already exists; use --force to overwrite")
    data = update_config(
        load_json(example_path),
        project_file=project_file,
        fixture_root=fixture_root,
        suite_name=suite_name,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return data


def next_step_text(output_path: Path, model: str = DEFAULT_MODEL, ubt_path: Path | None = None) -> str:
    config = output_path.as_posix()
    ubt_arg = str(ubt_path) if ubt_path else "<UnrealBuildTool.exe>"
    return "\n".join(
        [
            "Next steps:",
            f"python scripts/validate_holdout_cases.py --config {config} --allow-local-paths",
            "python scripts/build_symbol_graph.py",
            f"python scripts/eval_pass_at_k.py --metrics-only --config {config}",
            (
                "python scripts/eval_pass_at_k.py --live --require-live "
                f"--config {config} --model {model} --ubt-path \"{ubt_arg}\" --wrapper-timeout 1800"
            ),
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap ignored local live holdout config.")
    parser.add_argument("--project-file", default="", help="Path to the local .uproject for all generated cases.")
    parser.add_argument("--fixture-root", default="", help="Root containing one fixture directory per case id.")
    parser.add_argument("--suite-name", default=DEFAULT_SUITE_NAME)
    parser.add_argument("--force", action="store_true", help="Overwrite existing local config.")
    parser.add_argument("--example-config", type=Path, default=DEFAULT_EXAMPLE)
    parser.add_argument("--output-config", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    try:
        write_local_config(
            example_path=args.example_config,
            output_path=args.output_config,
            project_file=args.project_file,
            fixture_root=args.fixture_root,
            suite_name=args.suite_name,
            force=args.force,
        )
    except FileExistsError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"failed to bootstrap local holdout config: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote {args.output_config}")
    detected_ubt = detect_ubt_path()
    if detected_ubt:
        print(f"Detected UBT candidate: {detected_ubt}")
    else:
        print("UE 5.8 UBT candidate not detected; pass --ubt-path explicitly for live eval.")
    print(next_step_text(args.output_config, ubt_path=detected_ubt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
