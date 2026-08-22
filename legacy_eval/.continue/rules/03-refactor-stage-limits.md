---
name: Refactor Stage Limits
alwaysApply: true
description: Enforce R0-R4 incremental refactor stages and per-turn file limits.
---

# Refactor Stage Limits

- Architecture/refactor requests start at **R0 Discover** unless the user names a later stage.
- **R0**: plan only — call `refactor_plan_validate` and `refactor_impact_scan`. No code edits.
- **R1**: headers/interfaces only, max **3 files**.
- **R2/R3**: max **3 files** per turn, UBT after edits.
- **R4**: cleanup only, max **5 files**.
- Prototype requests use `prototype_component` or `prototype_subsystem` mode — **one type**, max **3 files**, build required.
