# Architecture after the ASTRA refactor

## Runtime ownership

prediction-loop.ts is 80,622 UTF-8/LF bytes (about 43% of its starting 187,252-byte Git blob). It owns round orchestration and collaborator wiring. It no longer owns LOW/HIGH arithmetic, evidence range subtraction, capability authority catalogs, recovery decisions, final delivery policy or final-input candidate selection.

- execution-state.ts: canonical RESEARCH, READ_RECOVERY, TOOL_REPLAN, FINAL_DELIVERY, OUTPUT_RECOVERY and TERMINATED phases, checked transitions, RoundTransaction planning/evidence durability.
- context-manager.ts: exact prompt assembly, final metadata/schema measurement, mandatory floor and finite compaction candidates, LOW postcondition, durable working-context commit gate.
- budget-broker.ts: sole owner of context/output/safety/read-envelope/post-tool arithmetic, batch reservations and generation budget.
- evidence-manager.ts: generic evidence identity/version/representation/range/content/availability model, exposure and return hooks, coverage/fingerprint/projection lifecycle. Existing WorkingContext remains the archive/window store; existing input-availability remains the coordinate algebra primitive.
- tool-capability-registry.ts: provider-qualified authority, read-profile resolution, collision refusal, capability scope/effect/approval/recovery classification.
- tool-boundary.ts: guard, dispatch/exposure/result events and reservation settlement/release. Guard approval does not assert provider start.
- recovery-coordinator.ts: progress, no-progress bounds and recovery termination decisions.
- delivery-controller.ts: final versus partial delivery outcome and delivery-attempt policy.
- Supporting owners: execution-config.ts, execution-contracts.ts, execution-instructions.ts, context-ports.ts, runtime-identity.ts, prediction-ui.ts, raw-tool-intent.ts, evidence-telemetry.ts.

## Watermark derivation

For exact measurement context length C, generation reserve G and safety margin S:

hardCeiling = max(0, C - G - S).

Read rounds reserve one shared result envelope R=min(4096, floor(hardCeiling/8)); no-read rounds use R=0. Post-tool reserve P=G for read rounds and 0 otherwise. HIGH=min(configuredTrigger, max(0, hardCeiling-R-P)). Configured LOW=min(configuredTarget, floor(HIGH*0.75)). Effective LOW=max(configuredLOW, measuredMandatoryFloor). If the mandatory floor is larger, the target is explicitly unreachable and required content remains. Compaction success requires finalExactInputTokens <= effectiveLowWaterTokens and the hard ceiling.

For the 38,912-token model and product defaults used by the stress probe, C=38,912, G=8,192, S=2,048: hard ceiling=28,672, R=P=0 (no read tools), HIGH=22,000 and LOW=min(18,000,16,500)=16,500. The independent live ratchet fixture used G=1,024, target=6,000 and trigger=9,000; its three exact-template post-compaction baselines were 4,737, 4,733 and 4,733.

## Evidence and transaction behavior

Git, file, symbol, log, Unity and Unreal observations share the same exposure, first-consumer retention, projection/archive, range identity and cancellation-safe return lifecycle. Source identity/version/representation qualify range coordinates; empty EOF adds no coverage; projection/rehydration does not create fresh source coverage; historical novelty does not claim current model-input availability. Incomplete/aborted planning does not commit as a completed assistant plan, while already returned provider results remain durable.

## Verification boundary

The runtime harness uses repository-built modules and the actual loaded tokenizer/template. The 380K cumulative stress harness stopped early after a truncated response; it did not reach its requested target. The installed GUI plugin, real Unity/Unreal editor workflows and SDK host-converted schema are not verified.
