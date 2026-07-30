#!/usr/bin/env python
"""Source-backed planning contract for code generation and existing-code edits.

This module is deliberately language/framework neutral.  It does not generate
or modify code.  Instead it tells a caller whether a draft is a generic example
or a project-specific proposal, which source surfaces must be read, and which
validation evidence is still required before presenting a patch as ready.

The contract complements (rather than replaces) Unreal API validation:
``code_sketch_claim_validate`` checks plausible engine names; this module checks
whether a code proposal is grounded in the target project at all.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

SOURCE_EXTENSIONS = {
    ".h", ".hpp", ".hh", ".inl", ".ipp", ".inc",
    ".cpp", ".c", ".cc", ".cxx", ".m", ".mm", ".cs",
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rs",
}
CHANGE_KINDS = {"new_file", "modify_existing", "single_file", "multifile"}
PROTECTED_SEGMENTS = {
    ".git", "binaries", "intermediate", "saved", "deriveddatacache",
    "node_modules", ".venv", "venv", "__pycache__", "dist", "build", "thirdparty",
}


def _hash_file(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


def _root_from_arg(project_root: str | Path | None) -> Path | None:
    if not project_root:
        return None
    candidate = Path(project_root).expanduser().resolve()
    if candidate.is_file() and candidate.suffix.lower() == ".uproject":
        return candidate.parent
    return candidate if candidate.is_dir() else None


def _relative_target(root: Path, raw_path: str) -> tuple[Path | None, str]:
    raw = str(raw_path or "").strip()
    if not raw:
        return None, "target path is empty"
    candidate = Path(raw)
    resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError:
        return None, f"target path escapes project root: {raw}"
    if any(part.lower() in PROTECTED_SEGMENTS for part in relative.parts):
        return None, f"target path is under a protected/generated directory: {raw}"
    return resolved, ""


def _paired_paths(path: Path, root: Path) -> list[Path]:
    suffix = path.suffix.lower()
    stem = path.stem
    candidates: list[Path] = []
    if suffix in {".h", ".hpp", ".hh"}:
        candidates.extend(path.with_suffix(ext) for ext in (".cpp", ".cc", ".cxx"))
        rel = path.relative_to(root).as_posix()
        if "/Public/" in f"/{rel}":
            private_rel = rel.replace("/Public/", "/Private/")
            candidates.extend((root / private_rel).with_suffix(ext) for ext in (".cpp", ".cc", ".cxx"))
    elif suffix in {".cpp", ".cc", ".cxx", ".c"}:
        candidates.extend(path.with_suffix(ext) for ext in (".h", ".hpp", ".hh"))
        rel = path.relative_to(root).as_posix()
        if "/Private/" in f"/{rel}":
            public_rel = rel.replace("/Private/", "/Public/")
            candidates.extend((root / public_rel).with_suffix(ext) for ext in (".h", ".hpp", ".hh"))
    return [candidate for candidate in candidates if candidate.is_file() and candidate.stem == stem]


def _known_symbols_in_file(graph: dict[str, Any] | None, path: Path) -> list[dict[str, Any]]:
    if not isinstance(graph, dict):
        return []
    target = str(path.resolve()).replace("\\", "/").lower()
    matches = []
    for row in graph.get("symbols") or []:
        if not isinstance(row, dict):
            continue
        row_path = str(row.get("file_path") or "").replace("\\", "/").lower()
        if row_path == target:
            matches.append(row)
    return matches


def build_generation_contract(
    request: str,
    *,
    project_root: str | Path | None = None,
    target_files: list[str] | None = None,
    change_kind: str = "modify_existing",
    validation_plan: list[str] | None = None,
    graph: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a deterministic pre-generation contract; this function never writes."""
    root = _root_from_arg(project_root)
    requested_kind = str(change_kind or "modify_existing").strip().lower()
    normalized_kind = requested_kind if requested_kind in CHANGE_KINDS else "modify_existing"
    target_values = [target_files] if isinstance(target_files, str) else (target_files or [])
    validation_values = [validation_plan] if isinstance(validation_plan, str) else (validation_plan or [])
    raw_targets = list(dict.fromkeys(str(item).strip() for item in target_values if str(item).strip()))
    validations = list(dict.fromkeys(str(item).strip() for item in validation_values if str(item).strip()))
    issues: list[str] = []
    warnings: list[str] = []
    targets: list[dict[str, Any]] = []
    required_reads: list[dict[str, Any]] = []
    invariants: list[str] = []
    if requested_kind not in CHANGE_KINDS:
        issues.append(
            f"unsupported changeKind: {requested_kind or '<empty>'}; "
            f"expected one of {', '.join(sorted(CHANGE_KINDS))}"
        )

    if not root:
        if raw_targets:
            issues.append("projectRoot is required to ground targetFiles in source evidence")
        return {
            "ok": not raw_targets and not issues,
            "mode": "generic_example" if not raw_targets and not issues else "blocked",
            "request": str(request or "").strip(),
            "changeKind": normalized_kind,
            "projectSpecific": False,
            "targets": [],
            "requiredReads": [],
            "invariants": ["Label the draft as a generic example; do not claim it matches any project API or architecture."],
            "validationRequired": ["compile/test in the receiving project before integration"],
            "issues": issues,
            "warnings": warnings,
            "writeGate": {"writesAllowed": False, "reason": "no project root/source target supplied"},
            "proofBoundary": "A generic code example is proposed guidance, not project-specific evidence.",
        }

    if not raw_targets:
        warnings.append("No targetFiles supplied: keep the result labeled as a generic draft, not a project patch.")
        return {
            "ok": not issues,
            "mode": "generic_example" if not issues else "blocked",
            "request": str(request or "").strip(),
            "changeKind": normalized_kind,
            "projectRoot": str(root),
            "projectSpecific": False,
            "targets": [],
            "requiredReads": [],
            "invariants": ["Do not claim project-specific symbol, API, owner, or architecture compatibility without target source evidence."],
            "validationRequired": ["select target files", "read target source", "compile/test in the receiving project"],
            "issues": issues,
            "warnings": warnings,
            "writeGate": {"writesAllowed": False, "reason": "target files are not specified"},
            "proofBoundary": "No target source was supplied; this is a proposed generic example only.",
        }

    resolved_targets: set[Path] = set()
    for raw_path in raw_targets:
        path, path_issue = _relative_target(root, raw_path)
        if not path:
            issues.append(path_issue)
            continue
        if path in resolved_targets:
            warnings.append(f"duplicate target resolves to the same path and was ignored: {raw_path}")
            continue
        resolved_targets.add(path)
        relative = path.relative_to(root).as_posix()
        exists = path.is_file()
        source_like = path.suffix.lower() in SOURCE_EXTENSIONS
        if normalized_kind in {"modify_existing", "single_file"} and not exists:
            issues.append(f"{relative}: target does not exist; use changeKind=new_file only when creation is intentional")
        if normalized_kind == "new_file" and exists:
            issues.append(f"{relative}: already exists; use changeKind=modify_existing and a patch workflow")
        if not source_like:
            issues.append(f"{relative}: not a recognized source file; code-generation writes are not allowed")

        target = {
            "path": relative,
            "absolutePath": str(path),
            "exists": exists,
            "sourceLike": source_like,
            "mode": "modify" if exists else "create",
            "knownSymbolCount": len(_known_symbols_in_file(graph, path)) if exists else 0,
        }
        if exists:
            try:
                digest = _hash_file(path)
            except OSError as exc:
                issues.append(f"{relative}: target could not be read for source evidence: {type(exc).__name__}: {exc}")
                targets.append(target)
                continue
            evidence = {
                "kind": "project_source",
                "location": f"{path}:1",
                "filePath": str(path),
                "lineStart": 1,
                "lineEnd": 1,
                "fileHash": digest,
                "observation": "Target file exists and must be read before a project-specific patch is proposed.",
            }
            target["sourceEvidence"] = evidence
            required_reads.append(evidence)
            known = _known_symbols_in_file(graph, path)
            if known:
                target["preserveSymbols"] = [
                    str(row.get("qualified_name") or row.get("symbol_name") or "")
                    for row in known[:16]
                    if str(row.get("qualified_name") or row.get("symbol_name") or "")
                ]
        pairs = _paired_paths(path, root) if exists and source_like else []
        if pairs:
            target["pairedSources"] = [pair.relative_to(root).as_posix() for pair in pairs]
            for pair in pairs:
                required_reads.append(
                    {
                        "kind": "project_source",
                        "location": f"{pair}:1",
                        "filePath": str(pair),
                        "lineStart": 1,
                        "lineEnd": 1,
                        "fileHash": _hash_file(pair),
                        "observation": "Header/implementation pair is an impacted declaration/definition surface.",
                    }
                )
            invariants.append(f"Keep declaration/definition compatible across {relative} and its paired source surface(s).")
        targets.append(target)

    if normalized_kind == "single_file" and len(targets) != 1:
        issues.append("changeKind=single_file requires exactly one resolved target")
    if normalized_kind == "new_file" and len(targets) != 1:
        issues.append("changeKind=new_file requires exactly one resolved target")
    if normalized_kind == "modify_existing" and len(targets) > 2:
        issues.append("changeKind=modify_existing supports at most two targets; use changeKind=multifile for a larger patch")
    if normalized_kind == "multifile" and len(targets) < 2:
        issues.append("changeKind=multifile requires at least two resolved targets")
    if targets:
        invariants.extend(
            [
                "Preserve existing public/reflected/serialized contracts unless an explicit migration plan is supplied.",
                "Do not present unverified framework/API names as compile-ready; run the API-specific validator where applicable.",
                "Do not claim runtime behavior from source presence alone; establish and validate the behavior path separately.",
            ]
        )

    required_validation = ["static validation", "build or language-appropriate compile", "targeted test/regression evidence"]
    for item in validations:
        if item not in required_validation:
            required_validation.append(item)
    required_reads = list({str(item["location"]): item for item in required_reads}.values())
    write_allowed = bool(targets) and not issues
    return {
        "ok": not issues,
        "mode": "project_specific" if targets else "blocked",
        "request": str(request or "").strip(),
        "changeKind": normalized_kind,
        "projectRoot": str(root),
        "projectSpecific": bool(targets),
        "targets": targets,
        "requiredReads": required_reads,
        "invariants": list(dict.fromkeys(invariants)),
        "validationRequired": required_validation,
        "issues": issues,
        "warnings": warnings,
        "writeGate": {
            "writesAllowed": write_allowed,
            "requiresReadBeforeWrite": True,
            "requiresValidationAfterWrite": True,
            "existingFilesRequirePatchWorkflow": any(target.get("exists") for target in targets),
            "maxTargetFiles": (
                1 if normalized_kind in {"single_file", "new_file"}
                else 2 if normalized_kind == "modify_existing"
                else 0
            ),
        },
        "proofBoundary": (
            "This contract establishes target/source scope only. It does not prove API correctness, "
            "build success, test success, data flow, state behavior, or runtime effects."
        ),
    }
