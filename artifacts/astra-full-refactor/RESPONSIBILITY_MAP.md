# Responsibility map

| Existing owner at start | New owner | Boundary retained |
|---|---|---|
| PredictionLoop phase booleans and retry transaction | ExecutionState, RoundTransaction | Loop drives transitions; completed plans commit |
| Inline assembly, measurement, compaction/floor choice and commit | ContextManager | Existing tokenizer/template and WorkingContext storage |
| Inline LOW/HIGH, tool/output reserve and generation fit | BudgetBroker, BatchReservation | Compatibility exports remain at context-budget boundary |
| Inline fingerprint/coverage/projection/exposure/capture | EvidenceManager, evidence-telemetry | Existing WorkingContext archive and input-availability range primitive |
| Inline provider and engine read authority | ToolCapabilityRegistry | Provider grants remain qualified; duplicate names refuse authority |
| Inline approval/dispatch/exposure/result/reservation flow | ToolBoundary | No provider start inference from guard permission |
| Inline recovery progress, retry profile and stop decision | RecoveryCoordinator | Configured lifecycle bounds remain unchanged |
| Inline final/partial output and output retry policy | DeliveryController | Existing user-visible callbacks remain in the loop boundary |
| Inline instructions and raw tool intent | execution-instructions, raw-tool-intent | Raw syntax stays classification-only |
| Inline runtime identity and UI event formatting | runtime-identity, prediction-ui | Existing event contract retained |
| Repeated checkpoint/current-history accumulation | continuity-text.js and direct-compaction-core.js | Prior checkpoint facts do not duplicate a richer current-history view |
| Git-only raw-result preservation lifecycle | EvidenceManager and working-context.js | Legacy Git setting remains a compatibility fallback |

New source modules: budget-broker.ts, context-manager.ts, context-ports.ts, delivery-controller.ts, evidence-manager.ts, evidence-telemetry.ts, execution-config.ts, execution-contracts.ts, execution-instructions.ts, execution-state.ts, prediction-ui.ts, raw-tool-intent.ts, recovery-coordinator.ts, runtime-identity.ts, tool-boundary.ts, tool-capability-registry.ts.

No source file was moved. New focused tests: test/refactor-policies.test.cjs; package test list, status source inventory and two completion-event mocks were updated.
