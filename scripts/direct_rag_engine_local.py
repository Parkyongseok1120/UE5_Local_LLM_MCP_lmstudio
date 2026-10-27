"""Explicit local declaration lookup using the existing project/engine owners."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from direct_rag_evidence import evidence_metadata_fits, evidence_metadata_minimum, fit_evidence_payload
from direct_rag_project_engine import project_engine_version
from direct_rag_project_selectors import classify_project_selectors, exact_project_descriptor
from direct_rag_result import CapabilityResult, failure
from engine_header_evidence import lookup_engine_header_evidence
from engine_definition_evidence import reflection_thunk_names
from engine_identity import read_unreal_build_identity as _build_identity
from project_name_resolver import resolve_project_name
from target_resolver import resolve_symbol_target
from unreal_contracts import contract_revision, engine_version_validated, load_contracts_or_none, related_contracts
from workspace_paths import resolve_engine_root_for_association

_RUNTIME_DEFAULT_EXCLUDES = ("Engine/Source/Editor", "Engine/Source/Developer", "Engine/Source/Programs", "Engine/Plugins")

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
        spaced = re.fullmatch(r"([A-Za-z_]\w{0,127})\s+([A-Za-z_]\w{0,127})", query, re.ASCII)
        corrected = f"{spaced.group(1)}::{spaced.group(2)}" if spaced else None
        return failure("INVALID_TOOL_ARGUMENTS",
                       "engine_local query must be Symbol or Owner::Symbol (for example AActor::TakeDamage)."
                       + (f" Did you mean {corrected}?" if corrected else ""),
                       **({"suggestion": {"tool": "unreal_symbol_lookup",
                                          "args": {"query": corrected, "sourceMode": "engine_local"}}} if corrected else {}))
    if owner and not re.fullmatch(r"[A-Za-z_]\w{0,127}", owner, re.ASCII):
        return failure("INVALID_TOOL_ARGUMENTS", "owner must be one exact type name.")
    # signature returns declarations and proven implementation locators, without bodies.
    view = str(arguments.get("view") or "full")
    if view not in ("signature", "full"):
        return failure("INVALID_TOOL_ARGUMENTS", "view must be signature or full.")
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
    # An override can explicitly declare the suffixed name in a derived class.
    # Prefer that exact declaration before considering a generated UHT thunk.
    thunk = re.fullmatch(r"(\w+?)_(Implementation|Validate)", symbol, re.ASCII)
    declared_symbol = symbol
    source = lookup_engine_header_evidence(root, [{"symbol": symbol, "receiverType": owner}],
                                          local_source_identity=identity["sha256"],
                                          include_definitions=True)
    rows = list(source.get("results") or [])
    if thunk and not rows:
        fallback = lookup_engine_header_evidence(root, [{"symbol": thunk.group(1), "receiverType": owner}],
                                                local_source_identity=identity["sha256"],
                                                include_definitions=True)
        read_bytes = source.get("readBytes", 0) + fallback.get("readBytes", 0)
        rows = [{**row, "definitionCandidates": [candidate for candidate in row.get("definitionCandidates", [])
                                                 if candidate.get("symbol_name") == symbol]}
                for row in fallback.get("results") or [] if symbol in reflection_thunk_names(row)]
        if rows:
            declared_symbol = thunk.group(1)
            # The explicit suffixed lookup's limits still bound this answer.
            reasons = {*source.get("coverage", {}).get("reasons", []), *fallback.get("coverage", {}).get("reasons", [])}
            source = {**fallback, "coverage": {**fallback.get("coverage", {}), "reasons": sorted(reasons)}}
        source = {**source, "readBytes": read_bytes}
    if _build_identity(root) != identity:
        return scoped_failure("ENGINE_SOURCE_CHANGED", "Engine identity changed during the source observation.")
    # An inherited member is reached through the requested owner; resolve the
    # requested name while each row keeps its declaring type in declaredIn.
    candidates = [{"symbol_name": declared_symbol,
                   "qualified_name": f"{owner}::{declared_symbol}" if owner else r["qualified_name"],
                   "filePath": r["source"], "line": r["line"]} for r in rows]
    target = resolve_symbol_target(f"{owner}::{declared_symbol}" if owner else declared_symbol, candidates, access="read")
    declaration_blocks = [f"{r['source']}:{r['line']}\n{r['excerpt']}" for r in rows]
    evidence = "\n\n".join(declaration_blocks)
    # Implementation bodies are evidence text; only their locators and
    # fingerprints are response metadata.
    definitions = [{k: v for k, v in c.items() if k != "excerpt"}
                   for r in rows for c in r.get("definitionCandidates", [])]
    definition_blocks = [f"[definition candidate] {c['source']}:{c['line']}\n{c['excerpt']}"
                         for r in rows for c in r.get("definitionCandidates", [])]
    definition_text = "\n\n".join(definition_blocks)
    if definition_text and view == "full":
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
    if view == "signature":
        payload["view"] = "signature"
        payload["definitionBodiesOmitted"] = True
    if definitions:
        # In full view only a cut body needs continuation. Signature view
        # omits every body but retains a targeted read when its end is proven.
        payload["definitionCandidates"] = [
            {**_with_range(c, root), "evidenceExcerpt": "omitted"} if view == "signature"
            else _with_range(c, root) if c.get("excerptTruncated") else c
            for c in definitions]
    if thunk and declared_symbol != symbol:
        payload["reflectionThunk"] = {
            "requestedSymbol": symbol, "declaredSymbol": declared_symbol, "suffix": f"_{thunk.group(2)}",
            "reflectionSpecifierObserved": True,
        }
    if not rows:
        # A bounded zero is not absence. Offer the one search that reads
        # engine text directly (the agent server's search_files over engine://).
        # The owner already failed, so search the bare token (any kind: function,
        # field or enumerator) in the one module where an owner type was read.
        modules = {t["moduleUri"] for t in payload["coverage"].get("observedTypeCandidates", []) if t.get("moduleUri")}
        token = {"query": rf"\b{re.escape(symbol)}\b", "regex": True}
        if len(modules) == 1:
            payload["suggestion"] = {"tool": "search_files", "scope": "observed_owner_module",
                                     "args": {"path": modules.pop(), **token}}
        else:
            # The header lookup also reads Editor and Plugins; this default does not.
            limit_note: dict[str, Any] = {"excludes": list(_RUNTIME_DEFAULT_EXCLUDES)}
            if modules:
                limit_note["observedModules"] = len(modules)
            payload["suggestion"] = {"tool": "search_files", "scope": "engine_runtime_default_module_unknown",
                                     "scopeLimit": limit_note,
                                     "args": {"path": "engine://Engine/Source/Runtime", **token, "maxFiles": 50000}}
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
    observed = payload["coverage"].get("observedTypeCandidates")
    if observed and not evidence_metadata_fits(payload, max_chars=limit):
        # Whole items only: a clipped URI would name a path that was never read.
        payload["coverage"] = {k: v for k, v in payload["coverage"].items() if k != "observedTypeCandidates"}
        payload["coverage"]["observedTypeCandidatesOmitted"] = len(observed)
    if not evidence_metadata_fits(payload, max_chars=limit):
        return scoped_failure("OUTPUT_LIMIT_EXCEEDED", "Local source identity does not fit the configured result limit. Increase maxChars and the detail/transport limit if it is lower.",
                              retry_allowed=True, retry_mode="different_arguments",
                              retry_minimum={"field": "maxChars", "value": evidence_metadata_minimum(payload)})
    payload, truncated = fit_evidence_payload(payload, max_chars=limit - 64)
    if truncated:
        payload["evidenceEnvelopeTruncated"] = True
        payload = _mark_clipped_evidence(payload, {"matches": declaration_blocks,
                                                   "definitionCandidates": definition_blocks}, root, limit - 64)
    return CapabilityResult(payload, char_limit=limit)


def _with_range(candidate: dict[str, Any], root: Path) -> dict[str, Any]:
    """Add the whole-function read_file_range on engine:// when its end is proven."""
    try:
        uri = "engine://" + Path(candidate["source"]).relative_to(root).as_posix()
    except ValueError:
        return candidate
    if not candidate.get("endLine"):
        return candidate
    return {**candidate, "rangeSuggestion": {"tool": "read_file_range", "args": {
        "path": uri, "startLine": candidate["line"], "endLine": candidate["endLine"]}}}


def _mark_clipped_evidence(payload: dict[str, Any], blocks: dict[str, list[str]], root: Path,
                           max_chars: int) -> dict[str, Any]:
    """Mark declaration and definition excerpts the envelope fit cut, so
    excerptTruncated=false (the excerpt stage) is not read as the delivered
    text being complete. Only a definition with a proven end gets a range."""
    for _ in range(3):
        evidence = str(payload.get("evidence") or "")
        updated, changed = {}, False
        for key, key_blocks in blocks.items():
            if key not in payload:
                continue
            marked = []
            for item, block in zip(payload[key] or [], key_blocks):
                state = None if block in evidence else (
                    "partial" if block.split("\n", 1)[0] + "\n" in evidence else "omitted")
                if item.get("evidenceExcerpt") != state:
                    changed = True
                    item = {k: v for k, v in item.items() if k != "evidenceExcerpt"}
                    if state:
                        item = {**(_with_range(item, root) if key == "definitionCandidates" else item),
                                "evidenceExcerpt": state}
                marked.append(item)
            updated[key] = marked
        if not changed:
            return payload
        annotated = {**payload, **updated}
        # The marks are metadata; never let them push the identity out of the cap.
        if not evidence_metadata_fits(annotated, max_chars=max_chars, reserve_chars=0):
            return payload
        payload, _ = fit_evidence_payload(annotated, max_chars=max_chars)
    return payload
