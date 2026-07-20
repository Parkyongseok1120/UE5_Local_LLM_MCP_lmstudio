# Unreal Context Compactor for LM Studio

This plugin is a model-facing context proxy. It keeps the visible LM Studio chat intact,
measures the actual tokenizer context, persists a deterministic checkpoint, and sends a
compacted `Chat` to the configured underlying local model when the remaining budget is low.

`targetModel` is optional when exactly one LLM is loaded; that model is selected automatically.
With zero or multiple loaded LLMs, the plugin fails with a list of candidates and asks for an
exact model key. The existing `mcp/unreal-agent` and `mcp/unreal-rag` plugins remain tool providers.

Version 0.3.2 is active by default (`enabled=true`, `observeOnly=false`). Strict tool-call
rejection is off by default, so existing LM Studio MCP behavior and multiple tool calls are
preserved. **Select `unreal-context-compactor` in the model dropdown for each chat that should use
the proxy. Selecting the underlying Qwen/GPT model directly bypasses compaction, even though the
plugin is installed.** Existing chats retain their previously selected model.

After sending one message through the proxy, run `npm run status` from this directory. A successful
check reports the routed target model and latest measured token budget. No activation evidence means
that the chat has not used `unreal-context-compactor` yet.


For local development, run lms dev from this directory. The plugin uses the existing mcp/unreal-agent and mcp/unreal-rag installations; it does not replace either MCP server.

## Installation file: Y

The normal user path is the root `INSTALL.bat` (or `install.sh` on Linux/macOS); choose the FULL
profile to install the MCP stack and context compactor together. The portable package restores the plugin dependencies,
runs its tests/build, installs it through LM Studio, and verifies the installed revision.

For a plugin-only repair, use the integrated CUSTOM profile with the `context_compactor` component.
It checks Node/npm and the LM Studio CLI, restores locked dependencies, runs unit tests and the
TypeScript build, then installs the plugin through `lms dev --install -y`.
