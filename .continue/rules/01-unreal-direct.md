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
- Read an existing file before changing it and use its returned SHA-256 for an
  exact patch. Re-read on conflict; never overwrite a concurrent change.
- Use `write_file` only for new files. Use the bounded atomic bundle for
  multi-file changes. Keep path containment, size/output limits, per-path locks,
  atomic recovery, delete confirmation, and command allowlists intact.
- Semantic/static findings are advisory. A permitted build can run immediately,
  and real UBT/UHT/compiler output is the authoritative diagnostic.
- Do not add C++ namespaces unless the target Unreal project explicitly requires
  them. Preserve Unreal reflection and `*.generated.h` rules.
- Echo a `repeatReceipt` only when this conversation retained the prior full
  result and deliberately wants a concise unchanged-result acknowledgement.
- Report what was actually read, changed, built, or blocked. Do not manufacture
  success or a compulsory next action.
