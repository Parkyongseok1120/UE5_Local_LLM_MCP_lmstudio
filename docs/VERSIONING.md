# Versioning

This repository uses **independent version numbers** per component. They are not forced to match on every commit.

| Component | Version | Location | Meaning |
|-----------|---------|----------|---------|
| **Product** | 1.3.1 | [`installer/manifest.json`](../installer/manifest.json), [`README.md`](../README.md) | User-facing release label and installable product metadata |
| **Node agent MCP** | 0.3.20 | [`lmstudio-unreal-agent-mcp/package.json`](../lmstudio-unreal-agent-mcp/package.json) | npm package semver for the agent server |
| **Context compactor plugin** | 0.4.50 / revision 97 | [`lmstudio-context-compactor-plugin/package.json`](../lmstudio-context-compactor-plugin/package.json), [`manifest.json`](../lmstudio-context-compactor-plugin/manifest.json) | LM Studio generator plugin behavior, continuity memory, and installed revision |
| **Portable manifest** | 2.1.12 | [`installer/manifest.json`](../installer/manifest.json) | Portable ZIP bundle metadata (layout + required files) |

## When to bump

- **Product**: Published user-visible release notes, holdout/eval milestones, beta/stable tags, or the next bundled product release. Post-release component fixes on `main` use their independent component version until the next product release is cut.
- **Node package**: Breaking or notable agent MCP API/behavior changes.
- **Context compactor plugin**: Generator behavior, checkpoint schema, or installable plugin revision changes.
- **Portable manifest**: Portable ZIP layout, bundled file set, or installable component payload/closure changes.

## Release alignment

For every prerelease or stable tag, record all component versions in the release notes. They may differ. The immutable `v1.3.0-rc2` snapshot contains product 1.3.0 RC2, node 0.3.15, context compactor 0.4.39/revision 85, and portable manifest 2.1.3. The stable `v1.3.0` line aligns product 1.3.0, node 0.3.19, context compactor 0.4.47/revision 94, and portable manifest 2.1.8.

Stable `v1.3.1` aligns product 1.3.1, Node agent MCP 0.3.20, context compactor 0.4.50/revision 97, and portable manifest 2.1.11. Node 0.3.20 bounds model-facing edits to focused receipt-chained regions while preserving the existing CAS and two-file atomic transaction owners. Context compactor 0.4.50 keeps runtime-local file receipts and snapshot registration counters out of durable checkpoints while selecting sanitizer policy from known field provenance: user-authored payment-receipt and code-symbol language remains verbatim, whereas assistant/tool-derived executable receipt-reuse prose is neutralized. Canonical file observations remain `fresh_read_required`, and the MCP receipt/CAS boundary is unchanged. Portable manifest 2.1.11 closes the provenance-aware sanitizer and its semantic-preservation regressions into the installable package. The installer does not activate the host-owned chat plugin, and the transparent-compaction opt-in defaults OFF. The immutable `v1.3.0` snapshot remains unchanged.

Current `main` keeps the product label at 1.3.1 and advances only the portable
manifest to **2.1.12** for the post-release Python-free launcher seed path and
its two bundled platform helpers. The published `v1.3.1` tag and its 2.1.11
artifact remain immutable.

The human-facing label is `1.3.1`, published with the distinct tag `v1.3.1`. **Do not force-move** an existing stable, RC, or Beta tag. `portablePackage.releaseReady: true` means that the automated release, package, installer, safety, and cross-platform gates are required to pass for publication. It does not claim a clean-machine physical installer lifecycle on every host or universal compatibility across Unreal projects, engine builds, plugins, and editor runtimes. Repository release notes retain the exact evidence boundary.

Evaluation history tied to product versions remains in the development repository and is intentionally excluded from the portable Direct package.
