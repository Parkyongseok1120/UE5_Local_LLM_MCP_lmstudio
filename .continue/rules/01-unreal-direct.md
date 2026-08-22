---
name: Unreal Direct MCP
alwaysApply: true
description: Use Unreal MCP servers as bounded capabilities while the selected model owns the workflow.
---

# Unreal Direct MCP

The selected model owns interpretation, tool choice, call order, retry decisions,
stopping, and the final answer. `unreal-rag` and `unreal-agent` provide bounded
capabilities; their suggestions and advisories never force a next tool.

- Use `unreal-rag` for exact project selection, indexed evidence, symbol lookup,
  health, rebuild status, and explicitly requested refreshes.
- Use `unreal-agent` for project discovery, bounded reads, CAS-safe edits,
  advisory static validation, UBT/UHT builds, Automation tests, and logs.
- There is no mandatory bootstrap, planner, task, route, compile loop, or
  validation-before-build sequence. Choose only useful capabilities.
- Resolve the exact `.uproject` when multiple projects are possible. Never
  hard-code an Unreal version or installation.
- Read an existing file before changing it and prefer its `fileVersionReceipt`
  for an exact patch. A valid raw `expectedHash` remains compatible, and a
  reliable same-session latest snapshot may resolve automatically. Use a
  successful mutation's new receipt for a consecutive edit. Re-read on
  `FILE_VERSION_CONFLICT` or `FILE_SNAPSHOT_*`; never overwrite a concurrent
  change.
- Use `write_file` only for new files. Use the bounded atomic bundle for
  multi-file changes, with current version evidence for every existing file.
  Keep path containment, size/output limits, per-path locks, atomic recovery,
  delete confirmation, and command allowlists intact.
- Semantic/static findings are advisory. A permitted build can run immediately,
  and real UBT/UHT/compiler output is the authoritative diagnostic.
- `target=Editor` resolves the selected project's canonical, configured
  preferred, or sole discovered custom Editor target; explicit non-Editor
  targets are unchanged. Build and Automation
  share the bounded process runner, and `fullLogPath` may be a bounded head/tail
  projection rather than unlimited raw output.
- Do not add C++ namespaces unless the target Unreal project explicitly requires
  them. Preserve Unreal reflection and `*.generated.h` rules.
- Echo a `repeatReceipt` only when this conversation retained the prior full
  result and deliberately wants a concise unchanged-result acknowledgement.
- Report what was actually read, changed, built, or blocked. Do not manufacture
  success or a compulsory next action.
