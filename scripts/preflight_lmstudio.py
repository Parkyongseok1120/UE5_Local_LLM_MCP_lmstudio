#!/usr/bin/env python
"""Check LM Studio Local Server reachability before Tier B evals."""

from __future__ import annotations

import argparse
import json
import sys
from urllib.error import URLError
from urllib.request import Request, urlopen

AUTO_MODEL_ALIASES = {"", "local-model", "local", "default"}
EMBED_MARKERS = ("embed", "embedding", "nomic", "bge", "e5-")


def fetch_lmstudio_model_ids(url: str, timeout: float = 5.0) -> list[str]:
    base = url.rstrip("/")
    req = Request(f"{base}/models", method="GET")
    with urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    models = payload.get("data") or payload.get("models") or []
    if not isinstance(models, list):
        return []
    return [str(m.get("id") or m) for m in models]


def is_embedding_model(model_id: str) -> bool:
    lower = model_id.lower()
    return any(marker in lower for marker in EMBED_MARKERS)


def resolve_lmstudio_model(url: str, requested: str = "", timeout: float = 5.0) -> str:
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


def resolve_loaded_chat_model(url: str, requested: str = "", timeout: float = 5.0) -> str:
    """Prefer a model with state=loaded from LM Studio v0 API, else /v1/models."""
    if requested and requested not in AUTO_MODEL_ALIASES:
        return requested
    base = url.rstrip("/").removesuffix("/v1")
    try:
        req = Request(f"{base}/api/v0/models", method="GET")
        with urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        models = payload.get("models") or payload.get("data") or []
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
        print(f"LM Studio OK at {report['url']} ({report['modelCount']} models)")
    else:
        err = report.get("error") or "unreachable"
        print(f"LM Studio not ready: {err}", file=sys.stderr)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
