# Versioning

This repository uses **independent version numbers** per component. They are not forced to match on every commit.

| Component | Version | Location | Meaning |
|-----------|---------|----------|---------|
| **Product** | 1.3.0 RC3 | [`installer/manifest.json`](../installer/manifest.json), [`README.md`](../README.md) | User-facing release label and installable product metadata |
| **Node agent MCP** | 0.3.4 | [`lmstudio-unreal-agent-mcp/package.json`](../lmstudio-unreal-agent-mcp/package.json) | npm package semver for the agent server |
| **Context compactor plugin** | 0.3.5 / revision 18 | [`lmstudio-context-compactor-plugin/package.json`](../lmstudio-context-compactor-plugin/package.json), [`manifest.json`](../lmstudio-context-compactor-plugin/manifest.json) | LM Studio generator plugin behavior, route telemetry, and installed revision |
| **Portable manifest** | 2.1.3 | [`installer/manifest.json`](../installer/manifest.json) | Portable ZIP bundle metadata (layout + required files) |

## When to bump

- **Product**: User-visible release notes, holdout/eval milestones, beta/stable tags, or bundled product behavior.
- **Node package**: Breaking or notable agent MCP API/behavior changes.
- **Context compactor plugin**: Generator behavior, checkpoint schema, or installable plugin revision changes.
- **Portable manifest**: Portable ZIP layout or bundled file set changes.

## Release alignment

For every prerelease or stable tag, record all component versions in the release notes. They may differ (for RC3: product 1.3.0 RC3, node 0.3.4, context compactor revision 18, portable manifest 2.1.3) as long as the release notes explain the relationship.

The human-facing label is `1.3.0 RC3`. Use the SemVer-compatible identifier `1.3.0-rc.3` in tags or systems that do not accept spaces; the recommended Git tag is `v1.3.0-rc.3` **only after** `installer/manifest.json` → `portablePackage.releaseReady` is `true` (Windows physical install verified and remaining release gates closed). Do not rewrite or move earlier RC tags. Until then, treat RC3 as a draft label, not a shippable release. Apple Silicon physical FULL install status is recorded in [`docs/Release_Notes_1_3_0_RC3.md`](Release_Notes_1_3_0_RC3.md).

See also [`docs/Version_Performance_History.md`](Version_Performance_History.md) for evaluation history tied to product versions.
