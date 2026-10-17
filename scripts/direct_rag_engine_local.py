"""Explicit local declaration lookup using the existing project/engine owners."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from direct_rag_evidence import evidence_metadata_fits, fit_evidence_payload
from direct_rag_project_engine import project_engine_version
from direct_rag_project_selectors import classify_project_selectors, exact_project_descriptor
from direct_rag_result import CapabilityResult, failure
from engine_header_evidence import lookup_engine_header_evidence
from engine_identity import read_unreal_build_identity as _build_identity
from project_name_resolver import resolve_project_name
from target_resolver import resolve_symbol_target
from unreal_contracts import contract_revision, engine_version_validated, load_contracts_or_none, related_contracts
from workspace_paths import resolve_engine_root_for_association


def _related_contracts(symbol: str, owner: str, rows: list[dict[str, Any]], version: str) -> dict[str, Any] | None:
    """Ids and one-sentence summaries only; the contract file stays the source."""
    data = load_contracts_or_none()
    if data is None:
        return {"status": "unavailable"}
    types = [t for t in dict.fromkeys([owner, *(t for r in rows for t in [r.get("declaredIn"), *(r.get("inheritancePath") or [])])]) if t]
    items = related_contracts(data, symbol, types)[:3]
    if not items:
        return None
    return {"revision": contract_revision(data), "engineVersionValidated": engine_version_validated(data, version),
            "items": [{"id": c["id"], "kind": c["kind"], "summary": c["statement"].split(". ")[0].rstrip(".") + "."}
                      for c in items]}


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
    # An inherited member is reached through the requested owner; resolve the
    # requested name while each row keeps its declaring type in declaredIn.
    candidates = [{"symbol_name": symbol,
                   "qualified_name": f"{owner}::{symbol}" if owner else r["qualified_name"],
                   "filePath": r["source"], "line": r["line"]} for r in rows]
    target = resolve_symbol_target(query, candidates, access="read")
    evidence = "\n\n".join(f"{r['source']}:{r['line']}\n{r['excerpt']}" for r in rows)
    # Implementation bodies are evidence text; only their locators and
    # fingerprints are response metadata.
    definitions = [{k: v for k, v in c.items() if k != "excerpt"}
                   for r in rows for c in r.get("definitionCandidates", [])]
    definition_text = "\n\n".join(f"[definition candidate] {c['source']}:{c['line']}\n{c['excerpt']}"
                                  for r in rows for c in r.get("definitionCandidates", []))
    if definition_text:
        evidence = f"{evidence}\n\n{definition_text}" if evidence else definition_text
    matches = [{k: v for k, v in r.items() if k not in ("excerpt", "definitionCandidates")} for r in rows]
    payload: dict[str, Any] = {"ok": True, "query": query, "requestedOwner": owner,
        "canonicalProject": str(descriptor), "canonicalProjectRoot": str(descriptor.parent),
        "sourceMode": "engine_local", "evidenceSource": "installed_engine_source",
        "requestedEngineAssociation": expected, "engineSelectionReason": resolution.get("source"),
        "observedEngineIdentity": identity, "coverage": source.get("coverage", {"completeness": "unknown"}),
        "status": source["status"], "matchCount": len(rows), "matches": matches,
        "targetResolution": target, "evidence": evidence, "readBytes": source.get("readBytes", 0),
        "sourceIdentity": {"canonicalProject": str(descriptor), "sourceMode": "engine_local", "engineRoot": str(root)},
        "sourceVersion": "observed:" + hashlib.sha256(json.dumps([identity, matches, definitions], sort_keys=True).encode()).hexdigest(),
        "declarationProof": "bounded_lexical_evidence; targetResolution_only_identifies_candidates",
        # Finding a declaration on a parent is not proof that it is accessible
        # or callable from the requested owner (access, hiding, overloads).
        "callabilityVerification": "not_verified_access_overload_and_macro_expansion"}
    if definitions:
        payload["definitionCandidates"] = definitions
    related = _related_contracts(symbol, owner, rows, identity["version"])
    if related:
        payload["relatedContracts"] = related
    # Remove whole candidate metadata before fitting excerpt text. Exact source
    # identities and partial fingerprints are never clipped into false hashes.
    while payload.get("definitionCandidates") and not evidence_metadata_fits(payload, max_chars=limit):
        payload["definitionCandidates"] = payload["definitionCandidates"][:-1]
        payload["definitionCandidatesOmitted"] = len(definitions) - len(payload["definitionCandidates"])
    while payload["matches"] and not evidence_metadata_fits(payload, max_chars=limit):
        payload["matches"] = payload["matches"][:-1]
        payload["matchMetadataOmitted"] = len(rows) - len(payload["matches"])
    if not evidence_metadata_fits(payload, max_chars=limit):
        return scoped_failure("OUTPUT_LIMIT_EXCEEDED", "Local source identity does not fit the configured result limit.")
    payload, truncated = fit_evidence_payload(payload, max_chars=limit - 64)
    if truncated:
        payload["evidenceEnvelopeTruncated"] = True
    return CapabilityResult(payload, char_limit=limit)
