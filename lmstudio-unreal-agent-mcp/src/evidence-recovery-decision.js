"use strict";

// Compatibility adapter only. The canonical Python reducer owns both the
// recovery status and the next command. Keeping this thin adapter lets older
// callers inspect a decision without maintaining a second semantic table in
// Node.
const { reduceCommittedEvent } = require("./task-control-transition");

function evidenceRecoveryDecision(taskState = {}, options = {}) {
  const working = JSON.parse(JSON.stringify(taskState || {}));
  reduceCommittedEvent(working, {
    kind: "EVIDENCE_STAGNATION",
    errorCode: String(options.errorCode || "EVIDENCE_STAGNATION"),
    targetFiles: Array.isArray(options.targetFiles)
      ? options.targetFiles.map((value) => String(value || "")).filter(Boolean).slice(0, 4)
      : [],
  });
  return working.recoveryObligation && typeof working.recoveryObligation === "object"
    ? working.recoveryObligation
    : {};
}

module.exports = { evidenceRecoveryDecision };
