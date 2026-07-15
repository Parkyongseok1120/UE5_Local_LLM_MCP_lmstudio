#!/usr/bin/env python
"""Check LM Studio Local Server reachability before Tier B evals."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from urllib.error import URLError
from urllib.request import Request, urlopen

AUTO_MODEL_ALIASES = {"", "local-model", "local", "default"}
EMBED_MARKERS = ("embed", "embedding", "nomic", "bge", "e5-")

# Allow overriding the default preflight timeout via environment variable.
# Useful on slow networks or remote LM Studio setups.
_DEFAULT_PREFLIGHT_TIMEOUT = 5.0
try:
    PREFLIGHT_TIMEOUT = float(os.environ.get("LMSTUDIO_PREFLIGHT_TIMEOUT") or _DEFAULT_PREFLIGHT_TIMEOUT)
except (TypeError, ValueError):
    PREFLIGHT_TIMEOUT = _DEFAULT_PREFLIGHT_TIMEOUT


def fetch_lmstudio_model_ids(url: str, timeout: float | None = None) -> list[str]:
    if timeout is None:
        timeout = PREFLIGHT_TIMEOUT
    base = url.rstrip("/")
    req = Request(f"{base}/models", method="GET")
    with urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    models = payload.get("data") or payload.get("models") or []
    if not isinstance(models, list):
        return []
    return [str(m.get("id") or m) for m in models]


def fetch_lmstudio_models_v0(url: str, timeout: float | None = None) -> list[dict]:
    """Return LM Studio model rows including loaded/max context when available."""
    if timeout is None:
        timeout = PREFLIGHT_TIMEOUT
    base = url.rstrip("/").removesuffix("/v1")
    req = Request(f"{base}/api/v0/models", method="GET")
    with urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    rows = payload.get("models") or payload.get("data") or []
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def fetch_lms_loaded_models(timeout: float | None = None) -> list[dict]:
    """Read LM Studio load-time context/parallel values when the lms CLI is installed."""
    try:
        completed = subprocess.run(
            ["lms", "ps", "--json"],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout if timeout is not None else PREFLIGHT_TIMEOUT,
        )
        payload = json.loads(completed.stdout or "[]") if completed.returncode == 0 else []
        return [row for row in payload if isinstance(row, dict)] if isinstance(payload, list) else []
    except (FileNotFoundError, OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError):
        return []


def is_embedding_model(model_id: str) -> bool:
    lower = model_id.lower()
    return any(marker in lower for marker in EMBED_MARKERS)


def resolve_lmstudio_model(url: str, requested: str = "", timeout: float | None = None) -> str:
    """Map local-model/default to the first loaded chat model on the server."""
    if requested and requested not in AUTO_MODEL_ALIASES:
        return requested
    try:
        ids = fetch_lmstudio_model_ids(url, timeout=timeout)
    except Exception:
        return requested or "local-model"
    for model_id in ids:
        if not is_embedding_model(model_id):
            return model_id
    return ids[0] if ids else (requested or "local-model")


def resolve_loaded_chat_model(url: str, requested: str = "", timeout: float | None = None) -> str:
    """Prefer a model with state=loaded from LM Studio v0 API, else /v1/models."""
    if requested and requested not in AUTO_MODEL_ALIASES:
        return requested
    _timeout = timeout if timeout is not None else PREFLIGHT_TIMEOUT
    try:
        models = fetch_lmstudio_models_v0(url, timeout=_timeout)
        if isinstance(models, list):
            for row in models:
                if str(row.get("state") or "").lower() != "loaded":
                    continue
                model_id = str(row.get("id") or row.get("path") or "")
                if model_id and not is_embedding_model(model_id):
                    return model_id
    except Exception:
        pass
    return resolve_lmstudio_model(url, requested, timeout=timeout)


def extract_assistant_text(message: dict) -> str:
    """Return visible assistant text; Qwen thinking models may use reasoning_content only."""
    content = str(message.get("content") or "").strip()
    if content:
        return content
    return str(message.get("reasoning_content") or "").strip()


def check_lmstudio(url: str, model: str = "", timeout: float = 5.0) -> dict:
    base = url.rstrip("/")
    result: dict = {"url": base, "reachable": False, "modelCount": 0, "modelOk": True}
    try:
        ids = fetch_lmstudio_model_ids(base, timeout=timeout)
        result["reachable"] = True
        result["modelCount"] = len(ids)
        chat_ids = [mid for mid in ids if not is_embedding_model(mid)]
        resolved = resolve_loaded_chat_model(base, model, timeout=timeout)
        result["resolvedModel"] = resolved
        try:
            v0_rows = fetch_lmstudio_models_v0(base, timeout=timeout)
        except Exception:
            v0_rows = []
        loaded_row = next(
            (row for row in v0_rows if str(row.get("state") or "").lower() == "loaded"
             and str(row.get("id") or row.get("path") or "") == resolved),
            None,
        )
        if loaded_row:
            loaded_context = int(loaded_row.get("loaded_context_length") or 0)
            max_context = int(loaded_row.get("max_context_length") or 0)
            result["loadedContextLength"] = loaded_context or None
            result["maxContextLength"] = max_context or None
            result["contextConfigured"] = bool(loaded_context and max_context and loaded_context <= max_context)
            result["contextHeadroom"] = max(0, max_context - loaded_context) if max_context else None
        loaded_cli = next(
            (row for row in fetch_lms_loaded_models(timeout=timeout)
             if str(row.get("identifier") or row.get("modelKey") or "") == resolved),
            None,
        )
        if loaded_cli:
            parallel = int(loaded_cli.get("parallel") or 1)
            result["parallelRequests"] = parallel
            result["recommendedParallelRequests"] = 1
            if parallel > 1 and int(result.get("loadedContextLength") or 0) >= 65536:
                result["longContextSingleAgentWarning"] = (
                    "Parallel > 1 is throughput-oriented. Reload with Parallel=1 for one long tool-calling chat."
                )
        if model in AUTO_MODEL_ALIASES:
            result["modelOk"] = bool(chat_ids)
        elif model:
            result["modelOk"] = model in ids or any(model in mid for mid in ids)
        else:
            result["modelOk"] = bool(chat_ids)
    except URLError as exc:
        result["error"] = str(exc.reason if hasattr(exc, "reason") else exc)
    except Exception as exc:
        result["error"] = str(exc)
    result["ok"] = result["reachable"] and result.get("modelOk", True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="LM Studio preflight check.")
    parser.add_argument("--url", default="http://localhost:1234/v1")
    parser.add_argument("--model", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = check_lmstudio(args.url, args.model)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif report.get("ok"):
        context = ""
        if report.get("loadedContextLength"):
            context = f", context {report['loadedContextLength']}/{report.get('maxContextLength') or '?'}"
        print(f"LM Studio OK at {report['url']} ({report['modelCount']} models{context})")
    else:
        err = report.get("error") or "unreachable"
        print(f"LM Studio not ready: {err}", file=sys.stderr)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
