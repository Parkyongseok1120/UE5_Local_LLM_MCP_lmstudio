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
- ``verified``: an exact symbol match exists in the index or project graph.
- ``weak``: only a graph prefix or owner-less match exists; treat as needs-confirmation.
- ``unverified``: no index evidence found; must not be presented as real API.

This tool never writes files and never runs a build. It is evidence only.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
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
MEMBER_CALL_CLAIM_RE = re.compile(
    r"\b(?P<receiver>[A-Za-z_][A-Za-z0-9_]*)\s*(?:->|\.)\s*"
    r"(?P<member>[A-Za-z_][A-Za-z0-9_]{2,})\s*\("
)
STATIC_CALL_CLAIM_RE = re.compile(
    r"\b(?P<receiver>[AUFSI][A-Z][A-Za-z0-9_]*)\s*::\s*"
    r"(?P<member>[A-Za-z_][A-Za-z0-9_]{2,})\s*\("
)
VARIABLE_TYPE_RE = re.compile(
    r"\b(?P<type>[AUFSI][A-Z][A-Za-z0-9_:]*)\s*(?:[*&]\s*)?"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\b"
)
TEMPLATE_VARIABLE_TYPE_RE = re.compile(
    r"\b(?:TObjectPtr|TWeakObjectPtr|TSoftObjectPtr|TSubclassOf)\s*<\s*"
    r"(?P<type>[AUFSI][A-Z][A-Za-z0-9_:]*)\s*>\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\b"
)
LOCAL_TYPE_DECL_RE = re.compile(
    r"\b(?:class|struct|enum(?:\s+class)?)\s+(?:[A-Z0-9_]+_API\s+)?([AUFSI][A-Z][A-Za-z0-9_]{2,})\b"
)
LOCAL_INHERITANCE_SKETCH_RE = re.compile(
    r"(?m)^\s*([AUFSI][A-Z][A-Za-z0-9_]{2,})\s*:\s*(?:public\s+)?"
    r"[AUFSI][A-Z][A-Za-z0-9_]{2,}\s*$"
)
LOCAL_DELEGATE_DECL_RE = re.compile(
    r"\bDECLARE_(?:DYNAMIC_)?MULTICAST_DELEGATE(?:_[A-Za-z]+Params?)?\s*\(\s*([A-Za-z_]\w*)"
)

# Identifiers that are ubiquitous UE building blocks; skipping them keeps the
# report focused on the risky, request-specific symbols.
COMMON_SAFE = {
    "UObject", "AActor", "UActorComponent", "USceneComponent", "UClass",
    "FString", "FName", "FText", "FVector", "FRotator", "FTransform",
    "UWorld", "APawn", "ACharacter", "APlayerController", "AGameModeBase", "AGameStateBase",
    "UWorldSubsystem", "UGameInstanceSubsystem", "UEngineSubsystem",
    "UCLASS", "USTRUCT", "UENUM", "UFUNCTION", "UPROPERTY", "UINTERFACE",
}

# This is an input safety boundary, not a symbol-count policy.  Large feature
# drafts must be split into the active implementation slice before validation.
MAX_SKETCH_CHARS = 12_000
EXACT_LOOKUP_BATCH_SIZE = 400


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


def extract_member_call_claims(text: str) -> list[dict[str, str]]:
    variable_types: dict[str, str] = {}
    for pattern in (VARIABLE_TYPE_RE, TEMPLATE_VARIABLE_TYPE_RE):
        for match in pattern.finditer(text or ""):
            variable_types[match.group("name")] = match.group("type").split("::")[-1]

    claims: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for match in MEMBER_CALL_CLAIM_RE.finditer(text or ""):
        receiver = match.group("receiver")
        member = match.group("member")
        receiver_type = variable_types.get(receiver, "")
        key = (receiver, receiver_type, member)
        if key not in seen:
            seen.add(key)
            claims.append(
                {
                    "receiver": receiver,
                    "receiverType": receiver_type,
                    "member": member,
                    "callKind": "member",
                }
            )
    for match in STATIC_CALL_CLAIM_RE.finditer(text or ""):
        receiver_type = match.group("receiver").split("::")[-1]
        member = match.group("member")
        key = (receiver_type, receiver_type, member)
        if key not in seen:
            seen.add(key)
            claims.append(
                {
                    "receiver": receiver_type,
                    "receiverType": receiver_type,
                    "member": member,
                    "callKind": "static",
                }
            )
    return claims


def extract_local_declarations(text: str) -> set[str]:
    """Return symbols introduced by the sketch itself, not claimed as engine APIs."""
    declared = {match.group(1) for match in LOCAL_TYPE_DECL_RE.finditer(text or "")}
    # Architecture sketches commonly use ``ANewType : AActor`` instead of a
    # complete C++ class declaration. Treat only this anchored inheritance form
    # as a proposed local declaration; engine/member claims remain fail-closed.
    declared.update(
        match.group(1) for match in LOCAL_INHERITANCE_SKETCH_RE.finditer(text or "")
    )
    declared.update(match.group(1) for match in LOCAL_DELEGATE_DECL_RE.finditer(text or ""))
    return declared



def _resolve_index(index: str | Path | None) -> Path:
    if index:
        return Path(index).resolve()
    from workspace_paths import resolve_index_path

    return resolve_index_path()


def _lookup(index: Path, symbol: str, top_k: int = 5) -> list[dict[str, Any]]:
    """Legacy semantic lookup retained for explicit, non-gating API searches.

    Sketch validation deliberately does not call this path: a cache miss can
    require several broad LIKE scans of a large RAG database, and fuzzy rows do
    not provide enough evidence to pass the fail-closed sketch gate anyway.
    """
    from rag_semantic import symbol_lookup

    try:
        return symbol_lookup(index, symbol, top_k=top_k)
    except Exception:
        return []


def _lookup_many_exact(
    index: Path,
    symbols: list[str],
    *,
    top_k: int = 5,
) -> tuple[dict[str, list[dict[str, Any]]], str, int]:
    """Resolve exact symbol names with bounded, indexed, read-only SQL batches."""

    ordered = list(dict.fromkeys(symbol for symbol in symbols if symbol))
    if not ordered or not index.is_file():
        return {}, "", 0

    rows_by_symbol: dict[str, list[dict[str, Any]]] = {}
    query_count = 0
    try:
        connection = sqlite3.connect(
            f"{index.resolve().as_uri()}?mode=ro",
            uri=True,
            timeout=1.0,
        )
        connection.row_factory = sqlite3.Row
        try:
            columns = {
                str(row[1])
                for row in connection.execute("pragma table_info(chunks)").fetchall()
            }
            required = {"symbol_name", "symbol_kind", "title", "locator"}
            if not required.issubset(columns):
                missing = ", ".join(sorted(required - columns))
                return {}, f"RAG chunks schema is missing required columns: {missing}", 0
            optional = [
                column
                for column in ("source", "project", "module_name", "metadata_json")
                if column in columns
            ]
            select_columns = ["symbol_name", "symbol_kind", "title", "locator", *optional]
            for start in range(0, len(ordered), EXACT_LOOKUP_BATCH_SIZE):
                batch = ordered[start : start + EXACT_LOOKUP_BATCH_SIZE]
                placeholders = ",".join("?" for _ in batch)
                query_count += 1
                matches = connection.execute(
                    f"select {', '.join(select_columns)} from chunks "
                    f"where symbol_name in ({placeholders}) "
                    "order by symbol_name, title, locator",
                    batch,
                ).fetchall()
                for match in matches:
                    item = dict(match)
                    metadata: dict[str, Any] = {}
                    raw_metadata = item.pop("metadata_json", "")
                    if raw_metadata:
                        try:
                            decoded = json.loads(str(raw_metadata))
                            metadata = decoded if isinstance(decoded, dict) else {}
                        except json.JSONDecodeError:
                            metadata = {}
                    qualified = str(
                        metadata.get("qualified_name")
                        or metadata.get("qualifiedName")
                        or ""
                    )
                    if qualified:
                        item["qualified_name"] = qualified
                    item["evidence_source"] = "rag_index_exact"
                    key = str(item.get("symbol_name") or "").casefold()
                    bucket = rows_by_symbol.setdefault(key, [])
                    if len(bucket) < top_k:
                        bucket.append(item)
        finally:
            connection.close()
    except (OSError, sqlite3.Error, ValueError) as exc:
        return {}, f"{type(exc).__name__}: {exc}", query_count
    return rows_by_symbol, "", query_count


def _graph_symbol_index(graph: dict[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    if not isinstance(graph, dict):
        return index
    for raw in graph.get("symbols") or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("symbol_name") or "").casefold()
        if name:
            index.setdefault(name, []).append(raw)
    return index


def _graph_lookup(
    graph: dict[str, Any] | None,
    symbol: str,
    top_k: int = 5,
    *,
    symbol_index: dict[str, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(graph, dict):
        return []
    target = symbol.casefold()
    exact: list[dict[str, Any]] = []
    prefix: list[dict[str, Any]] = []
    active_index = symbol_index if symbol_index is not None else _graph_symbol_index(graph)
    candidate_rows: list[dict[str, Any]] = list(active_index.get(target, []))
    if len(candidate_rows) < top_k:
        for folded, rows in active_index.items():
            if folded != target and (folded.startswith(target) or target.startswith(folded)):
                candidate_rows.extend(rows)
    for raw in candidate_rows:
        name = str(raw.get("symbol_name") or "")
        folded = name.casefold()
        if folded == target:
            bucket = exact
        elif folded.startswith(target) or target.startswith(folded):
            bucket = prefix
        else:
            continue
        row = dict(raw)
        row.setdefault("title", row.get("qualified_name") or name)
        path = str(row.get("file_path") or "")
        line = int(row.get("line_start") or 1)
        row.setdefault("locator", f"{path}:{line}" if path else "")
        row["evidence_source"] = "project_symbol_graph"
        bucket.append(row)
    return (exact + prefix)[:top_k]


def _receiver_owner_chain(graph: dict[str, Any] | None, receiver_type: str) -> set[str]:
    """Return the receiver and project-local base types allowed to own a member."""
    receiver = receiver_type.split("::")[-1].strip()
    if not receiver:
        return set()
    bases: dict[str, str] = {}
    for row in (graph or {}).get("symbols") or []:
        if not isinstance(row, dict) or row.get("symbol_kind") not in {"class", "struct"}:
            continue
        name = str(row.get("symbol_name") or "").split("::")[-1]
        base = str(row.get("base_class") or "").split("::")[-1]
        if name:
            bases[name.casefold()] = base
    owners = {receiver.casefold()}
    current = receiver
    while current and len(owners) < 32:
        base = bases.get(current.casefold(), "")
        folded = base.casefold()
        if not base or folded in owners:
            break
        owners.add(folded)
        current = base
    return owners


def _row_qualified_owner(row: dict[str, Any], symbol: str) -> str:
    qualified = str(row.get("qualified_name") or "").strip()
    if "::" in qualified:
        owner, member = qualified.rsplit("::", 1)
        if member.casefold() == symbol.casefold():
            return owner.split("::")[-1]
    return ""


def _classify_symbol(
    symbol: str,
    rows: list[dict[str, Any]],
    *,
    receiver_type: str = "",
    acceptable_owners: set[str] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
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
            "qualified_name": r.get("qualified_name"),
            "title": r.get("title"),
            "locator": r.get("locator"),
            "source": r.get("evidence_source") or "rag_index",
        }
        for r in (exact or prefix)[:3]
    ]
    if exact:
        if receiver_type:
            expected_owners = acceptable_owners or {receiver_type.split("::")[-1].casefold()}
            qualified_owners = {
                _row_qualified_owner(row, symbol).casefold()
                for row in exact
                if _row_qualified_owner(row, symbol)
            }
            if expected_owners & qualified_owners:
                return "verified", evidence
            if qualified_owners:
                return "unverified", evidence
            return "weak", evidence
        return "verified", evidence
    if prefix:
        return "weak", evidence
    return "unverified", []


def validate_sketch(
    sketch: str,
    index: str | Path | None = None,
    *,
    top_k: int = 5,
    graph: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from unreal_api_denylist import check_denylist

    sketch_chars = len(sketch or "")
    if sketch_chars > MAX_SKETCH_CHARS:
        return {
            "ok": False,
            "errorCode": "SKETCH_TOO_LARGE",
            "error": (
                f"Sketch is {sketch_chars} characters; the validation limit is "
                f"{MAX_SKETCH_CHARS}."
            ),
            "retryable": True,
            "verdictSummary": "Sketch was not inspected because it exceeds the active-slice size limit.",
            "indexPath": str(index or ""),
            "indexExists": False,
            "projectGraphAvailable": False,
            "projectGraphSymbolCount": 0,
            "symbolCount": 0,
            "localDeclarationCount": 0,
            "verifiedCount": 0,
            "knownBadCount": 0,
            "unverifiedCount": 0,
            "weakCount": 0,
            "results": [],
            "sketchCharCount": sketch_chars,
            "maxSketchChars": MAX_SKETCH_CHARS,
            "indexLookupMode": "not_started",
            "indexLookupSymbolCount": 0,
            "indexLookupQueryCount": 0,
            "guidance": (
                "Split the draft to the current target-file slice and validate that smaller sketch. "
                "Do not checkpoint or claim the code-sketch gate complete."
            ),
            "agentInstruction": (
                "Split to the active slice, keep targetFiles exact, and call "
                "unreal_code_sketch_claim_validate once with the smaller draft."
            ),
        }

    index_path = _resolve_index(index)
    index_exists = index_path.exists()
    graph_available = isinstance(graph, dict) and isinstance(graph.get("symbols"), list)
    graph_index = _graph_symbol_index(graph)

    denylist_hits = check_denylist(sketch)
    denied_terms = {hit["term"] for hit in denylist_hits}

    candidates = extract_symbols(sketch)
    member_claims = extract_member_call_claims(sketch)
    local_declarations = extract_local_declarations(sketch)
    lookup_symbols: list[str] = []
    for symbol in [*candidates, *(claim["member"] for claim in member_claims)]:
        if symbol in COMMON_SAFE or symbol in local_declarations:
            continue
        if symbol.casefold() in denied_terms:
            continue
        if symbol not in lookup_symbols:
            lookup_symbols.append(symbol)
    exact_rows, index_lookup_error, index_lookup_queries = _lookup_many_exact(
        index_path,
        lookup_symbols,
        top_k=top_k,
    ) if index_exists else ({}, "", 0)

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
                "replacement": hit.get("replacement") or "",
            }
        )

    def _consider(symbol: str, *, is_member: bool, receiver_type: str = "", receiver: str = "") -> None:
        key = f"{receiver_type.casefold()}::{symbol.casefold()}" if is_member else symbol.casefold()
        if key in seen or symbol.casefold() in denied_terms:
            return
        if symbol in COMMON_SAFE:
            return
        if symbol in local_declarations:
            return
        seen.add(key)
        if not index_exists and not graph_available:
            results.append(
                {
                    "symbol": symbol,
                    "verdict": "unverified",
                    "evidence": [],
                    "note": "RAG index not found; cannot verify. Confirm against the actual header.",
                }
            )
            return
        graph_rows = _graph_lookup(
            graph,
            symbol,
            top_k=top_k,
            symbol_index=graph_index,
        )
        rag_rows = exact_rows.get(symbol.casefold(), [])
        rows = graph_rows + [
            row for row in rag_rows
            if not any(
                str(existing.get("symbol_name") or "").casefold() == str(row.get("symbol_name") or "").casefold()
                and str(existing.get("qualified_name") or "").casefold()
                == str(row.get("qualified_name") or "").casefold()
                for existing in graph_rows
            )
        ]
        verdict, evidence = _classify_symbol(
            symbol,
            rows,
            receiver_type=receiver_type if is_member else "",
            acceptable_owners=_receiver_owner_chain(graph, receiver_type) if is_member else None,
        )
        note = ""
        if verdict == "unverified":
            note = (
                "No exact symbol evidence in the project graph or index. Do not present as a real API; "
                "confirm against the engine header or mark UNKNOWN."
            )
        elif verdict == "weak":
            note = "Only a prefix or owner-less match found; confirm the exact receiver and signature before use."
        if is_member and receiver_type and verdict == "unverified" and evidence:
            note = (
                f"{symbol} exists, but no exact {receiver_type}::{symbol} owner match was found. "
                "Do not treat a method on another receiver as valid."
            )
        elif is_member and not receiver_type and verdict == "verified":
            verdict = "weak"
            note = (
                f"Receiver type for {receiver or 'member call'}->{symbol} could not be inferred; "
                "confirm the receiver class and exact signature."
            )
        elif is_member and verdict != "verified":
            note = note or "Member/method call not confirmed on the receiver type; verify the exact signature."
        results.append(
            {
                "symbol": symbol,
                **({"receiver": receiver, "receiverType": receiver_type} if is_member else {}),
                "verdict": verdict,
                "evidence": [] if verdict == "verified" else evidence,
                "note": note,
            }
        )

    for symbol in candidates:
        _consider(symbol, is_member=False)
    for claim in member_claims:
        _consider(
            claim["member"],
            is_member=True,
            receiver_type=claim["receiverType"],
            receiver=claim["receiver"],
        )

    known_bad = sum(1 for r in results if r["verdict"] == "known_bad")
    unverified = sum(1 for r in results if r["verdict"] == "unverified")
    weak = sum(1 for r in results if r["verdict"] == "weak")
    verified = sum(1 for r in results if r["verdict"] == "verified")
    known_bad_terms = [
        str(result["symbol"])
        for result in results
        if result["verdict"] == "known_bad"
    ]
    known_bad_suffix = f" ({', '.join(known_bad_terms[:4])})" if known_bad_terms else ""
    verdict_summary = (
        f"{verified} verified, {weak} weak, {known_bad} known_bad"
        f"{known_bad_suffix}, "
        f"{unverified} unverified"
    )
    verdict_order = {"known_bad": 0, "unverified": 1, "weak": 2, "verified": 3}
    results.sort(key=lambda item: (verdict_order.get(str(item.get("verdict")), 9), str(item.get("symbol"))))

    payload = {
        "ok": known_bad == 0 and unverified == 0 and weak == 0 and not index_lookup_error,
        "verdictSummary": verdict_summary,
        "indexPath": str(index_path),
        "indexExists": index_exists,
        "projectGraphAvailable": graph_available,
        "projectGraphSymbolCount": sum(len(rows) for rows in graph_index.values()),
        "symbolCount": len(results),
        "localDeclarationCount": len(local_declarations),
        "verifiedCount": verified,
        "knownBadCount": known_bad,
        "unverifiedCount": unverified,
        "weakCount": weak,
        "results": results,
        "sketchCharCount": sketch_chars,
        "maxSketchChars": MAX_SKETCH_CHARS,
        "indexLookupMode": "exact_batch" if index_exists else "index_unavailable",
        "indexLookupSymbolCount": len(lookup_symbols),
        "indexLookupQueryCount": index_lookup_queries,
        "guidance": (
            "Replace every known_bad item using its replacement, downgrade unverified or weak "
            "symbols to UNKNOWN, then validate receiver types and exact signatures before presenting it. "
            "Keep proof level at Proposed."
        ),
    }
    if index_lookup_error:
        payload.update(
            {
                "ok": False,
                "errorCode": "SKETCH_INDEX_LOOKUP_FAILED",
                "error": index_lookup_error,
                "retryable": True,
                "agentInstruction": (
                    "Check unreal_rag_health once; do not repeat the same sketch validation "
                    "until the index is readable."
                ),
            }
        )
    return payload


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
