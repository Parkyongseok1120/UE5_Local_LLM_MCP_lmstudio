#!/usr/bin/env python
"""Bounded, version-local Unreal Engine header evidence lookup.

The RAG index is an acceleration layer, not proof that an API is absent.  This
module supplies the next evidence tier by locating likely owner headers inside
the configured Engine root and returning exact declaration excerpts.  It is
read-only, path-contained, and uses a process-local filename catalog so the
persistent MCP server scans an Engine tree at most once per root.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterable


_HEADER_CATALOGS: dict[str, dict[str, list[Path]]] = {}
_TYPE_DECLARATION_PATHS: dict[tuple[str, str], list[Path]] = {}
_SKIP_DIRS = {
    ".git",
    "Binaries",
    "DerivedDataCache",
    "Intermediate",
    "Saved",
    "ThirdParty",
}


def _identity(path: Path) -> str:
    resolved = str(path.resolve())
    return resolved.casefold() if os.name == "nt" else resolved


def _contained(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _lexically_contained(path: Path, root: Path) -> bool:
    """Cheap containment for paths enumerated by a trusted contained walker."""
    try:
        path.absolute().relative_to(root.absolute())
        return True
    except ValueError:
        return False


def _engine_source_roots(engine_root: Path) -> list[Path]:
    source = engine_root / "Engine" / "Source"
    if source.is_dir():
        return [source]
    # Small synthetic/test SDKs may contain only a plugin tree. Real installed
    # plugin APIs are indexed by the normal UE RAG pipeline; recursively
    # cataloguing every installed plugin on each short-lived validator process
    # is prohibitively expensive and duplicates that evidence layer.
    plugins = engine_root / "Engine" / "Plugins"
    return [plugins] if plugins.is_dir() else []


def _header_catalog(engine_root: Path) -> dict[str, list[Path]]:
    key = _identity(engine_root)
    cached = _HEADER_CATALOGS.get(key)
    if cached is not None:
        return cached
    catalog: dict[str, list[Path]] = {}
    for source_root in _engine_source_roots(engine_root):
        rg = shutil.which("rg")
        if rg:
            try:
                completed = subprocess.run(
                    [rg, "--files", str(source_root), "-g", "*.h", "-g", "*.hpp", "-g", "*.inl"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=20,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                completed = None
            if completed is not None and completed.returncode == 0:
                for raw_path in completed.stdout.splitlines():
                    path = Path(raw_path.strip())
                    if path.name and _lexically_contained(path, engine_root):
                        catalog.setdefault(path.name.casefold(), []).append(path)
                continue
        for directory, names, files in os.walk(source_root):
            names[:] = [name for name in names if name not in _SKIP_DIRS]
            for file_name in files:
                if not file_name.lower().endswith((".h", ".hpp", ".inl")):
                    continue
                path = Path(directory) / file_name
                if not _contained(path, engine_root):
                    continue
                catalog.setdefault(file_name.casefold(), []).append(path)
    _HEADER_CATALOGS[key] = catalog
    return catalog


def _unqualified(value: str) -> str:
    return str(value or "").split("::")[-1].strip()


def _header_names(owner_or_symbol: str) -> list[str]:
    value = _unqualified(owner_or_symbol)
    if not value:
        return []
    stems = [value]
    if len(value) > 2 and value[0] in "AUFSI" and value[1].isupper():
        stems.append(value[1:])
    names: list[str] = []
    for stem in stems:
        for suffix in (".h", ".hpp", ".inl"):
            name = f"{stem}{suffix}"
            if name.casefold() not in {item.casefold() for item in names}:
                names.append(name)
    return names


def _declaration_excerpt(text: str, symbol: str, *, owner: str = "") -> tuple[int, str] | None:
    lines = text.splitlines()
    token = re.compile(rf"\b{re.escape(symbol)}\b")
    method = re.compile(rf"\b{re.escape(symbol)}\s*\(")
    type_decl = re.compile(
        rf"\b(?:(?:class|struct)\s+(?:\w+_API\s+)?{re.escape(symbol)}\b|"
        rf"enum(?:\s+class)?\s+(?:\w+_API\s+)?{re.escape(symbol)}\b)"
    )
    patterns = (method, token) if owner else (type_decl,)
    for pattern in patterns:
        for index, line in enumerate(lines):
            if not pattern.search(line):
                continue
            start = max(0, index - 2)
            end = min(len(lines), index + 4)
            return index + 1, "\n".join(lines[start:end]).strip()
    return None


def _discover_type_declaration_paths(
    engine_root: Path,
    catalog: dict[str, list[Path]],
    symbols: Iterable[str],
    *,
    max_header_chars: int,
) -> tuple[dict[str, list[Path]], int]:
    """Find declarations whose header name does not match the Unreal type.

    Unreal often groups small public types in owner headers (for example,
    FLifetimeProperty is declared in CoreNet.h). Scan all catalogued headers
    once for the unresolved batch and cache both hits and misses by Engine
    root, so a persistent MCP process does not repeatedly walk the SDK.
    """

    root_key = _identity(engine_root)
    wanted = {
        _unqualified(symbol): (root_key, _unqualified(symbol).casefold())
        for symbol in symbols
        if _unqualified(symbol)
    }
    resolved: dict[str, list[Path]] = {}
    missing: dict[str, tuple[str, str]] = {}
    for symbol, cache_key in wanted.items():
        if cache_key in _TYPE_DECLARATION_PATHS:
            resolved[symbol] = list(_TYPE_DECLARATION_PATHS[cache_key])
        else:
            missing[symbol] = cache_key
    if not missing:
        return resolved, 0

    patterns = {
        symbol: re.compile(
            rf"\b(?:(?:class|struct)\s+(?:\w+_API\s+)?{re.escape(symbol)}\b|"
            rf"enum(?:\s+class)?\s+(?:\w+_API\s+)?{re.escape(symbol)}\b|"
            rf"using\s+{re.escape(symbol)}\s*=|"
            rf"typedef\b[^;\n]*\b{re.escape(symbol)}\s*;)"
        )
        for symbol in missing
    }
    found = {symbol: [] for symbol in missing}
    inspected = 0
    engine_source = engine_root / "Engine" / "Source"
    declaration_roots = [
        candidate
        for candidate in (
            engine_source / "Runtime",
            engine_source / "Developer",
            engine_source / "Editor",
        )
        if candidate.is_dir()
    ]
    # Filename-mismatched core declarations live under Engine/Source. Plugin
    # APIs overwhelmingly use a matching public header; scanning the entire
    # Plugins tree here turns one fallback into a multi-minute operation on
    # large installations. Matching plugin headers are still handled by the
    # normal filename catalog above.
    all_headers = [
        path
        for paths in catalog.values()
        for path in paths
        if any(_lexically_contained(path, candidate) for candidate in declaration_roots)
    ]
    rg = shutil.which("rg")
    if rg and declaration_roots:
        for symbol, pattern in patterns.items():
            for declaration_root in declaration_roots:
                try:
                    completed = subprocess.run(
                        [
                            rg,
                            "-l",
                            "--glob", "*.h",
                            "--glob", "*.hpp",
                            "--glob", "*.inl",
                            pattern.pattern,
                            str(declaration_root),
                        ],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=8,
                        check=False,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    completed = None
                if completed is None or completed.returncode not in {0, 1}:
                    break
                paths = [Path(line.strip()) for line in completed.stdout.splitlines() if line.strip()]
                found[symbol].extend(
                    path for path in paths if _lexically_contained(path, engine_root)
                )
                if found[symbol]:
                    found[symbol] = found[symbol][:12]
                    break
        else:
            for symbol, cache_key in missing.items():
                _TYPE_DECLARATION_PATHS[cache_key] = list(found[symbol])
                resolved[symbol] = list(found[symbol])
            return resolved, len(missing)
    for header in all_headers:
        if not _contained(header, engine_root):
            continue
        inspected += 1
        try:
            text = header.read_text(encoding="utf-8-sig", errors="replace")[:max_header_chars]
        except OSError:
            continue
        for symbol, pattern in patterns.items():
            if len(found[symbol]) < 12 and pattern.search(text):
                found[symbol].append(header)
    for symbol, cache_key in missing.items():
        _TYPE_DECLARATION_PATHS[cache_key] = list(found[symbol])
        resolved[symbol] = list(found[symbol])
    return resolved, inspected


def _split_parameters(value: str) -> list[str]:
    raw = str(value or "").strip()
    if not raw or raw == "void":
        return []
    parts: list[str] = []
    start = 0
    angle = paren = bracket = 0
    for index, char in enumerate(raw):
        if char == "<":
            angle += 1
        elif char == ">" and angle:
            angle -= 1
        elif char == "(":
            paren += 1
        elif char == ")" and paren:
            paren -= 1
        elif char == "[":
            bracket += 1
        elif char == "]" and bracket:
            bracket -= 1
        elif char == "," and not angle and not paren and not bracket:
            parts.append(raw[start:index].strip())
            start = index + 1
    parts.append(raw[start:].strip())
    return [part for part in parts if part]


_DECLARATION_PREFIX_REJECT = re.compile(
    r"^(?:return|co_return|if|else|for|while|switch|case|sizeof|static_assert)\b"
)


def _declaration_return_type(prefix: str) -> str | None:
    """Return a declaration's type prefix, or ``None`` for a call/expression.

    The previous parser accepted any ``Name(args);`` line.  That made calls,
    assignments, and even commented-out code look like declarations and later
    produced false return/parameter mismatches.  Keep this deliberately
    conservative: a missed inline declaration is UNKNOWN evidence, while a
    fabricated signature incorrectly closes a fail-closed write gate.
    """

    raw = str(prefix or "").strip()
    if not raw or any(marker in raw for marker in ("//", "/*", "*/", "=", "->")):
        return None
    cleaned = re.sub(r"\[\[[^\]]*\]\]", " ", raw)
    cleaned = re.sub(r"\bUE_(?:FORCEINLINE_HINT|NODISCARD)\b", " ", cleaned)
    cleaned = re.sub(r"\b(?:FORCEINLINE|FORCEINLINE_DEBUGGABLE)\b", " ", cleaned)
    cleaned = re.sub(
        r"\b(?:static|virtual|inline|constexpr|consteval|friend|explicit)\b",
        " ",
        cleaned,
    )
    cleaned = re.sub(r"\b[A-Z][A-Z0-9_]*_API\b", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned or _DECLARATION_PREFIX_REJECT.match(cleaned):
        return None
    # Out-of-class definitions may leave ``Owner::`` immediately before the
    # method token.  It is ownership syntax, not part of the return type.
    cleaned = re.sub(r"(?:[A-Za-z_]\w*(?:<[^<>\n]*>)?::)+\s*$", "", cleaned).strip()
    return cleaned


def _signature_contracts(text: str, symbol: str) -> list[dict[str, Any]]:
    declaration = re.compile(
        rf"(?m)^[ \t]*(?P<prefix>[^(){{}};\n]+?)\b{re.escape(symbol)}\s*"
        rf"\((?P<params>[^;{{}}]*)\)\s*(?:const\s*)?(?:override\s*)?"
        rf"(?:final\s*)?(?:noexcept(?:\s*\([^)]*\))?\s*)?(?P<end>;|\{{)"
    )
    contracts: list[dict[str, Any]] = []
    for match in declaration.finditer(text):
        return_type = _declaration_return_type(match.group("prefix"))
        if return_type is None:
            continue
        parameters = _split_parameters(match.group("params"))
        required = sum(1 for parameter in parameters if "=" not in parameter)
        contracts.append(
            {
                "returnType": return_type,
                "requiredArgumentCount": required,
                "maximumArgumentCount": len(parameters),
                "parameters": parameters,
                "declaration": match.group(0).strip(),
                "line": text.count("\n", 0, match.start()) + 1,
            }
        )
        if len(contracts) >= 8:
            break
    return contracts


def _candidate_header_rank(path: Path) -> tuple[int, int, int, str]:
    """Prefer public engine declarations over experimental/third-party twins."""

    folded = path.as_posix().casefold()
    third_party = 1 if "/thirdparty/" in folded else 0
    experimental = 1 if "/experimental/" in folded else 0
    private = 1 if "/private/" in folded else 0
    return (third_party, experimental, private, folded)


def lookup_engine_header_evidence(
    engine_root: str | Path | None,
    claims: Iterable[dict[str, str]],
    *,
    max_files_per_claim: int = 12,
    max_header_chars: int = 1_000_000,
) -> dict[str, Any]:
    """Return exact source excerpts keyed by ``owner::symbol`` or symbol.

    A missing result means only that bounded header discovery did not prove the
    claim.  It must never be interpreted as proof that the API does not exist.
    """

    root = Path(engine_root).expanduser().resolve() if engine_root else None
    if root is None or not root.is_dir():
        return {
            "status": "engine_root_unavailable",
            "engineRoot": str(root or ""),
            "catalogFileCount": 0,
            "results": {},
        }
    catalog = _header_catalog(root)
    results: dict[str, list[dict[str, Any]]] = {}
    inspected_files = 0
    claim_list = [dict(claim) for claim in claims]
    unresolved_types: list[str] = []
    for raw_claim in claim_list:
        symbol = _unqualified(raw_claim.get("symbol") or "")
        owner = _unqualified(raw_claim.get("receiverType") or "")
        # Resolve owners as declarations even when a similarly named header
        # exists.  Common aliases (FVector) and non-matching owner headers
        # (FMath -> UnrealMathUtility.h) otherwise bind to an unrelated
        # Vector.h/Math.h from another module.
        if owner:
            unresolved_types.append(owner)
        elif (
            symbol
            and raw_claim.get("allowDeclarationScan") is True
            and not any(
                catalog.get(name.casefold(), []) for name in _header_names(symbol)
            )
        ):
            unresolved_types.append(symbol)
    declaration_paths, declaration_scan_count = _discover_type_declaration_paths(
        root,
        catalog,
        unresolved_types,
        max_header_chars=max_header_chars,
    )
    inspected_files += declaration_scan_count
    for raw_claim in claim_list:
        symbol = _unqualified(raw_claim.get("symbol") or "")
        owner = _unqualified(raw_claim.get("receiverType") or "")
        if not symbol:
            continue
        key = f"{owner.casefold()}::{symbol.casefold()}" if owner else symbol.casefold()
        candidate_names = _header_names(owner or symbol)
        candidates: list[Path] = []
        if owner:
            candidates.extend(declaration_paths.get(owner, []))
        for name in candidate_names:
            candidates.extend(
                sorted(
                    catalog.get(name.casefold(), []),
                    key=_candidate_header_rank,
                )
            )
        if not owner:
            candidates.extend(declaration_paths.get(symbol, []))
        seen: set[str] = set()
        for header in candidates[:max_files_per_claim]:
            identity = _identity(header)
            if identity in seen or not _contained(header, root):
                continue
            seen.add(identity)
            inspected_files += 1
            try:
                text = header.read_text(encoding="utf-8-sig", errors="replace")[:max_header_chars]
            except OSError:
                continue
            found = _declaration_excerpt(text, symbol, owner=owner)
            if found is None:
                continue
            line, excerpt = found
            signatures = _signature_contracts(text, symbol) if owner else []
            # A filename/token match is not owner proof.  Without a parsed
            # declaration this may only be a call in an unrelated same-named
            # header, so keep the claim UNKNOWN instead of fabricating an
            # exact owner row.
            if owner and not signatures:
                continue
            if signatures:
                signature_line = int(signatures[0].get("line") or line)
                source_lines = text.splitlines()
                start = max(0, signature_line - 3)
                end = min(len(source_lines), signature_line + 3)
                line = signature_line
                excerpt = "\n".join(source_lines[start:end]).strip()
            results.setdefault(key, []).append(
                {
                    "symbol_name": symbol,
                    "symbol_kind": "method" if owner else "engine_type",
                    "qualified_name": f"{owner}::{symbol}" if owner else symbol,
                    "title": f"Engine header declaration for {owner + '::' if owner else ''}{symbol}",
                    "locator": f"{header}:{line}",
                    "source": str(header),
                    "excerpt": excerpt,
                    "evidence_source": "engine_header_exact",
                    **({"signatures": signatures} if signatures else {}),
                }
            )
            # The declaration-resolved or highest-ranked matching header owns
            # the bounded contract.  Do not merge lower-ranked experimental or
            # third-party twins into one impossible overload set.
            if owner:
                break
    return {
        "status": "ready",
        "engineRoot": str(root),
        "catalogFileCount": sum(len(paths) for paths in catalog.values()),
        "inspectedFileCount": inspected_files,
        "results": results,
    }


def clear_engine_header_catalog_cache() -> None:
    _HEADER_CATALOGS.clear()
    _TYPE_DECLARATION_PATHS.clear()
