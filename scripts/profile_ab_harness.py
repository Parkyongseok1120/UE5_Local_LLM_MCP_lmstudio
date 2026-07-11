#!/usr/bin/env python
"""24K vs 32K YaRN profile A/B harness for domain eval suites (P3)."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent


def run_eval(config: Path, profile_env: dict[str, str]) -> dict:
    env = {**dict(**__import__("os").environ), **profile_env}
    cmd = [
        sys.executable,
        str(SCRIPTS / "eval_pass_at_k.py"),
        "--config",
        str(config),
        "--dry-run",
    ]
    proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True)
    observed = {}
    try:
        from load_sampling_preset import load_sampling_preset, resolve_profile_name

        preset = load_sampling_preset(profile_env.get("UNREAL_RAG_MODEL_PROFILE"))
        observed = {
            "selectedProfile": resolve_profile_name(),
            "contextLengthRequested": preset.get("contextLength"),
            "promptContract": preset.get("promptContract"),
        }
    except Exception as exc:
        observed = {"error": str(exc)}
    return {
        "exitCode": proc.returncode,
        "stdout": proc.stdout[-4000:],
        "stderr": proc.stderr[-2000:],
        "observed": observed,
        "envOverrides": profile_env,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare 9B context profiles on domain eval configs.")
    parser.add_argument(
        "--config",
        default=str(ROOT / "config" / "rag_eval_component_domain.local.json"),
    )
    parser.add_argument("--output", default=str(ROOT / "Reports" / "eval" / "profile_ab_latest.json"))
    args = parser.parse_args()
    config = Path(args.config)
    profiles = {
        "context24k": {"UNREAL_RAG_MODEL_PROFILE": "qwen3_5_9b_deepseek_v4_flash"},
        "context32k_yarn": {
            "UNREAL_RAG_MODEL_PROFILE": "qwen3_5_9b_deepseek_v4_flash",
            "UNREAL_RAG_CONTEXT_LENGTH": "32768",
        },
    }
    results = {name: run_eval(config, env) for name, env in profiles.items()}
    payload = {"config": str(config), "profiles": profiles, "results": results, "liveValidated": False}
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if all(row["exitCode"] == 0 for row in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
