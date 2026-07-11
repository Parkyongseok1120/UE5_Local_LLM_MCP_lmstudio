#!/usr/bin/env python
"""Health checks for Unreal58-RAG stack (Phase A doctor)."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from rag_index_ops import index_health
from workspace_paths import (
    canonical_workspace_root,
    find_workspace_root,
    load_shared_config,
    resolve_engine_root,
    resolve_engine_source_root,
    resolve_engine_version,
    resolve_index_namespace,
    resolve_index_path,
    resolve_ubt_path,
)


def configure_stdio_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def resolve_python() -> Path | None:
    bundled = (
        Path.home()
        / ".cache"
        / "codex-runtimes"
        / "codex-primary-runtime"
        / "dependencies"
        / "python"
        / "python.exe"
    )
    if bundled.is_file():
        return bundled

    local_roots = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python",
    ]
    for root in local_roots:
        if not root.is_dir():
            continue
        for child in sorted(root.glob("Python*/python.exe"), reverse=True):
            if child.is_file():
                return child

    import shutil

    found = shutil.which("python")
    if found and "WindowsApps" not in found.replace("/", "\\"):
        return Path(found)
    return None


def resolve_node() -> Path | None:
    candidates = [
        Path(r"C:\Program Files\nodejs\node.exe"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "nodejs" / "node.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    import shutil

    found = shutil.which("node")
    return Path(found) if found else None


def run_version(exe: Path) -> str | None:
    try:
        proc = subprocess.run(
            [str(exe), "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except OSError:
        return None
    out = (proc.stdout or proc.stderr or "").strip()
    return out or None


def engine_version_from_root(engine_root: Path) -> str | None:
    folder = engine_root.name
    if folder.upper().startswith("UE_"):
        return folder[3:].replace("_", ".")
    return None


def read_project_engine_association(project_path: Path) -> str | None:
    if not project_path.is_file():
        return None
    try:
        data = json.loads(project_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    association = data.get("EngineAssociation")
    return str(association).strip() if association else None


def normalize_engine_version(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    match = re.search(r"(\d+(?:\.\d+)*)", text)
    return match.group(1) if match else text


def check(label: str, ok: bool, detail: str = "") -> dict:
    status = "PASS" if ok else "FAIL"
    entry = {"label": label, "status": status, "detail": detail}
    prefix = "[PASS]" if ok else "[FAIL]"
    line = f"{prefix} {label}"
    if detail:
        line += f" - {detail}"
    print(line)
    return entry


def warn(label: str, detail: str = "") -> dict:
    entry = {"label": label, "status": "WARN", "detail": detail}
    line = f"[WARN] {label}"
    if detail:
        line += f" - {detail}"
    print(line)
    return entry


def main() -> int:
    configure_stdio_utf8()

    parser = argparse.ArgumentParser(description="Unreal58-RAG doctor")
    parser.add_argument("--rag-root", type=Path, default=Path.cwd())
    args = parser.parse_args()

    rag_root = args.rag_root.resolve()
    if (rag_root / "config" / "workspace.json").exists():
        workspace_root = canonical_workspace_root(rag_root)
    else:
        workspace_root = find_workspace_root(rag_root)

    index_path = resolve_index_path(workspace_root)
    configured_engine_root = resolve_engine_root(workspace_root)
    configured_ubt = resolve_ubt_path(workspace_root)
    configured_source = resolve_engine_source_root(workspace_root)
    configured_engine_version = resolve_engine_version(workspace_root)
    index_namespace = resolve_index_namespace(workspace_root)

    shared_config = Path.home() / ".lmstudio" / "config" / "unreal-workspace.json"
    mcp_config = Path.home() / ".lmstudio" / "mcp.json"

    checks: list[dict] = []
    fail_count = 0

    python = resolve_python()
    if python:
        version = run_version(python)
        ok = bool(version and "Python" in version)
        checks.append(check("python", ok, f"{python} ({version or 'no version'})"))
    else:
        checks.append(check("python", False, "not found (WindowsApps stub excluded)"))

    node = resolve_node()
    if node:
        version = run_version(node)
        ok = bool(version)
        checks.append(check("node", ok, f"{node} ({version or 'no version'})"))
    else:
        checks.append(check("node", False, "not found"))

    checks.append(
        check("configured_engine_root", configured_engine_root.is_dir(), str(configured_engine_root))
    )
    checks.append(check("configured_ubt", configured_ubt.is_file(), str(configured_ubt)))
    checks.append(
        check("configured_source", configured_source.is_dir(), str(configured_source))
    )

    health = index_health(index_path)
    chunk_count = int(health.get("chunkCount") or 0)
    checks.append(
        check(
            "rag_index",
            index_path.is_file() and chunk_count > 0,
            f"{index_path} ({chunk_count} chunks, namespace={index_namespace})",
        )
    )

    include_owner_count = 0
    if index_path.is_file():
        try:
            import sqlite3

            conn = sqlite3.connect(index_path)
            tables = {
                str(row[0])
                for row in conn.execute("select name from sqlite_master where type='table'")
            }
            if "include_owners" in tables:
                include_owner_count = int(
                    conn.execute("select count(*) from include_owners").fetchone()[0]
                )

            # Verify that expected search indexes exist. Missing indexes degrade
            # sidecar query performance silently (rebuild index to add them).
            EXPECTED_INDEXES = {
                "chunks_source_idx",
                "chunks_source_title_idx",
                "chunks_title_idx",
                "chunks_symbol_name_idx",
            }
            existing_indexes = {
                str(row[0])
                for row in conn.execute("select name from sqlite_master where type='index'")
            }
            missing_indexes = EXPECTED_INDEXES - existing_indexes
            if missing_indexes:
                checks.append(
                    warn(
                        "rag_index_indexes",
                        f"missing indexes {sorted(missing_indexes)} — rebuild index: .\\rag.ps1 build",
                    )
                )
            else:
                checks.append(check("rag_index_indexes", True, f"{len(existing_indexes)} indexes present"))

            conn.close()
        except sqlite3.Error:
            include_owner_count = -1
    if include_owner_count == 0:
        checks.append(
            warn(
                "include_owners_sidecar",
                "0 rows — run: .\\rag.ps1 collect-module-graph; .\\rag.ps1 build-incremental",
            )
        )
    elif include_owner_count > 0:
        checks.append(
            check(
                "include_owners_sidecar",
                True,
                f"{include_owner_count} include_owner rows in {index_path.name}",
            )
        )

    manifest_path = index_path.parent / "build_manifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest_version = normalize_engine_version(str(manifest.get("engineVersion") or ""))
            configured_version = normalize_engine_version(configured_engine_version)
            version_ok = not manifest_version or manifest_version == configured_version
            checks.append(
                check(
                    "index_engine_version",
                    version_ok,
                    f"manifest={manifest_version or 'unknown'} workspace={configured_version}",
                )
            )
        except (OSError, json.JSONDecodeError) as exc:
            checks.append(check("index_engine_version", False, str(exc)))
    else:
        checks.append(
            check(
                "index_engine_version",
                False,
                f"build_manifest.json missing under {index_path.parent}",
            )
        )

    shared = load_shared_config()
    active_project = str(shared.get("activeProject") or "").strip()
    if active_project:
        project_path = Path(active_project)
        association = read_project_engine_association(project_path)
        configured_from_root = engine_version_from_root(configured_engine_root)
        if association and configured_from_root:
            assoc_norm = normalize_engine_version(association)
            root_norm = normalize_engine_version(configured_from_root)
            if assoc_norm != root_norm:
                checks.append(
                    warn(
                        "active_project_engine_mismatch",
                        (
                            f"{project_path.name} EngineAssociation={association} "
                            f"but configured engine is {configured_from_root} "
                            f"(index namespace {index_namespace}). "
                            "Switch index namespace or re-collect for this UE version."
                        ),
                    )
                )
            else:
                checks.append(
                    check(
                        "active_project_engine_mismatch",
                        True,
                        f"{project_path.name} EngineAssociation={association} matches configured engine",
                    )
                )
        elif association:
            checks.append(
                check(
                    "active_project_engine_mismatch",
                    True,
                    f"{project_path.name} EngineAssociation={association}",
                )
            )
    else:
        checks.append(check("active_project_engine_mismatch", True, "no activeProject configured"))

    shared_error = str(shared.get("_configError") or "")
    checks.append(
        check(
            "shared_config",
            shared_config.is_file() and not shared_error,
            shared_error or str(shared_config),
        )
    )
    checks.append(check("mcp_json", mcp_config.is_file(), str(mcp_config)))

    mcp_python_ok = False
    mcp_python_detail = "mcp.json missing"
    if mcp_config.is_file():
        try:
            mcp = json.loads(mcp_config.read_text(encoding="utf-8-sig"))
            rag_entry = (mcp.get("mcpServers") or {}).get("unreal-rag") or {}
            cmd = str(rag_entry.get("command") or "")
            if "WindowsApps" in cmd.replace("/", "\\"):
                mcp_python_detail = f"WindowsApps stub: {cmd}"
            elif cmd and Path(cmd).is_file():
                version = run_version(Path(cmd))
                mcp_python_ok = bool(version and "Python" in version)
                mcp_python_detail = f"{cmd} ({version or 'bad'})"
            else:
                mcp_python_detail = f"invalid command: {cmd or '(empty)'}"
        except (OSError, json.JSONDecodeError) as exc:
            mcp_python_detail = str(exc)
    checks.append(check("mcp_unreal_rag_python", mcp_python_ok, mcp_python_detail))

    agent_ok = False
    agent_detail = "mcp.json missing"
    if mcp_config.is_file():
        try:
            mcp = json.loads(mcp_config.read_text(encoding="utf-8-sig"))
            agent_entry = (mcp.get("mcpServers") or {}).get("unreal-agent") or {}
            cmd = str(agent_entry.get("command") or "")
            args_list = agent_entry.get("args") or []
            server_js = args_list[0] if args_list else ""
            agent_ok = bool(cmd and Path(cmd).is_file() and server_js and Path(str(server_js)).is_file())
            agent_detail = f"command={cmd}, server={server_js}"
        except (OSError, json.JSONDecodeError) as exc:
            agent_detail = str(exc)
    checks.append(check("mcp_unreal_agent", agent_ok, agent_detail))

    cline_ok = False
    cline_detail = "not configured"
    cline_paths = [
        Path.home() / ".cline" / "data" / "settings" / "cline_mcp_settings.json",
        Path(os.environ.get("APPDATA", ""))
        / "Code"
        / "User"
        / "globalStorage"
        / "saoudrizwan.claude-dev"
        / "settings"
        / "cline_mcp_settings.json",
    ]
    placeholder_tokens = (
        "{PYTHON_EXE}",
        "{REPO_ROOT}",
        "{NODE_EXE}",
        "{AGENT_MCP_ROOT}",
        "{LMSTUDIO_HOME}",
        "{USER_DOCUMENTS}",
    )
    for cline_path in cline_paths:
        if not cline_path.is_file():
            continue
        try:
            raw = cline_path.read_text(encoding="utf-8-sig")
            if any(token in raw for token in placeholder_tokens):
                checks.append(
                    check(
                        "cline_placeholders",
                        False,
                        f"{cline_path} has unresolved install placeholders",
                    )
                )
                fail_count += 1
                continue
            cline = json.loads(raw)
            servers = cline.get("mcpServers") or {}
            if servers.get("unreal-rag") and servers.get("unreal-agent"):
                cline_ok = True
                cline_detail = str(cline_path)
                rag_args = servers.get("unreal-rag", {}).get("args") or []
                rag_index = " ".join(str(a) for a in rag_args)
                expected_index = str(index_path).replace("/", "\\")
                if "unreal57" in rag_index:
                    checks.append(
                        check(
                            "cline_index_path",
                            False,
                            f"{cline_path} uses stale unreal57 index",
                        )
                    )
                    fail_count += 1
                elif index_namespace in rag_index.replace("/", "\\") or expected_index in rag_index:
                    checks.append(
                        check(
                            "cline_index_path",
                            True,
                            f"{cline_path} -> {index_namespace}",
                        )
                    )
                else:
                    checks.append(
                        check(
                            "cline_index_path",
                            False,
                            f"index path unclear in {cline_path} (expected namespace {index_namespace})",
                        )
                    )
                    fail_count += 1
                break
        except (OSError, json.JSONDecodeError) as exc:
            cline_detail = str(exc)
    if cline_ok:
        checks.append(check("cline_mcp_config", True, cline_detail))
    else:
        checks.append(warn("cline_mcp_config", cline_detail))

    for entry in checks:
        if entry["status"] == "FAIL":
            fail_count += 1

    print()
    if fail_count:
        print(f"{fail_count} check(s) failed.")
        return 1
    print("All doctor checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
