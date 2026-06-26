# Turn 1 — Inventory (review mode, thinking ON)

**Code writes forbidden.** Review only — no `.h/.cpp` implementation.

## Steps

1. `unreal_project_architecture` — load PAB summary (≤2k chars in chat).
2. `unreal_agent_session` with `mode=review`, explicit genres if known.
3. `search_files` (unreal-agent) for symbols mentioned in the user request.
4. **Mandatory `read_file`** on every file the user listed or PAB flagged for the review scope.
5. Optional: `unreal_rag_search mode=review, hybrid=false, top_k=4..6` for guideline evidence only.

## Output format

- Inventory table: class/subsystem/component/DataAsset already in project (from PAB + read_file).
- Files read list with one-line purpose per file.
- Explicit **no code / no refactor implementation** confirmation.

See also: `prompts/lmstudio_review_turn2_findings.md`, `RAG_Project_Guidelines/09_Quality_Gates_For_Unreal_Review.md`.
