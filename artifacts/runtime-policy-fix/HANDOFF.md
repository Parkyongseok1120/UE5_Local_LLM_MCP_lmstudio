# Revision 115 live handoff — FAIL

- Original start: `3c4375fd3041da16b4e6af721c17fa2c230d2f50`.
- Follow-up base: `868dedf8069c5b6d015794e32e5435ad540db219`.
- Implementation diff: `e64b7a8a6170cfd4dcdbd892706415be1b26e6ab1889f23344c803c44d701b19` (snapshot in implementation-diff.json). No commit/push.
- Version 0.4.68 / revision115, loaded swift-qwen3.8-27b, runtime context38912. UPDATE.bat exit0; installed SDK1.5.0 patch verified.

## Result

**FAIL: token stability and source integrity passed; early-fact recovery/final answer failed.**

Initial execution ea950628-1319-49c3-9cdc-fffb4833c728 completed46 predictions, 43 actual compactions and721157 cumulative exact templated input tokens after the first compaction. File-read compaction baselines were approximately16.3–16.6K, all below measured effectiveLOW. GUI supplied the single requested `계속해`; continuation74163b50-0fb4-4432-b5d5-15642b01f133 also failed archive admission. Combined48 predictions,45 compactions,754213 post-compaction cumulative exact input. This is summed actual prompt usage, not380K simultaneously resident context or unique source bytes.

All44 source observations (manifest1 + lock43) match source hashes, exact returned text, line counts and nextStartLine. Lock coverage1–680 is contiguous. At initial final report, only the final lock page673–680 remained as current raw input:1/44 original result pages. Historical coverage metadata is not raw content or semantic recall. Manifest Bridge value/hash and first three package name/version pairs were not recovered; final top-level package was also unverified. The source-byte integrity check cannot convert this into a quality PASS.

Both archive catalog requests failed with `Shared read-result budget exhausted. Split the read batch; use the returned evidence before requesting more.` No new unknown-channel warnings or new main.log error bytes. Two optional semantic calls across two executions, zero accepted summaries.

## Exact cause

With C38912, G8192, S2048, hard ceiling28672 and input16250, post-tool reserve8192 leaves4230 for a returned envelope. Source minimum2048 fit; archive minimum7168 did not. The central next-action check considered only the cheapest source reader, so it admitted a model input whose necessary historical read would later be denied by ToolBoundary. The isolated catalog tests did not exercise this budget/guard composition. Revision116 adds the production handler integration and fixes this boundary.

Water formula: H=C−G−S; R=max(minimumRead, min(4096,floor(H/8))); P=G for read tools; HIGH=min(configTrigger,H−R−P); configuredLOW=min(configTarget,floor(.75*HIGH)); effectiveLOW=max(configuredLOW, exact mandatory floor). Exact measurements and backend promptTokensCount matched for all48 predictions. Prefill milliseconds are time; cache token counters were unavailable and are not inferred.

## Implementation and tests

Responsibility mapping, moved/new modules and SDK installation details: IMPLEMENTATION.md. Commands/exit codes including intermediate fixture failures: test-results.json. Final regressions: compactor355, Unity35 (+4 skipped), Unreal247 (+1 skipped), Python1050 (+19 skipped), all exit0. Finite automated passes did not predict the live archive-budget failure.

## Remaining risk / rollback

Do not use revision115 as the successful380K result. Revision116 work is recorded separately in ../runtime-evidence-lifecycle-fix/. Archive has bounded128-record/8MiB/24h retention; source completeness and model quality remain separate. Receipt sanitization also caused pending-identity false positives on durable window commits; revision116 addresses this separately. SDK patch remains pinned to1.5.0.

Rollback point: base SHA above plus this folder's implementation.patch/new-file digests (or previous runtime-contract-fix snapshot for revision114). Preserve user UnitySymbolWorker binaries, project contents and conversations. Reinstall via UPDATE.bat and restart only affected integrations.
