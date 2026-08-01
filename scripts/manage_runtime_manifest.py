#!/usr/bin/env python3
"""Validate or safely update pinned runtime archive checksums.

Examples:
  python scripts/manage_runtime_manifest.py validate
  python scripts/manage_runtime_manifest.py list
  python scripts/manage_runtime_manifest.py update-checksums upstream-checksums.json

The update input is a JSON object mapping exact archive filenames to SHA-256.
Unknown filenames, missing current assets, and malformed hashes fail closed.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "installer" / "runtime-manifest.json"
BOOTSTRAP_PATH = ROOT / "installer" / "bootstrap_runtimes.py"
sys.path.insert(0, str(ROOT / "scripts"))

from atomic_io import atomic_write_text  # noqa: E402


def load_manifest() -> dict:
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("runtime manifest root must be a JSON object")
    return payload


def load_bootstrap_module():
    spec = importlib.util.spec_from_file_location("runtime_bootstrap_validation", BOOTSTRAP_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def validate_manifest() -> dict[str, int]:
    return load_bootstrap_module().validate_runtime_manifest()


def asset_rows(payload: dict) -> list[dict]:
    rows: list[dict] = []
    for runtime_name, definition in (payload.get("runtimes") or {}).items():
        if not isinstance(definition, dict):
            continue
        template = str(definition.get("urlTemplate") or "")
        version = str(definition.get("version") or "")
        for asset in definition.get("assets") or []:
            if not isinstance(asset, dict):
                continue
            rows.append(
                {
                    "runtime": runtime_name,
                    "version": version,
                    "platform": asset.get("platform"),
                    "architecture": asset.get("architecture"),
                    "filename": asset.get("filename"),
                    "url": template.format(version=version, asset=asset.get("filename")),
                    "sha256": asset.get("sha256"),
                    "executable": asset.get("executable"),
                    "executableProbe": definition.get("executableProbe"),
                }
            )
    return rows


def update_checksums(checksum_path: Path) -> dict[str, int]:
    updates = json.loads(checksum_path.read_text(encoding="utf-8"))
    if not isinstance(updates, dict):
        raise ValueError("checksum update must be a JSON object")
    normalized = {str(name): str(value).lower() for name, value in updates.items()}
    invalid = [name for name, value in normalized.items() if not re.fullmatch(r"[0-9a-f]{64}", value)]
    if invalid:
        raise ValueError(f"invalid SHA-256 for: {', '.join(sorted(invalid))}")
    payload = load_manifest()
    rows = asset_rows(payload)
    known = {str(row["filename"]) for row in rows}
    unknown = set(normalized) - known
    if unknown:
        raise ValueError(f"unknown runtime archive(s): {', '.join(sorted(unknown))}")
    if not normalized:
        raise ValueError("checksum update is empty")
    changed = 0
    for definition in (payload.get("runtimes") or {}).values():
        if not isinstance(definition, dict):
            continue
        for asset in definition.get("assets") or []:
            if not isinstance(asset, dict):
                continue
            filename = str(asset.get("filename") or "")
            if filename in normalized and asset.get("sha256") != normalized[filename]:
                asset["sha256"] = normalized[filename]
                changed += 1
    validation_module = load_bootstrap_module()
    validation = validation_module.validate_runtime_manifest(payload)
    atomic_write_text(
        MANIFEST_PATH,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {**validation, "updatedChecksumCount": changed}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "list", "update-checksums"))
    parser.add_argument("checksum_json", nargs="?", type=Path)
    args = parser.parse_args()
    if args.command == "validate":
        print(json.dumps({"ok": True, **validate_manifest()}, indent=2))
        return 0
    if args.command == "list":
        validate_manifest()
        print(json.dumps(asset_rows(load_manifest()), indent=2))
        return 0
    if args.checksum_json is None:
        parser.error("update-checksums requires a checksum JSON path")
    print(json.dumps({"ok": True, **update_checksums(args.checksum_json)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
