# Revision 116 — evidence budget and lifetime repair

Original start3c4375fd3041da16b4e6af721c17fa2c230d2f50; follow-up base868dedf8069c5b6d015794e32e5435ad540db219. Version0.4.69, revision116. No commit/push. User UnitySymbolWorker binaries excluded.

## Confirmed defects and owners

| Boundary | Cause | Correction / owner |
|---|---|---|
| Budget → dispatch | Source minimum certified affordability of a larger required archive operation | BudgetBroker minimum takes max(source minimum, archive minimum) when scoped archived evidence exists; round-input selects measured read-only recovery before dispatch |
| Live candidate → durable window | ContextManager stripped receipt/cursor before pending raw-envelope identity validation | WorkingContext validates live candidate, sanitizes once, persists sanitized hash bindings and consumer state; ContextManager delegates commit |
| Restart → first consumer | Sanitized result could acquire a second archive ID, losing the original pending binding | WorkingContext restores original evidence binding and reuses verified scoped record; remeasurement required |
| Rehydration → next input | Historical index/body results skipped source archiving, and also skipped first-consumer protection | Exact historical result keys are pinned independently; no recursive archive record; release only on completed consumer |
| Archive → model | An envelope could fit without a single body character and still claim success | EvidenceArchive returns response_budget_too_small for nonadvancing body pages |
| File → historical range | startLine/endLine were not included in archive sourceRange | Archive observation metadata carries actual returned source line bounds; projection reuses that metadata |
| Recovery candidate → admission | Pre-compaction generation fit could veto a fitting final candidate | round-input recomputes generation budget from the selected measured candidate |

## Contract/lifecycle audit

PredictionLoop connects explicit execution transitions, SDK callbacks, UI and telemetry. Input policy is in round-input, final exact LOW gate in ContextManager, numeric budgets and shared batch reservations in BudgetBroker, capability resolution in ToolCapabilityRegistry, scope/approval/reservation enforcement in ToolBoundary. RecoveryCoordinator owns progress/termination; DeliveryController owns report attempt admission. No new retry branch was added to PredictionLoop for the archive failure.

Read-only File/Git/Symbol/Log/Unity/Unreal observations use the same EvidenceManager → WorkingContext path. Provider-qualified capabilities and action-specific read profiles are retained; unknown/colliding tools do not gain authority by name. Historical body/index never grants fresh-source or mutation authority. Source range, historical envelope UTF16 offsets, and native byte ranges remain distinct.

Returned source results survive cancellation/truncation/failure; incomplete planning does not commit. A durable window is not a completed consumer. Commit validates prefix, exact fit, causal pairing, available refs and pending inputs before atomic write. Receipt/cursor/transport/reasoning are sanitized for persistence, never reconstructed as live authority. Restore requires matching scope/lineage/prefix/digest/refs and exact remeasurement. Retry preserves pending results; duplicate consumer callbacks do not repin them.

Archive retention is deliberately bounded (default128 records/8MiB/24h). Referenced/pending records cannot be reclaimed for quota; unrelated old consumed records may be evicted. Catalog now exposes this policy and states that it lists currently available records, not all previously observed evidence. TTL/quota/corruption/fork tests remain explicit. A successful44-page run cannot promise arbitrary-lifetime retention beyond these limits. Finite model input also cannot contain every previously returned page simultaneously.

## Verification plan / status

Meaningful tests cover the actual production handler and guard → catalog → read → final fact path, pending receipt identity across durable restart, historical-page first-consumer protection, nonadvancing pages,60 successive source pages with >=3 compactions,65-page derivedLOW stability, quota/TTL/scopes, cancellation, tool-call completion, SDK channel lifecycle and final delivery. The live380K run and independent final fact oracle are recorded separately; do not substitute source integrity or compacted=true for final-answer quality.

The inherited revision114/115 changes and prior full regression logs remain in neighboring artifact directories. HANDOFF.md will contain final commands/exit codes, exact live metrics, diff hash, risks and rollback point.
