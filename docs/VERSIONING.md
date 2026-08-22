# Versioning

This repository uses **independent version numbers** per component. They are not forced to match on every commit.

| Component | Version | Location | Meaning |
|-----------|---------|----------|---------|
| **Product** | 1.3.0 | [`installer/manifest.json`](../installer/manifest.json), [`README.md`](../README.md) | User-facing release label and installable product metadata |
| **Node agent MCP** | 0.3.20 | [`lmstudio-unreal-agent-mcp/package.json`](../lmstudio-unreal-agent-mcp/package.json) | npm package semver for the agent server |
| **Context compactor plugin** | 0.4.49 / revision 96 | [`lmstudio-context-compactor-plugin/package.json`](../lmstudio-context-compactor-plugin/package.json), [`manifest.json`](../lmstudio-context-compactor-plugin/manifest.json) | LM Studio generator plugin behavior, continuity memory, and installed revision |
| **Portable manifest** | 2.1.10 | [`installer/manifest.json`](../installer/manifest.json) | Portable ZIP bundle metadata (layout + required files) |

## When to bump

- **Product**: Published user-visible release notes, holdout/eval milestones, beta/stable tags, or the next bundled product release. Post-release component fixes on `main` use their independent component version until the next product release is cut.
- **Node package**: Breaking or notable agent MCP API/behavior changes.
- **Context compactor plugin**: Generator behavior, checkpoint schema, or installable plugin revision changes.
- **Portable manifest**: Portable ZIP layout or bundled file set changes.

## Release alignment

For every prerelease or stable tag, record all component versions in the release notes. They may differ. The immutable `v1.3.0-rc2` snapshot contains product 1.3.0 RC2, node 0.3.15, context compactor 0.4.39/revision 85, and portable manifest 2.1.3. The stable `v1.3.0` line aligns product 1.3.0, node 0.3.19, context compactor 0.4.47/revision 94, and portable manifest 2.1.8.

Post-release `main` carries Node agent MCP 0.3.20, context compactor 0.4.49/revision 96, and portable manifest 2.1.10. Node 0.3.20 bounds model-facing edits to focused receipt-chained regions while preserving the existing CAS and two-file atomic transaction owners. Context compactor 0.4.49 removes runtime-local file receipts and snapshot registration counters from durable checkpoints, keys file observations by canonical project and path, and marks them `fresh_read_required`; the MCP receipt/CAS boundary is unchanged. Portable manifest 2.1.10 closes the new sanitizer, file-observation, and transcript-regression dependencies into the installable package. The installer does not activate the host-owned chat plugin, and the transparent-compaction opt-in defaults OFF. This does not change the immutable `v1.3.0` tag snapshot or publish a new product release.

The human-facing label is `1.3.0`, published with the distinct tag `v1.3.0`. **Do not force-move** any existing RC/Beta tag. `portablePackage.releaseReady: true` means that the automated release, package, installer, safety, and cross-platform gates are required to pass for publication. It does not claim a clean-machine physical installer lifecycle on every host or universal compatibility across Unreal projects, engine builds, plugins, and editor runtimes. Repository release notes retain the exact evidence boundary.

Evaluation history tied to product versions remains in the development repository and is intentionally excluded from the portable Direct package.
