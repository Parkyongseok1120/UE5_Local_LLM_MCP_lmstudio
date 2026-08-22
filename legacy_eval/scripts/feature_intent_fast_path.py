"""Archived workflow-era feature-intent fast-path policy."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Any

from workspace_paths import ascii_windows_fold, filesystem_path_identity


_DISALLOWED_MARKERS = (
    "subsystem",
    "module",
    "plugin",
    "server",
    "client",
    "network",
    "multiplayer",
    "replicat",
    "rpc",
    "authority",
    "savegame",
    "save game",
    "persistence",
    "persistent",
    "migration",
    "schema",
    "config",
    "delete",
    "rename",
    "move file",
    "public api",
    "서브시스템",
    "모듈",
    "플러그인",
    "서버",
    "클라이언트",
    "네트워크",
    "멀티플레이",
    "복제",
    "권한",
    "저장",
    "로드",
    "영속",
    "마이그레이션",
    "스키마",
    "삭제",
    "이름 변경",
)

_ALLOWED_SUFFIXES = (".h", ".hpp", ".cpp", ".cc", ".cxx", ".inl")


def _test_filename_style(name: str) -> str:
    lowered = str(name or "").casefold()
    if lowered.endswith(".spec.cpp"):
        return "dot_spec"
    if lowered.endswith("tests.cpp"):
        return "plural_tests"
    if lowered.endswith("test.cpp"):
        return "singular_test"
    if lowered.endswith(".automation.cpp"):
        return "dot_automation"
    return ""


def _scope_owner(path: str, *, host_platform: str | None = None) -> str:
    parts = PurePosixPath(path.replace("\\", "/")).parts
    if not parts:
        return ""
    if ascii_windows_fold(parts[0]) == "source" and len(parts) >= 2:
        return filesystem_path_identity(
            "/".join(parts[:2]),
            host_platform=host_platform,
            trim_outer_slashes=True,
        )
    if ascii_windows_fold(parts[0]) == "plugins" and len(parts) >= 4:
        try:
            source_index = next(
                index
                for index, part in enumerate(parts)
                if ascii_windows_fold(part) == "source"
            )
        except StopIteration:
            return ""
        if source_index + 1 < len(parts):
            return filesystem_path_identity(
                "/".join(parts[: source_index + 2]),
                host_platform=host_platform,
                trim_outer_slashes=True,
            )
    return ""


def discover_project_test_convention(
    project_root: str | Path,
    target_file: str,
    *,
    host_platform: str | None = None,
) -> dict[str, Any]:
    """Prove a new test file follows an existing module-local convention."""

    root = Path(project_root).expanduser().resolve()
    target = (root / str(target_file or "")).resolve()
    try:
        relative = target.relative_to(root).as_posix()
    except ValueError:
        return {"conforms": False, "reason": "target escapes project root"}
    owner = _scope_owner(relative, host_platform=host_platform)
    target_style = _test_filename_style(target.name)
    if not owner or not target_style or not target.parent.is_dir():
        return {
            "conforms": False,
            "reason": "module owner, test filename style, and existing parent are required",
            "owner": owner,
            "targetStyle": target_style,
        }
    sibling_styles: dict[str, int] = {}
    sibling_examples: list[str] = []
    try:
        siblings = sorted(
            (item for item in target.parent.iterdir() if item.is_file()),
            key=lambda item: item.name.casefold(),
        )[:256]
    except OSError:
        siblings = []
    for sibling in siblings:
        style = _test_filename_style(sibling.name)
        if not style:
            continue
        sibling_styles[style] = sibling_styles.get(style, 0) + 1
        if len(sibling_examples) < 8:
            sibling_examples.append(sibling.name)
    matching_count = int(sibling_styles.get(target_style) or 0)
    return {
        "conforms": matching_count > 0,
        "reason": (
            "matches an existing module-local test filename style"
            if matching_count > 0
            else "no existing sibling proves this test filename style"
        ),
        "owner": owner,
        "directory": target.parent.relative_to(root).as_posix(),
        "targetStyle": target_style,
        "matchingSiblingCount": matching_count,
        "siblingStyleCounts": dict(sorted(sibling_styles.items())),
        "siblingExamples": sibling_examples,
    }


def evaluate_bounded_local_fast_path(
    request: str,
    *,
    target_files: list[str],
    target_snapshots: list[dict[str, Any]],
    host_platform: str | None = None,
) -> dict[str, Any]:
    """Return an auditable decision; defaults to ineligible on uncertainty."""

    reasons: list[str] = []
    text = re.sub(r"\s+", " ", str(request or "")).strip().casefold()
    requests_automation_test = "test" in text or "테스트" in text
    normalized_targets: list[str] = []
    seen_targets: set[str] = set()
    for raw_path in target_files:
        path = str(raw_path or "").strip().replace("\\", "/")
        identity = filesystem_path_identity(
            path,
            host_platform=host_platform,
            trim_outer_slashes=True,
        )
        if not path or identity in seen_targets:
            continue
        seen_targets.add(identity)
        normalized_targets.append(path)
    if not 1 <= len(normalized_targets) <= 2:
        reasons.append("selected slice must contain one or two exact files")
    snapshot_by_path = {
        filesystem_path_identity(
            item.get("path"),
            host_platform=host_platform,
            trim_outer_slashes=True,
        ): item
        for item in target_snapshots
        if isinstance(item, dict)
    }
    new_automation_tests: list[str] = []
    for path in normalized_targets:
        lowered = path.casefold()
        path_identity = filesystem_path_identity(
            path,
            host_platform=host_platform,
            trim_outer_slashes=True,
        )
        snapshot = snapshot_by_path.get(path_identity)
        if not lowered.endswith(_ALLOWED_SUFFIXES):
            reasons.append(f"{path}: only existing C++ source files are eligible")
        if lowered.endswith((".build.cs", ".target.cs")):
            reasons.append(f"{path}: build/module policy files require explicit intent")
        convention = (
            snapshot.get("projectConventionEvidence")
            if isinstance(snapshot, dict)
            and isinstance(snapshot.get("projectConventionEvidence"), dict)
            else {}
        )
        is_new_automation_test = bool(
            snapshot
            and snapshot.get("exists") is False
            and snapshot.get("parentExists") is True
            and lowered.endswith(".cpp")
            and convention.get("conforms") is True
            and filesystem_path_identity(
                convention.get("owner"),
                host_platform=host_platform,
                trim_outer_slashes=True,
            )
            == _scope_owner(path, host_platform=host_platform)
            and requests_automation_test
        )
        if is_new_automation_test:
            new_automation_tests.append(path)
        elif not snapshot or snapshot.get("exists") is not True:
            reasons.append(f"{path}: fast path cannot create a new target")
    if len(new_automation_tests) > 1:
        reasons.append("fast path can add at most one bounded automation test source")
    owners = {
        _scope_owner(path, host_platform=host_platform)
        for path in normalized_targets
    }
    if "" in owners or len(owners) != 1:
        reasons.append("targets must stay inside one existing Source module owner")

    marker_hits = [marker for marker in _DISALLOWED_MARKERS if marker in text]
    if marker_hits:
        reasons.append(
            "request changes architecture/authority/persistence scope: "
            + ", ".join(marker_hits[:4])
        )
    if re.search(r"\b(?:create|new)\s+(?:class|file|component|actor)\b", text):
        reasons.append("new type/file ownership requires explicit intent selection")
    if any(marker in text for marker in ("whole project", "entire project", "across modules", "프로젝트 전체")):
        reasons.append("project-wide scope is not bounded local work")

    eligible = not reasons
    return {
        "eligible": eligible,
        "policy": "bounded_local_existing_owner_v2",
        "selectedIntentId": "bounded_local" if eligible else "",
        "reasons": reasons,
        "targetFiles": normalized_targets,
        "owner": next(iter(owners), "") if len(owners) == 1 else "",
        "newAutomationTestFiles": new_automation_tests,
        "projectConventionEvidence": {
            path: dict(
                (
                    snapshot_by_path.get(
                        filesystem_path_identity(
                            path,
                            host_platform=host_platform,
                            trim_outer_slashes=True,
                        )
                    )
                    or {}
                ).get("projectConventionEvidence")
                or {}
            )
            for path in new_automation_tests
        },
        "serverOwnedPhases": [
            "SelectIntent",
            "ResolveSlice",
            "CaptureSnapshot",
            "BindIntent",
        ],
    }


def bounded_local_question_answers() -> dict[str, str]:
    """Explicit defaults guaranteed by the strict fast-path eligibility policy."""

    return {
        "ownershipLifetime": "Preserve the existing selected source owner and its lifetime.",
        "authorityReplication": "Preserve current authority; introduce no replication, RPC, or network policy.",
        "persistence": "Introduce no save, config, schema, or migration behavior.",
        "failureSemantics": "Fail closed and preserve the prior observable behavior outside the requested case.",
        "userVisibleBehavior": "Change only the behavior explicitly named in the bounded request.",
        "nonGoals": "No new subsystem, module, plugin, global state, persistence, or replication.",
    }
