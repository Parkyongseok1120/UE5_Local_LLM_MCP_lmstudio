# Turn 3 — Design delta (review mode, thinking OFF or low temp)

**Code writes forbidden** unless user explicitly asked for implementation.

## Steps

1. Turn 2 findings must be PASS after `unreal_review_claim_validate`.
2. Propose **delta-only** design improvements — reuse existing classes/subsystems/DataAssets.
3. `unreal_genre_scope_validate` if genre adapter applies (action_combat, shooter, etc.).

## Required sections

### Existing (do not duplicate)

List systems already in project that satisfy or partially satisfy the goal.

### Proposed (minimal delta)

Only changes that add capability without re-inventing Existing items.

### DoNotDuplicate

Explicit list: classes/subsystems/DataAssets the model must NOT propose again.

## Hard limits

- No compile-ready `.h/.cpp` blocks in Design Review Mode.
- Pseudocode or function signatures only when necessary.
- No unverified compile-ready claims without UBT/static validation mention.

See also: `RAG_Project_Guidelines/07_Design_Review_Scoring_Rubric.md`.
