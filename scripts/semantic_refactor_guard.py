#!/usr/bin/env python
"""Meaning-preservation contract for isolated Unreal refactor candidates.

The guard compares the current project with an isolated candidate tree. It
proves exact file/surface/proof identity only; it does not claim full runtime
behavioral equivalence.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

ALLOWED_ROOTS = ("Source", "Plugins", "Config")
REFLECTION_MACROS = (
    "UCLASS",
    "USTRUCT",
    "UENUM",
    "UINTERFACE",
    "UFUNCTION",
    "UPROPERTY",
)
BREAKING_SURFACE_TYPES = frozenset(
    {
        *(f"reflection_{name.lower()}" for name in REFLECTION_MACROS),
        "delegate",
        "public_type",
        "public_signature",
        "module_dependency",
        "plugin_module",
        "plugin_dependency",
        "config",
    }
)
WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")
CPP_HEADER_SUFFIXES = frozenset({".h", ".hh", ".hpp", ".inl"})
CPP_SUFFIXES = CPP_HEADER_SUFFIXES | frozenset({".c", ".cc", ".cpp", ".cxx"})
MAX_FILE_BYTES = 8 * 1024 * 1024
PROTECTED_SEGMENTS = frozenset(
    {".git", "binaries", "intermediate", "saved", "deriveddatacache"}
)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(_canonical_json(value).encode("utf-8"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_space(value: str) -> str:
    return " ".join(str(value or "").split())


def _normalize_relative_path(value: Any) -> tuple[str, str]:
    if not isinstance(value, str):
        return "", "changedFiles entries must be strings"
    raw = value.strip().replace("\\", "/")
    path = PurePosixPath(raw)
    if (
        not raw
        or raw.startswith("/")
        or WINDOWS_DRIVE_RE.match(raw)
        or ".." in path.parts
    ):
        return "", "paths must be project-relative without traversal"
    normalized = path.as_posix()
    if not normalized.startswith(tuple(f"{root}/" for root in ALLOWED_ROOTS)):
        return "", "paths must stay under Source, Plugins, or Config"
    if any(part.lower() in PROTECTED_SEGMENTS for part in path.parts):
        return "", "paths must not target generated/cache directories"
    return normalized, ""


def _resolve_root(value: str | Path | None) -> Path | None:
    if not value:
        return None
    candidate = Path(value).expanduser().resolve()
    if candidate.is_file() and candidate.suffix.lower() == ".uproject":
        candidate = candidate.parent
    return candidate if candidate.is_dir() else None


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _read_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _strip_cpp_comments(text: str) -> str:
    without_blocks = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", "", without_blocks)


def _macro_surfaces(text: str) -> list[tuple[str, str]]:
    clean = _strip_cpp_comments(text)
    lines = clean.splitlines()
    surfaces: list[tuple[str, str]] = []
    for index, line in enumerate(lines):
        for macro in REFLECTION_MACROS:
            if not re.search(rf"\b{macro}\s*(?:\([^)]*\))?", line):
                continue
            collected = [line.strip()]
            terminal = (
                re.compile(r"[{;]")
                if macro in {"UCLASS", "USTRUCT", "UENUM", "UINTERFACE"}
                else re.compile(r"[;{]")
            )
            if not terminal.search(line):
                for follower in lines[index + 1 : index + 13]:
                    stripped = follower.strip()
                    if not stripped:
                        continue
                    collected.append(stripped)
                    if terminal.search(stripped):
                        break
            value = _normalize_space(" ".join(collected))
            if value:
                surfaces.append((f"reflection_{macro.lower()}", value))
    return surfaces


def _delegate_surfaces(text: str) -> list[tuple[str, str]]:
    clean = _strip_cpp_comments(text)
    return [
        ("delegate", _normalize_space(match.group(0)))
        for match in re.finditer(
            r"\bDECLARE_[A-Za-z0-9_]*DELEGATE[A-Za-z0-9_]*\s*\([^;]*\)\s*;",
            clean,
            flags=re.DOTALL,
        )
    ]


def _public_signature_surfaces(text: str) -> list[tuple[str, str]]:
    clean = _strip_cpp_comments(text)
    access = ""
    buffer: list[str] = []
    surfaces: list[tuple[str, str]] = []
    for raw_line in clean.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        access_match = re.match(r"^(public|private|protected)\s*:\s*$", line)
        if access_match:
            access = access_match.group(1)
            buffer = []
            continue
        if access != "public" or line.startswith("#"):
            continue
        if line.startswith(REFLECTION_MACROS):
            continue
        buffer.append(line)
        if ";" not in line:
            if len(buffer) > 12:
                buffer = []
            continue
        declaration = _normalize_space(" ".join(buffer))
        buffer = []
        if declaration and not declaration.startswith(("using ", "typedef ")):
            surfaces.append(("public_signature", declaration))
    return surfaces


def _exported_type_surfaces(text: str) -> list[tuple[str, str]]:
    clean = _strip_cpp_comments(text)
    surfaces: list[tuple[str, str]] = []
    for match in re.finditer(
        r"\b(?:class|struct)\s+(?:[A-Za-z_][A-Za-z0-9_]*_API\s+)?"
        r"[A-Za-z_][A-Za-z0-9_]*(?:\s*:\s*[^{;]+)?\s*\{",
        clean,
    ):
        declaration = _normalize_space(match.group(0))
        if declaration:
            surfaces.append(("public_type", declaration))
    for match in re.finditer(
        r"\benum\s+(?:class\s+)?[A-Za-z_][A-Za-z0-9_]*"
        r"(?:\s*:\s*[A-Za-z_][A-Za-z0-9_:<>]*)?\s*\{",
        clean,
    ):
        declaration = _normalize_space(match.group(0))
        if declaration:
            surfaces.append(("public_type", declaration))
    for match in re.finditer(
        r"(?m)^[^#\n;{}]*\b[A-Za-z_][A-Za-z0-9_]*_API\b[^;{}]*;",
        clean,
    ):
        declaration = _normalize_space(match.group(0))
        if declaration and not declaration.startswith(("class ", "struct ")):
            surfaces.append(("public_signature", declaration))
    return surfaces


def _module_dependency_surfaces(text: str) -> list[tuple[str, str]]:
    clean = _strip_cpp_comments(text)
    surfaces: list[tuple[str, str]] = []
    pattern = re.compile(
        r"\b(?P<kind>(?:Public|Private)DependencyModuleNames|"
        r"(?:Public|Private)IncludePathModuleNames|"
        r"DynamicallyLoadedModuleNames|CircularlyReferencedDependentModules)"
        r"\s*\.\s*(?:Add|AddRange)\s*\((?P<body>.*?)\)\s*;",
        flags=re.DOTALL,
    )
    for match in pattern.finditer(clean):
        kind = match.group("kind")
        for module in re.findall(r'"([^"]+)"', match.group("body")):
            surfaces.append(("module_dependency", f"{kind}:{module.strip()}"))
    return surfaces


def _config_surfaces(text: str) -> list[tuple[str, str]]:
    section = ""
    surfaces: list[tuple[str, str]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith((";", "#")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        normalized_key = key.strip()
        if normalized_key:
            surfaces.append(
                ("config", f"{section}|{normalized_key}={value.strip()}")
            )
    return surfaces


def _plugin_descriptor_surfaces(text: str) -> list[tuple[str, str]]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, dict):
        return []
    surfaces: list[tuple[str, str]] = []
    modules = payload.get("Modules")
    for module in modules if isinstance(modules, list) else []:
        if not isinstance(module, dict):
            continue
        selected = {
            key: module.get(key)
            for key in ("Name", "Type", "LoadingPhase", "PlatformAllowList", "PlatformDenyList")
            if key in module
        }
        if selected.get("Name"):
            surfaces.append(("plugin_module", _canonical_json(selected)))
    plugins = payload.get("Plugins")
    for plugin in plugins if isinstance(plugins, list) else []:
        if not isinstance(plugin, dict):
            continue
        selected = {
            key: plugin.get(key)
            for key in ("Name", "Enabled", "Optional", "PlatformAllowList", "PlatformDenyList")
            if key in plugin
        }
        if selected.get("Name"):
            surfaces.append(("plugin_dependency", _canonical_json(selected)))
    return surfaces


def _extract_surfaces(relative_path: str, data: bytes) -> list[dict[str, str]]:
    suffix = Path(relative_path).suffix.lower()
    text = _read_text(data)
    raw: list[tuple[str, str]] = []
    if suffix in CPP_SUFFIXES:
        raw.extend(_macro_surfaces(text))
        raw.extend(_delegate_surfaces(text))
        if suffix in CPP_HEADER_SUFFIXES:
            raw.extend(_exported_type_surfaces(text))
            raw.extend(_public_signature_surfaces(text))
    if relative_path.endswith(".Build.cs"):
        raw.extend(_module_dependency_surfaces(text))
    if relative_path.startswith("Config/") and suffix == ".ini":
        raw.extend(_config_surfaces(text))
    if relative_path.startswith("Plugins/") and suffix == ".uplugin":
        raw.extend(_plugin_descriptor_surfaces(text))
    unique = sorted(set(raw))
    return [
        {
            "type": surface_type,
            "value": value,
            "surfaceId": _sha256_json(
                {
                    "path": relative_path,
                    "type": surface_type,
                    "value": value,
                }
            ),
        }
        for surface_type, value in unique
    ]


def capture_semantic_snapshot(
    root_value: str | Path | None,
    *,
    files: list[str] | None = None,
) -> dict[str, Any]:
    """Capture deterministic hashes and Unreal semantic surfaces."""
    root = _resolve_root(root_value)
    issues: list[str] = []
    if root is None:
        return {
            "ok": False,
            "snapshotHash": "",
            "files": [],
            "issues": ["snapshot root must be an existing project directory/.uproject"],
        }

    selected: list[str] = []
    if files is not None:
        if not isinstance(files, list):
            issues.append("files must be an array")
        else:
            for item in files:
                normalized, issue = _normalize_relative_path(item)
                if issue:
                    issues.append(f"{item!r}: {issue}")
                elif normalized not in selected:
                    selected.append(normalized)
    else:
        for allowed_root in ALLOWED_ROOTS:
            directory = root / allowed_root
            if not directory.is_dir():
                continue
            for candidate in directory.rglob("*"):
                relative = candidate.relative_to(root)
                if (
                    candidate.is_file()
                    and not any(
                        part.lower() in PROTECTED_SEGMENTS
                        for part in relative.parts
                    )
                ):
                    selected.append(relative.as_posix())
        selected = sorted(set(selected))

    entries: list[dict[str, Any]] = []
    for relative_path in sorted(selected):
        candidate = root / relative_path
        try:
            resolved = candidate.resolve()
            if not _path_is_within(resolved, root):
                issues.append(f"{relative_path}: resolved path escapes snapshot root")
                continue
            if not candidate.is_file():
                issues.append(f"{relative_path}: snapshot file does not exist")
                continue
            size = candidate.stat().st_size
            data = candidate.read_bytes() if size <= MAX_FILE_BYTES else b""
            content_hash = (
                _sha256_bytes(data)
                if size <= MAX_FILE_BYTES
                else _sha256_file(candidate)
            )
        except OSError as exc:
            issues.append(f"{relative_path}: could not be read ({type(exc).__name__}: {exc})")
            continue
        entries.append(
            {
                "path": relative_path,
                "contentHash": content_hash,
                "size": size,
                "surfaces": (
                    _extract_surfaces(relative_path, data)
                    if size <= MAX_FILE_BYTES
                    else []
                ),
            }
        )

    canonical = {"schemaVersion": 1, "files": entries}
    return {
        "ok": not issues,
        **canonical,
        "snapshotHash": _sha256_json(canonical) if not issues else "",
        "fileCount": len(entries),
        "surfaceCount": sum(len(entry["surfaces"]) for entry in entries),
        "issues": issues,
        "proofBoundary": (
            "The snapshot records deterministic file hashes and selected Unreal declaration/"
            "configuration surfaces. It does not prove runtime behavior."
        ),
    }


def _file_hashes(snapshot: dict[str, Any]) -> dict[str, str]:
    return {
        str(item.get("path") or ""): str(item.get("contentHash") or "")
        for item in snapshot.get("files") or []
        if isinstance(item, dict) and str(item.get("path") or "")
    }


def _surface_map(snapshot: dict[str, Any]) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for file_entry in snapshot.get("files") or []:
        if not isinstance(file_entry, dict):
            continue
        path = str(file_entry.get("path") or "")
        for surface in file_entry.get("surfaces") or []:
            if not isinstance(surface, dict):
                continue
            surface_id = str(surface.get("surfaceId") or "")
            if surface_id:
                result[surface_id] = {
                    "surfaceId": surface_id,
                    "path": path,
                    "type": str(surface.get("type") or ""),
                    "value": str(surface.get("value") or ""),
                }
    return result


def _proof_issues(
    proof: Any,
    *,
    label: str,
    diff_hash: str,
    changed_files: list[str],
) -> list[str]:
    if not isinstance(proof, dict):
        return [f"{label} must be an object"]
    issues: list[str] = []
    if proof.get("ok") is not True:
        issues.append(f"{label}.ok must be true")
    if not str(proof.get("artifactHash") or "").strip():
        issues.append(f"{label}.artifactHash is required")
    if str(proof.get("diffHash") or "").strip() != diff_hash:
        issues.append(f"{label}.diffHash must match the exact candidate diff")
    proof_files = proof.get("changedFiles")
    normalized_proof_files: list[str] = []
    if not isinstance(proof_files, list):
        issues.append(f"{label}.changedFiles must be an array")
    else:
        for item in proof_files:
            normalized, issue = _normalize_relative_path(item)
            if issue:
                issues.append(f"{label}.changedFiles: {issue}")
            elif normalized not in normalized_proof_files:
                normalized_proof_files.append(normalized)
        if sorted(normalized_proof_files) != changed_files:
            issues.append(
                f"{label}.changedFiles must exactly match the candidate changed files"
            )
    return issues


def _invariant_issues(
    invariants: Any,
    *,
    before_hash: str,
    after_hash: str,
) -> tuple[list[dict[str, Any]], list[str], bool]:
    if not isinstance(invariants, list) or not invariants:
        return [], ["at least one explicit semantic invariant is required"], False
    normalized: list[dict[str, Any]] = []
    issues: list[str] = []
    seen_ids: set[str] = set()
    runtime_required = False
    for index, value in enumerate(invariants):
        row = value if isinstance(value, dict) else {}
        prefix = f"invariants[{index}]"
        invariant_id = str(row.get("id") or "").strip()
        description = str(row.get("description") or "").strip()
        if not invariant_id:
            issues.append(f"{prefix}.id is required")
        elif invariant_id in seen_ids:
            issues.append(f"{prefix}.id must be unique")
        else:
            seen_ids.add(invariant_id)
        if not description:
            issues.append(f"{prefix}.description is required")
        if str(row.get("comparison") or "equals").strip() != "equals":
            issues.append(f"{prefix}.comparison must be equals")
        before = row.get("beforeObserver")
        after = row.get("afterObserver")
        if not isinstance(before, dict) or not isinstance(after, dict):
            issues.append(f"{prefix} requires beforeObserver and afterObserver objects")
            continue
        before_observer = str(before.get("observer") or "").strip()
        after_observer = str(after.get("observer") or "").strip()
        if not before_observer or before_observer != after_observer:
            issues.append(f"{prefix} observer identity must be nonempty and identical")
        for label, observer, expected_hash in (
            ("beforeObserver", before, before_hash),
            ("afterObserver", after, after_hash),
        ):
            if not str(observer.get("artifactHash") or "").strip():
                issues.append(f"{prefix}.{label}.artifactHash is required")
            if str(observer.get("snapshotHash") or "").strip() != expected_hash:
                issues.append(
                    f"{prefix}.{label}.snapshotHash must match its semantic snapshot"
                )
            if "value" not in observer:
                issues.append(f"{prefix}.{label}.value is required")
        values_equal = (
            "value" in before
            and "value" in after
            and _canonical_json(before.get("value")) == _canonical_json(after.get("value"))
        )
        if not values_equal:
            issues.append(f"{prefix} before/after observer values are not equal")
        runtime_sensitive = row.get("runtimeSensitive") is True
        runtime_required = runtime_required or runtime_sensitive
        normalized.append(
            {
                "id": invariant_id,
                "description": description,
                "comparison": "equals",
                "runtimeSensitive": runtime_sensitive,
                "observer": before_observer,
                "preserved": values_equal,
            }
        )
    return normalized, issues, runtime_required


def _coverage_issues(
    breaking_surfaces: list[dict[str, str]],
    contract: Any,
) -> tuple[list[dict[str, str]], list[str]]:
    if not breaking_surfaces:
        return [], []
    if not isinstance(contract, dict):
        return [], [
            "migrationCompatibilityContract is required for removed or changed semantic surfaces"
        ]
    coverage = contract.get("coverage")
    if not isinstance(coverage, list):
        return [], ["migrationCompatibilityContract.coverage must be an array"]
    valid: dict[str, dict[str, str]] = {}
    issues: list[str] = []
    for index, value in enumerate(coverage):
        row = value if isinstance(value, dict) else {}
        prefix = f"migrationCompatibilityContract.coverage[{index}]"
        surface_id = str(row.get("surfaceId") or "").strip()
        strategy = str(row.get("strategy") or "").strip()
        rationale = str(row.get("rationale") or "").strip()
        validation = str(row.get("validation") or "").strip()
        rollback = str(row.get("rollback") or "").strip()
        if strategy not in {"migration", "compatibility"}:
            issues.append(f"{prefix}.strategy must be migration or compatibility")
        if not all((surface_id, rationale, validation, rollback)):
            issues.append(
                f"{prefix} requires surfaceId, rationale, validation, and rollback"
            )
            continue
        valid[surface_id] = {
            "surfaceId": surface_id,
            "strategy": strategy,
            "rationale": rationale,
            "validation": validation,
            "rollback": rollback,
        }
    missing = [
        surface["surfaceId"]
        for surface in breaking_surfaces
        if surface["surfaceId"] not in valid
    ]
    if missing:
        issues.append(
            "migrationCompatibilityContract does not cover breaking surfaces: "
            + ", ".join(missing[:8])
        )
    return [valid[key] for key in sorted(valid)], issues


def compare_semantic_refactor(
    before_root: str | Path | None,
    after_root: str | Path | None,
    *,
    changed_files: list[str] | None,
    diff_hash: str,
    invariants: Any,
    static_proof: Any,
    build_proof: Any,
    runtime_proof: Any = None,
    migration_compatibility_contract: Any = None,
) -> dict[str, Any]:
    """Compare live and isolated trees and return a fail-closed write gate."""
    issues: list[str] = []
    before_path = _resolve_root(before_root)
    after_path = _resolve_root(after_root)
    if before_path is None:
        issues.append("projectRoot must be an existing project directory/.uproject")
    if after_path is None:
        issues.append("afterRoot must be an existing isolated candidate directory/.uproject")
    if before_path is not None and after_path is not None:
        if before_path == after_path:
            issues.append("afterRoot must be distinct from projectRoot")
        elif _path_is_within(before_path, after_path) or _path_is_within(
            after_path,
            before_path,
        ):
            issues.append("projectRoot and afterRoot must not contain one another")

    before = capture_semantic_snapshot(before_path)
    after = capture_semantic_snapshot(after_path)
    issues.extend(f"before snapshot: {item}" for item in before.get("issues") or [])
    issues.extend(f"after snapshot: {item}" for item in after.get("issues") or [])

    declared_files: list[str] = []
    if not isinstance(changed_files, list) or not changed_files:
        issues.append("changedFiles must be a nonempty array")
    else:
        for item in changed_files:
            normalized, issue = _normalize_relative_path(item)
            if issue:
                issues.append(f"changedFiles: {issue}")
            elif normalized not in declared_files:
                declared_files.append(normalized)
    declared_files.sort()

    before_hashes = _file_hashes(before)
    after_hashes = _file_hashes(after)
    actual_changed_files = sorted(
        path
        for path in set(before_hashes) | set(after_hashes)
        if before_hashes.get(path) != after_hashes.get(path)
    )
    if declared_files != actual_changed_files:
        issues.append(
            "changedFiles must exactly equal all Source/Plugins/Config differences "
            f"(declared={declared_files}, actual={actual_changed_files})"
        )
    transition = [
        {
            "path": path,
            "beforeHash": before_hashes.get(path, ""),
            "afterHash": after_hashes.get(path, ""),
        }
        for path in actual_changed_files
    ]
    computed_diff_hash = _sha256_json(transition)
    supplied_diff_hash = str(diff_hash or "").strip()
    if not supplied_diff_hash or supplied_diff_hash != computed_diff_hash:
        issues.append("diffHash must match the deterministic before/after file transition")

    before_surfaces = _surface_map(before)
    after_surfaces = _surface_map(after)
    removed_surfaces = [
        before_surfaces[surface_id]
        for surface_id in sorted(set(before_surfaces) - set(after_surfaces))
    ]
    added_surfaces = [
        after_surfaces[surface_id]
        for surface_id in sorted(set(after_surfaces) - set(before_surfaces))
    ]
    breaking_surfaces = [
        surface
        for surface in removed_surfaces
        if surface["type"] in BREAKING_SURFACE_TYPES
    ]
    coverage, coverage_errors = _coverage_issues(
        breaking_surfaces,
        migration_compatibility_contract,
    )
    issues.extend(coverage_errors)

    normalized_invariants, invariant_errors, runtime_required = _invariant_issues(
        invariants,
        before_hash=str(before.get("snapshotHash") or ""),
        after_hash=str(after.get("snapshotHash") or ""),
    )
    issues.extend(invariant_errors)
    issues.extend(
        _proof_issues(
            static_proof,
            label="staticProof",
            diff_hash=computed_diff_hash,
            changed_files=actual_changed_files,
        )
    )
    issues.extend(
        _proof_issues(
            build_proof,
            label="buildProof",
            diff_hash=computed_diff_hash,
            changed_files=actual_changed_files,
        )
    )
    if runtime_required:
        issues.extend(
            _proof_issues(
                runtime_proof,
                label="runtimeProof",
                diff_hash=computed_diff_hash,
                changed_files=actual_changed_files,
            )
        )

    return {
        "ok": not issues,
        "mode": "semantic_refactor_compare",
        "beforeSnapshot": before,
        "afterSnapshot": after,
        "changedFiles": actual_changed_files,
        "declaredChangedFiles": declared_files,
        "diffHash": computed_diff_hash,
        "suppliedDiffHash": supplied_diff_hash,
        "semanticDelta": {
            "removed": removed_surfaces,
            "added": added_surfaces,
            "breaking": breaking_surfaces,
            "coveredBreakingSurfaceIds": [
                item["surfaceId"] for item in coverage
            ],
        },
        "invariants": normalized_invariants,
        "runtimeProofRequired": runtime_required,
        "migrationCompatibilityCoverage": coverage,
        "issues": issues,
        "writeGate": {
            "writesAllowed": not issues,
            "exactChangedFileIdentity": declared_files == actual_changed_files,
            "exactDiffIdentity": supplied_diff_hash == computed_diff_hash,
            "semanticSurfacesPreservedOrCovered": not coverage_errors,
            "observerEvidencePaired": not invariant_errors,
            "proofsBoundToDiff": not any(
                issue.startswith(("staticProof", "buildProof", "runtimeProof"))
                for issue in issues
            ),
        },
        "proofBoundary": (
            "This guard proves deterministic file/surface identity, explicit observer equality, "
            "and proof binding for the isolated candidate. It is not equivalent to exhaustive "
            "behavioral, serialization, replication, asset, or production runtime proof."
        ),
    }
