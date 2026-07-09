#!/usr/bin/env python3
"""Snapshot / apply / validate / rollback orchestration for static autofix steps."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from unreal_static_validate import (
    Finding,
    can_run_autofix_ubt,
    format_findings,
    iter_source_files,
    read_text,
    validate_unreal_readiness,
)

AutofixApplyFn = Callable[[Path, list[Finding]], list[Path]]
PostValidateFn = Callable[[Path, list[Finding]], bool]


DRIFT_CODES_AFTER_AUTOFIX = frozenset(
    {
        "CPP_RETURN_TYPE_MISMATCH",
        "CALLBACK_FUNCTION_POINTER_MISMATCH",
        "INTERFACE_IMPLEMENTER_SIGNATURE_MISMATCH",
        "CPP_FUNCTION_SIGNATURE_MISMATCH",
        "DELEGATE_BROADCAST_SIGNATURE_MISMATCH",
        "BLUEPRINT_NATIVE_EVENT_IMPL_MISSING",
        "BLUEPRINT_NATIVE_EVENT_MANUAL_IMPL_DECL",
        "BLUEPRINT_IMPLEMENTABLE_EVENT_INVALID_IMPL",
        "CPP_DEFINITION_MISSING",
    }
)


@dataclass
class AutofixStep:
    name: str
    apply: AutofixApplyFn
    finding_codes: set[str] | None = None
    modes: set[str] | None = None
    post_validate: PostValidateFn | None = None


@dataclass
class AutofixResult:
    written: list[Path] = field(default_factory=list)
    step_names: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    restored: bool = False
    static_report: str = ""


def snapshot_paths(paths: list[Path]) -> dict[Path, str]:
    return {path.resolve(): read_text(path) for path in paths if path.is_file()}


def restore_paths(snapshot: dict[Path, str]) -> None:
    for path, content in snapshot.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")


def snapshot_source_tree(root: Path) -> dict[Path, str]:
    """Snapshot every source file currently on disk, keyed by resolved path.

    Must be called *before* any writes for the scope being protected, so the
    captured content reflects true pre-change state and can be used to
    genuinely restore it later.
    """

    return {path.resolve(): read_text(path) for path in iter_source_files(root)}


def restore_source_tree(root: Path, snapshot: dict[Path, str]) -> None:
    """Restore a previously captured snapshot, deleting any files that were
    created after the snapshot was taken (defensive; current autofix steps
    only ever modify pre-existing files)."""

    restore_paths(snapshot)
    for path in iter_source_files(root):
        resolved = path.resolve()
        if resolved not in snapshot:
            try:
                path.unlink()
            except OSError:
                pass


def describe_applied_steps(step_names: list[str]) -> str:
    if not step_names:
        return "Applied safe static autofix."
    return "Applied static autofix: " + ", ".join(step_names)


def step_should_run(step: AutofixStep, findings: list[Finding], mode: str) -> bool:
    if step.modes is not None and str(mode or "") not in step.modes:
        return False
    if not step.finding_codes:
        return bool(findings)
    present = {str(finding.code) for finding in findings}
    return bool(present & step.finding_codes)


def has_unresolved_drift(findings: list[Finding]) -> bool:
    return any(str(finding.code) in DRIFT_CODES_AFTER_AUTOFIX for finding in findings)


def run_autofix_pipeline(
    root: Path,
    findings: list[Finding],
    mode: str,
    steps: list[AutofixStep],
    *,
    module_graph: Path | None = None,
) -> AutofixResult:
    initial_snapshot = snapshot_source_tree(root)
    written: list[Path] = []
    step_names: list[str] = []

    for step in steps:
        if not step_should_run(step, findings, mode):
            continue
        # Snapshot the *entire* tree immediately before this step runs, so a
        # failed step (exception or post_validate rejection) can be reverted
        # to the exact state left behind by previously committed steps.
        pre_step_snapshot = snapshot_source_tree(root)
        try:
            step_written = step.apply(root, findings)
        except Exception:
            restore_source_tree(root, pre_step_snapshot)
            raise
        if not step_written:
            continue
        after_findings = validate_unreal_readiness(root, module_graph)
        if step.post_validate and not step.post_validate(root, after_findings):
            restore_source_tree(root, pre_step_snapshot)
            continue
        written.extend(step_written)
        step_names.append(step.name)
        findings = after_findings

    final_findings = validate_unreal_readiness(root, module_graph)
    if written and has_unresolved_drift(final_findings):
        restore_source_tree(root, initial_snapshot)
        restored_findings = validate_unreal_readiness(root, module_graph)
        return AutofixResult(
            written=[],
            step_names=[],
            findings=restored_findings,
            restored=True,
            static_report=format_findings(restored_findings),
        )

    return AutofixResult(
        written=written,
        step_names=step_names,
        findings=final_findings,
        static_report=format_findings(final_findings),
    )


def autofix_ubt_allowed(result: AutofixResult) -> bool:
    return can_run_autofix_ubt(result.findings, autofix_written=bool(result.written)) and not has_unresolved_drift(
        result.findings
    )
