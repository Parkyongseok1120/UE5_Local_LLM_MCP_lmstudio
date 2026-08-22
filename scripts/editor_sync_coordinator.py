#!/usr/bin/env python
"""Coordinate exact-project Editor export decisions and transactional ingest."""

from __future__ import annotations

from typing import Any

from direct_rag_editor_stage import transactional_editor_ingest
from editor_metadata_status import editor_metadata_status
from editor_sync_capture import EditorSyncCapture
from editor_sync_context import EditorSyncContext
from editor_sync_decision import (
    export_dir_summary,
    exports_newer_than_raw,
    needs_export_or_sync,
    raw_newest_mtime,
)


def _ingest_reason(
    context: EditorSyncContext,
    summary: dict[str, Any],
    status: dict[str, Any],
    raw_mtime: float | None,
    export_result: dict[str, Any] | None,
    *,
    force: bool,
) -> str:
    if force:
        return "forced"
    if not summary.get("files"):
        return ""
    if raw_mtime is None:
        return "no_raw_metadata"
    if exports_newer_than_raw(context.index_dir, summary, context.project_file):
        return "export_dir_newer_than_index"
    if status.get("needsEditorExport"):
        return "metadata_status_needs_export_or_ingest"
    if export_result and export_result.get("ok") is True:
        return "fresh_export"
    return ""


def _summary_error(summary: dict[str, Any]) -> dict[str, Any] | None:
    if not summary.get("errorCode"):
        return None
    return {
        "errorCode": str(summary["errorCode"]),
        "error": str(summary.get("error") or "Invalid Editor export completion manifest."),
    }


def sync_editor_context(
    context: EditorSyncContext,
    *,
    rebuild_index: bool,
    force_ingest: bool,
    auto_export: bool,
    force_export: bool,
    content_path: str | None,
    export_scope: str | None,
    export_mode: str,
) -> dict[str, Any]:
    status_before = editor_metadata_status(context.index_dir, context.project_file, 24.0)
    summary = export_dir_summary(context.export_dir, context.project_file)
    raw_mtime = raw_newest_mtime(context.index_dir, project_root=context.project_file)
    needs_work = needs_export_or_sync(
        status_before,
        summary,
        raw_mtime,
        force=force_ingest,
    )
    ingest: dict[str, Any] | None = None
    rebuild: dict[str, Any] | None = None
    committed: bool | None = None
    transaction_error: dict[str, Any] | None = None

    with EditorSyncCapture.for_context(context) as capture:
        if auto_export and (needs_work or force_export):
            capture.run_export(
                launch_authorized=True,
                content_path=content_path,
                export_scope=export_scope,
                export_mode=export_mode,
            )
            summary = export_dir_summary(capture.export_dir, context.project_file)
        status_current = editor_metadata_status(context.index_dir, context.project_file, 24.0)
        reason = _ingest_reason(
            context,
            summary,
            status_current,
            raw_mtime,
            capture.export_result,
            force=force_ingest,
        )
        if capture.export_result is not None and capture.export_result.get("ok") is not True:
            reason = ""
        transaction_error = _summary_error(summary)
        if reason and summary.get("files") and transaction_error is None:
            snapshot = capture.ensure_snapshot(expected_scope=str(export_scope or ""))
            if snapshot is None:
                transaction_error = capture.error
            else:
                transaction = transactional_editor_ingest(
                    workspace=context.workspace,
                    index_dir=context.index_dir,
                    export_dir=snapshot,
                    project=context.project_file,
                    rebuild_index=rebuild_index,
                    reason=reason,
                )
                ingest = transaction.get("ingest")
                rebuild = transaction.get("rebuild")
                committed = transaction.get("stageCommitted") is True
                if transaction.get("ok") is not True:
                    transaction_error = {
                        key: transaction.get(key)
                        for key in ("errorCode", "error")
                        if transaction.get(key) is not None
                    }

    status_after = editor_metadata_status(context.index_dir, context.project_file, 24.0)
    ok = bool(
        (capture.export_result is None or capture.export_result.get("ok"))
        and ((ingest and ingest.get("ok")) or (not reason and not status_after.get("needsEditorExport")))
        and (rebuild is None or rebuild.get("ok"))
        and transaction_error is None
    )
    return {
        "ok": ok,
        **context.response_identity(),
        "exportDir": summary,
        "exportResult": capture.export_result,
        "ingestReason": reason or None,
        "ingest": ingest,
        "rebuild": rebuild,
        "stageCommitted": committed,
        "transactionError": transaction_error,
        "metadataStatusBefore": status_before,
        "metadataStatusAfter": status_after,
    }


__all__ = ["sync_editor_context"]
