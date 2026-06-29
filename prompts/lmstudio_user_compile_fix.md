# LM Studio user prompt — compile fix

Paste after [session bootstrap](lmstudio_session_bootstrap.md). Replace placeholders.

---

UBT / compile failed. Fix with tools only (no code in chat until `read_file`).

**Error log (excerpt):**

```
<PASTE_BUILD_OR_EDITOR_ERROR_HERE>
```

**Steps:**

1. `unreal_agent_plan` with `mode=compile_fix` and request summarizing the error above
2. Follow `toolPolicy` from the plan — typically `unreal_rag_search` `mode=compile_fix`, then `read_file` on the failing file
3. `replace_in_file` on **one file** per turn
4. `build_unreal_project`
5. Repeat until success or state blocker with log line

One MCP tool per turn.
