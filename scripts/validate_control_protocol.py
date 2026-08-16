#!/usr/bin/env python3
"""Validate the shared control protocol and emitted literal error-code totality."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

from atomic_io import atomic_write_text


ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "config" / "control_protocol_spec.json"
PRODUCTION_TREES = (
    ROOT / "lmstudio-unreal-agent-mcp" / "src",
    ROOT / "lmstudio-context-compactor-plugin" / "src",
    ROOT / "scripts",
)
SOURCE_SUFFIXES = frozenset({".js", ".ts", ".py"})
ALLOWED_CATEGORIES = frozenset(
    {"auth", "conflict", "environment", "input", "internal", "recoverable", "terminal"}
)
ALLOWED_RETRY_POLICIES = frozenset(
    {
        "bounded_environment_retry",
        "changed_input",
        "never",
        "recovery_tool",
        "replan",
        "same_call_after_refresh",
        "user_action",
    }
)

_QUOTED_ERROR_CODE = re.compile(
    r"(?:[\"']errorCode[\"']|\berrorCode|[\"']error_code[\"']|\berror_code)"
    r"\s*[:=]\s*[\"'`]([A-Z][A-Z0-9_]{2,})[\"'`]"
)
_ERROR_CODE_FALLBACK = re.compile(
    r"(?:\berrorCode\b|\berror_code\b|[\"']errorCode[\"']|[\"']error_code[\"'])"
    r"[^\r\n]{0,120}?(?:\|\||\bor\b)\s*[\"'`]([A-Z][A-Z0-9_]{2,})[\"'`]"
)
_ERROR_CODE_TERNARY = re.compile(
    r"(?:\berrorCode\b|\berror_code\b|[\"']errorCode[\"']|[\"']error_code[\"'])"
    r"[\s\S]{0,180}?\?[\s\S]{0,120}?[\"'`]([A-Z][A-Z0-9_]{2,})[\"'`]"
    r"[\s\S]{0,80}?:[\s\S]{0,80}?[\"'`]([A-Z][A-Z0-9_]{2,})[\"'`]"
)


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def section_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def source_files() -> Iterable[Path]:
    for tree in PRODUCTION_TREES:
        if not tree.is_dir():
            continue
        for path in tree.rglob("*"):
            if path.is_file() and path.suffix.casefold() in SOURCE_SUFFIXES:
                if path.resolve() == Path(__file__).resolve():
                    continue
                yield path


def discover_emitted_error_codes() -> dict[str, list[str]]:
    discovered: dict[str, set[str]] = {}
    for path in source_files():
        text = path.read_text(encoding="utf-8-sig")
        relative = path.relative_to(ROOT).as_posix()
        for pattern in (_QUOTED_ERROR_CODE, _ERROR_CODE_FALLBACK, _ERROR_CODE_TERNARY):
            for match in pattern.finditer(text):
                for code in (group for group in match.groups() if group):
                    discovered.setdefault(code, set()).add(relative)
    return {code: sorted(paths) for code, paths in sorted(discovered.items())}


def load_spec(path: Path = SPEC_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("control protocol spec root must be an object")
    return payload


def protocol_hashes(spec: dict[str, Any]) -> dict[str, str]:
    return {
        "transitionPolicyHash": section_hash(spec.get("transitionPolicy")),
        "errorCatalogHash": section_hash(spec.get("errorCatalog")),
        "authorizationSchemaHash": section_hash(spec.get("authorizationSchema")),
        "controlSchemaHash": section_hash(spec.get("controlSchema")),
    }


def flatten_error_catalog(raw_catalog: Any) -> tuple[dict[str, dict[str, Any]], list[str]]:
    errors: list[str] = []
    flattened: dict[str, dict[str, Any]] = {}
    if not isinstance(raw_catalog, dict):
        return {}, ["errorCatalog must be an object"]
    for category, retry_groups in sorted(raw_catalog.items()):
        if category not in ALLOWED_CATEGORIES:
            errors.append(f"errorCatalog has invalid category {category!r}")
            continue
        if not isinstance(retry_groups, dict):
            errors.append(f"errorCatalog.{category} must be an object")
            continue
        for retry, codes in sorted(retry_groups.items()):
            if retry not in ALLOWED_RETRY_POLICIES:
                errors.append(f"errorCatalog.{category} has invalid retry policy {retry!r}")
                continue
            if not isinstance(codes, list):
                errors.append(f"errorCatalog.{category}.{retry} must be an array")
                continue
            if codes != sorted(set(map(str, codes))):
                errors.append(f"errorCatalog.{category}.{retry} must be sorted and unique")
            for raw_code in codes:
                code = str(raw_code)
                if code in flattened:
                    errors.append(f"{code}: appears more than once in errorCatalog")
                    continue
                flattened[code] = {
                    "category": category,
                    "retry": retry,
                    "terminal": category == "terminal",
                }
    return flattened, errors


def validate_spec(spec: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if int(spec.get("schemaVersion") or 0) < 1:
        errors.append("schemaVersion must be a positive integer")
    if int(spec.get("protocolVersion") or 0) < 1:
        errors.append("protocolVersion must be a positive integer")
    for section in ("transitionPolicy", "authorizationSchema", "controlSchema"):
        if not isinstance(spec.get(section), dict) or not spec.get(section):
            errors.append(f"{section} must be a non-empty object")

    catalog, catalog_errors = flatten_error_catalog(spec.get("errorCatalog"))
    errors.extend(catalog_errors)
    discovered = discover_emitted_error_codes()
    unclassified = sorted(set(discovered) - set(catalog))
    if unclassified:
        errors.append("unclassified emitted error codes: " + ", ".join(unclassified))

    for code, policy in sorted(catalog.items()):
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", str(code)):
            errors.append(f"invalid error code key: {code}")
            continue
        category = str(policy.get("category") or "")
        retry = str(policy.get("retry") or "")
        terminal = policy.get("terminal")
        if category not in ALLOWED_CATEGORIES:
            errors.append(f"{code}: invalid category {category!r}")
        if retry not in ALLOWED_RETRY_POLICIES:
            errors.append(f"{code}: invalid retry policy {retry!r}")
        if terminal is True and retry not in {"never", "user_action"}:
            errors.append(f"{code}: terminal errors cannot use retry policy {retry!r}")
        if category == "input" and retry == "same_call_after_refresh":
            errors.append(f"{code}: input errors require changed arguments, not a blind same-call retry")
        if category == "internal" and retry == "same_call_after_refresh":
            errors.append(f"{code}: internal errors cannot be declared blindly retryable")
        if code not in discovered:
            errors.append(f"{code}: catalog entry is not emitted")

    if errors:
        raise ValueError("\n".join(errors))
    return {
        "ok": True,
        "emittedErrorCodeCount": len(discovered),
        "catalogEntryCount": len(catalog),
        "hashes": protocol_hashes(spec),
    }


def suggested_classification(code: str) -> dict[str, Any]:
    """Create a conservative review aid; suggestions are never written automatically."""
    if any(marker in code for marker in ("AUTH", "OWNER_CAPABILITY", "OWNERSHIP")):
        return {"category": "auth", "retry": "recovery_tool", "terminal": False}
    if any(marker in code for marker in ("CONFLICT", "STALE", "MISMATCH")):
        return {"category": "conflict", "retry": "recovery_tool", "terminal": False}
    if any(marker in code for marker in ("DISABLED", "UNAVAILABLE", "TIMEOUT", "EXECUTOR", "PROCESS")):
        return {"category": "environment", "retry": "bounded_environment_retry", "terminal": False}
    if any(marker in code for marker in ("INVALID", "REQUIRED", "MISSING", "NOT_FOUND", "TOO_LARGE")):
        return {"category": "input", "retry": "changed_input", "terminal": False}
    if any(marker in code for marker in ("INTERNAL", "CORRUPT", "INTEGRITY", "ROLLBACK_INCOMPLETE")):
        return {"category": "internal", "retry": "recovery_tool", "terminal": False}
    if any(marker in code for marker in ("CANCELLED", "CANCELED", "UNSUPPORTED")):
        return {"category": "terminal", "retry": "user_action", "terminal": True}
    return {"category": "recoverable", "retry": "replan", "terminal": False}


_CLASSIFICATION_OVERRIDES: dict[str, tuple[str, str]] = {
    "AUTOMATION_DISABLED": ("environment", "user_action"),
    "AUTOMATION_LOG_WRITE_FAILED": ("environment", "bounded_environment_retry"),
    "AUTOMATION_OUTPUT_DECODE_FAILED": ("internal", "recovery_tool"),
    "BLOCKED": ("terminal", "user_action"),
    "BUILD_DISABLED": ("environment", "user_action"),
    "BUILD_RECOVERY_REQUIRED_EVIDENCE": ("recoverable", "recovery_tool"),
    "BUNDLE_PATH_LOCKED": ("environment", "bounded_environment_retry"),
    "CONTEXT_COMPACTOR_NOT_ACTIVE": ("environment", "user_action"),
    "CONTROL_RUNTIME_VERSION_MISMATCH": ("terminal", "user_action"),
    "ENGINE_ASSOCIATION_UNRESOLVED": ("input", "changed_input"),
    "ENGINE_ROOT_UNRESOLVED": ("input", "changed_input"),
    "FEATURE_INTENT_BLOCKING_QUESTIONS": ("recoverable", "user_action"),
    "FILE_ALREADY_EXISTS": ("input", "changed_input"),
    "HUMAN_APPROVAL_CHANNEL_REQUIRED": ("recoverable", "user_action"),
    "MUTATION_COMPENSATION_FAILED": ("internal", "recovery_tool"),
    "MUTATION_DISK_ROLLBACK_FAILED": ("internal", "recovery_tool"),
    "MUTATION_LOCK_BUSY": ("environment", "bounded_environment_retry"),
    "MUTATION_ROLLBACK_RECONCILIATION_FAILED": ("internal", "recovery_tool"),
    "PLUGIN_INSTALL_FAILED": ("environment", "bounded_environment_retry"),
    "RECOVERY_ENVIRONMENT_BLOCKED": ("terminal", "user_action"),
    "RECOVERY_ERROR_EVIDENCE_NOT_FOUND": ("recoverable", "replan"),
    "RECOVERY_JOURNAL_RESOLUTION_FAILED": ("internal", "recovery_tool"),
    "RECOVERY_JOURNAL_RESOLVER_UNAVAILABLE": ("environment", "bounded_environment_retry"),
    "ROLLBACK_CHECKPOINT_FAILED": ("internal", "recovery_tool"),
    "ROLLBACK_RECONCILIATION_INCOMPLETE": ("internal", "recovery_tool"),
    "RUNTIME_EXECUTION_DISABLED": ("environment", "user_action"),
    "SERVER_WORKFLOW_BLOCKED": ("terminal", "user_action"),
    "TASK_AUTH_FAILED": ("auth", "recovery_tool"),
    "TASK_AUTH_INCOMPLETE": ("auth", "same_call_after_refresh"),
    "TASK_AUTH_INVALID_FORMAT": ("auth", "recovery_tool"),
    "TASK_AUTH_MISMATCH": ("auth", "recovery_tool"),
    "TASK_AUTH_REFRESH_UNAVAILABLE": ("auth", "recovery_tool"),
    "TASK_CANCELLATION_UNCERTAIN": ("terminal", "user_action"),
    "TASK_FOREIGN_HEALTHY": ("terminal", "user_action"),
    "TASK_LOCK_BUSY": ("environment", "bounded_environment_retry"),
    "TASK_ROUTE_AUTH_FAILED": ("auth", "recovery_tool"),
    "TASK_ROUTE_OWNERSHIP_REQUIRED": ("auth", "same_call_after_refresh"),
    "TASK_ROUTE_STALE": ("auth", "same_call_after_refresh"),
    "TASK_STATE_LOCKED": ("environment", "bounded_environment_retry"),
    "TASK_STATE_ROOT_UNAVAILABLE": ("environment", "user_action"),
    "TRANSACTION_RECONCILIATION_REQUIRED": ("recoverable", "recovery_tool"),
    "TRANSACTION_RECOVERY_PROMOTION_FAILED": ("internal", "recovery_tool"),
    "UBT_NOT_FOUND": ("environment", "user_action"),
    "UNKNOWN_TOOL": ("input", "changed_input"),
    "UNREAL_EDITOR_CMD_NOT_FOUND": ("environment", "user_action"),
}


def grouped_suggestions(codes: Iterable[str]) -> dict[str, dict[str, list[str]]]:
    grouped: dict[str, dict[str, list[str]]] = {}
    for code in sorted(set(codes)):
        suggested = suggested_classification(code)
        category, retry = _CLASSIFICATION_OVERRIDES.get(
            code,
            (str(suggested["category"]), str(suggested["retry"])),
        )
        grouped.setdefault(category, {}).setdefault(retry, []).append(code)
    return {
        category: {retry: sorted(values) for retry, values in sorted(retry_groups.items())}
        for category, retry_groups in sorted(grouped.items())
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, default=SPEC_PATH)
    parser.add_argument("--suggest", action="store_true", help="print conservative entries missing from the catalog")
    parser.add_argument(
        "--bootstrap-catalog",
        action="store_true",
        help="mechanically replace the catalog with conservative grouped classifications for review",
    )
    args = parser.parse_args()
    spec = load_spec(args.spec)
    if args.bootstrap_catalog:
        spec["errorCatalog"] = grouped_suggestions(discover_emitted_error_codes())
        atomic_write_text(
            args.spec,
            json.dumps(spec, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps({"updated": str(args.spec), "count": len(discover_emitted_error_codes())}, indent=2))
        return 0
    if args.suggest:
        catalog, _ = flatten_error_catalog(spec.get("errorCatalog"))
        missing = {
            code: suggested_classification(code)
            for code in discover_emitted_error_codes()
            if code not in catalog
        }
        print(json.dumps(missing, ensure_ascii=False, indent=2, sort_keys=True))
        return 1 if missing else 0
    print(json.dumps(validate_spec(spec), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
