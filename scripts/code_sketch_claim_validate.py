#!/usr/bin/env python
"""Validate Unreal API names in a drafted code sketch against the RAG symbol index.

Small local models frequently emit plausible-but-nonexistent Unreal APIs when
asked for a "code sketch / 시안" in plain chat. This validator extracts the
Unreal-style symbols from a draft, checks each one against the local symbol
index (positive existence check) and a curated denylist (negative check), and
returns a per-symbol verdict so the model can downgrade or remove unverified
APIs before presenting compile-ready code.

Verdicts:
- ``known_bad``: symbol matches the invented-API / wrong-lifecycle denylist.
- ``verified``: an exact or prefix symbol match exists in the index.
- ``weak``: only a fuzzy/semantic match exists; treat as needs-confirmation.
- ``unverified``: no index evidence found; must not be presented as real API.

This tool never writes files and never runs a build. It is evidence only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# Unreal-style identifiers: prefixed types (U/A/F/S/I + PascalCase) and common
# gameplay class suffixes. Mirrors the extraction used elsewhere in the stack.
SYMBOL_RES = (
    re.compile(r"\b[AUFSI][A-Z][A-Za-z0-9_]{2,}\b"),
    re.compile(r"\b[A-Z][A-Za-z0-9_]+(?:Component|Subsystem|Character|Actor|GameMode|Widget|Manager)\b"),
)
# Method/member calls the model asserts exist, e.g. Player->SetRestoreState(...).
MEMBER_CALL_RE = re.compile(r"(?:->|\.)\s*([A-Za-z_][A-Za-z0-9_]{2,})\s*\(")

# Identifiers that are ubiquitous UE building blocks; skipping them keeps the
# report focused on the risky, request-specific symbols.
COMMON_SAFE = {
    "UObject", "AActor", "UActorComponent", "USceneComponent", "UClass",
    "FString", "FName", "FText", "FVector", "FRotator", "FTransform",
    "UWorld", "APawn", "ACharacter", "APlayerController", "AGameModeBase",
    "UWorldSubsystem", "UGameInstanceSubsystem", "UEngineSubsystem",
}


def extract_symbols(text: str) -> list[str]:
    found: list[str] = []
    for pattern in SYMBOL_RES:
        for match in pattern.finditer(text or ""):
            sym = match.group(0)
            if sym not in found:
                found.append(sym)
    return found


def extract_member_calls(text: str) -> list[str]:
    found: list[str] = []
    for match in MEMBER_CALL_RE.finditer(text or ""):
        name = match.group(1)
        if name and name not in found:
            found.append(name)
    return found


def _resolve_index(index: str | Path | None) -> Path:
    if index:
        return Path(index).resolve()
    from workspace_paths import resolve_index_path

    return resolve_index_path()


def _lookup(index: Path, symbol: str, top_k: int = 5) -> list[dict[str, Any]]:
    from rag_semantic import symbol_lookup

    try:
        return symbol_lookup(index, symbol, top_k=top_k)
    except Exception:
        return []


def _classify_symbol(symbol: str, rows: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    target = symbol.lower()
    exact: list[dict[str, Any]] = []
    prefix: list[dict[str, Any]] = []
    for row in rows:
        name = str(row.get("symbol_name") or "").lower()
        if not name:
            continue
        if name == target:
            exact.append(row)
        elif name.startswith(target) or target.startswith(name):
            prefix.append(row)
    evidence = [
        {
            "symbol_name": r.get("symbol_name"),
            "symbol_kind": r.get("symbol_kind"),
            "title": r.get("title"),
            "locator": r.get("locator"),
        }
        for r in (exact or prefix)[:3]
    ]
    if exact:
        return "verified", evidence
    if prefix:
        return "weak", evidence
    return "unverified", []


def validate_sketch(
    sketch: str,
    index: str | Path | None = None,
    *,
    top_k: int = 5,
) -> dict[str, Any]:
    from unreal_api_denylist import check_denylist

    index_path = _resolve_index(index)
    index_exists = index_path.exists()

    denylist_hits = check_denylist(sketch)
    denied_terms = {hit["term"] for hit in denylist_hits}

    candidates = extract_symbols(sketch)
    member_calls = extract_member_calls(sketch)

    results: list[dict[str, Any]] = []
    seen: set[str] = set()

    for hit in denylist_hits:
        term = hit["term"]
        if term in seen:
            continue
        seen.add(term)
        results.append(
            {
                "symbol": term,
                "verdict": "known_bad",
                "evidence": [],
                "note": hit["message"],
            }
        )

    def _consider(symbol: str, *, is_member: bool) -> None:
        key = symbol.lower()
        if key in seen or key in denied_terms:
            return
        if symbol in COMMON_SAFE:
            return
        seen.add(key)
        if not index_exists:
            results.append(
                {
                    "symbol": symbol,
                    "verdict": "unverified",
                    "evidence": [],
                    "note": "RAG index not found; cannot verify. Confirm against the actual header.",
                }
            )
            return
        rows = _lookup(index_path, symbol, top_k=top_k)
        verdict, evidence = _classify_symbol(symbol, rows)
        note = ""
        if verdict == "unverified":
            note = (
                "No symbol evidence in index. Do not present as a real API; "
                "confirm against the engine header or mark UNKNOWN."
            )
        elif verdict == "weak":
            note = "Only a fuzzy match found; confirm the exact name/signature before use."
        if is_member and verdict != "verified":
            note = note or "Member/method call not confirmed in index; verify the exact signature."
        results.append(
            {
                "symbol": symbol,
                "verdict": verdict,
                "evidence": evidence,
                "note": note,
            }
        )

    for symbol in candidates:
        _consider(symbol, is_member=False)
    for symbol in member_calls:
        _consider(symbol, is_member=True)

    known_bad = sum(1 for r in results if r["verdict"] == "known_bad")
    unverified = sum(1 for r in results if r["verdict"] == "unverified")
    weak = sum(1 for r in results if r["verdict"] == "weak")

    return {
        "ok": known_bad == 0 and unverified == 0,
        "indexPath": str(index_path),
        "indexExists": index_exists,
        "symbolCount": len(results),
        "knownBadCount": known_bad,
        "unverifiedCount": unverified,
        "weakCount": weak,
        "results": results,
        "guidance": (
            "Remove or downgrade every known_bad/unverified symbol before presenting "
            "compile-ready code. Keep proof level at Proposed."
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate Unreal API names in a code sketch.")
    parser.add_argument("--sketch", default="", help="Sketch text to validate")
    parser.add_argument("--sketch-file", default="", help="File containing sketch text")
    parser.add_argument("--index", default="", help="Path to rag.sqlite (defaults to workspace index)")
    parser.add_argument("--top-k", type=int, default=5)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    sketch = args.sketch
    if args.sketch_file:
        sketch = Path(args.sketch_file).read_text(encoding="utf-8-sig")
    if not sketch.strip():
        print("No sketch text provided. Use --sketch or --sketch-file.", file=sys.stderr)
        return 2
    payload = validate_sketch(sketch, args.index or None, top_k=args.top_k)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
