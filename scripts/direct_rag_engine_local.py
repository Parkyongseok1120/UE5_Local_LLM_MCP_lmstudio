"""Explicit local declaration lookup using the existing project/engine owners."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from direct_rag_evidence import evidence_metadata_fits, fit_evidence_payload
from direct_rag_project_engine import project_engine_version
from direct_rag_project_selectors import classify_project_selectors, exact_project_descriptor
from direct_rag_result import CapabilityResult, failure
from engine_header_evidence import lookup_engine_header_evidence
from project_name_resolver import resolve_project_name
from target_resolver import resolve_symbol_target
from workspace_paths import resolve_engine_root_for_association


def _build_identity(root: Path) -> dict[str, Any]:
    path = root / "Engine/Build/Build.version"
    try:
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            if before.st_size > 16384:
                raise ValueError("Build.version exceeds the identity observation limit")
            raw = stream.read(16384)
            after = os.fstat(stream.fileno())
        current = path.stat()
        if any((s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns) !=
               (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) for s in (after, current)):
            raise ValueError("Build.version changed during observation")
        value = json.loads(raw.decode("utf-8-sig"))
        fields = {k: value[k] for k in ("MajorVersion", "MinorVersion", "PatchVersion", "Changelist")
                  if isinstance(value, dict) and type(value.get(k)) is int and value[k] >= 0}
        if len(fields) < 2 or not {"MajorVersion", "MinorVersion"} <= fields.keys():
            raise ValueError("Build.version lacks a verified major/minor")
        version = ".".join(str(fields[k]) for k in ("MajorVersion", "MinorVersion", "PatchVersion") if k in fields)
        return {"status": "observed", "engineRoot": str(root), "version": version, **fields,
                "source": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "hashScope": "file"}
    except (OSError, ValueError, TypeError) as exc:
        return {"status": "unknown", "engineRoot": str(root), "source": str(path), "reason": str(exc)[:180]}


def engine_local_symbol(runtime: Any, arguments: dict[str, Any], limit: int) -> CapabilityResult:
    query = str(arguments.get("query") or "").strip()
    owner = str(arguments.get("owner") or "").strip()
    if not re.fullmatch(r"[A-Za-z_]\w{0,127}(?:::[A-Za-z_]\w{0,127})?", query, re.ASCII):
        return failure("INVALID_TOOL_ARGUMENTS", "engine_local query must be Symbol or Owner::Symbol.")
    if owner and not re.fullmatch(r"[A-Za-z_]\w{0,127}", owner, re.ASCII):
        return failure("INVALID_TOOL_ARGUMENTS", "owner must be one exact type name.")
    if "::" in query:
        qualified_owner, symbol = query.split("::")
        if owner and owner != qualified_owner:
            return failure("INVALID_TOOL_ARGUMENTS", "owner conflicts with the qualified query.")
        owner = qualified_owner
    else:
        symbol = query
    workspace = Path(runtime.workspace)
    selected = classify_project_selectors(arguments.get("project"), workspace, use_active=True)
    if not selected.get("ok"):
        return failure(selected["errorCode"], selected["error"])
    descriptors = list(selected["descriptors"])
    if len(descriptors) + len(selected["names"]) != 1:
        return failure("PROJECT_SELECTOR_AMBIGUOUS", "engine_local requires one exact project or active .uproject.")
    for name in selected["names"]:
        resolved = resolve_project_name(workspace, name)
        descriptor = exact_project_descriptor((resolved.get("selected") or {}).get("projectPath")) if resolved.get("ok") else None
        if descriptor is None:
            return failure(str(resolved.get("errorCode") or "PROJECT_SELECTOR_NOT_FOUND"),
                           "The name did not resolve to one current .uproject; pass an exact project path.")
        descriptors.append(descriptor)
    descriptor = exact_project_descriptor(descriptors[0])
    if descriptor is None:
        return failure("PROJECT_SELECTOR_NOT_FOUND", "The selected project no longer exists.")
    def scoped_failure(code: str, message: str, **extra: Any) -> CapabilityResult:
        return failure(code, message, canonicalProject=str(descriptor), canonicalProjectRoot=str(descriptor.parent),
                       sourceMode="engine_local", evidenceSource="installed_engine_source", **extra)

    binding = project_engine_version(descriptor, workspace)
    if not binding.get("ok"):
        return scoped_failure(binding["errorCode"], binding["error"])
    resolution = resolve_engine_root_for_association(binding["engineAssociation"], workspace)
    if not resolution.get("ok"):
        return scoped_failure(str(resolution.get("errorCode") or "ENGINE_ROOT_UNAVAILABLE"), str(resolution.get("error") or "Engine root unavailable."))
    root = Path(resolution["engineRoot"]).resolve()
    identity = _build_identity(root)
    if identity["status"] != "observed":
        return scoped_failure("ENGINE_SOURCE_IDENTITY_UNVERIFIED", "Cannot verify the selected engine Build.version.", observedEngineIdentity=identity)
    expected = str(binding["engineAssociation"])
    if re.fullmatch(r"\d+\.\d+(?:\.\d+)?", expected) and identity["version"].split(".")[:2] != expected.split(".")[:2]:
        return scoped_failure("ENGINE_SOURCE_VERSION_MISMATCH", "Selected engine source differs from the project association.",
                       requestedEngineAssociation=expected, observedEngineIdentity=identity)
    source = lookup_engine_header_evidence(root, [{"symbol": symbol, "receiverType": owner}],
                                          local_source_identity=identity["sha256"])
    if _build_identity(root) != identity:
        return scoped_failure("ENGINE_SOURCE_CHANGED", "Engine identity changed during the source observation.")
    rows = list(source.get("results") or [])
    candidates = [{"symbol_name": symbol, "qualified_name": r["qualified_name"], "filePath": r["source"],
                   "line": r["line"]} for r in rows]
    target = resolve_symbol_target(query, candidates, access="read")
    evidence = "\n\n".join(f"{r['source']}:{r['line']}\n{r['excerpt']}" for r in rows)
    matches = [{k: v for k, v in r.items() if k != "excerpt"} for r in rows]
    payload: dict[str, Any] = {"ok": True, "query": query, "requestedOwner": owner,
        "canonicalProject": str(descriptor), "canonicalProjectRoot": str(descriptor.parent),
        "sourceMode": "engine_local", "evidenceSource": "installed_engine_source",
        "requestedEngineAssociation": expected, "engineSelectionReason": resolution.get("source"),
        "observedEngineIdentity": identity, "coverage": source.get("coverage", {"completeness": "unknown"}),
        "status": source["status"], "matchCount": len(rows), "matches": matches,
        "targetResolution": target, "evidence": evidence, "readBytes": source.get("readBytes", 0),
        "sourceIdentity": {"canonicalProject": str(descriptor), "sourceMode": "engine_local", "engineRoot": str(root)},
        "sourceVersion": "observed:" + hashlib.sha256(json.dumps([identity, matches], sort_keys=True).encode()).hexdigest(),
        "declarationProof": "bounded_lexical_evidence; targetResolution_only_identifies_candidates"}
    # Remove whole candidate metadata before fitting excerpt text. Exact source
    # identities and partial fingerprints are never clipped into false hashes.
    while payload["matches"] and not evidence_metadata_fits(payload, max_chars=limit):
        payload["matches"] = payload["matches"][:-1]
        payload["matchMetadataOmitted"] = len(rows) - len(payload["matches"])
    if not evidence_metadata_fits(payload, max_chars=limit):
        return scoped_failure("OUTPUT_LIMIT_EXCEEDED", "Local source identity does not fit the configured result limit.")
    payload, truncated = fit_evidence_payload(payload, max_chars=limit - 64)
    if truncated:
        payload["evidenceEnvelopeTruncated"] = True
    return CapabilityResult(payload, char_limit=limit)
