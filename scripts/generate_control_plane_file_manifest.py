#!/usr/bin/env python3
"""Generate an auditable classification for every source-controlled repository file."""

from __future__ import annotations

import json
import subprocess
from collections import Counter
from pathlib import Path

from atomic_io import atomic_write_text


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "CONTROL_PLANE_FILE_MANIFEST.json"
CATEGORIES = (
    "request classification and planning",
    "durable task state",
    "canonical transition reducer",
    "Node execution adapter",
    "RAG MCP",
    "LM Studio Compactor",
    "source discovery and evidence",
    "mutation journal and rollback",
    "static validation",
    "build and automation",
    "synthesis preparation and commit",
    "UI delivery",
    "protocol and schemas",
    "runtime identity and packaging",
    "installer",
    "tests and CI",
    "documentation or non-runtime data",
)


def repository_files() -> list[str]:
    completed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    paths = {line.strip().replace("\\", "/") for line in completed.stdout.splitlines() if line.strip()}
    paths.add(OUTPUT.relative_to(ROOT).as_posix())
    return sorted(paths)


def categories_for(path: str) -> list[str]:
    lowered = path.casefold()
    name = Path(path).name.casefold()
    categories: set[str] = set()
    if lowered.startswith("tests/") or "/test/" in lowered or lowered.startswith(".github/"):
        categories.add("tests and CI")
    if lowered.startswith(("docs/", "rag_project_guidelines/", "game_design_docs/", "prompts/", "skills/", ".continue/")):
        categories.add("documentation or non-runtime data")
    if lowered.endswith((".png", ".jpg", ".jpeg", ".gif", ".md", ".txt")):
        categories.add("documentation or non-runtime data")
    if lowered.startswith("installer/") or name in {"install.py", "install.sh", "install.bat"} or "installer_support" in lowered:
        categories.add("installer")
    if any(token in lowered for token in ("runtime_identity", "build_integrated_package", "package.json", "package-lock.json", "manifest.json", "requirements", "release")):
        categories.add("runtime identity and packaging")
    if lowered.startswith("config/") or "schema" in lowered or "protocol" in lowered or "state_registry" in lowered or "state-registry" in lowered:
        categories.add("protocol and schemas")
    if lowered.startswith("lmstudio-context-compactor-plugin/"):
        categories.add("LM Studio Compactor")
    if "generator" in name or "checkpoint-store" in lowered:
        categories.add("UI delivery")
    if lowered.startswith("lmstudio-unreal-agent-mcp/src/"):
        categories.add("Node execution adapter")
    if any(token in lowered for token in ("unreal_rag_mcp", "rag_context", "query_rag", "mcp_tool_compact", "rag_server")):
        categories.add("RAG MCP")
    if any(token in lowered for token in ("phase_tool_router", "control_transition", "control_state_machine")):
        categories.add("canonical transition reducer")
    if any(token in lowered for token in ("task_api", "task-auth", "task_continuity", "task_gate_history", "checkpoint", "job_store", "durable")):
        categories.add("durable task state")
    if any(token in lowered for token in ("intent", "plan", "orchestrator", "classification", "semantic_ambiguity", "target_resolver")):
        categories.add("request classification and planning")
    if any(token in lowered for token in ("evidence", "read_file", "read-symbol", "search", "source_extension", "synthesis_readiness", "repo_audit")):
        categories.add("source discovery and evidence")
    if any(token in lowered for token in ("mutation", "rollback", "atomic_io", "write-lock", "edit-bundle", "validate-write")):
        categories.add("mutation journal and rollback")
    if any(token in lowered for token in ("static_valid", "semantic_refactor_guard", "code_sketch_claim", "change_impact")):
        categories.add("static validation")
    if any(token in lowered for token in ("build", "automation", "unreal-detect", "runtime_verify", "job_")):
        categories.add("build and automation")
    if any(token in lowered for token in ("synthesis", "commit_synthesis", "ack_synthesis")):
        categories.add("synthesis preparation and commit")
    if not categories:
        categories.add("documentation or non-runtime data")
    return [category for category in CATEGORIES if category in categories]


def detailed_audit(path: str, categories: list[str]) -> tuple[bool, str]:
    lowered = path.casefold()
    if "tests and CI" in categories:
        return False, "Verification-only code; it can detect regressions but is not loaded by the production control plane."
    if lowered.startswith(("docs/", "rag_project_guidelines/", "game_design_docs/", "prompts/", "skills/", ".continue/")) or Path(path).suffix.casefold() in {".md", ".png", ".jpg", ".jpeg", ".gif", ".txt"}:
        return False, "Documentation, prompt guidance, fixture output, or media; it cannot persist or authorize canonical task transitions."
    if lowered.startswith("tools/"):
        return False, "Developer tooling is not imported by the installed Agent/RAG/Compactor runtime."
    production_prefix = lowered.startswith(("scripts/", "config/", "lmstudio-unreal-agent-mcp/src/", "lmstudio-context-compactor-plugin/src/", "installer/"))
    root_runtime = "/" not in lowered and Path(path).suffix.casefold() in {".py", ".ps1", ".bat", ".sh", ".json"}
    if production_prefix or root_runtime:
        return True, ""
    return False, "Repository metadata or non-runtime data is not imported by production control components."


def main() -> int:
    entries = []
    counts: Counter[str] = Counter()
    detailed_count = 0
    for path in repository_files():
        categories = categories_for(path)
        detailed, reason = detailed_audit(path, categories)
        counts.update(categories)
        detailed_count += int(detailed)
        entries.append(
            {
                "path": path,
                "primaryCategory": categories[0],
                "categories": categories,
                "detailedRuntimeAudit": detailed,
                **({"exclusionReason": reason} if reason else {}),
            }
        )
    payload = {
        "schemaVersion": 1,
        "scope": "all git-index files plus files staged for this correction",
        "categories": list(CATEGORIES),
        "fileCount": len(entries),
        "detailedRuntimeAuditCount": detailed_count,
        "categoryCounts": {category: counts[category] for category in CATEGORIES},
        "entries": entries,
    }
    atomic_write_text(OUTPUT, json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": OUTPUT.relative_to(ROOT).as_posix(), "fileCount": len(entries)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
