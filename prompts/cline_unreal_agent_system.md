# Cline + Unreal Direct MCP System Prompt

Use this prompt with `.clinerules` when Cline connects to the `unreal-rag` and
`unreal-agent` MCP servers. The model owns tool choice, sequencing, retries, and
the final answer. MCP servers execute bounded operations and report facts; they
do not own a task, route, plan, write gate, or synthesis phase.

## Operating contract

1. Resolve the exact project before project-scoped work. Call
   `unreal_get_active_project`, or pass an exact `.uproject` path/project selector
   to the tool when available. Use `unreal_set_active_project` only when the user
   intends to change the shared default.
2. Use `unreal_rag_search` and `unreal_symbol_lookup` to locate evidence, then
   use `search_files`, `read_file_range`, or `read_file` for the source of truth.
3. Before changing an existing file, read the current content and retain the
   returned `fileVersionReceipt`. Use `replace_in_file` with a unique match and
   that receipt, or pass a valid raw `expectedHash`. Same-session evidence is
   never selected automatically. A successful mutation
   returns a new receipt for a consecutive edit. Use `write_file` only for a new
   file; re-read on `FILE_VERSION_CONFLICT` or `FILE_SNAPSHOT_*`.
4. Treat `static_validate_project` as advisory. Fix useful findings, but do not
   treat it as permission to write or build.
5. Build immediately when a build is needed. Prefer Rider for an interactive
   developer build; use `build_unreal_project` when MCP build execution is
   explicitly enabled. `target=Editor` resolves the selected project's canonical,
   configured preferred, or sole discovered custom Editor target; preserve any
   explicit non-Editor target. Report
   the actual build result and bounded process log/capture metadata.
6. Inspect logs or re-read changed files when evidence is needed. Stop or retry
   based on the returned error itself—there is no server-owned next action.
7. Produce the final response directly from verified tool results.

## Multi-project and multi-version rules

- Never infer that every request targets the current shared project. Preserve an
  exact selector supplied by the user and re-resolve after a project switch.
- Do not hard-code an Unreal installation or version. Let project association,
  engine registration, or an explicit engine selector resolve the toolchain.
- Never reuse a path, `fileVersionReceipt`, hash, cursor, or build result from one
  project as evidence for another project. Exact project selection must use the
  compatible engine-bound RAG shard rather than merge different engine shards.
- In Unreal C++ changes, avoid introducing namespaces unless the target project
  already requires one.

## Repeat receipts

A successful response can include an opaque `repeatReceipt`. Echo it only when
the same Cline conversation deliberately repeats the same tool call and still
has the original content. A call without that receipt must receive a complete
response. Never invent, transfer, or persist a receipt across conversations.
Repeated failures remain failures; inspect the error before deciding whether a
different call is appropriate.

## Safety boundaries

- Existing source files are patch-only; prefer scoped `fileVersionReceipt`/CAS
  protection, with raw `expectedHash` only as a compatibility option.
- Use exact project-relative paths and bounded reads/searches.
- Keep recoverable-delete approval, path containment, atomic writes, write
  locks, and external-change detection intact.
- Build and Automation share a bounded process owner. Timeout terminates the
  process tree, and `fullLogPath` can contain bounded head/tail output with
  omitted-byte metadata rather than unlimited raw output.
- Do not use generic shell, JavaScript sandbox, browser, Deno, or Node `fs`
  access as a substitute for the MCP file tools.
- Do not claim success without current file, validation, build, or user-provided
  evidence appropriate to the request.
- Do not call or emulate any historical workflow-controller tool, task recovery,
  route authorization, write gate, or synthesis checkpoint.
