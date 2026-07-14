# Turn 2 — Findings (review mode, thinking ON)

**Code writes forbidden.** Grounded findings only — no new architecture proposals yet.

## Steps

1. For each file from Turn 1, produce findings tied to **evidence**.
2. For logic / design findings: read the sibling `.h` UENUM and field comments **before** treating a `.cpp` early-return as a bug.
3. Each major claim must cite `path:line` from `read_file` OR grep evidence from project source. Prefer citing **both** `.h` contract and `.cpp` implementation for logic claims.
4. Every finding must set `verdict`: `Bug` | `ByDesign` | `Ambiguous` | `NeedsRuntimeProof`.
5. `unreal_review_claim_validate` — batch validate negative claims ("missing", "unused", "no Subsystem") **and** logic-missing claims ("누락", "로직 없음", "does nothing"). Revise on `by_design_contract` / `header_contract_unread`.
6. If claim validation FAILs, revise findings before ending turn.
7. Optional: `unreal_rag_search mode=review` for rubric/guideline support (not a substitute for file evidence).

## Output format

Per file:

| Severity | Finding | Verdict | Evidence (path:line) | Risk |
|----------|---------|---------|----------------------|------|

Severity: blocker / should-fix / later.

Verdict meanings:

- `Bug` — implementation contradicts an explicit header/runtime contract with evidence.
- `ByDesign` — early return / no-op / socket look-at matches header comments (do not "fix").
- `Ambiguous` — header and implementation disagree; ask or require runtime proof, do not invent a patch.
- `NeedsRuntimeProof` — cannot prove without PIE/log evidence.

## Forbidden

- "Subsystem으로 분리" when PAB already lists equivalent Subsystem.
- "DataAsset 없음" when `*DataAsset` exists in PAB or source.
- Generic textbook patterns without project-specific evidence.
- Labeling intentional AuthoredWorld / authored-asset no-ops as "missing SetActorTransform" without reading the enum docs.
- Declaring Bug from a non-default enum combination without stating that the defaults differ.

See also: `prompts/lmstudio_review_turn3_design.md`.
