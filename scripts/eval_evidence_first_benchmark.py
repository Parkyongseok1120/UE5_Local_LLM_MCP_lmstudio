#!/usr/bin/env python3
"""Run paired LM Studio skill-OFF/ON evidence-first benchmarks by domain."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "config" / "evidence_first_benchmark_cases.json"
PRESET = (
    ROOT
    / "skills"
    / "evidence-first-code-audit"
    / "assets"
    / "lmstudio-evidence-first.preset.json"
)
OFF_SYSTEM_PROMPT = "Act as a concise code reviewer and implementation assistant. Ground claims in supplied code."


def load_cases(path: Path = DEFAULT_CASES) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("benchmark cases must be a non-empty array")
    return cases


def skill_system_prompt() -> str:
    preset = json.loads(PRESET.read_text(encoding="utf-8"))
    for field in preset["operation"]["fields"]:
        if field.get("key") == "llm.prediction.systemPrompt":
            return str(field.get("value") or "")
    raise ValueError("LM Studio preset is missing llm.prediction.systemPrompt")


def build_user_prompt(case: dict[str, Any]) -> str:
    parts = [str(case["task"]), "Use only the supplied artifacts and state remaining unknowns."]
    for snippet in case.get("snippets") or []:
        suffix = Path(str(snippet["path"])).suffix.lstrip(".") or "text"
        parts.append(f"File: {snippet['path']}\n```{suffix}\n{snippet['content']}\n```")
    return "\n\n".join(parts)


def _hits(patterns: list[str], answer: str) -> list[str]:
    return [pattern for pattern in patterns if re.search(pattern, answer, re.I | re.S)]


def score_answer(case: dict[str, Any], answer: str) -> dict[str, Any]:
    required = list(case.get("requiredPatterns") or [])
    forbidden = list(case.get("forbiddenPatterns") or [])
    structure = list(case.get("structurePatterns") or [])
    required_hits = _hits(required, answer)
    forbidden_hits = _hits(forbidden, answer)
    structure_hits = _hits(structure, answer)
    substantive = len(required_hits) / len(required) if required else 1.0
    safe = 1.0 - (len(forbidden_hits) / len(forbidden) if forbidden else 0.0)
    structured = len(structure_hits) / len(structure) if structure else 1.0
    score = 100.0 * (0.6 * substantive + 0.2 * safe + 0.2 * structured)
    return {
        "score": round(score, 2),
        "passed": score >= 80.0 and not forbidden_hits,
        "requiredHitRate": round(substantive, 3),
        "structureHitRate": round(structured, 3),
        "missingRequired": [pattern for pattern in required if pattern not in required_hits],
        "forbiddenHits": forbidden_hits,
        "missingStructure": [pattern for pattern in structure if pattern not in structure_hits],
    }


def evaluate_fixtures(cases: list[dict[str, Any]]) -> dict[str, Any]:
    good = [score_answer(case, case["goodAnswerFixture"]) for case in cases]
    bad = [score_answer(case, case["badAnswerFixture"]) for case in cases]
    good_passed = sum(row["passed"] for row in good)
    bad_rejected = sum(not row["passed"] for row in bad)
    return {
        "ok": good_passed == len(cases) and bad_rejected == len(cases),
        "goodPassed": good_passed,
        "badRejected": bad_rejected,
        "total": len(cases),
        "good": good,
        "bad": bad,
    }


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("LM_API_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _post_json(url: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=_headers(),
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _extract_rest_text(payload: dict[str, Any]) -> str:
    parts: list[str] = []
    for item in payload.get("output") or []:
        if item.get("type") == "message":
            content = item.get("content")
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("text"):
                        parts.append(str(block["text"]))
    return "\n".join(parts).strip()


def _tool_provenance(payload: dict[str, Any]) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return
        kind = str(value.get("type") or "").lower()
        function = value.get("function") if isinstance(value.get("function"), dict) else {}
        name = str(
            value.get("tool")
            or value.get("tool_name")
            or value.get("name")
            or function.get("name")
            or ""
        )
        provider = value.get("provider_info") if isinstance(value.get("provider_info"), dict) else {}
        plugin_id = str(provider.get("plugin_id") or value.get("plugin_id") or "")
        if "tool" in kind or name.startswith("evidence_first_"):
            row = {"type": kind, "name": name, "pluginId": plugin_id}
            if row not in found:
                found.append(row)
        for child in value.values():
            if isinstance(child, (dict, list)):
                visit(child)

    visit(payload.get("output") or [])
    return found


def _native_mcp_verified(provenance: list[dict[str, str]]) -> bool:
    return any(
        row.get("pluginId") == "mcp/evidence-first"
        and row.get("name") in {"evidence_first_contract", "evidence_first_validate"}
        for row in provenance
    )


def chat(
    *,
    base_url: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    enable_mcp: bool,
    timeout: int,
    require_mcp: bool,
) -> tuple[str, str, list[dict[str, str]]]:
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    rest_payload: dict[str, Any] = {
        "model": model,
        "input": user_prompt,
        "system_prompt": (
            "/no_think\nReturn only the final answer, without hidden reasoning, in at most 500 words.\n"
            + system_prompt
        ),
        "temperature": 0,
        "max_output_tokens": 1600,
        "store": False,
    }
    if enable_mcp:
        rest_payload["integrations"] = [
            {
                "type": "plugin",
                "id": "mcp/evidence-first",
                "allowed_tools": [
                    "evidence_first_contract",
                    "evidence_first_validate",
                    "evidence_first_status",
                ],
            }
        ]
    try:
        response = _post_json(f"{base}/api/v1/chat", rest_payload, timeout)
        text = _extract_rest_text(response)
        provenance = _tool_provenance(response)
        if not text:
            raise RuntimeError("LM Studio REST response contained no assistant message")
        if enable_mcp and require_mcp and not _native_mcp_verified(provenance):
            raise RuntimeError("native LM Studio response did not prove an evidence-first MCP tool call")
        return text, "rest-v1", provenance
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, RuntimeError) as exc:
        label = "MCP request" if enable_mcp else "chat request"
        raise RuntimeError(f"native LM Studio {label} failed: {exc}") from exc


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, dict[str, list[float]]] = defaultdict(lambda: {"off": [], "on": []})
    for row in rows:
        grouped[row["domain"]][row["condition"]].append(float(row["score"]))

    domains: dict[str, Any] = {}
    for domain, conditions in sorted(grouped.items()):
        off = sum(conditions["off"]) / len(conditions["off"]) if conditions["off"] else 0.0
        on = sum(conditions["on"]) / len(conditions["on"]) if conditions["on"] else 0.0
        delta = on - off
        relative = (delta / off * 100.0) if off else None
        sample_count = min(len(conditions["off"]), len(conditions["on"]))
        unique_cases = len(
            {
                row["id"]
                for row in rows
                if row["domain"] == domain and row["condition"] == "on" and row.get("id")
            }
        ) or sample_count
        domains[domain] = {
            "skillOffScore": round(off, 2),
            "skillOnScore": round(on, 2),
            "absoluteImprovementPoints": round(delta, 2),
            "relativeImprovementPercent": round(relative, 2) if relative is not None else None,
            "pairedSamples": sample_count,
            "uniqueCases": unique_cases,
            "confidence": "benchmark" if unique_cases >= 30 else "exploratory",
        }
    all_off = [float(row["score"]) for row in rows if row["condition"] == "off"]
    all_on = [float(row["score"]) for row in rows if row["condition"] == "on"]
    off_mean = sum(all_off) / len(all_off) if all_off else 0.0
    on_mean = sum(all_on) / len(all_on) if all_on else 0.0
    delta = on_mean - off_mean
    return {
        "skillOffScore": round(off_mean, 2),
        "skillOnScore": round(on_mean, 2),
        "absoluteImprovementPoints": round(delta, 2),
        "relativeImprovementPercent": round(delta / off_mean * 100.0, 2) if off_mean else None,
        "pairedSamples": min(len(all_off), len(all_on)),
        "confidence": "benchmark" if min(len(all_off), len(all_on)) >= 30 else "exploratory",
        "domains": domains,
    }


def run_live(args: argparse.Namespace, cases: list[dict[str, Any]]) -> dict[str, Any]:
    if not args.model:
        raise ValueError("--model is required for --live")
    selected = [case for case in cases if not args.domain or case["domain"] in args.domain]
    if not selected:
        raise ValueError("no benchmark cases matched --domain")
    rows: list[dict[str, Any]] = []
    on_prompt = skill_system_prompt()
    for repeat in range(args.repeats):
        for case_index, case in enumerate(selected):
            user_prompt = build_user_prompt(case)
            conditions = [
                ("off", OFF_SYSTEM_PROMPT, False),
                ("on", on_prompt, not args.skip_mcp),
            ]
            if (repeat + case_index) % 2:
                conditions.reverse()
            for condition, system_prompt, enable_mcp in conditions:
                started = time.monotonic()
                error = ""
                try:
                    answer, api, provenance = chat(
                        base_url=args.url,
                        model=args.model,
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                        enable_mcp=enable_mcp,
                        timeout=args.timeout,
                        require_mcp=args.require_mcp,
                    )
                    score = score_answer(case, answer)
                except RuntimeError as exc:
                    answer, api, provenance = "", "rest-v1", []
                    error = str(exc)
                    score = {
                        "score": 0.0,
                        "passed": False,
                        "requiredHitRate": 0.0,
                        "structureHitRate": 0.0,
                        "missingRequired": list(case.get("requiredPatterns") or []),
                        "forbiddenHits": [],
                        "missingStructure": list(case.get("structurePatterns") or []),
                    }
                row = {
                    "id": case["id"],
                    "domain": case["domain"],
                    "repeat": repeat + 1,
                    "condition": condition,
                    "api": api,
                    "toolProvenance": provenance,
                    "mcpVerified": _native_mcp_verified(provenance) if enable_mcp else False,
                    "elapsedSeconds": round(time.monotonic() - started, 2),
                    "answer": answer,
                    "error": error,
                    **score,
                }
                rows.append(row)
                print(
                    f"[{condition.upper()}] {case['id']} score={score['score']:.2f} api={api}"
                    + (f" error={error}" if error else ""),
                    file=sys.stderr,
                )
                if args.output:
                    checkpoint = {
                        "ok": False,
                        "mode": "live-paired-partial",
                        "model": args.model,
                        "summary": summarize(rows),
                        "rows": rows,
                    }
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    args.output.write_text(
                        json.dumps(checkpoint, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
    errors = [row for row in rows if row.get("error")]
    mcp_verified = all(
        row["mcpVerified"]
        for row in rows
        if row["condition"] == "on"
    ) if not args.skip_mcp else False
    return {
        "ok": not errors and (not args.require_mcp or mcp_verified),
        "mode": "live-paired",
        "model": args.model,
        "mcpRequestedForSkillOn": not args.skip_mcp,
        "mcpVerifiedForAllSkillOn": mcp_verified,
        "errors": len(errors),
        "summary": summarize(rows),
        "rows": rows,
    }


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--model", default="")
    parser.add_argument("--url", default="http://localhost:1234")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--domain", action="append", default=[])
    parser.add_argument("--skip-mcp", action="store_true")
    parser.add_argument("--require-mcp", action="store_true")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        cases = load_cases(args.cases)
        result = run_live(args, cases) if args.live else evaluate_fixtures(cases)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError, urllib.error.URLError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
