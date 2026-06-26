#!/usr/bin/env python
"""Collect Unreal build, UHT, linker, and editor log errors into JSONL."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


LOG_EXTENSIONS = {".log", ".txt"}
SKIP_DIRS = {".git", ".vs", "DerivedDataCache", "Binaries"}
ERROR_PATTERNS = [
    re.compile(r"(?P<file>[A-Za-z]:\\[^:\r\n]+|\S+\.(?:h|hpp|cpp|cs|inl|uhtmanifest))\((?P<line>\d+)\)\s*:\s*(?P<severity>fatal error|error|warning)\s+(?P<code>C\d+|LNK\d+)?\s*:?\s*(?P<message>.*)", re.IGNORECASE),
    re.compile(r"(?P<file>[A-Za-z]:\\[^:\r\n]+|\S+\.(?:h|hpp|cpp|cs|inl)):(?P<line>\d+):(?P<column>\d+)?:?\s*(?P<severity>fatal error|error|warning):\s*(?P<message>.*)", re.IGNORECASE),
    re.compile(r"(?P<severity>fatal error|error)\s+(?P<code>LNK\d+|C\d+)\s*:?\s*(?P<message>.*)", re.IGNORECASE),
    re.compile(r"(?:Log[A-Za-z0-9_]+:\s*)?(?P<severity>Error|Warning):\s*(?P<message>.*)", re.IGNORECASE),
]
CODE_RE = re.compile(r"\b(C\d{4}|LNK\d{4}|MSB\d{4}|UHT|UnrealHeaderTool)\b", re.IGNORECASE)
FILE_RE = re.compile(r"([A-Za-z]:\\[^:\r\n]+?\.(?:h|hpp|cpp|cs|inl|Build\.cs|uproject|uplugin)|[A-Za-z0-9_./\\-]+\.(?:h|hpp|cpp|cs|inl|Build\.cs|uproject|uplugin))")
INCLUDE_RE = re.compile(r"Cannot open include file:\s*'([^']+)'", re.IGNORECASE)
SYMBOL_RE = re.compile(r"(?:unresolved external symbol|Undefined symbols).*?([A-Za-z_][A-Za-z0-9_:~<>]*)", re.IGNORECASE)


def stable_id(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def read_text(path: Path) -> str | None:
    for encoding in ("utf-8-sig", "utf-8", "cp949", "latin-1"):
        try:
            return path.read_text(encoding=encoding, errors="replace")
        except OSError as exc:
            print(f"[skip] {path} ({exc})")
            return None
    return None


def should_skip(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    return any(part in SKIP_DIRS for part in relative.parts)


def infer_project(root: Path, path: Path) -> str:
    parts = path.parts
    if "Saved" in parts:
        index = parts.index("Saved")
        if index > 0:
            return parts[index - 1]
    for parent in path.parents:
        if parent.suffix == ".uproject":
            return parent.stem
    return root.name


def classify_error(message: str, code: str) -> str:
    value = f"{code} {message}".lower()
    if "generated.h" in value or "unrealheadertool" in value or "uht" in value:
        return "reflection_fix"
    if "cannot open include file" in value or "build.cs" in value or "module" in value:
        return "module_fix"
    if "lnk" in value or "unresolved external symbol" in value:
        return "link_fix"
    if "ensure" in value or "assert" in value or "crash" in value:
        return "runtime_debug"
    return "compile_fix"


def find_error_match(line: str) -> re.Match | None:
    for pattern in ERROR_PATTERNS:
        match = pattern.search(line)
        if match:
            return match
    return None


def context_block(lines: list[str], index: int, radius: int) -> str:
    start = max(0, index - radius)
    end = min(len(lines), index + radius + 1)
    return "\n".join(lines[start:end]).strip()


def extract_error(path: Path, root: Path, lines: list[str], index: int, radius: int) -> dict | None:
    line = lines[index].rstrip()
    match = find_error_match(line)
    if not match:
        return None

    groups = match.groupdict()
    message = str(groups.get("message") or line).strip()
    code_match = CODE_RE.search(line)
    error_code = str(groups.get("code") or (code_match.group(1) if code_match else "") or "").upper()
    severity = str(groups.get("severity") or "error").lower()
    error_file = str(groups.get("file") or "")
    if not error_file:
        file_match = FILE_RE.search(line)
        error_file = file_match.group(1) if file_match else ""

    include_match = INCLUDE_RE.search(line)
    symbol_match = SYMBOL_RE.search(line)
    symbol_name = ""
    if include_match:
        symbol_name = include_match.group(1)
    elif symbol_match:
        symbol_name = symbol_match.group(1)

    error_kind = classify_error(message, error_code)
    project = infer_project(root, path)
    text = "\n".join(
        [
            f"Build log error kind: {error_kind}",
            f"Severity: {severity}",
            f"Error code: {error_code or '(none detected)'}",
            f"Error file: {error_file or '(none detected)'}",
            f"Log file: {path}",
            f"Project: {project}",
            f"Symbol or include: {symbol_name or '(none detected)'}",
            "",
            "Message:",
            message,
            "",
            "Context:",
            context_block(lines, index, radius),
        ]
    )
    metadata = {
        "root": str(root),
        "relative_path": path.relative_to(root).as_posix() if path.is_relative_to(root) else path.as_posix(),
        "extension": path.suffix.lower(),
        "project": project,
        "error_code": error_code,
        "error_file": error_file,
        "error_kind": error_kind,
        "symbol_name": symbol_name,
        "symbol_kind": "error",
        "module_name": "",
        "line": str(groups.get("line") or ""),
        "severity": severity,
    }
    title_bits = [error_kind]
    if error_code:
        title_bits.append(error_code)
    if symbol_name:
        title_bits.append(symbol_name)
    elif error_file:
        title_bits.append(Path(error_file).name)
    return {
        "id": stable_id(f"build_log:{path}:{index}:{line}"),
        "source": "build_log",
        "path": str(path),
        "title": " - ".join(title_bits),
        "text": text,
        "metadata": metadata,
    }


def collect(args: argparse.Namespace) -> None:
    roots = [Path(value).expanduser().resolve() for value in (args.root or ["."])]
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    written = 0
    scanned = 0
    with out_path.open("w", encoding="utf-8") as handle:
        for root in roots:
            if not root.exists():
                print(f"[skip] missing root: {root}")
                continue
            for path in sorted(root.rglob("*")):
                if scanned >= args.max_files:
                    break
                if not path.is_file() or path.suffix.lower() not in LOG_EXTENSIONS:
                    continue
                if should_skip(path, root) or path.stat().st_size > args.max_bytes:
                    continue
                if args.logs_only and "logs" not in [part.lower() for part in path.parts]:
                    continue
                text = read_text(path)
                if not text:
                    continue
                scanned += 1
                lines = text.splitlines()
                seen_lines: set[int] = set()
                for index, line in enumerate(lines):
                    if index in seen_lines or not find_error_match(line):
                        continue
                    item = extract_error(path, root, lines, index, args.context_lines)
                    if not item:
                        continue
                    handle.write(json.dumps(item, ensure_ascii=False) + "\n")
                    written += 1
                    for offset in range(index, min(len(lines), index + args.group_following_lines + 1)):
                        seen_lines.add(offset)

    print(f"done: scanned {scanned} log files and wrote {written} error records to {out_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect Unreal build/editor log errors as JSONL.")
    parser.add_argument("--root", action="append", default=None)
    parser.add_argument("--out", default="data/unreal58/raw_build_logs.jsonl")
    parser.add_argument("--max-files", type=int, default=300)
    parser.add_argument("--max-bytes", type=int, default=25_000_000)
    parser.add_argument("--context-lines", type=int, default=3)
    parser.add_argument("--group-following-lines", type=int, default=2)
    parser.add_argument("--logs-only", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    collect(parse_args())
