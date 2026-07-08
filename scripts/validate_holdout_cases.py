#!/usr/bin/env python
"""Validate public-safe real-project holdout eval case configs."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from error_taxonomy import route_error_action  # noqa: E402
from module_resolver import resolve_modules_from_error, resolve_modules_from_text  # noqa: E402
from subkind_policy import validate_policy_coverage  # noqa: E402

ALLOWED_CATEGORIES = {
    "missing module dependency",
    "generated.h / UHT reflection error",
    "header/cpp signature mismatch",
    "unresolved external / missing cpp definition",
    "editor-only include in runtime module",
    "EnhancedInput dependency issue",
    "UMG dependency issue",
    "Niagara dependency issue",
    "GameplayTags dependency issue",
    "AI module dependency issue",
    "NavigationSystem dependency issue",
    "LevelSequence dependency issue",
    "wrong include owner / missing include path",
    "BlueprintNativeEvent signature issue",
    "BlueprintImplementableEvent signature issue",
    "UObject raw pointer / reflection warning",
    "constructor lifecycle misuse",
    "delegate binding signature issue",
    "component registration issue",
    "subsystem registration issue",
    "plugin/module dependency issue",
    "UObject lifecycle issue",
    "simple multi-file compile refactor",
    "common compile regression",
}
REQUIRED_FIELDS = {"id", "category", "mode", "notes"}
PUBLIC_PATH_PATTERNS = (
    re.compile(r"\b[A-Za-z]:[\\/](?:Users|home|Projects|Work|Dev)[\\/]", re.I),
    re.compile(r"/Users/", re.I),
    re.compile(r"/home/", re.I),
)
SUBKIND_COMPAT = {
    "C1083_MISSING_INCLUDE": {"C1083_MISSING_INCLUDE", "MISSING_INCLUDE_OWNER_MODULE", "INCLUDE_GENERIC"},
    "GENERATED_H_NOT_LAST": {"GENERATED_H_NOT_LAST", "UHT_GENERIC"},
    "UHT_MISSING_BODY_MACRO": {"UHT_MISSING_BODY_MACRO", "UHT_GENERIC"},
}


def _case_text(case: dict[str, Any]) -> str:
    return "\n".join(str(case.get(key) or "") for key in ("errorLog", "buildLog", "fixtureDir", "notes"))


def _contains_private_path(value: Any) -> bool:
    text = json.dumps(value, ensure_ascii=False).replace("\\\\", "\\")
    return any(pattern.search(text) for pattern in PUBLIC_PATH_PATTERNS)


def validate_cases(config: dict[str, Any], *, allow_local_paths: bool = False) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    warnings: list[str] = []
    cases = config.get("cases") if isinstance(config, dict) else None
    if not isinstance(cases, list) or not cases:
        return ["config must contain non-empty cases[]"], {}

    ids: set[str] = set()
    categories: set[str] = set()
    taxonomy_covered = 0
    module_covered = 0
    missing_optional: dict[str, int] = {"expectedFilesToRead": 0, "expectedPatchTargets": 0, "forbiddenPatchTargets": 0}

    for index, case in enumerate(cases):
        label = str(case.get("id") or f"case[{index}]")
        missing = sorted(field for field in REQUIRED_FIELDS if not case.get(field))
        if missing:
            errors.append(f"{label}: missing required fields: {', '.join(missing)}")
        if label in ids:
            errors.append(f"{label}: duplicate id")
        ids.add(label)

        category = str(case.get("category") or "")
        categories.add(category)
        if category not in ALLOWED_CATEGORIES:
            errors.append(f"{label}: unsupported category: {category}")
        if not allow_local_paths and _contains_private_path(case):
            errors.append(f"{label}: contains local/private absolute path")
        if not any(case.get(key) for key in ("errorLog", "buildLog", "fixtureDir")):
            errors.append(f"{label}: must include errorLog, buildLog, or fixtureDir")

        for field in ("expectedModules", "expectedPatchTargets", "forbiddenPatchTargets"):
            if field in case and not isinstance(case.get(field), list):
                errors.append(f"{label}: {field} must be a list")
        for field in missing_optional:
            if field not in case:
                missing_optional[field] += 1

        text = _case_text(case)
        expected_subkind = str(case.get("expectedErrorSubkind") or "")
        if expected_subkind:
            route = route_error_action(text)
            actual = str(route.get("errorSubkind") or "")
            compatible = SUBKIND_COMPAT.get(expected_subkind, {expected_subkind})
            if actual not in compatible:
                errors.append(f"{label}: expectedErrorSubkind={expected_subkind}, route returned {actual}")
            taxonomy_covered += 1
            errors.extend(f"{label}: {issue}" for issue in validate_policy_coverage(case))

        expected_modules = [str(item) for item in case.get("expectedModules") or []]
        if expected_modules:
            detected = set(resolve_modules_from_error(text)) | set(resolve_modules_from_text(text))
            missing_modules = [module for module in expected_modules if module not in detected]
            if missing_modules:
                errors.append(f"{label}: expectedModules not detected: {', '.join(missing_modules)}")
            module_covered += 1

    summary = {
        "caseCount": len(cases),
        "categoriesCovered": sorted(categories),
        "missingOptionalFields": missing_optional,
        "taxonomyCoveredCases": taxonomy_covered,
        "moduleResolverCoveredCases": module_covered,
        "warnings": warnings,
    }
    return errors, summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate real-project holdout case config.")
    parser.add_argument("--config", type=Path, default=Path("config/rag_eval_real_project_holdout_cases.json"))
    parser.add_argument(
        "--allow-local-paths",
        action="store_true",
        help="Allow private local absolute paths for ignored .local.json live baseline configs.",
    )
    args = parser.parse_args()

    try:
        config = json.loads(args.config.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"failed to read {args.config}: {exc}", file=sys.stderr)
        return 1

    errors, summary = validate_cases(config, allow_local_paths=args.allow_local_paths)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if errors:
        print("Validation errors:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"Holdout validation OK: {summary['caseCount']} cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
