# 1.3.0 RC1 — draft / not shippable

**Status: NOT RELEASE-READY.** Product label is aligned to **1.3.0 RC1** (this draft was briefly staged as RC3 before earlier published RC1/RC2 labels were renamed to Beta4/Beta5). This candidate is **not** approved for public distribution until Windows physical install validation and the remaining blockers below are closed. `installer/manifest.json` → `portablePackage.releaseReady` remains **`false`**.

## Component versions (aligned)

| Component | Version |
|-----------|---------|
| Product | 1.3.0 RC1 (`1.3.0-rc.1`) |
| Portable manifest | 2.1.3 |
| Node agent MCP | 0.3.4 |
| Context compactor | 0.3.5 / revision 18 |

## Hygiene focus in this draft

- Removed personal campaign dumps (local AI prompts/sessions, private project automation reports, marathon logs) from the product tree.
- Portable package builder switched to an **allowlist** of ship directories/files with forbidden-inventory assertion; forbidden marker escapes and exact-name unit tests were hardened.
- OSS scanner covers tracked `.log` / `.json` / `.txt` / `.md` and cross-platform absolute home paths.
- Tracked text BOM/encoding gates cover `.js`, `.json`, `.md`, `.py`, `.ps1`, `.sh`, `.yml`, `.yaml`.
- macOS support split: **Apple Silicon FULL install verified on physical hardware**; Intel Mac blocks LM Studio stack early; custom Codex/Cline-only remains allowed.
- `install.sh` continues to require host **Python 3.10+** before bootstrap (documented as an explicit prerequisite).

## Apple Silicon macOS FULL install (physical log)

**Result: PASS** (`installer result ok: true`). Host: darwin-arm64.

| Check | Result |
|-------|--------|
| darwin-arm64 runtime detection | PASS |
| Python 3.12 / Node 20 / npm / PowerShell 7 bootstrap | PASS |
| Context Compactor suite | PASS (45/45) |
| LM Studio plugin install and activation | PASS |
| UE 5.8 auto-discovery | PASS |
| Full RAG indexing | PASS (88,829 chunks) |
| Evidence-first MCP smoke | PASS |
| Final installer result | `ok: true` |

## Known limitations (separate from Apple Silicon install PASS)

| Item | Status |
|------|--------|
| Unreal Editor asset metadata headless export | **FAIL** |
| LM Studio API server connectivity | **UNVERIFIED** — API server was not running during the test |
| Windows physical install | **not yet verified** |
| Installer signing / notarization | **not claimed** |

## Still blocking distribution

- Windows physical install certification incomplete.
- Remaining limitation gates above (headless asset export; LM Studio API connectivity when the server is running).
- No claim of measured live-model uplift for RC1.
- Git history may still contain personal path / private campaign artifacts on older Develop tips; history rewrite is out of scope for this hygiene track.

Do not advertise RC1 as deployable until `installer/manifest.json` → `portablePackage.releaseReady` is set `true` after Windows physical verification and the remaining release gates pass.
