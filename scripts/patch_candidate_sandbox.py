#!/usr/bin/env python
"""Materialize unified-diff candidates in isolated project copies."""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

EXCLUDED_DIRS = frozenset(
    {".git", "Binaries", "DerivedDataCache", "Intermediate", "Saved"}
)
PATCH_PATH_RE = re.compile(r"^\+\+\+\s+b/(.+)$", re.MULTILINE)
CANDIDATE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _copy_project(source: Path, target: Path) -> None:
    shutil.copytree(
        source,
        target,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(*EXCLUDED_DIRS),
    )


def materialize_patch_candidate_sandboxes(
    *,
    project_root: str | Path,
    candidates: list[dict[str, Any]],
    sandbox_root: str | Path,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    source = Path(project_root).expanduser().resolve()
    destination = Path(sandbox_root).expanduser().resolve()
    issues: list[str] = []
    if not source.is_dir():
        return {"ok": False, "issues": ["project_root does not exist"]}
    if destination == source or _is_within(destination, source):
        return {
            "ok": False,
            "issues": ["sandbox_root must be outside project_root"],
        }
    if not 2 <= len(candidates) <= 4:
        return {"ok": False, "issues": ["two to four patch candidates are required"]}
    destination.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates):
        candidate_id = str(candidate.get("id") or f"candidate-{index + 1}").strip()
        patch = str(candidate.get("patch") or "")
        patch_hash = hashlib.sha256(patch.encode("utf-8")).hexdigest()
        changed_files = list(dict.fromkeys(PATCH_PATH_RE.findall(patch)))
        candidate_issues: list[str] = []
        valid_candidate_id = CANDIDATE_ID_RE.fullmatch(candidate_id) is not None
        if not valid_candidate_id:
            candidate_issues.append(
                "candidate id must match [A-Za-z0-9_.-]{1,64}"
            )
        if not patch.strip() or not changed_files:
            candidate_issues.append("candidate patch must be a unified diff with b/ paths")
        isolated_root = Path(
            tempfile.mkdtemp(
                prefix=(
                    f"unreal-{candidate_id}-"
                    if valid_candidate_id
                    else f"unreal-candidate-{index + 1}-"
                ),
                dir=destination,
            )
        )
        if not candidate_issues:
            try:
                _copy_project(source, isolated_root)
                checked = runner(
                    ["git", "apply", "--check", "--whitespace=nowarn", "-"],
                    cwd=str(isolated_root),
                    input=patch,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if int(checked.returncode) != 0:
                    candidate_issues.append(
                        "git apply --check failed: "
                        + str(checked.stderr or checked.stdout or "")[-1000:]
                    )
                else:
                    applied = runner(
                        ["git", "apply", "--whitespace=nowarn", "-"],
                        cwd=str(isolated_root),
                        input=patch,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    if int(applied.returncode) != 0:
                        candidate_issues.append(
                            "git apply failed: "
                            + str(applied.stderr or applied.stdout or "")[-1000:]
                        )
            except OSError as exc:
                candidate_issues.append(f"sandbox materialization failed: {exc}")
        results.append(
            {
                "id": candidate_id,
                "changedFiles": changed_files,
                "diffHash": patch_hash,
                "sandboxEvidence": {
                    "isolatedRoot": str(isolated_root),
                    "staticPassed": not candidate_issues,
                    "staticProof": {
                        "ok": not candidate_issues,
                        "artifactHash": patch_hash if not candidate_issues else "",
                        "reportPath": "",
                    },
                    "buildPassed": False,
                    "buildProof": {},
                    "runtimeCompatible": candidate.get("runtimeCompatible") is True,
                    "invariantResults": dict(candidate.get("invariantResults") or {}),
                    "materializationIssues": candidate_issues,
                },
            }
        )
        issues.extend(f"{candidate_id}: {issue}" for issue in candidate_issues)
    return {
        "ok": not issues,
        "candidates": results,
        "issues": issues,
        "nextAction": (
            "run identical static/build checks in each isolatedRoot, then compare evidence"
        ),
        "proofBoundary": (
            "Sandboxes contain applied source patches but exclude generated directories. "
            "They are not build-verified until an Unreal build runs in each isolatedRoot."
        ),
    }
