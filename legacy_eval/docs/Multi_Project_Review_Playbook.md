# Multi-Project Review Playbook

Grounded architecture/code review across multiple `.uproject` repos.

## Project switch checklist

1. `.\rag.ps1 pick-project` — select active `.uproject`
2. `.\rag.ps1 sync-active-project` — index project source
3. `.\rag.ps1 collect-project-architecture` — rebuild PAB
4. `.\rag.ps1 doctor` — verify index + PAB freshness

## Review workflow (3-turn)

| Turn | Prompt | Tools |
|------|--------|-------|
| 1 | `prompts/lmstudio_review_turn1_inventory.md` | PAB, `read_file`, `search_files` — **no writes** |
| 2 | `prompts/lmstudio_review_turn2_findings.md` | Findings with `path:line` citations |
| 3 | `prompts/lmstudio_review_turn3_design.md` | Existing / Proposed / DoNotDuplicate |

After Turn 2: `unreal_review_claim_validate` (MCP) or `eval-project-review` (static fixtures).

## CLI shortcut

```powershell
.\rag.ps1 review-project -Question "Review targeting and cinematic flow"
```

## Korean reviews

Optional: use EXAONE for plan/critique (Phase 5 `UNREAL_RAG_MODEL_PROFILE`) + Qwen for execute.

## Eval

```powershell
.\rag.ps1 eval-project-review   # MJS fixture cases, no LLM
```
