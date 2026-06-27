#!/usr/bin/env python
"""clangd/LSP helper - navigation only, not build truth (Phase 15)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

TRUST_NAV = "navigation_only_not_build_truth"
DIAG_TRUST = "low_trust_unless_ubt_confirms"


def clangd_available() -> bool:
    return shutil.which("clangd") is not None


def find_compile_commands(project_root: Path) -> Path | None:
    candidates = [
        project_root / "compile_commands.json",
        project_root / "Intermediate" / "Build" / "compile_commands.json",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


class ClangdSession:
    """Minimal JSON-RPC over clangd stdio."""

    def __init__(self, compile_commands: Path) -> None:
        self._proc = subprocess.Popen(
            ["clangd", f"--compile-commands-dir={compile_commands.parent}"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self._lock = threading.Lock()
        self._id = 0
        self._initialize()

    def _initialize(self) -> None:
        self._request(
            "initialize",
            {
                "processId": os.getpid(),
                "rootUri": None,
                "capabilities": {},
            },
        )
        self._notify("initialized", {})

    def _notify(self, method: str, params: dict) -> None:
        if not self._proc.stdin:
            return
        msg = json.dumps({"jsonrpc": "2.0", "method": method, "params": params})
        self._proc.stdin.write(msg + "\n")
        self._proc.stdin.flush()

    def _request(self, method: str, params: dict, timeout: float = 8.0) -> dict:
        with self._lock:
            self._id += 1
            req_id = self._id
            if not self._proc.stdin or not self._proc.stdout:
                return {"error": "clangd not running"}
            msg = json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
            self._proc.stdin.write(msg + "\n")
            self._proc.stdin.flush()
            import time

            deadline = time.time() + timeout
            while time.time() < deadline:
                line = self._proc.stdout.readline()
                if not line:
                    break
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if payload.get("id") == req_id:
                    return payload.get("result") or payload
            return {"error": "timeout"}

    def close(self) -> None:
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
            self._proc.terminate()
            self._proc.wait(timeout=3)
        except Exception:
            pass


def _file_uri(path: Path) -> str:
    return path.resolve().as_uri()


def _heuristic_symbols(project_root: Path, rel_path: str) -> dict[str, Any]:
    target = (project_root / rel_path).resolve()
    if not target.is_file():
        return {"ok": False, "error": "file not found"}
    text = target.read_text(encoding="utf-8", errors="replace")
    symbols = []
    for idx, line in enumerate(text.splitlines(), start=1):
        if re.search(r"\b(class|struct|enum|void|UPROPERTY|UFUNCTION)\b", line):
            symbols.append({"line": idx, "text": line.strip()[:120], "kind": "heuristic"})
    return {"ok": True, "path": rel_path, "symbols": symbols[:50], "trust": "heuristic_not_clangd"}


def document_symbols(project_root: Path, rel_path: str) -> dict[str, Any]:
    cc = find_compile_commands(project_root)
    target = (project_root / rel_path).resolve()
    if not target.is_file():
        return {"ok": False, "error": "file not found"}
    if not clangd_available() or not cc:
        return _heuristic_symbols(project_root, rel_path)
    session = ClangdSession(cc)
    try:
        result = session._request(
            "textDocument/documentSymbol",
            {"textDocument": {"uri": _file_uri(target)}},
        )
        symbols = []
        for item in result if isinstance(result, list) else []:
            symbols.append(
                {
                    "name": item.get("name"),
                    "kind": item.get("kind"),
                    "line": (item.get("range") or {}).get("start", {}).get("line", 0) + 1,
                }
            )
        return {"ok": True, "path": rel_path, "symbols": symbols[:80], "trust": TRUST_NAV}
    except Exception as exc:
        return _heuristic_symbols(project_root, rel_path) | {"clangdError": str(exc)}
    finally:
        session.close()


def goto_definition(project_root: Path, rel_path: str, line: int, column: int = 1) -> dict[str, Any]:
    cc = find_compile_commands(project_root)
    target = (project_root / rel_path).resolve()
    if not target.is_file():
        return {"ok": False, "error": "file not found"}
    if not clangd_available() or not cc:
        return {"ok": False, "error": "clangd or compile_commands unavailable", "trust": TRUST_NAV}
    session = ClangdSession(cc)
    try:
        result = session._request(
            "textDocument/definition",
            {
                "textDocument": {"uri": _file_uri(target)},
                "position": {"line": max(0, line - 1), "character": max(0, column - 1)},
            },
        )
        locs = result if isinstance(result, list) else [result] if result else []
        return {"ok": True, "locations": locs, "trust": TRUST_NAV}
    finally:
        session.close()


def find_references(project_root: Path, rel_path: str, line: int, column: int = 1) -> dict[str, Any]:
    cc = find_compile_commands(project_root)
    target = (project_root / rel_path).resolve()
    if not target.is_file():
        return {"ok": False, "error": "file not found"}
    if not clangd_available() or not cc:
        return _grep_references(project_root, rel_path, line)
    session = ClangdSession(cc)
    try:
        result = session._request(
            "textDocument/references",
            {
                "textDocument": {"uri": _file_uri(target)},
                "position": {"line": max(0, line - 1), "character": max(0, column - 1)},
                "context": {"includeDeclaration": True},
            },
        )
        refs = result if isinstance(result, list) else []
        return {"ok": True, "references": refs[:50], "trust": TRUST_NAV}
    finally:
        session.close()


def _grep_references(project_root: Path, rel_path: str, line: int) -> dict[str, Any]:
    target = (project_root / rel_path).resolve()
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    if line < 1 or line > len(lines):
        return {"ok": False, "error": "line out of range"}
    text_line = lines[line - 1]
    tokens = re.findall(r"\b[A-Z][A-Za-z0-9_]+\b", text_line)
    if not tokens:
        return {"ok": False, "error": "no symbol token on line", "trust": "grep_fallback"}
    symbol = tokens[0]
    from refactor_plan import scan_symbol_impact

    impact = scan_symbol_impact(str(project_root), symbol, max_files=30)
    return {"ok": True, "symbol": symbol, "matches": impact.get("matches") or [], "trust": "grep_fallback"}


def header_source_pair(project_root: Path, rel_path: str) -> dict[str, Any]:
    path = Path(rel_path)
    stem = path.stem
    if path.suffix.lower() == ".h":
        cpp = project_root / path.parent / f"{stem}.cpp"
        return {"header": rel_path, "source": str(cpp.relative_to(project_root)) if cpp.is_file() else None}
    if path.suffix.lower() == ".cpp":
        header = project_root / path.parent / f"{stem}.h"
        return {"source": rel_path, "header": str(header.relative_to(project_root)) if header.is_file() else None}
    return {"path": rel_path}


def decl_def_mismatch(project_root: Path, rel_path: str) -> dict[str, Any]:
    pair = header_source_pair(project_root, rel_path)
    header_rel = pair.get("header")
    source_rel = pair.get("source")
    if not header_rel or not source_rel:
        return {"ok": True, "issues": [], "note": "pair incomplete"}
    header_syms = {s.get("text", "") for s in document_symbols(project_root, header_rel).get("symbols") or []}
    source_syms = {s.get("text", "") for s in document_symbols(project_root, source_rel).get("symbols") or []}
    issues = []
    if not header_syms and not source_syms:
        issues.append("Could not extract symbols from header/source pair.")
    return {"ok": len(issues) == 0, "issues": issues, "trust": TRUST_NAV}


def run_clangd_query(
    compile_commands: Path,
    file_path: Path,
    line: int,
    column: int,
    query: str,
) -> dict[str, Any]:
    root = compile_commands.parent.parent if "Intermediate" in str(compile_commands) else compile_commands.parent
    rel = str(file_path.relative_to(root)) if file_path.is_relative_to(root) else str(file_path)
    if query == "definition":
        return goto_definition(root, rel, line, column)
    if query == "references":
        return find_references(root, rel, line, column)
    return document_symbols(root, rel)
