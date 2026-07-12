# Versioning

This repository uses **independent version numbers** per component. They are not forced to match on every commit.

| Component | Version | Location | Meaning |
|-----------|---------|----------|---------|
| **Product** | 1.2.5 | [`README.md`](../README.md) | User-facing release label and changelog narrative |
| **Node agent MCP** | 0.3.0 | [`lmstudio-unreal-agent-mcp/package.json`](../lmstudio-unreal-agent-mcp/package.json) | npm package semver for the agent server |
| **Portable manifest** | 1.0.0 | [`installer/manifest.json`](../installer/manifest.json) | Portable ZIP bundle metadata (layout + required files) |

## When to bump

- **Product (README)**: User-visible release notes, holdout/eval milestones, or stable tag.
- **Node package**: Breaking or notable agent MCP API/behavior changes.
- **Portable manifest**: Portable ZIP layout or bundled file set changes.

## Release alignment

For a **stable tag**, record all three versions in the release notes. They may differ (e.g. product 1.2.5, node 0.3.0, manifest 1.0.0) as long as the release notes explain the relationship.

See also [`docs/Version_Performance_History.md`](Version_Performance_History.md) for evaluation history tied to product versions.
