"use strict";

module.exports = {
  "source": "config/control_state_machine.json",
  "version": 1,
  "events": [
    "EVIDENCE_STAGNATION",
    "GATE_VALIDATION_FAILED",
    "HANDLER_RECOVERY_FACT",
    "PHASE_BUDGET_EXHAUSTED",
    "TOOL_RESULT_COMMITTED"
  ],
  "synthesisLifecycle": [
    "commit_acked",
    "commit_sent",
    "delivered",
    "delivery_pending",
    "delivery_reemit_authorized",
    "delivery_uncertain",
    "evidence_recovery",
    "pending",
    "prepared",
    "rejected_stale"
  ],
  "proxyLifecycleStates": [
    "commit_acked",
    "commit_sent",
    "committed",
    "completed",
    "delivered",
    "delivery_pending",
    "delivery_reemit_authorized",
    "delivery_uncertain",
    "evidence_recovery",
    "pending",
    "prepared",
    "rejected_stale"
  ]
};
