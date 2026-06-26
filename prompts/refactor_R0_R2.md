# Refactor R0–R2 — LM Studio User Prompt

Use when splitting responsibilities or moving implementation incrementally.

## Stage contract (pick ONE per turn)

| Stage | Output | Code? |
|-------|--------|-------|
| **R0** | SSOT table, impact files, risks | **No code** |
| **R1** | Interface/header boundaries only | Headers only |
| **R2** | Move **one** class implementation | ≤3 files |

## Tool order

1. `unreal_refactor_impact_scan` (symbol/class name)
2. `unreal_rag_search` with `mode=refactor_r0` … `refactor_r2`
3. `unreal_refactor_plan_validate` before any edit
4. `read_file` / `write_file` — **max 3 files**
5. `build_unreal_project`

## Hard limits

- R0: no `#include`, `UCLASS`, `GENERATED_BODY`, or function bodies
- R2/R3: one turn ≤ **3 files**; build must pass before next stage
- On build failure: fix only — **no architecture changes** that turn

## Sampling (Qwen 27B)

- temperature: **0.1–0.2**
- prefer short plans; cite project-specific names as placeholders when unsure
