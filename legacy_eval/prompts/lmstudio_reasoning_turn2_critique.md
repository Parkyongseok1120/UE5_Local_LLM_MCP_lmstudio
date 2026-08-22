# Turn 2 — Critique (thinking ON, temperature 0.6)

**Code writes forbidden.** Self-review the Turn 1 plan.

## Steps

1. `read_file` on files listed in the plan (unreal-agent).
2. `unreal_genre_scope_validate` — genre Must Have vs plan + existing code.
3. `unreal_runtime_config_check` — if fixing runtime/config (preview gaps).
4. `unreal_rag_search mode=refactor_r0` for any missing evidence.
5. Update plan: mark PASS/FAIL per slice; list assumptions (max 3).

## Output

- Gap list with severity (blocker / should-fix / later).
- Revised slice list for Turn 3 (≤3 files, ≤1 concern per slice).
- Confirm plan_validate still passes.

See also: `prompts/runtime_debug_session.md`.
