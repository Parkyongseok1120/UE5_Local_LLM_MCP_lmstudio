#!/usr/bin/env python
"""Optional live Soulslike/action_combat eval via LM Studio (Tier B). Default: dry-run."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from eval_reasoning import load_cases, run_case  # noqa: E402
from genre_scope_validate import validate_genre_scope  # noqa: E402
from preflight_lmstudio import check_lmstudio, extract_assistant_text  # noqa: E402


def lmstudio_reachable(url: str) -> bool:
    try:
        with urlopen(f"{url.rstrip('/')}/models", timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def dry_run_report(workspace: Path) -> dict:
    harness_path = workspace / "config" / "rag_eval_agent_harness_cases.json"
    harness = json.loads(harness_path.read_text(encoding="utf-8-sig"))
    soulslike = next(
        (c for c in harness.get("cases") or [] if "soulslike" in c.get("id", "")),
        None,
    )
    reasoning = load_cases(workspace)
    genre_cases = [c for c in reasoning.get("cases") or [] if "soulslike" in c.get("id", "")]

    return {
        "mode": "dry-run",
        "harnessCase": soulslike.get("id") if soulslike else None,
        "harnessTurns": len(soulslike.get("turns") or []) if soulslike else 0,
        "reasoningSoulslikeCases": len(genre_cases),
        "genreScopeSample": validate_genre_scope("action_combat", plan_text="dodge stagger camera combat component"),
        "note": "Pass --no-dry-run to call LM Studio and run reasoning KPI cases.",
    }


def run_live(workspace: Path, index_path: Path, url: str, model: str) -> dict:
    if not lmstudio_reachable(url):
        return {"ok": False, "error": f"LM Studio not reachable at {url}"}

    config = load_cases(workspace)
    cases = [c for c in config.get("cases") or [] if "soulslike" in c.get("id", "")]
    if not cases:
        cases = config.get("cases") or []

    results = [run_case(workspace, case, index_path) for case in cases]
    failed = [r for r in results if not r.get("pass")]

    # Optional single chat probe
    chat_ok = False
    chat_error = ""
    try:
        body = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Plan an action_combat soulslike slice: list dodge OR block, stagger, "
                        "camera, combat component. No code."
                    ),
                }
            ],
            "temperature": 0.4,
            "max_tokens": 512,
        }
        req = Request(
            f"{url.rstrip('/')}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=120) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        message = (payload.get("choices") or [{}])[0].get("message") or {}
        content = extract_assistant_text(message)
        chat_ok = len(content) > 40
    except Exception as exc:
        chat_error = str(exc)

    return {
        "ok": len(failed) == 0 and chat_ok,
        "chatOk": chat_ok,
        "chatError": chat_error,
        "reasoningResults": results,
        "failedCount": len(failed),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Soulslike live eval (optional LM Studio).")
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--no-dry-run", action="store_true", help="Run live LM Studio + reasoning cases")
    parser.add_argument("--url", default="http://localhost:1234/v1")
    parser.add_argument("--model", default="local-model")
    parser.add_argument("--require-live", action="store_true", help="Exit 1 if LM Studio unreachable (Tier B gate)")
    args = parser.parse_args()

    workspace = SCRIPTS.parent
    index_path = workspace / "data" / "unreal58" / "rag.sqlite"

    live = args.no_dry_run
    if live:
        preflight = check_lmstudio(args.url, args.model)
        if not preflight.get("ok"):
            msg = preflight.get("error") or "LM Studio not reachable"
            print(f"[SKIP] {msg}", file=sys.stderr)
            report = {"ok": False, "error": msg, "skipped": True}
            kpi = {
                "generatedAt": datetime.now(timezone.utc).isoformat(),
                "mode": "live",
                "report": report,
                "pass": False,
            }
            out = workspace / "data" / "baseline" / "soulslike-live-kpi.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(kpi, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"Wrote {out}")
            return 1 if args.require_live else 0
        model = str(preflight.get("resolvedModel") or args.model)
        print(f"Using LM Studio model: {model}")
        report = run_live(workspace, index_path, args.url, model)
        for row in report.get("reasoningResults") or []:
            status = "PASS" if row.get("pass") else "FAIL"
            print(f"[{status}] {row['id']} ({row.get('type')})")
        if report.get("chatError"):
            print(f"Chat probe error: {report['chatError']}")
    else:
        report = dry_run_report(workspace)
        print(json.dumps(report, ensure_ascii=False, indent=2))

    kpi = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "mode": "live" if live else "dry-run",
        "report": report,
        "pass": report.get("ok", False) if live else False,
    }
    out = workspace / "data" / "baseline" / "soulslike-live-kpi.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(kpi, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {out}")

    if live and not report.get("ok"):
        return 1
    if not live:
        print("Dry-run only — use --no-dry-run for live eval. Exiting 0 (informational).")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
