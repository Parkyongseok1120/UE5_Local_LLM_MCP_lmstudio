#!/usr/bin/env python3
"""Poll holdout artifact dirs and append per-case pass/fail summaries."""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def case_run_complete(run_dir: Path) -> bool:
    retry_path = run_dir / "retry_state.json"
    if retry_path.is_file():
        return True
    final_answer = run_dir / "final_answer.md"
    if not final_answer.is_file():
        return False
    try:
        text = final_answer.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return False
    return "Status: BUILD_OK" in text or "Status: SKIPPED_BUILD" in text or "Status: FAILED" in text


def summarize_case(case_id: str, run_dir: Path) -> str:
    retry_path = run_dir / "retry_state.json"
    if not retry_path.is_file():
        final_answer = run_dir / "final_answer.md"
        if final_answer.is_file():
            try:
                text = final_answer.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                text = ""
            if "Status: BUILD_OK" in text or "Status: SKIPPED_BUILD" in text:
                return f"[PASS] {case_id} (1 attempts)"
            if "Status: FAILED" in text:
                return f"[FAIL] {case_id} (see final_answer.md)"
        return f"[{case_id}] UNKNOWN (no retry_state.json)"

    try:
        data = json.loads(retry_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return f"[{case_id}] UNKNOWN (retry_state read error: {exc})"

    attempts = data.get("attempts") or []
    latest = data.get("latest") or (attempts[-1] if attempts else {})
    passed = any(bool(row.get("passed")) for row in attempts) or bool(latest.get("passed"))
    status = "PASS" if passed else "FAIL"
    n_attempts = len(attempts)

    lines = [f"[{status}] {case_id} ({n_attempts} attempts)"]
    if passed:
        first_pass = next((row for row in attempts if row.get("passed")), latest)
        changed = first_pass.get("changedPaths") or first_pass.get("appliedChangedPaths") or []
        if changed:
            lines.append(f"  patched: {', '.join(changed[:5])}")
        return "\n".join(lines)

    # Failure details
    last = latest or (attempts[-1] if attempts else {})
    code = last.get("errorCode") or "?"
    subkind = last.get("errorSubkind") or "?"
    lines.append(f"  last error: {code} / {subkind}")

    if last.get("validationRejected"):
        blockers = last.get("validationBlockers") or []
        kind = last.get("validationRejectionKind") or "validation_rejected"
        lines.append(f"  validation: {kind}")
        for blocker in blockers[:2]:
            lines.append(f"    - {str(blocker)[:240]}")

    msg = str(last.get("errorMessage") or "").strip()
    if msg and not last.get("validationRejected"):
        lines.append(f"  detail: {msg[:240]}")

    changed = last.get("changedPaths") or last.get("appliedChangedPaths") or []
    if changed:
        lines.append(f"  last changed: {', '.join(changed[:5])}")

    # First actionable build failure if retries were validation-only
    build_fail = next(
        (
            row
            for row in attempts
            if row.get("buildLogPath") and not row.get("validationRejected")
        ),
        None,
    )
    if build_fail and build_fail is not last:
        lines.append(
            f"  first build fail: {build_fail.get('errorCode')} / {build_fail.get('errorSubkind')}"
        )
        if build_fail.get("changedPaths"):
            lines.append(f"    patched: {', '.join(build_fail['changedPaths'][:3])}")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--progress-file", type=Path, required=True)
    parser.add_argument("--poll-seconds", type=float, default=15.0)
    parser.add_argument("--eval-pid", type=int, default=0)
    args = parser.parse_args()

    artifact_dir = args.artifact_dir.resolve()
    progress_file = args.progress_file.resolve()
    seen: set[str] = set()
    if progress_file.is_file():
        for line in progress_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("[PASS]") or line.startswith("[FAIL]"):
                parts = line.split("]", 1)
                if parts:
                    case_part = parts[0].split("[", 1)[-1].strip()
                    if case_part in {"PASS", "FAIL"}:
                        case_id = line.split("]", 2)[1].strip().split(" ", 1)[0]
                        seen.add(case_id)

    progress_file.parent.mkdir(parents=True, exist_ok=True)
    total_known = 36

    while True:
        case_dirs = sorted(
            (p for p in artifact_dir.iterdir() if p.is_dir()),
            key=lambda p: p.stat().st_mtime,
        )
        for case_dir in case_dirs:
            case_id = case_dir.name
            if case_id in seen:
                continue
            run_dir = case_dir / "wrapper_run"
            if not case_run_complete(run_dir):
                continue
            summary = summarize_case(case_id, run_dir)
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            block = f"\n--- {stamp} case {len(seen)+1}/{total_known} ---\n{summary}\n"
            with progress_file.open("a", encoding="utf-8") as fh:
                fh.write(block)
                fh.flush()
            print(block, flush=True)
            seen.add(case_id)

        if args.eval_pid and not _pid_alive(args.eval_pid):
            if len(seen) >= total_known or not any(artifact_dir.iterdir()):
                break
            # eval exited; one final scan then stop
            time.sleep(2)
            break
        time.sleep(args.poll_seconds)

    return 0


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return True
    try:
        import os

        os.kill(pid, 0)
        return True
    except OSError:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
