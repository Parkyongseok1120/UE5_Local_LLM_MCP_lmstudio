# LM Studio user prompt - compile fix

Paste after [session bootstrap](lmstudio_session_bootstrap.md). Replace placeholders.

---

UBT / compile failed. Fix with tools only. Do not answer with code until the failing file has been read through MCP.

**Error log (excerpt):**

```
<PASTE_BUILD_OR_EDITOR_ERROR_HERE>
```

**Steps:**

1. `unreal_agent_plan` with `mode=compile_fix` and a request summarizing the error above
2. If a build response is available, its `recovery.requiredNextTool` overrides the generic plan: copy its args exactly, then read the failing range once
3. `replace_in_file` on **one file** per turn
4. `build_unreal_project`
5. Repeat until success or state the blocker with the exact log line

One MCP tool per turn.

Never use `run_javascript`, `js-code-sandbox`, `Deno.readTextFile`, `Deno.writeTextFile`, or Node `fs` for project file edits.

**Build.cs / module dependency fixes:** when the error or user request requires a missing module (e.g. `GameplayTags`), you must edit the relevant `*.Build.cs` and return a concrete patch. Do not only explain the dependency.
