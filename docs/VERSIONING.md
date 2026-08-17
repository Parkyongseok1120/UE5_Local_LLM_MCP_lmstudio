# Versioning

This repository uses **independent version numbers** per component. They are not forced to match on every commit.

| Component | Version | Location | Meaning |
|-----------|---------|----------|---------|
| **Product** | 1.3.0 RC3 | [`installer/manifest.json`](../installer/manifest.json), [`README.md`](../README.md) | User-facing release label and installable product metadata |
| **Node agent MCP** | 0.3.17 | [`lmstudio-unreal-agent-mcp/package.json`](../lmstudio-unreal-agent-mcp/package.json) | npm package semver for the agent server |
| **Context compactor plugin** | 0.4.42 / revision 88 | [`lmstudio-context-compactor-plugin/package.json`](../lmstudio-context-compactor-plugin/package.json), [`manifest.json`](../lmstudio-context-compactor-plugin/manifest.json) | LM Studio generator plugin behavior, route telemetry, and installed revision |
| **Portable manifest** | 2.1.5 | [`installer/manifest.json`](../installer/manifest.json) | Portable ZIP bundle metadata (layout + required files) |

## When to bump

- **Product**: User-visible release notes, holdout/eval milestones, beta/stable tags, or bundled product behavior.
- **Node package**: Breaking or notable agent MCP API/behavior changes.
- **Context compactor plugin**: Generator behavior, checkpoint schema, or installable plugin revision changes.
- **Portable manifest**: Portable ZIP layout or bundled file set changes.

## Release alignment

For every prerelease or stable tag, record all component versions in the release notes. They may differ. The immutable `v1.3.0-rc2` snapshot contains product 1.3.0 RC2, node 0.3.15, context compactor 0.4.39/revision 85, and portable manifest 2.1.3. RC3 packages the subsequently verified hardening as product 1.3.0 RC3, node 0.3.16, context compactor 0.4.40/revision 86, and portable manifest 2.1.5.

The human-facing label is `1.3.0 RC3`. Publish this candidate with the distinct tag `v1.3.0-rc3`. **Do not force-move** any existing RC/Beta tag. RC3 is a GitHub prerelease; `portablePackage.releaseReady` stays `false` until Windows physical installation and the remaining stable-release gates close. See [`docs/Release_Notes_1_3_0_RC3.md`](Release_Notes_1_3_0_RC3.md).

See also [`docs/Version_Performance_History.md`](Version_Performance_History.md) for evaluation history tied to product versions.
