#!/usr/bin/env python
"""Strict loader for config/unreal_contracts.json (standard library only).

The JSON file is the single source for Unreal engine facts and project
policies shared by the static validators, the engine_local symbol lookup and
the generated model guidance. It holds data only; analysis logic stays in the
validators. A malformed file raises ContractError so CI fails, while runtime
callers use ``load_contracts_or_none`` and report the analysis as unavailable.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

CONTRACTS_PATH = Path(__file__).resolve().parents[1] / "config" / "unreal_contracts.json"
SCHEMA_VERSION = 1
KINDS = frozenset({"engine_fact", "project_policy"})
TOPICS = frozenset({"lifecycle", "subsystem", "threading", "networking", "verification"})
_CONTRACT_KEYS = {"id", "revision", "kind", "topic", "applicability", "statement", "preconditions",
                  "exceptions", "evidenceRefs", "guidanceRefs"}
_LIFECYCLE_KEYS = {"description", "parents", "overrides", "alternatives", "extraOverrideCandidates",
                   "invalidOverrideNames", "contractRefs"}


class ContractError(ValueError):
    """The contract file is missing or does not match the schema."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def _string_list(value: Any, label: str) -> list[str]:
    _require(isinstance(value, list) and all(isinstance(item, str) and item for item in value),
             f"{label} must be a list of non-empty strings")
    return value


def _validate_lifecycle(value: Any) -> None:
    _require(isinstance(value, dict) and set(value) == _LIFECYCLE_KEYS, "lifecycle has unexpected keys")
    parents = value["parents"]
    _require(isinstance(parents, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in parents.items()),
             "lifecycle.parents must map type names to a parent name")
    for start in parents:
        seen = {start}
        current = parents[start]
        while current in parents:
            _require(current not in seen, f"lifecycle.parents has a cycle at {start}")
            seen.add(current)
            current = parents[current]
        _require(current == "UObject", f"lifecycle.parents chain of {start} must end at UObject")
    overrides = value["overrides"]
    _require(isinstance(overrides, dict) and "UObject" in overrides, "lifecycle.overrides must include UObject")
    for name, hooks in overrides.items():
        _string_list(hooks, f"lifecycle.overrides.{name}")
        _require(name == "UObject" or name in parents, f"lifecycle.overrides.{name} has no parent entry")
    alternatives = value["alternatives"]
    _require(isinstance(alternatives, dict) and set(alternatives) == set(overrides),
             "lifecycle.alternatives must cover exactly the override owners")
    _string_list(value["extraOverrideCandidates"], "lifecycle.extraOverrideCandidates")
    invalid = value["invalidOverrideNames"]
    _require(isinstance(invalid, dict) and all(k == k.lower() and isinstance(v, str) and v for k, v in invalid.items()),
             "lifecycle.invalidOverrideNames must map lowercase names to messages")
    _string_list(value["contractRefs"], "lifecycle.contractRefs")


def _validate_contract(value: Any, versions: list[str]) -> None:
    _require(isinstance(value, dict) and set(value) == _CONTRACT_KEYS, f"contract has unexpected keys: {value!r}"[:200])
    cid = value["id"]
    _require(isinstance(cid, str) and cid and cid == cid.strip(), "contract id must be a non-empty string")
    _require(type(value["revision"]) is int and value["revision"] >= 1, f"{cid}: revision must be a positive int")
    _require(value["kind"] in KINDS, f"{cid}: unknown kind")
    _require(value["topic"] in TOPICS, f"{cid}: unknown topic")
    applicability = value["applicability"]
    _require(isinstance(applicability, dict), f"{cid}: applicability must be an object")
    engine_versions = _string_list(applicability.get("engineVersions"), f"{cid}: applicability.engineVersions")
    if value["kind"] == "engine_fact":
        _require(all(v in versions for v in engine_versions), f"{cid}: engine fact applies to an unvalidated version")
        _require(bool(value["evidenceRefs"]), f"{cid}: an engine fact needs engine source evidence")
    _require(isinstance(value["statement"], str) and value["statement"].strip(), f"{cid}: statement is empty")
    _string_list(value["preconditions"], f"{cid}: preconditions")
    _require(isinstance(value["exceptions"], list), f"{cid}: exceptions must be a list")
    _string_list(value["exceptions"], f"{cid}: exceptions")
    _string_list(value["guidanceRefs"], f"{cid}: guidanceRefs")
    refs = value["evidenceRefs"]
    _require(isinstance(refs, list), f"{cid}: evidenceRefs must be a list")
    for ref in refs:
        _require(isinstance(ref, dict) and {"path", "symbol"} <= set(ref) <= {"path", "symbol", "line"},
                 f"{cid}: evidence ref needs path and symbol")
        _require(isinstance(ref["path"], str) and ref["path"].startswith("Engine/") and ".." not in ref["path"],
                 f"{cid}: evidence path must be engine-root relative")
        _require("line" not in ref or (type(ref["line"]) is int and ref["line"] >= 1), f"{cid}: bad evidence line")


def validate_contracts(data: Any) -> dict[str, Any]:
    _require(isinstance(data, dict), "contract file must be a JSON object")
    _require(data.get("schemaVersion") == SCHEMA_VERSION, "unsupported contract schemaVersion")
    _require(type(data.get("revision")) is int and data["revision"] >= 1, "revision must be a positive int")
    versions = _string_list(data.get("validatedEngineVersions"), "validatedEngineVersions")
    _validate_lifecycle(data.get("lifecycle"))
    contracts = data.get("contracts")
    _require(isinstance(contracts, list) and contracts, "contracts must be a non-empty list")
    ids: set[str] = set()
    for contract in contracts:
        _validate_contract(contract, versions)
        _require(contract["id"] not in ids, f"duplicate contract id {contract['id']}")
        ids.add(contract["id"])
    for ref in data["lifecycle"]["contractRefs"]:
        _require(ref in ids, f"lifecycle references unknown contract {ref}")
    return data


@lru_cache(maxsize=4)
def _load(path: str) -> dict[str, Any]:
    try:
        raw = Path(path).read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ContractError(f"contract file unavailable: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ContractError(f"contract file is not valid JSON: {exc}") from exc
    return validate_contracts(data)


def load_contracts(path: Path | str | None = None) -> dict[str, Any]:
    return _load(str(Path(path or CONTRACTS_PATH).resolve()))


def load_contracts_or_none(path: Path | str | None = None) -> dict[str, Any] | None:
    try:
        return load_contracts(path)
    except ContractError:
        return None


def contract_revision(data: dict[str, Any]) -> str:
    return f"unreal_contracts@{data['revision']}"


def contract_by_id(data: dict[str, Any], contract_id: str) -> dict[str, Any] | None:
    return next((c for c in data["contracts"] if c["id"] == contract_id), None)


def contracts_for_topic(data: dict[str, Any], topic: str) -> list[dict[str, Any]]:
    return [c for c in data["contracts"] if c["topic"] == topic]


def related_contracts(data: dict[str, Any], symbol: str, types: list[str]) -> list[dict[str, Any]]:
    """Contracts whose evidence names symbol on one of types, or that are about symbol itself.

    A type-level contract only relates when the symbol also appears in its
    evidence or statement, so unrelated members of AActor do not pull in the
    actor lifecycle contract.
    """
    related = []
    for contract in data["contracts"]:
        evidence = [str(ref.get("symbol", "")) for ref in contract["evidenceRefs"]]
        contract_types = set(contract["applicability"].get("types", []))
        named = any(f"{owner}::{symbol}" in ref for owner in types for ref in evidence)
        about_type = symbol in contract_types
        mentioned = bool(contract_types & set(types)) and (
            any(symbol in ref for ref in evidence) or re.search(rf"\b{re.escape(symbol)}\b", contract["statement"]))
        if named or about_type or mentioned:
            related.append(contract)
    return related


def engine_version_validated(data: dict[str, Any], version: str) -> bool:
    """Whether contracts were validated for this engine (major.minor)."""
    return ".".join(str(version or "").split(".")[:2]) in data["validatedEngineVersions"]


__all__ = [
    "CONTRACTS_PATH", "ContractError", "contract_by_id", "contract_revision", "contracts_for_topic",
    "engine_version_validated", "load_contracts", "load_contracts_or_none", "related_contracts", "validate_contracts",
]
