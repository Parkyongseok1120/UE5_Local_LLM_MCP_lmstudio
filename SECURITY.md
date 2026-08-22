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

The **unreal-agent** MCP server disables writes and commands unless explicitly
enabled through `ALLOW_WRITE`, `ALLOW_COMMANDS`, and `ALLOW_UNREAL_BUILD`. Reads
are contained to `WORKSPACE_ROOT` plus the exact selected project's root. An
explicit existing `.uproject` may be selected outside configured discovery/search
roots, but that authorizes only that exact project's containment boundary; it
does not authorize a parent directory or a same-name clone.

Mutation scope is narrower than read scope: `Source/**`, `Config/**`, plugin
source plus the exact plugin descriptor, and the exact selected `.uproject`.
Generated/cache/VCS areas such as `Saved`, `Binaries`, `Intermediate`,
`DerivedDataCache`, `.git`, and `.vs` are denied. Lexical and resolved-real-path
checks reject `..`, symlink, and junction escapes.

File-version receipts are opaque compare-and-swap evidence, not authority. A
receipt is scoped to the canonical selected project, canonical file, observed
version, and reliable transport session (or Strict conversation where
applicable), and expires or may be evicted. It cannot be replayed for another
file, session, or same-name clone; those attempts fail with
`FILE_SNAPSHOT_SCOPE_MISMATCH`. Missing, expired, evicted, or runtime-invalid
evidence fails with `FILE_SNAPSHOT_INVALID`, while changed content fails with
`FILE_VERSION_CONFLICT`. Callers may instead supply the exact current SHA-256.
Ownerless transports do not receive an unsafe automatic "latest read" lookup.
Delete additionally requires a matching proposal token and explicit approval.

Review `lmstudio-unreal-agent-mcp/README.md` before enabling write or build tools in production project trees.

## RAG provenance and local data

Project-scoped RAG rows must carry the canonical `.uproject` parent
(`project_root`) and descriptor stem (`project`). Same-name clones remain distinct,
and legacy rows migrate only when prior path/descriptor evidence identifies one
owner. Ambiguous or missing provenance fails closed. Index manifests bind each
generation to one engine association/version; a call never merges sibling engine
shards.

Managed indexes live under `<state-home>/indexes/<namespace>/` (default state
home `~/.evidence-first`) and may contain excerpts from licensed Epic source or
private project files. Treat the entire state home, custom external indexes,
Editor exports, build logs, and generated receipts as local sensitive data; do
not publish them as release assets or issue attachments.

## Reporting issues

If you discover a security issue in this repository's tooling, open a private report to the maintainer or file a GitHub security advisory once the repo is public. Do not include proprietary Epic source or personal project code in public issues.

## Pre-push check

Run `scripts/installer_support/Verify-Oss-Ready.ps1` before your first public push.
