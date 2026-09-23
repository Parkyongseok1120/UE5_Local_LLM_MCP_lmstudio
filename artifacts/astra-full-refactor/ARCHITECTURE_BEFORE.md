# Source audit at starting SHA

PredictionLoop owns configuration, exact measurement, checkpoint selection, prompt assembly, semantic summaries, capability authority, raw syntax classification, range algebra, recovery progress, final delivery, guards, persistence and UI. WorkingContext already owns the durable archive and window storage. Reuse those stores and the deterministic checkpoint, input availability and generation budget primitives.

Confirmed source path: handler -> initial projection -> preliminary measure -> soft/hard checkpoint -> semantic summary -> availability injection -> final measure -> emergency only if hard ceiling exceeded -> commit when remaining >= 0 -> act. The working-input target is telemetry only after final reassembly. A reduction or `compacted=true` is accepted even above LOW. Mandatory floor is measured but not used to select the final candidate. This permits rising baselines; it does not establish which token metric the historical 26/34/42K observations used.

Alternative inflation paths: semantic note and availability metadata added after preliminary selection; tool schema cost; first-consumer retention and growing checkpoint facts. These must be measured separately, not attributed to backend cache without runtime evidence.

Current booleans: finalizationPending, researchRecoveryProfileActive, toolPlanningRetryPending encode phases; boundedAudit/observeOnly are configuration; finalizing/researchRecoveryRound/toolPlanningRetryRound are derived views; predictionCompleted is an event; researchRecoveryEpisodeStarted and retry/final attempts are bounded lifecycle counters.

Contracts to retain: provider start unknown despite guard allow; completed exposure gating; typed range endpoints; exclusive empty EOF; representation-aware coordinates; novelty distinct from availability; generic content-body projection; provider-qualified authority; raw syntax never executed; mutation approval.

Risks R01–R18 from the instruction packet remain acceptance scope. Live host schema conversion/cache/callback behavior needs direct runtime evidence; offline tests cannot prove it.
