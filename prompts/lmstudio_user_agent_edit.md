# LM Studio user prompt — single-file agent edit

Paste after [session bootstrap](lmstudio_session_bootstrap.md).

---

Edit request — use MCP tools only.

**Goal:** <ONE_SENTENCE_TASK>

**Target file:** `Source/<Module>/.../<File>.cpp` (relative to active project)

**Rules:**

1. `unreal_agent_plan` first (`mode=agent_edit` or `auto`)
2. `unreal_rag_search` then `read_file` on the target file
3. Apply change with **`replace_in_file`** only (no full file in chat)
4. `build_unreal_project` if C++ or Build.cs changed

One tool per turn. Max 2 files for this task; prefer **1 file** per patch turn.
