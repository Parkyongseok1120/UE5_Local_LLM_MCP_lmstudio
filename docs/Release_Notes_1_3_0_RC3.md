# 1.3.0 RC3 — draft / not shippable

**Status: NOT RELEASE-READY.** Product label is aligned to **1.3.0 RC3**, but this candidate is **not** approved for public distribution until install validation and remaining blockers below are closed.

## Component versions (aligned)

| Component | Version |
|-----------|---------|
| Product | 1.3.0 RC3 (`1.3.0-rc.3`) |
| Portable manifest | 2.1.3 |
| Node agent MCP | 0.3.4 |
| Context compactor | 0.3.5 / revision 18 |

## Hygiene focus in this draft

- Removed personal campaign dumps (local AI prompts/sessions, private project automation reports, marathon logs) from the product tree.
- Portable package builder switched to an **allowlist** of ship directories/files with forbidden-inventory assertion.
- OSS scanner covers tracked `.log` / `.json` / `.txt` / `.md` and cross-platform absolute home paths.
- macOS support split: Apple Silicon installer path uncertified; Intel Mac blocks LM Studio stack early; custom Codex/Cline-only remains allowed.
- `install.sh` continues to require host **Python 3.10+** before bootstrap (documented as an explicit prerequisite).

## Still blocking distribution

- Physical Windows / Apple Silicon install certification incomplete.
- Git history still contains personal path / private campaign artifacts on the Develop tip prior to the hygiene commit; release branch history rewrite may be required before tagging.
- Apple Silicon LM Studio path is not notarized/certified.
- No claim of measured live-model uplift for RC3.

Do not advertise RC3 as deployable until `installer/manifest.json` → `portablePackage.releaseReady` is set `true` after those gates pass.
