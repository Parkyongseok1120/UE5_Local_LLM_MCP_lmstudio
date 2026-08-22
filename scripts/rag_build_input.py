"""Read and chunk JSONL build inputs with fail-closed syntax validation."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path

TOKEN_RE = re.compile(r"\S+")
FORBIDDEN_INPUT_SOURCES = frozenset({"module_graph"})


class JsonlInputError(RuntimeError):
    """An input row cannot be safely interpreted as a collected document."""


def approx_tokens(text: str) -> list[str]:
    return TOKEN_RE.findall(text)


def resolve_chunk_params(
    source: str,
    metadata: dict,
    *,
    default_chunk_tokens: int = 900,
    default_overlap_tokens: int = 120,
) -> tuple[int, int]:
    del metadata
    if source == "unreal_symbol":
        return 300, 60
    return default_chunk_tokens, default_overlap_tokens


def chunk_text(text: str, chunk_tokens: int, overlap_tokens: int) -> list[str]:
    tokens = approx_tokens(text)
    if not tokens:
        return []
    if len(tokens) <= chunk_tokens:
        return [" ".join(tokens)]
    chunks: list[str] = []
    start = 0
    step = max(1, chunk_tokens - overlap_tokens)
    while start < len(tokens):
        end = min(start + chunk_tokens, len(tokens))
        chunks.append(" ".join(tokens[start:end]))
        if end == len(tokens):
            break
        start += step
    return chunks


def read_jsonl(paths: list[Path]) -> Iterator[tuple[Path, int, dict]]:
    for path in paths:
        if not path.exists():
            print(f"[skip] missing input: {path}")
            continue
        with path.open("r", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                payload = line.strip()
                if not payload:
                    continue
                try:
                    document = json.loads(payload)
                except json.JSONDecodeError as exc:
                    raise JsonlInputError(
                        f"Malformed JSONL at {path}:{line_no}: {exc.msg}"
                    ) from exc
                if not isinstance(document, dict):
                    raise JsonlInputError(
                        f"Malformed JSONL at {path}:{line_no}: expected a JSON object"
                    )
                source = str(document.get("source") or "unknown")
                if source in FORBIDDEN_INPUT_SOURCES:
                    raise JsonlInputError(
                        f"Unsupported Direct RAG source at {path}:{line_no}: {source}"
                    )
                yield path, line_no, document
