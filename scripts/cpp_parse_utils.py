#!/usr/bin/env python
"""Shared lightweight C++ text helpers for validators."""

from __future__ import annotations

import re


def mask_comments_and_strings(text: str) -> str:
    """Replace comments and string literals with spaces (same length) for safe scanning."""
    source = str(text or "")
    out = list(source)
    index = 0
    length = len(source)
    while index < length:
        ch = source[index]
        nxt = source[index + 1] if index + 1 < length else ""
        if ch == "/" and nxt == "/":
            end = source.find("\n", index)
            if end == -1:
                end = length
            for pos in range(index, end):
                out[pos] = " "
            index = end
            continue
        if ch == "/" and nxt == "*":
            end = source.find("*/", index + 2)
            if end == -1:
                end = length - 2
            end += 2
            for pos in range(index, min(end, length)):
                out[pos] = " "
            index = end
            continue
        if ch == '"':
            index = _mask_string_literal(source, out, index, '"')
            continue
        if ch == "'":
            index = _mask_string_literal(source, out, index, "'")
            continue
        if source.startswith("R\"", index):
            index = _mask_raw_string_literal(source, out, index)
            continue
        if re.match(r"\bTEXT\s*\(\s*\"", source[index:]):
            open_paren = source.index("(", index)
            index = _mask_string_literal(source, out, open_paren + 1, '"')
            continue
        index += 1
    return "".join(out)


def _mask_string_literal(source: str, out: list[str], index: int, quote: str) -> int:
    if index >= len(source) or source[index] != quote:
        return index + 1
    pos = index + 1
    while pos < len(source):
        if source[pos] == "\\":
            pos += 2
            continue
        if source[pos] == quote:
            pos += 1
            break
        pos += 1
    for fill in range(index, min(pos, len(source))):
        out[fill] = " "
    return pos


def _mask_raw_string_literal(source: str, out: list[str], index: int) -> int:
    if not source.startswith("R\"", index):
        return index + 1
    delim_start = index + 2
    delim_end = source.find("(", delim_start)
    if delim_end == -1:
        return index + 1
    delim = source[delim_start:delim_end]
    start = index
    close_marker = f"){delim}\""
    close = source.find(close_marker, delim_end + 1)
    if close == -1:
        close = len(source)
    else:
        close += len(close_marker)
    for fill in range(start, min(close, len(source))):
        out[fill] = " "
    return close


def find_balanced_parens(text: str, open_index: int) -> int:
    if open_index < 0 or open_index >= len(text) or text[open_index] != "(":
        return -1
    depth = 0
    in_string = False
    quote = ""
    index = open_index
    while index < len(text):
        ch = text[index]
        if in_string:
            if ch == "\\":
                index += 2
                continue
            if ch == quote:
                in_string = False
            index += 1
            continue
        if ch in "\"'":
            in_string = True
            quote = ch
            index += 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return -1


def extract_macro_blocks(text: str, macro_name: str) -> list[tuple[int, int, str]]:
    blocks: list[tuple[int, int, str]] = []
    pattern = re.compile(rf"\b{re.escape(macro_name)}\s*\(")
    for match in pattern.finditer(text):
        open_index = match.end() - 1
        close_index = find_balanced_parens(text, open_index)
        if close_index == -1:
            continue
        blocks.append((match.start(), close_index + 1, text[match.start() : close_index + 1]))
    return blocks


def _with_editor_condition(stripped: str) -> bool:
    return bool(
        re.match(r"#ifdef\s+WITH_EDITOR\b", stripped, re.IGNORECASE)
        or re.match(r"#if\s+defined\s*\(\s*WITH_EDITOR\s*\)", stripped, re.IGNORECASE)
        or re.match(r"#if\s+WITH_EDITOR\b", stripped, re.IGNORECASE)
    )


def _ifndef_with_editor(stripped: str) -> bool:
    return bool(re.match(r"#ifndef\s+WITH_EDITOR\b", stripped, re.IGNORECASE))


def preprocessor_editor_safe_regions(text: str) -> list[tuple[int, int]]:
    """Return byte ranges where WITH_EDITOR preprocessor blocks are active (true branch only)."""
    regions: list[tuple[int, int]] = []
    stack: list[bool] = [False]
    region_start: int | None = None
    offset = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if _with_editor_condition(stripped):
            stack.append(True)
        elif _ifndef_with_editor(stripped):
            stack.append(False)
        elif re.match(r"#if(?:def|ndef)?\b", stripped, re.IGNORECASE) or re.match(
            r"#if\s+defined\s*\(", stripped, re.IGNORECASE
        ):
            stack.append(stack[-1])
        elif stripped.startswith("#elif"):
            if len(stack) > 1:
                stack[-1] = _with_editor_condition(stripped)
        elif stripped.startswith("#else"):
            if len(stack) > 1:
                stack[-1] = not stack[-1]
        elif stripped.startswith("#endif"):
            if len(stack) > 1:
                stack.pop()
        safe = stack[-1]
        if safe and region_start is None:
            region_start = offset
        elif not safe and region_start is not None:
            regions.append((region_start, offset))
            region_start = None
        offset += len(line)
    if region_start is not None:
        regions.append((region_start, offset))
    return regions


def preprocessor_with_editor_regions(text: str) -> list[tuple[int, int]]:
    return preprocessor_editor_safe_regions(text)


def offset_in_regions(offset: int, regions: list[tuple[int, int]]) -> bool:
    return any(start <= offset < end for start, end in regions)
