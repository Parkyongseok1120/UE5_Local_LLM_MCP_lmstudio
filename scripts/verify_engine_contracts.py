#!/usr/bin/env python
"""Carry engine-fact evidence from a validated engine version to another one.

Each engine fact cites engine source lines checked on a validated version. A
fact holds on another installed version when the cited source window (the
cited line and its neighbours) appears unchanged there. This script finds that
window in the target engine and reports the target line, or names the
references whose source differs and therefore need a human review before the
target version can be listed as validated. It reads engine source only; with
``--write`` it records the found lines in the contract file.

Usage:
  python scripts/verify_engine_contracts.py --from 5.8 --from-root "<installed-engine-5.8>" \
      --to 5.7 --to-root "<installed-engine-5.7>" [--write]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from engine_contracts import CONTRACTS_PATH, evidence_line, validate_contracts  # noqa: E402

WINDOW = 3


def _lines(path: Path) -> list[str] | None:
    try:
        # Split on newlines only: splitlines() also breaks at form feeds and
        # other separators engine sources contain, which shifts line numbers.
        return [line.rstrip("\r") for line in path.read_text(encoding="utf-8-sig", errors="replace").split("\n")]
    except OSError:
        return None


def _normalized(line: str) -> str:
    return " ".join(line.split())


def _window(lines: list[str], line: int) -> tuple[list[str], int]:
    start, end = max(0, line - 1 - WINDOW), min(len(lines), line + WINDOW)
    return [_normalized(text) for text in lines[start:end]], line - 1 - start


def locate(source: list[str], target: list[str], line: int) -> int | None:
    """Target line whose neighbourhood equals the cited window, if unique."""
    window, offset = _window(source, line)
    if not any(window):
        return None
    normalized = [_normalized(text) for text in target]
    found = [index for index in range(len(normalized) - len(window) + 1)
             if normalized[index:index + len(window)] == window]
    return found[0] + offset + 1 if len(found) == 1 else None


def cited_line(source: list[str], target: list[str], line: int) -> int | None:
    """When only the neighbourhood changed (comments, blank lines), the cited
    declaration itself still carries over if it occurs exactly once."""
    wanted = _normalized(source[line - 1])
    if len(wanted) < 12:
        return None
    found = [index for index, text in enumerate(target) if _normalized(text) == wanted]
    return found[0] + 1 if len(found) == 1 else None


CARRIED = {"window_identical", "cited_line_identical"}


def verify(data: dict[str, Any], from_version: str, from_root: Path, to_version: str, to_root: Path) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    cache: dict[tuple[Path, str], list[str] | None] = {}

    def read(root: Path, relative: str) -> list[str] | None:
        key = (root, relative)
        if key not in cache:
            cache[key] = _lines(root / relative)
        return cache[key]

    for contract in data["contracts"]:
        if contract["kind"] != "engine_fact":
            continue
        for ref in contract["evidenceRefs"]:
            line = evidence_line(ref, from_version)
            entry = {"contract": contract["id"], "path": ref["path"], "symbol": ref["symbol"], "fromLine": line}
            source, target = read(from_root, ref["path"]), read(to_root, ref["path"])
            if line is None:
                entry["status"] = "no_cited_line"
            elif source is None or line > len(source):
                entry["status"] = "source_unreadable"
            elif target is None:
                entry["status"] = "target_file_missing"
            else:
                found = locate(source, target, line)
                cited = cited_line(source, target, line) if not found else None
                entry.update({"status": "window_identical", "toLine": found} if found
                             else {"status": "cited_line_identical", "toLine": cited} if cited
                             else {"status": "window_differs"})
            results.append(entry)
    differs = [r for r in results if r["status"] not in CARRIED]
    return {"from": from_version, "to": to_version, "references": len(results), "identical": len(results) - len(differs),
            "citedLineOnly": [r for r in results if r["status"] == "cited_line_identical"],
            "needsReview": differs, "results": results}


def record(data: dict[str, Any], report: dict[str, Any]) -> None:
    """Write per-version lines; only call when every reference was identical."""
    by_key = {(r["contract"], r["path"], r["symbol"]): r for r in report["results"]}
    for contract in data["contracts"]:
        if contract["kind"] != "engine_fact":
            continue
        for ref in contract["evidenceRefs"]:
            found = by_key[(contract["id"], ref["path"], ref["symbol"])]
            lines = dict(ref.get("lines") or {})
            if "line" in ref:
                lines[report["from"]] = ref.pop("line")
            lines[report["to"]] = found["toLine"]
            ref["lines"] = dict(sorted(lines.items()))
        versions = set(contract["applicability"]["engineVersions"]) | {report["to"]}
        contract["applicability"]["engineVersions"] = sorted(versions)
    data["validatedEngineVersions"] = sorted(set(data["validatedEngineVersions"]) | {report["to"]})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--contracts", default=str(CONTRACTS_PATH))
    parser.add_argument("--from", dest="from_version", required=True)
    parser.add_argument("--from-root", required=True)
    parser.add_argument("--to", dest="to_version", required=True)
    parser.add_argument("--to-root", required=True)
    parser.add_argument("--write", action="store_true", help="record target lines when every reference is identical")
    args = parser.parse_args(argv)
    path = Path(args.contracts)
    raw = path.read_text(encoding="utf-8-sig")
    data = validate_contracts(json.loads(raw))
    report = verify(data, args.from_version, Path(args.from_root), args.to_version, Path(args.to_root))
    summary = {k: v for k, v in report.items() if k != "results"}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if report["needsReview"]:
        return 1
    if args.write:
        record(data, report)
        validate_contracts(data)
        newline = "\r\n" if "\r\n" in raw else "\n"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2).replace("\n", newline) + newline, encoding="utf-8", newline="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
