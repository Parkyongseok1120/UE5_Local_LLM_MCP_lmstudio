# Turn 2 — Findings (review mode, thinking ON)

**Code writes forbidden.** Grounded findings only — no new architecture proposals yet.

## Steps

1. For each file from Turn 1, produce findings tied to **evidence**.
2. For logic / design findings: read the sibling `.h` UENUM and field comments **before** treating a `.cpp` early-return as a bug.
3. If a claim depends on `Super::`, a framework lifecycle, or framework API semantics, read the direct base implementation or authoritative version-matched documentation. Project callsites and user-authored RAG are insufficient proof of framework behavior.
4. Trace behavioral claims as `entry → decision/dispatch → mutation/side_effect → observer` where available. Structured stages may use only those six exact names in causal order, and every entry needs `stageStatus`: `present` | `expected_missing` | `unknown`. Put construction details in the evidence/observation instead of inventing a stage. A component declaration, constructor registration, event, request, or base call is not proof of the final mutation.
5. Compare symmetric paths when applicable (player/enemy, client/server, success/failure, start/recovery) and trace declared DataAsset/config fields to runtime readers.
6. Each major claim must cite `path:line` evidence. Prefer both contract and implementation evidence; add framework-source evidence for framework claims.
7. Every finding must set `verdict`: `Bug` | `ByDesign` | `Ambiguous` | `NeedsRuntimeProof`, plus `severity`, `claimType`, and `proofLevel`. Use one claim type: `existence` | `behavior` | `framework_semantics` | `wiring` | `state_transition` | `data_flow` | `architecture` | `codegen`.
8. Challenge the leading explanation once: record counterevidence or an alternative cause checked, then scan for a more severe failure in the same user-visible flow.
9. `unreal_review_claim_validate` — submit major findings as structured evidence packets. Legacy strings remain allowed for simple negative/existence claims. Revise on any FAIL reason, including `framework_semantics_unverified`, `behavior_path_incomplete`, or `presence_not_wiring`.
10. Optional: `unreal_rag_search mode=review` for rubric/guideline support (not a substitute for file evidence).

## Output format

Per finding:

| Severity | Finding | Verdict | ClaimType | ProofLevel | Evidence | BehaviorPath | Counterevidence | Unknowns |
|----------|---------|---------|-----------|------------|----------|--------------|-----------------|----------|

Severity: `P0` (primary behavior cannot work/critical corruption), `P1` (major common-path failure), `P2` (lower-frequency correctness or extensibility risk), or `P3` (optional/style).

Verdict meanings:

- `Bug` — implementation contradicts an explicit header/runtime contract with evidence.
- `ByDesign` — early return / no-op / socket look-at matches header comments (do not "fix").
- `Ambiguous` — header and implementation disagree; ask or require runtime proof, do not invent a patch.
- `NeedsRuntimeProof` — cannot prove without PIE/log evidence.

Proof levels: `Proposed` | `SourceVerified` | `StaticVerified` | `BuildVerified` | `TestVerified` | `RuntimeVerified`.

Evidence kinds: `requirement` | `project_source` | `framework_source` | `official_docs` | `static_analysis` | `build` | `test` | `runtime` | `generated_metadata`. A verified proof level must have its matching evidence kind; for example, `BuildVerified` requires `build` evidence and cannot be inferred from a source citation.

Structured validator packet shape:

```json
{
  "claim": "...",
  "verdict": "Bug",
  "severity": "P0",
  "proofLevel": "SourceVerified",
  "claimType": "wiring",
  "evidence": [{"kind": "project_source", "location": "Source/X.cpp:10", "observation": "..."}],
  "behaviorPath": [
    {"stage": "entry", "stageStatus": "present", "location": "Source/A.cpp:10", "symbol": "Request"},
    {"stage": "decision", "stageStatus": "present", "location": "Source/B.cpp:20", "symbol": "Validate"},
    {"stage": "mutation", "stageStatus": "present", "location": "Source/C.cpp:30", "symbol": "Apply"}
  ],
  "counterEvidence": [{"kind": "project_source", "location": "Source/D.cpp:40", "observation": "Alternative path checked"}],
  "unknowns": []
}
```

## Forbidden

- "Subsystem으로 분리" when PAB already lists equivalent Subsystem.
- "DataAsset 없음" when `*DataAsset` exists in PAB or source.
- Generic textbook patterns without project-specific evidence.
- Treating `present`, `constructed`, or `registered` as proof that the requested runtime path reaches a mutation.
- Explaining framework behavior from memory without direct framework-source or authoritative-doc evidence.
- Labeling intentional AuthoredWorld / authored-asset no-ops as "missing SetActorTransform" without reading the enum docs.
- Declaring Bug from a non-default enum combination without stating that the defaults differ.

See also: `prompts/lmstudio_review_turn3_design.md`.
