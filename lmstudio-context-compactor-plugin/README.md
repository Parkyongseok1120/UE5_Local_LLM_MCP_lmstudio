# Unreal Context Compactor for LM Studio

This LM Studio plugin compacts chat context transparently while the model selected in
LM Studio remains the reasoning, sampling, and tool-use owner. It measures context
pressure with that selected model, keeps the latest real user request verbatim, and
replaces older history with deterministic factual memory only when the remaining
budget is low.

The plugin does not select a target model, replace the selected model's sampling
settings, filter MCP tools, or interpret Unreal workflow state. The existing
`mcp/unreal-agent` and `mcp/unreal-rag` integrations remain independent tool
providers. Because compaction is limited to chat history, the same plugin can be
used across Unreal Engine versions and projects.

Version 0.4.50 defaults to disabled mode. The top-level LM Studio chat-plugin switch
and the nested `Enable transparent compaction` switch are independent, and both must
be explicitly enabled for compaction to run. `observeOnly` can be enabled in the
plugin settings to measure pressure without changing the model-facing history. Soft
compaction retains recent complete turns. Hard compaction keeps the current user
turn separately and emits bounded `[Direct continuity state v2]` factual memory:
the active objective, continuation antecedent for an elliptical follow-up, active
project, current work status, unresolved items, archived objectives, recent tool
outcomes, and relevant file/build facts. File observations are keyed by canonical
project descriptor/root plus canonical path and retain the observed SHA-256, time,
and operation. Every compacted observation is marked
`mutationSnapshotState: fresh_read_required`. Runtime-local
`fileVersionReceipt` values, snapshot registration counters, and executable
receipt instructions are removed from current and inherited checkpoints. A receipt
may remain usable only in a recent, uncompressed tool result from the live runtime;
it never becomes durable continuity memory. Sanitization uses the field provenance
known during continuity assembly: user-authored payment `receipt`, `ReceiptActor`,
`FPaymentReceipt`, `영수증`, and `리시트` language remains intact, while operational
assistant/tool prose that directs reuse of a file-mutation receipt is neutralized.
Generic receipt vocabulary is not a capability. This state cannot plan, route,
authorize tools, require a next call, or declare the request complete. If exact
token measurement is unavailable, a message-count threshold provides a
conservative fallback.

## Use in LM Studio

1. Load and select the actual LLM. Qwen 3.8 27B is the current validated recommendation; Muse Glimmer is under testing and is not yet a validated recommendation.
2. Leave the top-level `codex/unreal-context-compactor` switch OFF for the default
   setup. The installer does not enable this LM Studio-owned state; verify it is OFF
   in every new or existing chat.
3. Leave the nested `Enable transparent compaction` switch OFF. It does not control
   top-level chat activation and has no effect while the top-level switch is off.
4. Only for a deliberate per-chat compaction test, enable both switches and keep
   using the actual LLM as the chat model.

The integrated installer installs and pins the plugin so it remains available, but
does not activate it for a chat. It deliberately avoids rewriting LM Studio's private
per-chat conversation storage, which is version-specific. Existing chat activation
must therefore be turned off in that chat's plugin panel.

## Status and development

Run `npm run status` to verify that the direct prediction-loop source layout is
complete and that `src/index.ts` registers the expected handler. This is a source
verification only; it never reports runtime activation based on file presence.
`npm run test:active` intentionally exits with code 3 while the current LM Studio
hook cannot provide durable activation evidence.

For local development, run:

```text
npm ci
npm test
npm run dev
```

The supported installation path is the repository root `INSTALL.bat` on Windows or
`install.sh` on macOS/Linux. Select a profile that includes `context_compactor`, or
use that component in a CUSTOM installation for a plugin-only repair. The installer
restores locked dependencies, runs the focused tests and TypeScript build, installs
the plugin with `lms dev --install -y`, and verifies its owner/name/revision identity
plus a non-empty compiled `.lmstudio/production.js` bundle.
