"""Shared control-protocol schema loader and deterministic section identities."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


HASH_SECTIONS = {
    "transitionPolicyHash": "transitionPolicy",
    "errorCatalogHash": "errorCatalog",
    "authorizationSchemaHash": "authorizationSchema",
    "controlSchemaHash": "controlSchema",
}


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def section_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _candidate_paths(
    *,
    repository_root: str | Path | None = None,
    spec_path: str | Path | None = None,
) -> list[Path]:
    root = Path(repository_root).expanduser().resolve() if repository_root else Path(__file__).resolve().parents[1]
    values = [
        str(spec_path or "").strip(),
        os.environ.get("CONTROL_PROTOCOL_SPEC", "").strip(),
        str(root / "config" / "control_protocol_spec.json"),
        str(Path(__file__).resolve().parents[1] / "config" / "control_protocol_spec.json"),
    ]
    output: list[Path] = []
    for raw in values:
        if not raw:
            continue
        candidate = Path(raw).expanduser().resolve()
        if candidate not in output:
            output.append(candidate)
    return output


def _embedded_spec(manifest_path: str | Path | None = None) -> dict[str, Any] | None:
    raw = str(manifest_path or os.environ.get("CONTROL_RUNTIME_MANIFEST") or "").strip()
    if not raw:
        return None
    try:
        manifest = json.loads(Path(raw).expanduser().resolve().read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    spec = manifest.get("protocolSpec") if isinstance(manifest, dict) else None
    return spec if isinstance(spec, dict) else None


def _validate_spec(spec: Any) -> dict[str, Any]:
    if not isinstance(spec, dict):
        raise RuntimeError("control protocol spec must be an object")
    if int(spec.get("schemaVersion") or 0) < 1:
        raise RuntimeError("control protocol schemaVersion is missing")
    if int(spec.get("protocolVersion") or 0) < 1:
        raise RuntimeError("control protocol protocolVersion is missing")
    for section in HASH_SECTIONS.values():
        if not isinstance(spec.get(section), dict):
            raise RuntimeError(f"control protocol {section} is missing")
    return spec


def load_control_protocol_spec(
    *,
    repository_root: str | Path | None = None,
    spec_path: str | Path | None = None,
    manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    for candidate in _candidate_paths(repository_root=repository_root, spec_path=spec_path):
        if candidate.is_file():
            return _validate_spec(json.loads(candidate.read_text(encoding="utf-8-sig")))
    embedded = _embedded_spec(manifest_path)
    if embedded is not None:
        return _validate_spec(embedded)
    raise RuntimeError(
        "CONTROL_PROTOCOL_SPEC_UNAVAILABLE: config/control_protocol_spec.json is missing"
    )


def control_protocol_identity(**kwargs: Any) -> dict[str, Any]:
    spec = load_control_protocol_spec(**kwargs)
    return {
        "protocolVersion": int(spec["protocolVersion"]),
        **{
            field: section_hash(spec[section])
            for field, section in HASH_SECTIONS.items()
        },
    }
