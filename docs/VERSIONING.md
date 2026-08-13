# Versioning

This repository uses **independent version numbers** per component. They are not forced to match on every commit.

| Component | Version | Location | Meaning |
|-----------|---------|----------|---------|
| **Product** | 1.3.0 RC1 | [`installer/manifest.json`](../installer/manifest.json), [`README.md`](../README.md) | User-facing release label and installable product metadata |
| **Node agent MCP** | 0.3.14 | [`lmstudio-unreal-agent-mcp/package.json`](../lmstudio-unreal-agent-mcp/package.json) | npm package semver for the agent server |
| **Context compactor plugin** | 0.4.33 / revision 79 | [`lmstudio-context-compactor-plugin/package.json`](../lmstudio-context-compactor-plugin/package.json), [`manifest.json`](../lmstudio-context-compactor-plugin/manifest.json) | LM Studio generator plugin behavior, route telemetry, and installed revision |
| **Portable manifest** | 2.1.3 | [`installer/manifest.json`](../installer/manifest.json) | Portable ZIP bundle metadata (layout + required files) |

## When to bump

- **Product**: User-visible release notes, holdout/eval milestones, beta/stable tags, or bundled product behavior.
- **Node package**: Breaking or notable agent MCP API/behavior changes.
- **Context compactor plugin**: Generator behavior, checkpoint schema, or installable plugin revision changes.
- **Portable manifest**: Portable ZIP layout or bundled file set changes.

## Release alignment

For every prerelease or stable tag, record all component versions in the release notes. They may differ (for RC1: product 1.3.0 RC1, node 0.3.4, context compactor revision 18, portable manifest 2.1.3) as long as the release notes explain the relationship.

The human-facing label is `1.3.0 RC1` (this draft; formerly staged as RC3 before the Beta4/Beta5 relabel). Use the SemVer-compatible identifier `1.3.0-rc.1` in metadata. **Do not force-move** the existing Git tag `v1.3.0-rc.1`: it remains the historical alias for **1.3.0 Beta4** (the previously published RC1). When this draft becomes `releaseReady`, publish a new ship tag/release without rewriting that historical tag. Former published label `1.3.0 RC2` (tag `v1.3.0-rc.2`) was renamed in docs/GitHub title to **1.3.0 Beta5**. Until release gates close, treat this RC1 as a draft label, not a shippable release. Apple Silicon physical FULL install status is recorded in [`docs/Release_Notes_1_3_0_RC1.md`](Release_Notes_1_3_0_RC1.md).

See also [`docs/Version_Performance_History.md`](Version_Performance_History.md) for evaluation history tied to product versions.
