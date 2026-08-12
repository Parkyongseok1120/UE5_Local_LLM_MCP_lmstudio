# Unreal Context Compactor for LM Studio

This plugin is a model-facing context proxy. It keeps the visible LM Studio chat intact,
measures the actual tokenizer context, persists a deterministic checkpoint, and sends a
compacted `Chat` to the configured underlying local model when the remaining budget is low.

`targetModel` is optional when exactly one LLM is loaded; that model is selected automatically.
With zero or multiple loaded LLMs, the plugin fails with a list of candidates and asks for an
exact model key. The existing `mcp/unreal-agent` and `mcp/unreal-rag` plugins remain tool providers.

Version 0.4.13 is active by default (`enabled=true`, `observeOnly=false`). It persists a checkpoint
before every prediction and buffers model text/tool calls until LM Studio confirms a safe stop.
Context-limit and max-output truncations are discarded instead of being presented as completed work.
Explicit reasoning fragments stream as live progress while final text and tool calls remain atomic.
The latest server-owned `activeTools` route is also intersected with the combined LM Studio catalog
before prediction, preventing stale cross-MCP tool schemas from causing avoidable rejected calls.
Strict tool-call rejection remains off by default, so multiple valid tool calls are preserved.

> **Important — you must select this plugin as the chat model**  
> 1. Load the underlying LLM (e.g. Qwen) once; leave it loaded.  
> 2. **Create a new chat** (existing chats keep the old selection).  
> 3. Choose **`unreal-context-compactor`** in that chat’s **model dropdown**.  
> Selecting the underlying Qwen/GPT model directly **bypasses** compaction even though the plugin is installed.

After sending one message through the proxy, run `npm run status` from this directory on Windows,
macOS, or Linux. A successful check requires fresh routing evidence (30 minutes by default), and
reports the routed target model and latest measured token budget. Historical stale evidence cannot
make an inactive chat look active.

The proxy is advisory for normal AGENT installs. Selecting Qwen/GPT directly bypasses compaction but does not disable server-authorized writes. A strict proxy requirement is an explicit LM Studio-only administrator policy; other frontends must use their own continuity proof.

Per-session storage keeps the newest 20 checkpoint generations, three rolled event files, and the active files. A bounded daily GC removes completed/inactive sessions after 90 days and cancelled sessions after 30 days. Active/running sessions and sessions containing quarantined `*.corrupt-*` artifacts are never auto-deleted. Retention can be increased with `LMS_CONTEXT_COMPACTOR_COMPLETED_RETENTION_DAYS`, `LMS_CONTEXT_COMPACTOR_CANCELLED_RETENTION_DAYS`, and `LMS_CONTEXT_COMPACTOR_INACTIVE_RETENTION_DAYS`.


For local development, run lms dev from this directory. The plugin uses the existing mcp/unreal-agent and mcp/unreal-rag installations; it does not replace either MCP server.

## Installation file: Y

The normal user path is the root `INSTALL.bat` (or `install.sh` on Linux/macOS); choose the FULL
profile to install the MCP stack and context compactor together. The portable package restores the plugin dependencies,
runs its tests/build, installs it through LM Studio, and verifies the installed revision.

For a plugin-only repair, use the integrated CUSTOM profile with the `context_compactor` component.
It checks Node/npm and the LM Studio CLI, restores locked dependencies, runs unit tests and the
TypeScript build, then installs the plugin through `lms dev --install -y`.
