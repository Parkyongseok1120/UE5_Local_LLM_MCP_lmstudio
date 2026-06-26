#!/usr/bin/env python
"""Validate genre adapter Must Have scope for plans and optional project scans."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

GENRE_RULES: dict[str, dict[str, Any]] = {
    "action_combat": {
        "label": "Action Combat Prototype Adapter",
        "planKeywords": {
            "camera": (r"spring\s*arm|camera|3rd\s*person|third\s*person|3인칭", "3rd person camera"),
            "combat_component": (r"combat\s*component|UCombatComponent|USoulsCombat", "combat component"),
            "stagger": (r"stagger|경직|hit\s*react", "stagger or hit reaction"),
            "dodge_or_block": (r"dodge|block|roll|회피|방어|parry|패리", "dodge OR block"),
            "attack_trace": (r"attack\s*trace|trace\s*component|line\s*trace|sphere\s*sweep", "attack trace"),
        },
        "projectScan": {
            "camera": (r"SpringArmComponent|UCameraComponent", "*.h"),
            "combat_component": (r"UActorComponent|CombatComponent", "*.h"),
            "stagger": (r"Stagger|OnStaggered", "*"),
            "dodge_or_block": (r"Dodge|Block|Roll|Parry|Evade", "*"),
            "attack_trace": (r"AttackTrace|LineTraceSingle|SphereTrace", "*.cpp"),
        },
    },
}


def _check_text(text: str, rules: dict[str, tuple[str, str]]) -> dict[str, Any]:
    lowered = text.lower()
    items: list[dict[str, Any]] = []
    for key, (pattern, label) in rules.items():
        found = bool(re.search(pattern, text, re.IGNORECASE) or re.search(pattern, lowered))
        items.append({"id": key, "label": label, "found": found})
    return {"items": items, "passCount": sum(1 for i in items if i["found"]), "total": len(items)}


def _scan_project(project_root: Path, scan_rules: dict[str, tuple[str, str]]) -> dict[str, Any]:
    combined = ""
    if (project_root / "Source").is_dir():
        for path in (project_root / "Source").rglob("*"):
            if path.suffix.lower() not in {".h", ".cpp", ".cs"}:
                continue
            if "Intermediate" in path.parts:
                continue
            try:
                combined += path.read_text(encoding="utf-8", errors="replace") + "\n"
            except OSError:
                continue
    return _check_text(combined, scan_rules)


def validate_genre_scope(
    genre: str,
    plan_text: str = "",
    project_root: str | Path | None = None,
    *,
    min_pass_ratio: float = 0.6,
) -> dict[str, Any]:
    genre_id = str(genre or "").strip().lower().replace(" ", "_")
    spec = GENRE_RULES.get(genre_id)
    if not spec:
        return {
            "ok": False,
            "genre": genre_id,
            "issues": [f"Unknown genre adapter: {genre_id}"],
            "warnings": [],
            "passed": [],
        }

    issues: list[str] = []
    warnings: list[str] = []
    passed: list[str] = []
    plan_result: dict[str, Any] | None = None
    project_result: dict[str, Any] | None = None

    if plan_text.strip():
        plan_result = _check_text(plan_text, spec["planKeywords"])
        for item in plan_result["items"]:
            if item["found"]:
                passed.append(f"Plan mentions {item['label']}.")
            else:
                warnings.append(f"Plan missing {item['label']}.")

    if project_root:
        root = Path(project_root).expanduser().resolve()
        if root.suffix.lower() == ".uproject":
            root = root.parent
        project_result = _scan_project(root, spec["projectScan"])
        for item in project_result["items"]:
            if item["found"]:
                passed.append(f"Project has {item['label']}.")
            else:
                issues.append(f"Project missing Must Have: {item['label']}.")

    plan_ratio = 1.0
    if plan_result and plan_result["total"]:
        plan_ratio = plan_result["passCount"] / plan_result["total"]
    proj_ratio = 1.0
    if project_result and project_result["total"]:
        proj_ratio = project_result["passCount"] / project_result["total"]

    effective_ratio = min(plan_ratio, proj_ratio) if project_result else plan_ratio
    ok = len(issues) == 0 and effective_ratio >= min_pass_ratio

    if genre_id == "action_combat" and project_result:
        dodge = next((i for i in project_result["items"] if i["id"] == "dodge_or_block"), None)
        if dodge and not dodge["found"]:
            issues.append("Action Combat Must Have: dodge OR block not found in project.")

    return {
        "ok": ok,
        "genre": genre_id,
        "label": spec["label"],
        "issues": issues,
        "warnings": warnings,
        "passed": passed,
        "planCheck": plan_result,
        "projectCheck": project_result,
        "passRatio": round(effective_ratio, 3),
    }


def main() -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Validate genre adapter scope.")
    parser.add_argument("--genre", default="action_combat")
    parser.add_argument("--plan-text", default="")
    parser.add_argument("--plan-file", default="")
    parser.add_argument("--project-root", default="")
    args = parser.parse_args()
    plan = args.plan_text
    if args.plan_file:
        plan = Path(args.plan_file).read_text(encoding="utf-8-sig")
    result = validate_genre_scope(args.genre, plan, args.project_root or None)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
