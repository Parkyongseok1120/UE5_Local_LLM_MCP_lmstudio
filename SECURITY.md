# Security Policy

## Supported scope

This stack runs **locally** on your machine. It is not a hosted service.

## Never commit

- `~/.lmstudio/mcp.json` or any file containing API keys (Tavily, cloud LLM keys, etc.)
- `config/workspace.json`, `config/agent-mcp.json`, or other machine-specific paths
- `data/` indexes, `*.sqlite`, build logs, or project snapshots from your Unreal projects
- Personal absolute paths in shipped config or eval files (use `$HOME` / `{REPO_ROOT}` placeholders)

Use the provided `*.template.json` and `*.example.json` files instead.

## MCP safety defaults

The **unreal-agent** MCP server restricts file access to `WORKSPACE_ROOT` and disables writes/commands unless explicitly enabled via environment variables (`ALLOW_WRITE`, `ALLOW_COMMANDS`, `ALLOW_UNREAL_BUILD`).

Review `lmstudio-unreal-agent-mcp/README.md` before enabling write or build tools in production project trees.

## Reporting issues

If you discover a security issue in this repository's tooling, open a private report to the maintainer or file a GitHub security advisory once the repo is public. Do not include proprietary Epic source or personal project code in public issues.

## Pre-push check

Run `scripts/installer_support/Verify-Oss-Ready.ps1` before your first public push.
