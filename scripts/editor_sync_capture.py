#!/usr/bin/env python
"""Own the lock, export runner, and validated snapshot lifecycle for Editor sync."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from direct_rag_editor_snapshot import (
    create_editor_export_snapshot,
    discard_editor_export_snapshot,
)
from direct_rag_refresh_lock import DirectRagRefreshBusyError, index_refresh_lock
from editor_export_contract import EditorExportContractError
from editor_sync_context import EditorSyncContext


@dataclass
class EditorSyncCapture:
    context: EditorSyncContext
    export_dir: Path
    snapshot: Path | None = None
    export_result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None

    @classmethod
    def for_context(cls, context: EditorSyncContext) -> EditorSyncCapture:
        return cls(context=context, export_dir=context.export_dir)

    def _capture_locked(self, *, expected_scope: str = "") -> None:
        self.snapshot = create_editor_export_snapshot(
            self.export_dir,
            self.context.index_dir.parent,
            self.context.project_file,
            expected_scope=expected_scope,
        )

    def run_export(
        self,
        *,
        launch_authorized: bool,
        content_path: str | None,
        export_scope: str | None,
        export_mode: str,
    ) -> dict[str, Any]:
        if not launch_authorized:
            self.export_result = {
                "ok": False,
                "errorCode": "EDITOR_LAUNCH_NOT_AUTHORIZED",
                "error": "Unreal Editor launch requires explicit caller authorization.",
            }
            return self.export_result
        from editor_export_runner import run_editor_export

        try:
            with index_refresh_lock(self.export_dir):
                result = run_editor_export(
                    export_dir=self.export_dir,
                    content_path=content_path,
                    scope=export_scope,  # type: ignore[arg-type]
                    mode=export_mode,  # type: ignore[arg-type]
                    uproject=self.context.project_file,
                )
                if result.get("ok") is True:
                    actual_export = Path(
                        str(result.get("exportDir") or self.export_dir)
                    ).expanduser().resolve()
                    if actual_export != self.export_dir.resolve():
                        raise EditorExportContractError(
                            "Editor export runner changed the exact project export directory"
                        )
                    self.export_dir = actual_export
                    self._capture_locked(expected_scope=str(export_scope or ""))
                self.export_result = result
        except DirectRagRefreshBusyError as exc:
            self.export_result = {
                "ok": False,
                "errorCode": "EDITOR_EXPORT_BUSY",
                "error": str(exc),
            }
        except EditorExportContractError as exc:
            self.export_result = {
                "ok": False,
                "errorCode": "EDITOR_EXPORT_CONTRACT_INVALID",
                "error": str(exc),
            }
        except Exception as exc:
            self.export_result = {
                "ok": False,
                "errorCode": "EDITOR_EXPORT_SNAPSHOT_FAILED",
                "error": str(exc),
            }
        return self.export_result

    def ensure_snapshot(self, *, expected_scope: str = "") -> Path | None:
        if self.snapshot is not None:
            return self.snapshot
        try:
            with index_refresh_lock(self.export_dir):
                self._capture_locked(expected_scope=expected_scope)
        except DirectRagRefreshBusyError as exc:
            self.error = {
                "errorCode": "EDITOR_EXPORT_BUSY",
                "error": str(exc),
            }
        except EditorExportContractError as exc:
            self.error = {
                "errorCode": "EDITOR_EXPORT_CONTRACT_INVALID",
                "error": str(exc),
            }
        except Exception as exc:
            self.error = {
                "errorCode": "EDITOR_EXPORT_SNAPSHOT_FAILED",
                "error": str(exc),
            }
        return self.snapshot

    def close(self) -> None:
        discard_editor_export_snapshot(self.snapshot)
        self.snapshot = None

    def __enter__(self) -> EditorSyncCapture:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


__all__ = ["EditorSyncCapture"]
