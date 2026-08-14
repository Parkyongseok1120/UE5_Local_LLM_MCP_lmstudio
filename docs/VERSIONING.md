# Versioning

This repository uses **independent version numbers** per component. They are not forced to match on every commit.

| Component | Version | Location | Meaning |
|-----------|---------|----------|---------|
| **Product** | 1.3.0 RC2 | [`installer/manifest.json`](../installer/manifest.json), [`README.md`](../README.md) | User-facing release label and installable product metadata |
| **Node agent MCP** | 0.3.15 | [`lmstudio-unreal-agent-mcp/package.json`](../lmstudio-unreal-agent-mcp/package.json) | npm package semver for the agent server |
| **Context compactor plugin** | 0.4.39 / revision 85 | [`lmstudio-context-compactor-plugin/package.json`](../lmstudio-context-compactor-plugin/package.json), [`manifest.json`](../lmstudio-context-compactor-plugin/manifest.json) | LM Studio generator plugin behavior, route telemetry, and installed revision |
| **Portable manifest** | 2.1.3 | [`installer/manifest.json`](../installer/manifest.json) | Portable ZIP bundle metadata (layout + required files) |

## When to bump

- **Product**: User-visible release notes, holdout/eval milestones, beta/stable tags, or bundled product behavior.
- **Node package**: Breaking or notable agent MCP API/behavior changes.
- **Context compactor plugin**: Generator behavior, checkpoint schema, or installable plugin revision changes.
- **Portable manifest**: Portable ZIP layout or bundled file set changes.

## Release alignment

For every prerelease or stable tag, record all component versions in the release notes. They may differ (for RC2: product 1.3.0 RC2, node 0.3.15, context compactor 0.4.39/revision 85, portable manifest 2.1.3) as long as the release notes explain the relationship.

The human-facing label is `1.3.0 RC2`. Publish this candidate with the distinct tag `v1.3.0-rc2`. **Do not force-move** the existing tags `v1.3.0-rc.1` and `v1.3.0-rc.2`: they remain historical aliases for **1.3.0 Beta4** and **1.3.0 Beta5**. The no-dot `rc2` tag intentionally preserves that history. RC2 is a GitHub prerelease; `portablePackage.releaseReady` stays `false` until Windows physical installation and the remaining stable-release gates close. See [`docs/Release_Notes_1_3_0_RC2.md`](Release_Notes_1_3_0_RC2.md).

See also [`docs/Version_Performance_History.md`](Version_Performance_History.md) for evaluation history tied to product versions.
