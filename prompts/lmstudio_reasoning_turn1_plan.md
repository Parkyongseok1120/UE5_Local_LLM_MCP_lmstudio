# Turn 1 — Plan (thinking ON, temperature 0.6)

**Code writes forbidden.** Use MCP knowledge tools only.

## Steps

1. `unreal_agent_session` with explicit `genres` when known (e.g. `action_combat` for soulslike/melee).
2. `unreal_rag_search` with `mode=refactor_r0`, `hybrid=false`, `top_k=4..6`.
3. Draft R0 plan: SSOT table, impacted files (≤8 total, slices of ≤3 for execute), risks, build notes.
4. `unreal_refactor_plan_validate` stage=R0 — fix issues before ending turn.
5. Optional: `unreal_genre_scope_validate` if genre adapter applies.

## Output format

- Genre + Must Have gap list (if action_combat: dodge OR block, stagger, camera, combat component).
- Slice order (which 1–3 files in Turn 3).
- Explicit **no code** confirmation.

See also: `prompts/refactor_R0_R2.md`, `prompts/prototype_component.md`.
