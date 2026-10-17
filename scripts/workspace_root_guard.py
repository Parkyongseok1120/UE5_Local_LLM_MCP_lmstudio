"""Checks for the WORKSPACE_ROOT the installer writes into MCP configuration.

Shared by ``install.py`` (before writing) and ``scripts/rag_doctor.py`` (on an
existing install) so both report the same findings. A finding is observed from
the file system only; nothing here changes configuration.

- ``foreign_harness_checkout``: the first workspace root is another copy of this
  harness (for example a baseline git worktree), while the installed servers
  run from a different checkout. Tools then report paths from the wrong tree.
- ``active_project_outside_workspace``: the active project is not under the
  workspace root. That is supported (``project://`` still reaches it), but
  workspace-relative browsing does not, so it is reported as a notice.
"""
from __future__ import annotations

from pathlib import Path

HARNESS_MARKERS = ("install.py", "lmstudio-unreal-agent-mcp")


def _resolved(path: Path) -> Path:
    try:
        return path.expanduser().resolve()
    except OSError:
        return path.expanduser().absolute()


def _is_within(path: Path, root: Path) -> bool:
    try:
        _resolved(path).relative_to(_resolved(root))
        return True
    except ValueError:
        return False


def is_harness_checkout(root: Path) -> bool:
    return all((root / marker).exists() for marker in HARNESS_MARKERS)


def workspace_root_findings(workspace_root: Path | None, active_project: Path | None,
                            harness_root: Path) -> list[dict[str, str]]:
    if workspace_root is None:
        return []
    findings: list[dict[str, str]] = []
    root = _resolved(workspace_root)
    if is_harness_checkout(root) and root != _resolved(harness_root):
        kind = "linked git worktree" if (root / ".git").is_file() else "copy"
        findings.append({
            "code": "foreign_harness_checkout", "severity": "block",
            "message": (f"WORKSPACE_ROOT {root} is another harness {kind}; the installed servers run from "
                        f"{_resolved(harness_root)}. Choose the project folder or this checkout as the first "
                        "--workspace-root."),
        })
    if active_project is not None and not _is_within(active_project, root):
        findings.append({
            "code": "active_project_outside_workspace", "severity": "notice",
            "message": (f"active project {_resolved(active_project)} is outside WORKSPACE_ROOT {root}; "
                        "project:// URIs still reach it, workspace-relative paths do not."),
        })
    return findings
