# Turn 2 — Findings (review mode, thinking ON)

**Code writes forbidden.** Grounded findings only — no new architecture proposals yet.

## Steps

1. For each file from Turn 1, produce findings tied to **evidence**.
2. Each major claim must cite `path:line` from `read_file` OR grep evidence from project source.
3. `unreal_review_claim_validate` — batch validate negative claims ("missing", "unused", "no Subsystem").
4. If claim validation FAILs, revise findings before ending turn.
5. Optional: `unreal_rag_search mode=review` for rubric/guideline support (not a substitute for file evidence).

## Output format

Per file:

| Severity | Finding | Evidence (path:line) | Risk |
|----------|---------|----------------------|------|

Severity: blocker / should-fix / later.

## Forbidden

- "Subsystem으로 분리" when PAB already lists equivalent Subsystem.
- "DataAsset 없음" when `*DataAsset` exists in PAB or source.
- Generic textbook patterns without project-specific evidence.

See also: `prompts/lmstudio_review_turn3_design.md`.
