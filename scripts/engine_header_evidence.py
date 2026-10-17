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
                   generation: int | None = None, *, skip: frozenset[str] | set[str] = frozenset(_SKIP_DIRS),
                   max_entries: int = 16384, max_paths: int = 4096, ttl_seconds: float = 30,
                   tag: str = "local-v1") -> tuple[list[Path], str]:
    """Bound filename discovery as well as reads; no recursive content search."""
    key = (_cache_root_identity(module, host_platform=host_platform), identity, tag)
    cached = _LOCAL_CATALOGS.get(key)
    if cached and time.monotonic() - cached[0] < ttl_seconds:
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
                    if time.monotonic() >= deadline or entries > max_entries or len(paths) >= max_paths:
                        reason = "deadline" if time.monotonic() >= deadline else "catalog_limit"
                        break
                    if entry.is_symlink() or entry.name in skip:
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
    # UE_DEPRECATED* precedes many engine declarations; without stripping it
    # a deprecated API was never found as a declaration at all.
    for match in reversed(list(re.finditer(
            r"\b(?:UFUNCTION|UPROPERTY|UMETA|GENERATED_BODY|GENERATED_UCLASS_BODY|UE_DEPRECATED\w*)\s*\(", prefix))):
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
    kinds = {start: "type"} if not owner else {}
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
                kinds[match.start()] = "enum_value" if enum_owner else "method"
            elif not enum_owner:
                type_prefix = re.sub(r"\b(?:public|protected|private)\s*:", " ", declaration_prefix).strip()
                # A bounded field declaration, not an initializer reference,
                # nested member, friend, function call or arbitrary macro.
                field_type = r"(?:(?:static|mutable|const|volatile|constexpr)\s+)*[A-Za-z_]\w*(?:::\w+)*(?:\s*<[^;{}()=]+>)?(?:\s*[*&])*"
                field_tail = r"\s*(?:\[[^\]{};]*\]\s*)*(?::\s*\d+\s*|=\s*(?:\{[^{};]*\}|[^;{}]*)\s*|\{[^{};]*\}\s*)?;"
                non_field = re.search(r"\b(?:using|typedef|class|struct|enum|union|friend|return)\b", type_prefix)
                if not non_field and re.fullmatch(field_type, type_prefix) and re.match(field_tail, tail):
                    positions.append(match.start())
                    kinds[match.start()] = "field"
    if not positions:
        return None
    pos = positions[0]
    line = text.count("\n", 0, pos) + 1
    lines = text.splitlines()
    excerpt_start = max(0, line - 2)
    # Include a preceding multiline reflection annotation when lexically
    # adjacent. This bounded window does not prove absence or macro expansion.
    prefix_start = max(0, line - 25)
    prefix = "\n".join(lines[prefix_start:line - 1])
    lexical_prefix = _lexical_source(prefix)
    annotations = list(re.finditer(r"\b(?:UFUNCTION|UPROPERTY|UMETA)\s*\(", lexical_prefix))
    if annotations:
        annotation = annotations[-1]
        depth, end_annotation = 1, annotation.end()
        while end_annotation < len(prefix) and depth:
            depth += (lexical_prefix[end_annotation] == "(") - (lexical_prefix[end_annotation] == ")")
            end_annotation += 1
        if depth == 0 and not re.search(r"[;{}]", lexical_prefix[end_annotation:]):
            excerpt_start = prefix_start + prefix.count("\n", 0, annotation.start())
    excerpt = "\n".join(lines[excerpt_start:min(len(lines), line + 5)])
    conditional = bool(re.search(r"^\s*#\s*(?:if|ifdef|ifndef|elif|define)\b", masked[:end], re.M))
    access = "public" if re.match(r"(?:struct|enum)\b", masked[start:opening]) else "private"
    for label in re.finditer(r"\b(public|protected|private)\s*:", masked[opening + 1:pos]):
        preceding = masked[opening + 1:opening + 1 + label.start()]
        if preceding.count("{") == preceding.count("}"):
            access = label.group(1)
    bases = re.findall(r"\b(?:public|protected|private)\s+(?:virtual\s+)?([A-Za-z_]\w*(?:::\w+)*)", masked[start:opening])[:8]
    return {"line": line, "excerpt": excerpt[:1800], "excerptStartLine": excerpt_start + 1,
            "excerptTruncated": len(excerpt) > 1800,
            "annotationVerification": "bounded_lexical_context; absence_and_expansion_unverified",
            "observedOwner": owner or symbol,
            "requestedOwner": owner or symbol, "declarationKind": kinds[pos],
            "lexicalAccess": access if owner else "not_applicable",
            "parentCandidates": bases, "inheritanceVerification": "parent_declarations_not_searched",
            "declarationStatus": "candidate_conditional" if conditional else "lexical_declaration",
            "signatureVerification": "lexical_only; overloads_inheritance_macros_and_callability_unverified"}


# Public API headers only: private/internal implementation headers are not an
# includable contract, and skipping them keeps a full Runtime+Editor+Plugins
# filename catalog near one second on an installed engine.
_PUBLIC_SKIP_DIRS = frozenset(_SKIP_DIRS | {"Private", "Internal", "Tests", "Content", "Resources",
                                            "Docs", "Extras", "Config", "Build", "Shaders"})
_PUBLIC_ROOTS = ("Engine/Source/Runtime", "Engine/Source/Editor", "Engine/Plugins")
# Separate from the 3 s read deadline: discovery is cached per engine identity.
_PUBLIC_CATALOG_SECONDS = 8.0
_INHERITANCE_MAX_DEPTH = 8
_INHERITANCE_MAX_TYPES = 32
_INHERITANCE_MAX_FILES = 12
_CANDIDATES_PER_TYPE = 4
_READ_LIMIT_BYTES = 1048576


def _type_parents(text: str, owner: str) -> list[str] | None:
    """Direct C++ bases of ``owner`` when exactly one definition is visible."""
    masked = _lexical_source(text)
    region = _owner_region(masked, owner)
    if not region:
        return None
    start, opening, _ = region
    bases = re.findall(r"\b(?:public|protected|private)\s+(?:virtual\s+)?([A-Za-z_]\w*(?:::\w+)*)",
                       masked[start:opening])[:8]
    return [base.split("::")[-1] for base in bases]


def _declaration_specifiers(excerpt: str, symbol: str) -> dict[str, Any]:
    """Specifiers lexically observed in the bounded excerpt; never a compiled fact."""
    observed: dict[str, Any] = {"verification": "lexical_excerpt_only"}
    reflection = re.findall(r"\b(UFUNCTION|UPROPERTY)\s*\(([^()]*(?:\([^()]*\)[^()]*)*)\)", excerpt)
    if reflection:
        macro, arguments = reflection[-1]
        observed["reflection"] = {"macro": macro, "arguments": re.sub(r"\s+", " ", arguments).strip()[:400]}
    line = next((item for item in excerpt.splitlines() if re.search(r"\b" + re.escape(symbol) + r"\b", item)), "")
    head, _, tail = line.partition(symbol)
    observed["keywords"] = [k for k in ("virtual", "static", "inline", "constexpr", "explicit")
                            if re.search(r"\b" + k + r"\b", head)]
    observed["qualifiers"] = [k for k in ("const", "override", "final") if re.search(r"\b" + k + r"\b", tail)]
    if re.search(r"=\s*0\s*;", tail):
        observed["qualifiers"].append("pure_virtual")
    if re.search(r"=\s*delete\b", tail):
        observed["qualifiers"].append("deleted")
    return observed


def _deprecation(excerpt: str) -> dict[str, Any]:
    match = re.search(r"\bUE_DEPRECATED(?:_FORGAME)?\s*\(\s*([0-9.]+)\s*,\s*\"([^\"]{0,240})", excerpt)
    if match:
        return {"observed": True, "version": match.group(1), "message": match.group(2), "source": "UE_DEPRECATED"}
    if re.search(r"\bDeprecatedFunction\b|\bDEPRECATED_[A-Z_]+\b", excerpt):
        return {"observed": True, "source": "reflection_metadata"}
    # Absence in a bounded excerpt is not proof that the API is current.
    return {"observed": False, "verification": "not_observed_in_bounded_excerpt"}


def _module_owner(header: Path, root: Path) -> dict[str, Any]:
    try:
        parts = list(header.relative_to(root).parts)
    except ValueError:
        return {"status": "unknown"}
    if "Source" not in parts:
        return {"status": "unknown"}
    index = len(parts) - 1 - parts[::-1].index("Source")
    module_index = index + 1
    if module_index < len(parts) - 1 and parts[module_index] in {"Runtime", "Editor", "Developer"} \
            and parts[:2] == ["Engine", "Source"]:
        module_index += 1
    if module_index >= len(parts) - 1:
        return {"status": "unknown"}
    module = parts[module_index]
    build = Path(*parts[:module_index + 1]) / f"{module}.Build.cs"
    owner: dict[str, Any] = {"status": "observed" if (root / build).is_file() else "build_cs_not_found",
                             "module": module, "buildCs": build.as_posix()}
    if parts[:2] == ["Engine", "Plugins"]:
        owner["pluginPath"] = "/".join(parts[:index])
    return owner


def _public_header_index(root: Path, identity: str, host_platform: str | None, generation: int,
                         reasons: set[str]) -> dict[str, list[Path]]:
    catalog_deadline = time.monotonic() + _PUBLIC_CATALOG_SECONDS
    index: dict[str, list[Path]] = {}
    for relative in _PUBLIC_ROOTS:
        base = root / relative
        if not base.is_dir() or not _contained(base, root):
            continue
        paths, status = _local_catalog(base, identity, catalog_deadline, host_platform, generation,
                                       skip=_PUBLIC_SKIP_DIRS, max_entries=600000, max_paths=120000,
                                       ttl_seconds=600, tag="public-v1")
        if status != "complete_filename_catalog":
            reasons.add(status)
        for path in paths:
            index.setdefault(path.stem.lower(), []).append(path)
    return index


def _type_candidates(index: dict[str, list[Path]], type_name: str) -> list[Path]:
    exact = {p for name in _header_names(type_name) for p in index.get(Path(name).stem.lower(), [])}
    if exact:
        return sorted(exact, key=str)
    stem = _unqualified(type_name)
    stem = stem[1:] if len(stem) > 1 and stem[0] in "AUFSEI" and stem[1].isupper() else stem
    tokens = [t.lower() for t in re.findall(r"[A-Z][a-z]+|[A-Z]+(?=[A-Z][a-z]|$)", stem) if len(t) >= 4]
    if not tokens:
        return []
    # Prefer headers whose name covers more of the type's words (a Tickable*
    # type may live in WorldSubsystem.h); every hint is re-read before use.
    scored = [(sum(len(t) for t in tokens if t in name), name) for name in index if any(t in name for t in tokens)]
    ranked = sorted(scored, key=lambda item: (-item[0], len(item[1]), item[1]))
    return [p for _, name in ranked[:_CANDIDATES_PER_TYPE] for p in sorted(index[name], key=str)]


def _read_bounded_header(header: Path, root: Path, budget: int) -> tuple[bytes, bool] | str:
    """Read a header prefix with identity/stability checks; a reason on failure."""
    if not _contained(header, root):
        return "containment"
    try:
        with header.open("rb") as stream:
            before = os.fstat(stream.fileno())
            raw = stream.read(min(262144, budget))
            after = os.fstat(stream.fileno())
        current = header.stat()
    except OSError:
        return "read_unavailable"
    stable = all((s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns) ==
                 (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) for s in (after, current))
    if not stable or not _contained(header, root):
        return "source_changed"
    return raw, len(raw) == before.st_size


def _bounded_local_lookup(engine_root: str | Path | None, claims: list[dict[str, str]], identity: str,
                          *, host_platform: str | None = None) -> dict[str, Any]:
    generation = _LOCAL_CACHE_GENERATION
    root = Path(engine_root).expanduser().resolve() if engine_root else None
    if root is None or len(claims) != 1:
        return {"status": "engine_root_unavailable", "results": {}, "readBytes": 0}
    claim = claims[0]
    symbol = str(claim.get("symbol") or "")
    owner = str(claim.get("receiverType") or "")
    reasons: set[str] = set()
    index = _public_header_index(root, identity, host_platform, generation, reasons)
    deadline = time.monotonic() + 3.0
    results: list[dict[str, Any]] = []
    read_bytes = inspected = 0
    visited: list[str] = []
    edges: list[list[str]] = []
    unresolved: list[str] = []
    stop = False
    # Breadth-first from the requested owner. The nearest declaration on a
    # branch hides that name in its parents, so a found branch stops there.
    queue: list[tuple[str, list[str]]] = [(owner or symbol, [owner or symbol])]
    while queue and not stop:
        type_name, chain = queue.pop(0)
        if type_name in visited:
            continue
        if len(visited) >= _INHERITANCE_MAX_TYPES:
            reasons.add("type_limit")
            break
        visited.append(type_name)
        parents: list[str] | None = None
        declared_here = False
        for header in _type_candidates(index, type_name)[:_CANDIDATES_PER_TYPE]:
            if time.monotonic() >= deadline:
                reasons.add("deadline")
                stop = True
                break
            if inspected >= _INHERITANCE_MAX_FILES or read_bytes >= _READ_LIMIT_BYTES:
                reasons.add("file_limit" if inspected >= _INHERITANCE_MAX_FILES else "read_limit")
                stop = True
                break
            observation = _read_bounded_header(header, root, _READ_LIMIT_BYTES - read_bytes)
            if isinstance(observation, str):
                reasons.add(observation)
                if observation == "read_unavailable":
                    # A cached positive path no longer exists; rediscover next time.
                    for relative in _PUBLIC_ROOTS:
                        _LOCAL_CATALOGS.pop((_cache_root_identity(root / relative, host_platform=host_platform),
                                             identity, "public-v1"), None)
                continue
            raw, complete = observation
            read_bytes += len(raw)
            inspected += 1
            if not complete:
                reasons.add("read_limit")
            text = raw.decode("utf-8-sig", errors="replace")
            found = _bounded_declaration(text, symbol, type_name if owner else "")
            if found:
                module = _module_owner(header, root)
                results.append({
                    "symbol_name": symbol,
                    "qualified_name": f"{type_name}::{symbol}" if owner else symbol,
                    "source": str(header.resolve()),
                    "moduleRoot": str((root / module["buildCs"]).parent) if module.get("buildCs") else "",
                    **found,
                    "requestedOwner": owner or symbol, "observedOwner": type_name, "declaredIn": type_name,
                    "inheritancePath": list(chain),
                    "specifiers": _declaration_specifiers(found["excerpt"], symbol),
                    "deprecated": _deprecation(found["excerpt"]), "moduleOwner": module,
                    "fingerprint": {"sha256": hashlib.sha256(raw).hexdigest(),
                                    "hashScope": "file" if complete else "byte_range",
                                    "readByteRange": [0, len(raw)], "readBytes": len(raw), "completeFile": complete}})
                declared_here = True
                break
            if owner and parents is None:
                parents = _type_parents(text, type_name)
                if parents is not None:
                    break
        if declared_here or not owner or stop:
            continue
        if parents is None:
            unresolved.append(type_name)
            continue
        if len(chain) > _INHERITANCE_MAX_DEPTH:
            reasons.add("depth_limit")
            continue
        for parent in parents:
            edges.append([type_name, parent])
            queue.append((parent, chain + [parent]))
    declaring = sorted({row["declaredIn"] for row in results})
    limited = reasons & {"deadline", "type_limit", "file_limit", "depth_limit", "read_limit"}
    searched = "not_applicable" if not owner else (
        "parents_searched" if not unresolved and not limited and not queue else "partial")
    for row in results:
        row["inheritanceVerification"] = searched
    # Filename discovery is intentionally incomplete as a declaration search.
    # Even a complete catalog and zero hits cannot establish API absence.
    return {"status": "ready" if results else "coverage_limited", "engineRoot": str(root),
            "results": results, "readBytes": read_bytes, "inspectedFileCount": inspected,
            "coverage": {"scope": "bounded_public_header_candidates", "completeness": "partial",
                         "reasons": sorted(reasons), "apiAbsenceVerified": False,
                         "inheritance": {"verification": searched, "visitedTypes": visited[:16],
                                         "edges": edges[:16], "unresolvedTypes": unresolved[:8],
                                         "ambiguity": "multiple_declaring_types" if len(declaring) > 1 else None,
                                         "limits": {"depth": _INHERITANCE_MAX_DEPTH, "types": _INHERITANCE_MAX_TYPES,
                                                    "files": _INHERITANCE_MAX_FILES}}}}
