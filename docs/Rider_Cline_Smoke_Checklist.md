# Rider + Cline Direct MCP Smoke Checklist

Use this after installing the Cline component. It is a capability matrix, not a
mandatory workflow: run only the read, mutation, build, or refresh checks that
are safe and relevant in the selected project.

## Prerequisites

- [ ] Node.js 20+ and Python 3.10+
- [ ] A supported Unreal project and its associated engine/toolchain
- [ ] JetBrains Rider with Unreal support, if Rider builds/debugging are wanted
- [ ] Cline extension or CLI with a reliable tool-calling model/provider

## Install verification

```powershell
.\rag.ps1 doctor
```

Development checkouts can additionally run
`.\scripts\installer_support\Verify-UnrealMcp.ps1`; it is not part of the
portable runtime.

- [ ] `unreal-rag` points to `scripts/unreal_rag_direct.py`.
- [ ] `unreal-agent` points to `src/direct-server.js`.
- [ ] No installer placeholders remain in the two MCP entries.
- [ ] Existing unrelated MCP entries were preserved.
- [ ] [`.clinerules`](../.clinerules) and the
  [Direct Cline prompt](../prompts/cline_unreal_agent_system.md) describe the
  same model-owned Direct contract.

## Read-only capability checks

These checks must not create a task, route lock, plan, or project-wide owner.

- [ ] `unreal_get_active_project` returns the shared `.uproject` or a clear
  unselected observation.
- [ ] If needed, `unreal_set_active_project` accepts one exact existing
  `.uproject`; otherwise use an exact per-call project selector.
- [ ] `unreal_rag_health` and `unreal_rag_rebuild_status` return one consistent
  observational envelope without a compulsory next tool.
- [ ] `get_workspace_info` reports the agent's resolved roots and enabled safety
  switches.
- [ ] A bounded `read_file`/`read_file_range` works on a known selected-project
  source file and returns both a SHA-256 hash and opaque `fileVersionReceipt`.
- [ ] A second conversation can read/search without cancelling or resuming work
  from the first conversation; it cannot borrow the first conversation's scoped
  file snapshot receipt when a reliable owner is present.

## Optional mutation check

Run this only in a disposable line/comment and only when `ALLOW_WRITE=1` is an
intentional choice.

- [ ] Re-read the file immediately before editing.
- [ ] `replace_in_file` uses the current `fileVersionReceipt`, exact old text,
  and `expectedOccurrences=1`. A valid raw `expectedHash` remains a compatibility
  alternative.
- [ ] A deliberately stale receipt or hash is rejected with
  `FILE_VERSION_CONFLICT` without overwriting the file.
- [ ] Revert the smoke edit by explicitly passing the receipt returned by the
  first mutation; the server never selects same-session evidence automatically,
  and an avoidable re-read is not required between successful consecutive edits.
- [ ] A missing, expired, or wrong-scope receipt fails closed with
  `FILE_SNAPSHOT_REQUIRED`, `FILE_SNAPSHOT_INVALID`, or
  `FILE_SNAPSHOT_SCOPE_MISMATCH` as applicable.

Atomic bundles are available for one or two bounded existing-file patches; they
are optional and must preflight each patch's explicit receipt or compatible raw
hash before committing, preserving atomic CAS and rollback
guarantees. A deletion proposal returns its own short-lived approval token and
`fileVersionReceipt`; deletion requires those values (or a compatible raw hash),
the enabled source-deletion switch, and explicit user approval.

## Optional validation and build checks

- [ ] `static_validate_project`, if called, reports advisory findings and does
  not issue a write/build permission token.
- [ ] Rider can build the exact selected target/toolchain, or
  `build_unreal_project` runs immediately when `ALLOW_UNREAL_BUILD=1`.
- [ ] Passing `target=Editor` resolves the selected project's canonical,
  configured preferred, or sole discovered custom Editor target; an explicit
  non-Editor target is left unchanged.
- [ ] The result contains the real outcome and bounded log path/first useful
  error; no prior static-validation certificate is required. Build and Automation
  share the bounded process runner, timeout terminates the process tree, and
  `fullLogPath` may be a bounded head/tail projection with omitted-byte metadata.

## Pass criteria

The checks you deliberately selected succeed or return one truthful bounded
error. Unselected destructive or build checks are not failures. No check should
require a workflow session, a server-selected next tool, or manual authorization
JSON copied from another conversation.

If Cline still contains unresolved installer placeholders, preview a repair:

```powershell
python install.py --profile custom --components codex,lmstudio,unreal,cline --cline-settings C:\path\to\cline_mcp_settings.json --dry-run
```
