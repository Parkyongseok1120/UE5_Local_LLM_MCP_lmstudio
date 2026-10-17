"""Bounded implementation (.cpp) candidates for an engine declaration.

A header declaration says what a function is, not what it does. After
``engine_header_evidence`` finds the declaring type, this module looks in the
declaring module's ``Private`` tree for ``Owner::Symbol(...) {`` definitions and
returns a short body excerpt, so an audit can read the real behaviour (for
example the value ``AActor::TakeDamage`` returns) instead of guessing it.

The search shares the caller's budget (deadline, files, bytes). It never
proves absence: a missing definition may sit in an unsearched module, behind a
macro, or in a file beyond the budget, and the result says so.
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from cpp_parse_utils import mask_comments_and_strings

DEFINITION_FILE_LIMIT_BYTES = 786432
DEFINITION_EXCERPT_LINES = 40
DEFINITION_EXCERPT_CHARS = 3200
MAX_CANDIDATES_PER_ROW = 3
MAX_PRIVATE_ENTRIES = 6000
_SKIP_DIRS = frozenset({".git", "Binaries", "DerivedDataCache", "Intermediate", "Saved", "ThirdParty", "Tests"})
_STATEMENT_WORDS = re.compile(r"\b(?:return|if|while|for|switch|else|new|delete|throw|case|do)\b")

ReadSource = Callable[[Path, Path, int, int], "tuple[bytes, bool] | str"]
Lexical = Callable[[str], str]


@dataclass
class DefinitionBudget:
    """Mutable allowance shared with the declaration search."""

    deadline: float
    max_files: int
    max_bytes: int
    inspected: int = 0
    read_bytes: int = 0
    reasons: set[str] = field(default_factory=set)

    def exhausted(self) -> bool:
        if time.monotonic() >= self.deadline:
            self.reasons.add("deadline")
        elif self.inspected >= self.max_files:
            self.reasons.add("file_limit")
        elif self.read_bytes >= self.max_bytes:
            self.reasons.add("read_limit")
        else:
            return False
        return True


def _balanced(text: str, open_index: int, opener: str, closer: str, limit: int = 8192) -> int:
    depth = 0
    for index in range(open_index, min(len(text), open_index + limit)):
        char = text[index]
        if char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return index
    return -1


def find_definitions(text: str, owner: str, symbol: str, lexical: Lexical) -> list[dict[str, Any]]:
    """Definitions ``Owner::Symbol(...) [qualifiers] {`` with a body excerpt.

    Calls such as ``Super::Symbol(...)`` or ``Owner::Symbol(...);`` are not
    definitions: the text before the name must read like a return type and the
    parameter list must be followed by a body.
    """
    if f"{owner}::{symbol}" not in re.sub(r"\s*::\s*", "::", text):
        return []
    masked = lexical(text)
    lines = text.splitlines()
    pattern = re.compile(r"(?<![\w:])" + re.escape(owner) + r"\s*::\s*" + re.escape(symbol) + r"\s*\(")
    found: list[dict[str, Any]] = []
    for match in pattern.finditer(masked):
        close = _balanced(masked, match.end() - 1, "(", ")")
        if close < 0:
            continue
        tail = masked[close + 1:close + 320]
        body_open = re.match(
            r"\s*(?:const\b\s*)?(?:noexcept\b\s*)?(?:override\b\s*)?(?:final\b\s*)?(?:&&?\s*)?(\{)", tail)
        if not body_open:
            continue
        boundary = max(masked.rfind(";", 0, match.start()), masked.rfind("}", 0, match.start()),
                       masked.rfind("{", 0, match.start()))
        prefix = masked[boundary + 1:match.start()]
        # Drop preprocessor lines that sit between the previous statement and the signature.
        prefix = "\n".join(line for line in prefix.split("\n") if not line.lstrip().startswith("#"))
        if any(c in prefix for c in "=().") or "->" in prefix or _STATEMENT_WORDS.search(prefix):
            continue
        signature_start = boundary + 1 + (len(masked[boundary + 1:match.start()])
                                          - len(masked[boundary + 1:match.start()].lstrip()))
        brace = close + 1 + body_open.end(1) - 1
        end = _balanced(masked, brace, "{", "}", limit=200000)
        start_line = text.count("\n", 0, signature_start) + 1
        end_line = text.count("\n", 0, end if end >= 0 else len(text)) + 1
        window = lines[start_line - 1:min(end_line, start_line - 1 + DEFINITION_EXCERPT_LINES)]
        excerpt = "\n".join(window)
        found.append({
            "line": start_line,
            "endLine": end_line if end >= 0 else None,
            "excerpt": excerpt[:DEFINITION_EXCERPT_CHARS],
            "excerptStartLine": start_line,
            "excerptTruncated": len(excerpt) > DEFINITION_EXCERPT_CHARS or end_line - start_line + 1 > DEFINITION_EXCERPT_LINES
                                 or end < 0,
        })
    return found


def _stem_hints(owner: str, header: Path) -> list[str]:
    bare = owner[1:] if len(owner) > 1 and owner[0] in "AUFSEI" and owner[1].isupper() else owner
    return list(dict.fromkeys(hint.lower() for hint in (bare, header.stem, owner) if hint))


def private_sources(module_root: Path, header: Path, owner: str, deadline: float) -> tuple[list[Path], set[str]]:
    """``*.cpp`` under the module's Private tree, best name matches first."""
    reasons: set[str] = set()
    base = module_root / "Private"
    if not base.is_dir():
        return [], {"definition_private_tree_missing"}
    hints = _stem_hints(owner, header)
    paths: list[Path] = []
    for directory, names, files in os.walk(base):
        names[:] = sorted(name for name in names if name not in _SKIP_DIRS)
        if time.monotonic() >= deadline:
            reasons.add("deadline")
            break
        for file_name in sorted(files):
            if file_name.lower().endswith(".cpp"):
                paths.append(Path(directory) / file_name)
        if len(paths) >= MAX_PRIVATE_ENTRIES:
            reasons.add("definition_catalog_limit")
            break

    def rank(path: Path) -> tuple[int, int, str]:
        stem = path.stem.lower()
        exact = 0 if stem in hints else 1 if any(stem.startswith(hint) for hint in hints) else 2
        return exact, len(path.name), str(path)

    return sorted(paths, key=rank), reasons


def reflection_thunk_names(row: dict[str, Any]) -> list[str]:
    """Definition names for a declaration: the symbol itself plus the
    generated-thunk bodies a reflected function is implemented as.

    UHT declares ``X_Implementation``/``X_Validate`` only in the generated
    header, so the hand-written .cpp body of a BlueprintNativeEvent or RPC is
    ``Owner::X_Implementation``. The names come from the observed UFUNCTION
    specifiers, never from a guess about the symbol.
    """
    symbol = str(row.get("symbol_name") or "")
    names = [symbol]
    reflection = (row.get("specifiers") or {}).get("reflection") or {}
    if reflection.get("macro") != "UFUNCTION":
        return names
    # Category="Server" and meta=(WithValidation, ...) are metadata, not
    # function flags. Values such as Server=CustomImpl also do not establish
    # the default suffixed name. Keep only bare, top-level specifier tokens.
    arguments = mask_comments_and_strings(str(reflection.get("arguments") or ""))
    specifiers: set[str] = set()
    depth = start = 0
    for index, char in enumerate(arguments + ","):
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            specifiers.add(arguments[start:index].strip())
            start = index + 1
    if specifiers & {"BlueprintNativeEvent", "Server", "Client", "NetMulticast"}:
        names.append(f"{symbol}_Implementation")
    if "WithValidation" in specifiers:
        names.append(f"{symbol}_Validate")
    return names


def attach_definition_candidates(rows: list[dict[str, Any]], root: Path, budget: DefinitionBudget, *,
                                 read_source: ReadSource, lexical: Lexical) -> None:
    """Add ``definitionCandidates`` and ``definitionSearch`` to each method row."""
    for row in rows:
        if row.get("declarationKind") != "method":
            continue
        owner, symbol = str(row.get("declaredIn") or ""), str(row.get("symbol_name") or "")
        module = str(row.get("moduleRoot") or "")
        search: dict[str, Any] = {"scope": "declaring_module_private_cpp", "absenceVerified": False, "filesRead": 0,
                                  "definitionNames": reflection_thunk_names(row)}
        row["definitionSearch"] = search
        row["definitionCandidates"] = []
        if not owner or not symbol or not module:
            search.update(status="module_unknown", reasons=["definition_module_unknown"])
            continue
        candidates, reasons = private_sources(Path(module), Path(str(row.get("source") or "")), owner, budget.deadline)
        reasons = set(reasons)
        for source in candidates:
            if len(row["definitionCandidates"]) >= MAX_CANDIDATES_PER_ROW:
                break
            if budget.exhausted():
                reasons |= budget.reasons
                break
            observation = read_source(source, root, budget.max_bytes - budget.read_bytes, DEFINITION_FILE_LIMIT_BYTES)
            if isinstance(observation, str):
                reasons.add(observation)
                continue
            raw, complete = observation
            budget.read_bytes += len(raw)
            budget.inspected += 1
            search["filesRead"] += 1
            if not complete:
                reasons.add("definition_file_partial")
            text = raw.decode("utf-8-sig", errors="replace")
            for name, definition in ((name, d) for name in reflection_thunk_names(row)
                                     for d in find_definitions(text, owner, name, lexical)):
                row["definitionCandidates"].append({
                    "source": str(source.resolve()), "declaredIn": owner, "symbol_name": name, **definition,
                    "definitionStatus": "lexical_definition_candidate",
                    "fingerprint": {"sha256": hashlib.sha256(raw).hexdigest(),
                                    "hashScope": "file" if complete else "byte_range",
                                    "readByteRange": [0, len(raw)], "readBytes": len(raw), "completeFile": complete}})
                if len(row["definitionCandidates"]) >= MAX_CANDIDATES_PER_ROW:
                    break
        reasons |= {reason for reason in budget.reasons}
        found = bool(row["definitionCandidates"])
        search["status"] = "candidates_found" if found else "none_found_in_searched_files"
        if len(row["definitionCandidates"]) >= MAX_CANDIDATES_PER_ROW:
            reasons.add("candidate_limit")
        search["reasons"] = sorted(reasons)
