"""Validate the generated pattern knowledge export; never project source authority."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def _canonical(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace("\r\n", "\n").replace("\r", "\n")
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    if isinstance(value, dict):
        return {key: _canonical(value[key]) for key in sorted(value)}
    return value


def content_hash(value: Any) -> str:
    encoded = json.dumps(_canonical(value), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def load_pattern_catalog(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schemaVersion") != 1 or not isinstance(value.get("patterns"), list):
        raise ValueError("Unsupported architecture pattern export")
    seen: set[str] = set()
    for item in value["patterns"]:
        pattern_id = item.get("patternId")
        if not isinstance(pattern_id, str) or pattern_id in seen:
            raise ValueError("Duplicate or invalid pattern identity")
        seen.add(pattern_id)
        body = {key: item[key] for key in ("patternId", "sectionId", "title", "text", "applicability", "selection", "sources")}
        if content_hash(body) != item.get("contentHash"):
            raise ValueError(f"Pattern hash mismatch: {pattern_id}")
    pairs = [{"patternId": item["patternId"], "contentHash": item["contentHash"]}
             for item in sorted(value["patterns"], key=lambda item: item["patternId"])]
    if content_hash({"schemaVersion": 1, "patterns": pairs}) != value.get("catalogHash"):
        raise ValueError("Pattern catalog hash mismatch")
    return value


def pattern_rows(path: Path) -> list[dict[str, Any]]:
    catalog = load_pattern_catalog(path)
    return [{
        "id": hashlib.sha256(f"architecture-pattern:{item['patternId']}:{item['contentHash']}".encode()).hexdigest(),
        "source": "project_guideline", "path": f"docs/model-guidance/unreal-patterns.md#{item['sectionId'].split('/')[-1]}",
        "title": item["title"], "text": item["text"],
        "metadata": {
            "guidanceKind": "architecture_pattern", "patternId": item["patternId"],
            "contentHash": item["contentHash"], "catalogHash": catalog["catalogHash"],
            "patternConcept": item["applicability"]["concept"],
            "validatedApiVersions": item["applicability"]["validatedApiVersions"],
            "patternSources": [source["url"] for source in item["sources"]],
            "relative_path": f"Core_Architecture/{item['patternId']}.md", "extension": ".md", "guideline": True,
        },
    } for item in catalog["patterns"]]


def collected_catalog_current(raw: Path, export: Path) -> bool:
    if not raw.is_file():
        return False
    expected = load_pattern_catalog(export)
    wanted = {(item["patternId"], item["contentHash"]) for item in expected["patterns"]}
    actual: list[tuple[str, str]] = []
    try:
        with raw.open(encoding="utf-8") as handle:
            for line in handle:
                metadata = json.loads(line).get("metadata") or {}
                if metadata.get("guidanceKind") == "architecture_pattern":
                    if metadata.get("catalogHash") != expected["catalogHash"]:
                        return False
                    actual.append((metadata.get("patternId"), metadata.get("contentHash")))
    except (OSError, ValueError, TypeError):
        return False
    return len(actual) == len(wanted) and set(actual) == wanted


class PatternInventory:
    """One generation's canonical knowledge identities, owned by the index writer."""

    def __init__(self) -> None:
        self.patterns: dict[str, str] = {}
        self.catalog_hashes: set[str] = set()

    def add(self, metadata: dict[str, Any]) -> bool:
        if metadata.get("guidanceKind") != "architecture_pattern":
            return True
        pattern_id, version = metadata.get("patternId"), metadata.get("contentHash")
        if not isinstance(pattern_id, str) or not isinstance(version, str):
            raise ValueError("Invalid architecture pattern identity")
        if pattern_id in self.patterns:
            if self.patterns[pattern_id] != version:
                raise ValueError("Mixed versions of one architecture pattern")
            return False
        self.patterns[pattern_id] = version
        self.catalog_hashes.add(str(metadata.get("catalogHash") or ""))
        return True

    def catalog_hash(self) -> str:
        return content_hash({"schemaVersion": 1, "patterns": [
            {"patternId": key, "contentHash": self.patterns[key]} for key in sorted(self.patterns)]})

    def validate(self) -> None:
        if self.patterns and self.catalog_hashes != {self.catalog_hash()}:
            raise ValueError("Incomplete or mixed architecture pattern catalog")
