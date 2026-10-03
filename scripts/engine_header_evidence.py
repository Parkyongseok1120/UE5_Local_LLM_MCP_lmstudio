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
import hashlib
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Iterable

from workspace_paths import (
    ascii_windows_fold,
    canonical_absolute_path_identity,
    filesystem_path_identity,
    is_windows_host_platform,
)


_HEADER_CATALOGS: dict[str, dict[str, list[Path]]] = {}
_TYPE_DECLARATION_PATHS: dict[tuple[str, str], list[Path]] = {}
_MAX_PYTHON_DECLARATION_SCAN_FILES = 512
_LOCAL_CATALOGS: OrderedDict[tuple[str, str, str], tuple[float, list[Path]]] = OrderedDict()
_LOCAL_CACHE_GENERATION = 0
_SKIP_DIRS = {
    ".git",
    "Binaries",
    "DerivedDataCache",
    "Intermediate",
    "Saved",
    "ThirdParty",
}


def _identity(path: Path, *, host_platform: str | None = None) -> str:
    return canonical_absolute_path_identity(
        path,
        host_platform=host_platform,
    )


def _cache_root_identity(path: Path, *, host_platform: str | None = None) -> str:
    host_kind = "windows" if is_windows_host_platform(host_platform) else "posix"
    return f"{host_kind}:{_identity(path, host_platform=host_platform)}"


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


def _header_catalog(
    engine_root: Path,
    *,
    host_platform: str | None = None,
) -> dict[str, list[Path]]:
    key = _cache_root_identity(engine_root, host_platform=host_platform)
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
                        catalog.setdefault(
                            filesystem_path_identity(
                                path.name,
                                host_platform=host_platform,
                            ),
                            [],
                        ).append(path)
                continue
        for directory, names, files in os.walk(source_root):
            names[:] = [name for name in names if name not in _SKIP_DIRS]
            for file_name in files:
                if not file_name.lower().endswith((".h", ".hpp", ".inl")):
                    continue
                path = Path(directory) / file_name
                # ``os.walk`` starts at a source root that was itself resolved
                # below ``engine_root`` and every child name comes from that
                # walker.  Resolving every one of the 40k+ installed UE
                # headers performs a filesystem round-trip per file (and made
                # the first GUI validator call take more than a minute when
                # ``rg`` was not on LM Studio's PATH).  Lexical containment is
                # sufficient for this trusted enumeration and keeps the
                # portable Python fallback bounded on Windows, Linux, and
                # macOS.  Individual candidate files are still resolved and
                # checked again before their contents are read.
                if not _lexically_contained(path, engine_root):
                    continue
                catalog.setdefault(
                    filesystem_path_identity(
                        file_name,
                        host_platform=host_platform,
                    ),
                    [],
                ).append(path)
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
            if ascii_windows_fold(name) not in {
                ascii_windows_fold(item) for item in names
            }:
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
    host_platform: str | None = None,
) -> tuple[dict[str, list[Path]], int]:
    """Find declarations whose header name does not match the Unreal type.

    Unreal often groups small public types in owner headers (for example,
    FLifetimeProperty is declared in CoreNet.h). Scan all catalogued headers
    once for the unresolved batch and cache both hits and misses by Engine
    root, so a persistent MCP process does not repeatedly walk the SDK.
    """

    root_key = _cache_root_identity(
        engine_root,
        host_platform=host_platform,
    )
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
    # Reading every installed Engine header in Python is not a bounded
    # fallback.  On a normal UE installation this is tens of thousands of
    # files and made one LM Studio tool call take almost two minutes whenever
    # ``rg`` was absent from the GUI process PATH.  Filename-matched headers
    # have already been checked above.  Preserve the exhaustive Python path for
    # small synthetic/source SDKs, but leave large-tree misses unresolved so
    # the validator can escalate them once to UHT/UBT compiler proof.
    if len(all_headers) > _MAX_PYTHON_DECLARATION_SCAN_FILES:
        for symbol, cache_key in missing.items():
            _TYPE_DECLARATION_PATHS[cache_key] = []
            resolved[symbol] = []
        return resolved, 0
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


def _candidate_header_rank(
    path: Path,
    *,
    host_platform: str | None = None,
) -> tuple[int, int, int, str]:
    """Prefer public engine declarations over experimental/third-party twins."""

    folded = path.as_posix().casefold()
    third_party = 1 if "/thirdparty/" in folded else 0
    experimental = 1 if "/experimental/" in folded else 0
    private = 1 if "/private/" in folded else 0
    return (
        third_party,
        experimental,
        private,
        filesystem_path_identity(path.as_posix(), host_platform=host_platform),
    )


def lookup_engine_header_evidence(
    engine_root: str | Path | None,
    claims: Iterable[dict[str, str]],
    *,
    max_files_per_claim: int = 12,
    max_header_chars: int = 1_000_000,
    host_platform: str | None = None,
    local_source_identity: str | None = None,
) -> dict[str, Any]:
    """Return exact source excerpts keyed by ``owner::symbol`` or symbol.

    A missing result means only that bounded header discovery did not prove the
    claim.  It must never be interpreted as proof that the API does not exist.
    """

    if local_source_identity is not None:
        return _bounded_local_lookup(engine_root, list(claims), local_source_identity, host_platform=host_platform)
    root = Path(engine_root).expanduser().resolve() if engine_root else None
    if root is None or not root.is_dir():
        return {
            "status": "engine_root_unavailable",
            "engineRoot": str(root or ""),
            "catalogFileCount": 0,
            "results": {},
        }
    catalog = _header_catalog(root, host_platform=host_platform)
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
                catalog.get(
                    filesystem_path_identity(name, host_platform=host_platform),
                    [],
                )
                for name in _header_names(symbol)
            )
        ):
            unresolved_types.append(symbol)
    declaration_paths, declaration_scan_count = _discover_type_declaration_paths(
        root,
        catalog,
        unresolved_types,
        max_header_chars=max_header_chars,
        host_platform=host_platform,
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
                    catalog.get(
                        filesystem_path_identity(name, host_platform=host_platform),
                        [],
                    ),
                    key=lambda path: _candidate_header_rank(
                        path,
                        host_platform=host_platform,
                    ),
                )
            )
        if not owner:
            candidates.extend(declaration_paths.get(symbol, []))
        seen: set[str] = set()
        for header in candidates[:max_files_per_claim]:
            identity = _identity(header, host_platform=host_platform)
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


def resolve_engine_include_path(
    engine_root: str | Path | None,
    include_path: str,
    *,
    host_platform: str | None = None,
) -> dict[str, Any]:
    """Resolve an exact quoted include through the cached Engine header catalog."""

    root = Path(engine_root).expanduser().resolve() if engine_root else None
    normalized = str(include_path or "").replace("\\", "/").strip("/")
    if root is None or not root.is_dir():
        return {
            "ok": False,
            "status": "engine_root_unavailable",
            "include": normalized,
            "matches": [],
        }
    if not normalized or Path(normalized).suffix.casefold() not in {".h", ".hpp", ".hh", ".inl"}:
        return {"ok": False, "status": "invalid_include", "include": normalized, "matches": []}
    catalog = _header_catalog(root, host_platform=host_platform)
    candidates = catalog.get(
        filesystem_path_identity(
            Path(normalized).name,
            host_platform=host_platform,
        ),
        [],
    )
    suffix = "/" + filesystem_path_identity(
        normalized,
        host_platform=host_platform,
        trim_outer_slashes=True,
    )
    exact = [
        path
        for path in candidates
        if filesystem_path_identity(
            path.as_posix(),
            host_platform=host_platform,
        ).endswith(suffix)
        and _contained(path, root)
    ]
    matches = exact or [path for path in candidates if _contained(path, root)]
    matches = sorted(
        matches,
        key=lambda path: _candidate_header_rank(
            path,
            host_platform=host_platform,
        ),
    )
    return {
        "ok": bool(matches),
        "status": "resolved" if matches else "not_found",
        "include": normalized,
        "matches": [str(path) for path in matches[:8]],
        "ambiguous": len(matches) > 1 and not exact,
    }


def clear_engine_header_catalog_cache() -> None:
    global _LOCAL_CACHE_GENERATION
    _LOCAL_CACHE_GENERATION += 1
    _LOCAL_CATALOGS.clear()
    _HEADER_CATALOGS.clear()
    _TYPE_DECLARATION_PATHS.clear()


def _local_catalog(module: Path, identity: str, deadline: float, host_platform: str | None,
                   generation: int | None = None) -> tuple[list[Path], str]:
    """Bound filename discovery as well as reads; no recursive content search."""
    key = (_cache_root_identity(module, host_platform=host_platform), identity, "local-v1")
    cached = _LOCAL_CATALOGS.get(key)
    if cached and time.monotonic() - cached[0] < 30:
        _LOCAL_CATALOGS.move_to_end(key)
        return cached[1], "complete_filename_catalog"
    generation = _LOCAL_CACHE_GENERATION if generation is None else generation
    paths: list[Path] = []
    pending = [module]
    entries = 0
    reason = "complete_filename_catalog"
    try:
        while pending:
            if time.monotonic() >= deadline:
                reason = "deadline"
                break
            directory = pending.pop()
            with os.scandir(directory) as listing:
                for entry in listing:
                    entries += 1
                    if time.monotonic() >= deadline or entries > 16384 or len(paths) >= 4096:
                        reason = "deadline" if time.monotonic() >= deadline else "catalog_limit"
                        break
                    if entry.is_symlink() or entry.name in _SKIP_DIRS:
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        child = Path(entry.path)
                        if _contained(child, module):
                            pending.append(child)
                    elif entry.name.lower().endswith((".h", ".hpp", ".inl")):
                        paths.append(Path(entry.path))
                if reason != "complete_filename_catalog":
                    break
    except OSError:
        reason = "catalog_unavailable"
    if reason == "complete_filename_catalog" and generation == _LOCAL_CACHE_GENERATION:
        # A changed source version evicts the same module's old catalog.
        for old in list(_LOCAL_CATALOGS):
            if old[0] == key[0] and old != key:
                del _LOCAL_CATALOGS[old]
        _LOCAL_CATALOGS[key] = (time.monotonic(), paths)
        _LOCAL_CATALOGS.move_to_end(key)
        while len(_LOCAL_CATALOGS) > 8:
            _LOCAL_CATALOGS.popitem(last=False)
    return paths, reason


def _lexical_source(text: str) -> str:
    # Keep offsets/lines while excluding literals and comments. Raw strings
    # and preprocessor conditions are not interpreted as active C++ contracts.
    pattern = r'R"([^ ()\\\t\r\n]{0,16})\([\s\S]*?\)\1"|/\*[\s\S]*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\x27(?:\\.|[^\x27\\])*\x27'
    return re.sub(pattern, lambda m: re.sub(r"[^\n]", " ", m.group()), text)


def _owner_region(masked: str, owner: str) -> tuple[int, int, int] | None:
    pattern = r"\b(?:class|struct|enum(?:\s+class)?)\s+(?:\w+_API\s+)?" + re.escape(owner) + r"\b"
    regions = []
    for declaration in re.finditer(pattern, masked):
        opening = masked.find("{", declaration.end(), declaration.end() + 2048)
        if opening < 0 or ";" in masked[declaration.end():opening]:
            continue
        depth = 1
        end = opening + 1
        while end < len(masked) and depth:
            depth += (masked[end] == "{") - (masked[end] == "}")
            end += 1
        if depth == 0:
            regions.append((declaration.start(), opening, end))
    return regions[0] if len(regions) == 1 else None


def _without_reflection_annotations(prefix: str) -> str:
    # Remove only known UE annotation invocations, with balanced parentheses.
    # This does not evaluate macros or assert their compiled expansion.
    for match in reversed(list(re.finditer(r"\b(?:UFUNCTION|UPROPERTY|UMETA)\s*\(", prefix))):
        depth, end = 1, match.end()
        while end < len(prefix) and depth:
            depth += (prefix[end] == "(") - (prefix[end] == ")")
            end += 1
        if depth == 0:
            prefix = prefix[:match.start()] + " " + prefix[end:]
    return prefix


def _bounded_declaration(text: str, symbol: str, owner: str) -> dict[str, Any] | None:
    masked = _lexical_source(text)
    region = _owner_region(masked, owner or symbol)
    if not region:
        return None
    start, opening, end = region
    enum_owner = bool(re.match(r"enum\b", masked[start:opening]))
    positions = [start] if not owner else []
    if owner:
        depth = 0
        wanted = re.compile(r"\b" + re.escape(symbol) + r"\b")
        for match in wanted.finditer(masked, opening + 1, end - 1):
            prefix = masked[opening + 1:match.start()]
            depth = prefix.count("{") - prefix.count("}")
            if depth != 0:
                continue
            # A member initializer or qualified call is not this owner's
            # declaration merely because it occurs at class brace depth zero.
            boundary = max(prefix.rfind(";"), prefix.rfind("}"), prefix.rfind("{"), prefix.rfind(",") if enum_owner else -1) + 1
            declaration_prefix = _without_reflection_annotations(prefix[boundary:])
            if any(c in declaration_prefix for c in "=()") or re.search(r"\bfriend\b|(?:\.|->|::)\s*$", declaration_prefix):
                continue
            tail = masked[match.end():match.end() + 2048]
            if (not enum_owner and re.match(r"\s*\(", tail)) or (enum_owner and re.match(r"\s*(?:UMETA\s*\(|=|,|}|$)", tail)):
                positions.append(match.start())
    if not positions:
        return None
    pos = positions[0]
    line = text.count("\n", 0, pos) + 1
    lines = text.splitlines()
    excerpt = "\n".join(lines[max(0, line - 2):min(len(lines), line + 5)])
    conditional = bool(re.search(r"^\s*#\s*(?:if|ifdef|ifndef|elif|define)\b", masked[:end], re.M))
    return {"line": line, "excerpt": excerpt[:1800], "observedOwner": owner or symbol,
            "declarationStatus": "candidate_conditional" if conditional else "lexical_declaration",
            "signatureVerification": "lexical_only; overloads_inheritance_macros_and_callability_unverified"}


def _bounded_local_lookup(engine_root: str | Path | None, claims: list[dict[str, str]], identity: str,
                          *, host_platform: str | None = None) -> dict[str, Any]:
    generation = _LOCAL_CACHE_GENERATION
    root = Path(engine_root).expanduser().resolve() if engine_root else None
    if root is None or len(claims) != 1:
        return {"status": "engine_root_unavailable", "results": {}, "readBytes": 0}
    claim = claims[0]
    symbol = str(claim.get("symbol") or "")
    owner = str(claim.get("receiverType") or "")
    deadline = time.monotonic() + 3.0
    modules = [root / "Engine/Source/Runtime" / name for name in ("Core", "CoreUObject", "Engine")]
    if "input" in (owner + symbol).lower():
        modules.insert(0, root / "Engine/Plugins/EnhancedInput/Source/EnhancedInput")
    modules = [m for m in modules if m.is_dir() and _contained(m, root)]
    candidates: dict[str, tuple[Path, Path, int]] = {}
    reasons: set[str] = set()
    names = {Path(n).stem.lower() for n in _header_names(owner or symbol)}
    stem = _unqualified(owner or symbol)
    stem = stem[1:] if len(stem) > 1 and stem[0] in "AUFSEI" and stem[1].isupper() else stem
    tokens = [t.lower() for t in re.findall(r"[A-Z][a-z]+|[A-Z]+(?=[A-Z][a-z]|$)", stem)]
    for module in modules:
        if time.monotonic() >= deadline:
            reasons.add("deadline")
            break
        paths, status = _local_catalog(module, identity, deadline, host_platform, generation)
        if status != "complete_filename_catalog":
            reasons.add(status)
        for path in paths:
            if time.monotonic() >= deadline:
                reasons.add("deadline")
                break
            name = path.stem.lower()
            score = 100 if name in names else sum(len(t) for t in tokens if len(t) >= 4 and t in name)
            if score:
                key = _identity(path, host_platform=host_platform)
                candidates.setdefault(key, (path, module, score))
    ranked = sorted(candidates.values(), key=lambda c: (-c[2], str(c[0])))
    if len(ranked) > 4:
        reasons.add("candidate_limit")
    results = []
    read_bytes = 0
    inspected = 0
    for header, module, _ in ranked[:4]:
        if time.monotonic() >= deadline:
            reasons.add("deadline")
            break
        if not _contained(header, module) or not _contained(module, root):
            reasons.add("containment")
            continue
        try:
            with header.open("rb") as stream:
                before = os.fstat(stream.fileno())
                raw = stream.read(min(262144, 1048576 - read_bytes))
                after = os.fstat(stream.fileno())
            read_bytes += len(raw)
            inspected += 1
            current = header.stat()
            stable = all((s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns) ==
                         (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) for s in (after, current))
            if not stable or not _contained(header, module):
                reasons.add("source_changed")
                continue
            complete = len(raw) == before.st_size
            if not complete:
                reasons.add("read_limit")
            text = raw.decode("utf-8-sig", errors="replace")
            found = _bounded_declaration(text, symbol, owner)
            if not found:
                continue
            results.append({"symbol_name": symbol, "qualified_name": f"{owner}::{symbol}" if owner else symbol,
                            "source": str(header.resolve()), "moduleRoot": str(module.resolve()), **found,
                            "fingerprint": {"sha256": hashlib.sha256(raw).hexdigest(),
                                            "hashScope": "file" if complete else "byte_range",
                                            "readByteRange": [0, len(raw)], "readBytes": len(raw), "completeFile": complete}})
        except OSError:
            reasons.add("read_unavailable")
            # A cached positive path no longer exists. Do not keep serving the
            # stale catalog; the next bounded request discovers current names.
            _LOCAL_CATALOGS.pop((_cache_root_identity(module, host_platform=host_platform), identity, "local-v1"), None)
    # Filename discovery is intentionally incomplete as a declaration search.
    # Even a complete catalog and zero hits cannot establish API absence.
    return {"status": "ready" if results else "coverage_limited", "engineRoot": str(root),
            "results": results, "readBytes": read_bytes, "inspectedFileCount": inspected,
            "coverage": {"scope": "bounded_module_filename_candidates", "completeness": "partial",
                         "reasons": sorted(reasons), "apiAbsenceVerified": False}}
