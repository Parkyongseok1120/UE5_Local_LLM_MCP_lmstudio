"use strict";

const crypto = require("crypto");

const DISCOVERY_TOOLS = new Set([
  "unreal_rag_search",
  "unreal_symbol_lookup",
  "list_directory",
  "search_files",
  "read_file",
  "read_file_range",
  "read_symbol",
  "read_unreal_logs",
]);

function stable(value) {
  if (Array.isArray(value)) return value.map(stable);
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(
    Object.keys(value).sort().map((key) => [key, stable(value[key])])
  );
}

function canonicalHash(value) {
  return crypto.createHash("sha256").update(JSON.stringify(stable(value))).digest("hex");
}

function cleanStrings(value) {
  if (!Array.isArray(value)) return [];
  return [...new Set(value.map((item) => String(item || "").trim()).filter(Boolean))];
}

function nonNegativeInt(value) {
  const parsed = Number(value || 0);
  return Number.isFinite(parsed) ? Math.max(0, Math.trunc(parsed)) : 0;
}

function normalizePath(value) {
  return String(value || "").trim().replace(/\\/g, "/").replace(/^\.\//, "").replace(/^project:\/\//i, "").replace(/^\/+|\/+$/g, "");
}

function mutationToolForState(state, route) {
  const selectedSlice = route.selectedSlice && typeof route.selectedSlice === "object"
    ? route.selectedSlice
    : {};
  const files = cleanStrings(selectedSlice.files);
  if (!files.length) return "";
  if (files.length > 1) return "apply_edit_bundle";
  const selected = normalizePath(files[0]).toLowerCase();
  const snapshots = [
    ...(Array.isArray(state.selectedTargetSnapshots) ? state.selectedTargetSnapshots : []),
    ...(Array.isArray(state.featureTargetSnapshots) ? state.featureTargetSnapshots : []),
  ];
  const snapshot = snapshots.find((item) => (
    item && typeof item === "object"
    && normalizePath(item.path || item.relativePath).toLowerCase() === selected
  ));
  if (!snapshot) return "apply_edit_bundle";
  return snapshot.exists === true ? "replace_in_file" : "write_file";
}

function completedSketchForScope(state) {
  const completed = state.completedGates && typeof state.completedGates === "object"
    ? state.completedGates
    : {};
  const record = completed.unreal_code_sketch_claim_validate;
  if (!record || typeof record !== "object" || record.status !== "completed") return null;
  if (String(record.gateSetHash || "") !== String(state.requiredGateSetHash || "")) return null;
  if (String(record.planRevision || "") !== String(state.planRevision || "")) return null;
  if (String(record.activeSliceId || "") !== String(state.activeSliceId || "")) return null;
  return record;
}

function preGateSourceReadPath(state, pendingGates) {
  if (!pendingGates.length || pendingGates[0] !== "unreal_code_sketch_claim_validate") return "";
  if (state.writeGate?.mustReadBeforeWrite !== true) return "";
  const evidenceFiles = state.directSourceEvidence?.files && typeof state.directSourceEvidence.files === "object"
    ? state.directSourceEvidence.files
    : {};
  const evidencePaths = new Set(Object.entries(evidenceFiles)
    .filter(([, item]) => item && typeof item === "object")
    .map(([key, item]) => normalizePath(item.path || key).toLowerCase()));
  const snapshots = Array.isArray(state.selectedTargetSnapshots) ? state.selectedTargetSnapshots : [];
  for (const snapshot of snapshots) {
    if (!snapshot || typeof snapshot !== "object" || snapshot.exists !== true) continue;
    const targetPath = normalizePath(snapshot.path || snapshot.relativePath);
    if (targetPath && !evidencePaths.has(targetPath.toLowerCase())) return targetPath;
  }
  return "";
}

function deriveNextObligation(state) {
  const route = state.toolRoute && typeof state.toolRoute === "object" ? state.toolRoute : {};
  const status = String(state.status || "running").trim().toLowerCase();
  const phase = String(route.phase || "unknown");
  const activeTools = cleanStrings(route.activeTools);
  const pendingGates = cleanStrings(route.pendingGates);
  let requiredName = "";
  let requiredArgs = {};
  let disposition = "continue";
  let retryValue = "allowed";
  let blocker = null;
  let discoveryOnly = false;

  if (status === "completed") disposition = "complete";
  else if (["cancelled", "failed", "cancellation_uncertain"].includes(status)) disposition = "workflow_stop";
  else if (["pending_approval", "awaiting_approval"].includes(status)) disposition = "await_user";
  else if (status === "running") {
    const buildRecovery = state.buildRecovery && typeof state.buildRecovery === "object" ? state.buildRecovery : {};
    const buildVerification = state.buildVerification && typeof state.buildVerification === "object" ? state.buildVerification : {};
    const checkpoint = state.continuity?.checkpoint && typeof state.continuity.checkpoint === "object"
      ? state.continuity.checkpoint
      : {};
    const preGateReadPath = preGateSourceReadPath(state, pendingGates);
    const taskKind = String(state.taskKind || "").trim().toLowerCase();
    const initialCompileDiagnostic = (
      ["compile_fix", "reflection_fix", "module_fix"].includes(taskKind)
      && pendingGates.length > 0
      && nonNegativeInt(state.mutationGeneration) === 0
      && Object.keys(buildRecovery).length === 0
      && !(
        state.buildBlocker
        && typeof state.buildBlocker === "object"
        && Object.keys(state.buildBlocker).length > 0
      )
      && !(Array.isArray(state.buildProofHistory) && state.buildProofHistory.length)
    );
    if (state.slicePlanningRequired === true) {
      discoveryOnly = true;
    } else if (String(buildRecovery.status || "") === "evidence_required") {
      requiredName = String(buildRecovery.requiredNextTool || "").trim();
      requiredArgs = buildRecovery.requiredNextToolArgs && typeof buildRecovery.requiredNextToolArgs === "object"
        ? { ...buildRecovery.requiredNextToolArgs }
        : {};
    } else if (String(buildVerification.status || "") === "pending_automation") {
      requiredName = "run_unreal_automation_tests";
      const testFilter = String(buildVerification.testFilter || "").trim();
      requiredArgs = testFilter ? { testFilter } : {};
    } else if (initialCompileDiagnostic) {
      requiredName = "build_unreal_project";
    } else if (preGateReadPath) {
      requiredName = "read_file";
      requiredArgs = { path: preGateReadPath };
    } else if (pendingGates.length) {
      const gate = pendingGates[0];
      const attempt = state.failedGateAttempts?.[gate] && typeof state.failedGateAttempts[gate] === "object"
        ? state.failedGateAttempts[gate]
        : {};
      const recoverySatisfied = Boolean(attempt.recoverySatisfiedAt);
      if (Number(attempt.attemptCount || 0) >= 2 && !recoverySatisfied) {
        disposition = "rediscover";
        retryValue = "forbidden";
        blocker = { code: "REPEATED_GATE_BLOCKER", fingerprint: String(attempt.fingerprint || "") };
      } else {
        const recoveryTool = recoverySatisfied ? "" : String(attempt.nextAction || "").trim();
        requiredName = activeTools.includes(recoveryTool) ? recoveryTool : gate;
        retryValue = recoveryTool ? "once" : "allowed";
      }
    } else {
      let checkpointAction = String(checkpoint.requiredNextAction || "").trim();
      const completedNames = new Set(Object.entries(state.completedGates || {})
        .filter(([, record]) => record && record.status === "completed")
        .map(([name]) => name));
      if (completedNames.has(checkpointAction)) checkpointAction = "";
      const sketch = completedSketchForScope(state);
      const mutationGeneration = nonNegativeInt(state.mutationGeneration);
      const sketchGeneration = nonNegativeInt(sketch?.mutationGeneration);
      const checkpointGeneration = nonNegativeInt(checkpoint.mutationGeneration);
      const validation = checkpoint.validation && typeof checkpoint.validation === "object"
        ? checkpoint.validation
        : {};
      const validationStatus = String(validation.status || "").trim().toLowerCase();
      const validationRecovery = validation.recovery && typeof validation.recovery === "object"
        ? validation.recovery
        : {};
      const validationRecoverySatisfied = Boolean(
        String(validationRecovery.status || "") === "evidence_satisfied"
        && nonNegativeInt(validationRecovery.mutationGeneration) === mutationGeneration
      );
      const mutationRequired = Boolean(
        phase === "executor"
        && sketch
        && sketchGeneration === mutationGeneration
      );
      const currentMutationCheckpoint = Boolean(
        phase === "executor"
        && sketch
        && checkpointGeneration === mutationGeneration
        && mutationGeneration > sketchGeneration
      );
      if (mutationRequired) requiredName = mutationToolForState(state, route);
      else if (currentMutationCheckpoint && validationStatus === "passed") {
        requiredName = "build_unreal_project";
      } else if (currentMutationCheckpoint && validationStatus === "failed") {
        if (validationRecoverySatisfied) {
          requiredName = mutationToolForState(state, route);
        } else {
          requiredName = "read_file";
          const findingPath = String(validation.firstFinding?.path || "").trim();
          requiredArgs = findingPath ? { path: findingPath } : {};
        }
      } else if (currentMutationCheckpoint) {
        requiredName = "static_validate_project";
      } else if (checkpointAction && activeTools.includes(checkpointAction)) {
        requiredName = checkpointAction;
      }
    }
    if (requiredName) disposition = requiredName === "unreal_task_checkpoint" ? "checkpoint" : "require_tool";
  }

  const allowedTools = requiredName
    ? [requiredName]
    : ["complete", "workflow_stop", "await_user"].includes(disposition)
      ? []
      : disposition === "rediscover" || discoveryOnly
        ? activeTools.filter((name) => (
          DISCOVERY_TOOLS.has(name)
          || (discoveryOnly && name === String(pendingGates[0] || ""))
        ))
        : activeTools;
  return {
    version: 2,
    authoritative: true,
    taskSessionId: String(state.taskSessionId || ""),
    planRevision: String(state.planRevision || ""),
    activeSliceId: String(state.activeSliceId || ""),
    phase,
    disposition,
    requiredTool: requiredName ? { name: requiredName, args: requiredArgs } : null,
    allowedTools,
    routeHash: String(route.routeHash || ""),
    pendingGates,
    retryPolicy: { sameSemanticInput: retryValue },
    blocker,
    mutationGeneration: nonNegativeInt(state.mutationGeneration),
  };
}

function commitControlTransition(state) {
  const control = deriveNextObligation(state);
  const material = [
    control.taskSessionId,
    control.planRevision,
    control.activeSliceId,
    control.phase,
    control.disposition,
    control.requiredTool,
    control.allowedTools,
    control.routeHash,
    control.pendingGates,
    control.blocker,
    control.mutationGeneration,
  ];
  const fingerprint = canonicalHash(material);
  let epoch = nonNegativeInt(state.controlEpoch);
  if (fingerprint !== String(state.controlFingerprint || "")) epoch += 1;
  control.epoch = epoch;
  control.fingerprint = fingerprint;
  state.controlEpoch = epoch;
  state.controlFingerprint = fingerprint;
  state.controlState = control;
  return state;
}

module.exports = {
  canonicalHash,
  commitControlTransition,
  deriveNextObligation,
  mutationToolForState,
};
