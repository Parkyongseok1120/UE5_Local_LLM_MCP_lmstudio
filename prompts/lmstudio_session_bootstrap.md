# LM Studio session bootstrap (first user message)

Paste as the **first user message** in every new LM Studio MCP chat before any coding request.

---

Session start. Perform **only** these steps, then reply `ready`:

1. `unreal_get_active_project` (unreal-rag)
2. `unreal_rag_health` (unreal-rag)
3. `get_workspace_info` (unreal-agent)

Allowed project file tools are only the `unreal-rag` and `unreal-agent` MCP tools. Never call `run_javascript`, `js-code-sandbox`, `Deno.readTextFile`, `Deno.writeTextFile`, Node `fs`, or browser/code-sandbox tools for project file I/O. Those tools are not rooted at the active Unreal project. Use `read_file_range`, `read_file`, and `replace_in_file`; use `write_file` only for brand-new files.

`write_file` is create-only: it refuses to overwrite existing files, so use `replace_in_file` to edit them and never retry `write_file` after a "file already exists" or timeout error — verify state with `read_file` first. After a successful write, continue automatically; stop and wait for the user only on a timeout, validation failure/rollback, a failed tool call, or a repeating error.

If the active project is not your target `.uproject`, call `unreal_set_active_project` with the correct path from `projectContext.uprojectPath`.

If `unreal_rag_health` returns `okForChat=false` or `chatAction=stop_and_report_rag_rebuild_required`, do **not** search project files for RAG repair scripts. Reply `rag_blocked` plus the reported `recommendedCommand` / `recommendedDoctorCommand`.

Do not analyze or answer coding questions until bootstrap is done. One tool per turn. Never print raw reasoning/control tokens such as `<|channel>thought`, `<channel|>`, or `<|tool_call>`.
