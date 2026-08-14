"use strict";

function stableStringify(value) {
  if (Array.isArray(value)) {
    return `[${value.map((item) => stableStringify(item)).join(",")}]`;
  }
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => (
      `${JSON.stringify(key)}:${stableStringify(value[key])}`
    )).join(",")}}`;
  }
  return JSON.stringify(value);
}

function exactRecoveryLogObligation(taskState, observedArgs) {
  const recovery = taskState?.recoveryObligation
    && typeof taskState.recoveryObligation === "object"
    ? taskState.recoveryObligation
    : {};
  const requiredTool = recovery.requiredTool && typeof recovery.requiredTool === "object"
    ? recovery.requiredTool
    : {};
  const requiredArgs = requiredTool.args && typeof requiredTool.args === "object"
    ? requiredTool.args
    : {};
  const matched = (
    String(recovery.status || "").trim().toLowerCase() === "evidence_required"
    && String(requiredTool.name || "").trim() === "read_unreal_logs"
    && stableStringify(requiredArgs) === stableStringify(observedArgs || {})
  );
  return { matched, recovery, requiredTool, requiredArgs };
}

function recoveryLogSource(recovery, requestedLogFile) {
  const explicit = String(recovery?.source || "").trim();
  if (explicit) return explicit;
  return String(requestedLogFile || "").toLowerCase().includes("automation")
    ? "automation"
    : "build";
}

module.exports = {
  exactRecoveryLogObligation,
  recoveryLogSource,
};
