# Refactor Stage Contract (R0–R4)

## Keywords

refactor, incremental refactor, R0 discover, R1 boundary, R2 move implementation, R3 rewire, R4 cleanup, SSOT, responsibility split, architecture review

Korean: 리팩터, 점진적 리팩터, 아키텍처, 책임 분리, SSOT

## Purpose

Large refactors must proceed in **stages**. A local model must not rewrite many files in one turn. Each stage has allowed outputs and a done condition.

## Stage R0 — Discover (no code)

**Allowed:** file list, SSOT table, call graph notes, risk list, stage plan  
**Forbidden:** code edits, new UCLASS headers

Done when:

- Every moved state has one named owner
- Impact file list is ≤ 15 paths or explicitly phased
- User confirms scope or next stage

## Stage R1 — Boundary (headers / interfaces only)

**Allowed:** new interface, forward declarations, empty or minimal class shells, moved public API declarations  
**Forbidden:** large `.cpp` bodies, deleting old implementations

Done when:

- Project compiles or only expected link errors from deferred implementations
- Call direction is documented (who calls whom)

## Stage R2 — Move implementation (one class per turn)

**Allowed:** move one implementation cluster (one class or one subsystem)  
**Max files touched:** 3

Done when:

- UBT passes for the project or the remaining errors are isolated to R3

## Stage R3 — Rewire callers

**Allowed:** update call sites, includes, Build.cs for callers  
**Max files touched:** 3

Done when:

- UBT passes
- No duplicate SSOT for the same state

## Stage R4 — Cleanup

**Allowed:** remove dead code, stale includes, deprecated aliases  
**Max files touched:** 5

Done when:

- UBT passes
- No references remain to removed symbols (grep evidence)

## SSOT gate (all stages)

- Do not copy Health/Inventory/Cooldown into a second owner "for convenience"
- UI and Controller do not own gameplay state
- DataAsset holds tuning data, not runtime mutable state

## RAG citation gate

- Core Architecture rules ≠ Project-specific Lyra/example names
- If the plan reuses an example class name from RAG, label it as **project-specific evidence**, not a universal rule

## Answer format for R0

```markdown
## R0 Discover
### Goal
### SSOT table
| State | Owner | Readers |
### Impact files
### Risks
### Proposed next stage (R1/R2)
```
