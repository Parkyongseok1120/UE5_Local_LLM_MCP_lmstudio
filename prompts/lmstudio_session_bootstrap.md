# LM Studio session bootstrap (first user message)

Paste as the **first user message** in every new LM Studio MCP chat before any coding request.

---

Session start. Perform **only** these steps, then reply `ready`:

1. `unreal_get_active_project` (unreal-rag)
2. `unreal_rag_health` (unreal-rag)
3. `get_workspace_info` (unreal-agent)

If the active project is not your target `.uproject`, call `unreal_set_active_project` with the correct path from `projectContext.uprojectPath`.

If `unreal_rag_health` returns `okForChat=false` or `chatAction=stop_and_report_rag_rebuild_required`, do **not** search project files for RAG repair scripts. Reply `rag_blocked` plus the reported `recommendedCommand` / `recommendedDoctorCommand`.

Do not analyze or answer coding questions until bootstrap is done. One tool per turn. Never print raw reasoning/control tokens such as `<|channel>thought`, `<channel|>`, or `<|tool_call>`.
