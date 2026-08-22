# Turn 1 — Inventory (review mode, thinking ON)

**Code writes forbidden.** Review only — no `.h/.cpp` implementation.

## Steps

1. `unreal_get_active_project` — confirm the review target.
2. **Essential path (default):** `search_files` on `project://Source` for symbols in the user request, then **mandatory `read_file` / `read_file_range`** on every listed or matched file. Optional: `unreal_rag_search mode=review, hybrid=false, top_k=4..6`.
3. **Extended only** (when `unreal_project_architecture` appears in `tools/list`): call it for a PAB summary (≤2k chars in chat), then still read the target files.
4. Optional: `unreal_agent_session` with `mode=review`, explicit genres if known.
5. For behavior named in the request, inventory the entry point, decision/dispatch layer, state owner, mutation API, configuration source, and observer/test. Mark each surface `declared`, `constructed`, `registered`, `reachable`, `called`, `mutates`, or `observed`; do not collapse these states.
6. Do **not** invent inventory from memory when PAB is unavailable — cite `search_files` / `read_file` evidence only.

## Output format

- Inventory table: class/subsystem/component/DataAsset already in project (from PAB when available + `read_file`).
- Behavior coverage table: requested flow → entry → decision → final mutation/side effect → observer, with gaps left explicit.
- Files read list with one-line purpose per file.
- Explicit **no code / no refactor implementation** confirmation.

See also: `prompts/lmstudio_review_turn2_findings.md`, `RAG_Project_Guidelines/09_Quality_Gates_For_Unreal_Review.md`.
