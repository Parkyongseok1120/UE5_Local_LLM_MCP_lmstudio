"use strict";

const crypto = require("crypto");
const path = require("path");
const {
  filesystemPathIdentity,
  normalizePortablePath,
} = require("./filesystem-path-identity");

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
const REPEATED_GATE_REDISCOVERY_TOOLS = new Set([
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
  return normalizePortablePath(value, { trimOuterSlashes: true });
}

function transitionPathIdentity(value, hostPlatform = process.platform) {
  return filesystemPathIdentity(value, hostPlatform, { trimOuterSlashes: true });
}

function authoritativeProjectRoot(state) {
  const workspaceRoot = String(state.workspaceRoot || "").trim();
  const routeScope = state.routeScope && typeof state.routeScope === "object"
    ? state.routeScope
    : {};
  const rawProject = String(routeScope.projectFile || state.projectFile || "").trim();
  if (rawProject) {
    const projectBase = String(
      routeScope.workspaceRoot || workspaceRoot || ""
    ).trim();
    const resolvedProject = path.resolve(
      !path.isAbsolute(rawProject) && projectBase
        ? path.join(projectBase, rawProject)
        : rawProject
    );
    return path.extname(resolvedProject).toLowerCase() === ".uproject"
      ? path.dirname(resolvedProject)
      : resolvedProject;
  }
  return workspaceRoot ? path.resolve(workspaceRoot) : "";
}

function authoritativeProjectFile(state) {
  const workspaceRoot = String(state.workspaceRoot || "").trim();
  const routeScope = state.routeScope && typeof state.routeScope === "object"
    ? state.routeScope
    : {};
  const rawProject = String(routeScope.projectFile || state.projectFile || "").trim();
  if (!rawProject) return "";
  const projectBase = String(routeScope.workspaceRoot || workspaceRoot || "").trim();
  const resolvedProject = path.resolve(
    !path.isAbsolute(rawProject) && projectBase
      ? path.join(projectBase, rawProject)
      : rawProject
  );
  return path.extname(resolvedProject).toLowerCase() === ".uproject"
    ? resolvedProject
    : "";
}

function mutationToolForState(state, route, hostPlatform = process.platform) {
  const selectedSlice = route.selectedSlice && typeof route.selectedSlice === "object"
    ? route.selectedSlice
    : {};
  const files = cleanStrings(selectedSlice.files);
  if (!files.length) return "";
  if (files.length > 1) return "apply_edit_bundle";
  const selected = transitionPathIdentity(files[0], hostPlatform);
  const selectedSnapshots = Array.isArray(state.selectedTargetSnapshots)
    ? state.selectedTargetSnapshots
    : [];
  const snapshots = selectedSnapshots.length
    ? selectedSnapshots
    : (Array.isArray(state.featureTargetSnapshots) ? state.featureTargetSnapshots : []);
  const snapshot = snapshots.find((item) => (
    item && typeof item === "object"
    && transitionPathIdentity(item.path || item.relativePath, hostPlatform) === selected
  ));
  if (!snapshot) return "apply_edit_bundle";
  return snapshot.exists === true ? "replace_in_file" : "write_file";
}

function failedGateAttemptForCurrentScope(state, gate) {
  const attempt = state.failedGateAttempts?.[gate]
    && typeof state.failedGateAttempts[gate] === "object"
    ? state.failedGateAttempts[gate]
    : {};
  if (!Object.keys(attempt).length) return {};
  if (!["gateSetHash", "planRevision", "activeSliceId", "mutationGeneration"]
    .every((field) => Object.prototype.hasOwnProperty.call(attempt, field))) return {};
  return (
    String(attempt.gateSetHash || "") === String(state.requiredGateSetHash || "")
    && String(attempt.planRevision || "") === String(state.planRevision || "")
    && String(attempt.activeSliceId || "") === String(state.activeSliceId || "")
    && nonNegativeInt(attempt.mutationGeneration) === nonNegativeInt(state.mutationGeneration)
  ) ? attempt : {};
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

function preGateSourceReadPath(state, pendingGates, hostPlatform = process.platform) {
  if (!pendingGates.length || pendingGates[0] !== "unreal_code_sketch_claim_validate") return "";
  if (state.writeGate?.mustReadBeforeWrite !== true) return "";
  const evidenceFiles = state.directSourceEvidence?.files && typeof state.directSourceEvidence.files === "object"
    ? state.directSourceEvidence.files
    : {};
  const evidencePaths = new Set(Object.entries(evidenceFiles)
    .filter(([, item]) => item && typeof item === "object")
    .map(([key, item]) => transitionPathIdentity(item.path || key, hostPlatform)));
  const snapshots = Array.isArray(state.selectedTargetSnapshots) ? state.selectedTargetSnapshots : [];
  for (const snapshot of snapshots) {
    if (!snapshot || typeof snapshot !== "object" || snapshot.exists !== true) continue;
    const targetPath = normalizePath(snapshot.path || snapshot.relativePath);
    if (targetPath && !evidencePaths.has(transitionPathIdentity(targetPath, hostPlatform))) return targetPath;
  }
  return "";
}

function validationFindingRecovery(firstFinding) {
  const finding = firstFinding && typeof firstFinding === "object" ? firstFinding : {};
  const targetPath = normalizePath(finding.path);
  const line = nonNegativeInt(finding.line);
  if (targetPath) {
    if (line > 0) {
      return {
        status: "evidence_required",
        scopeDisposition: "in_slice",
        requiredTool: {
          name: "read_file_range",
          args: {
            path: targetPath,
            startLine: Math.max(1, line - 20),
            endLine: line + 20,
          },
        },
        targetFiles: [targetPath],
      };
    }
    return {
      status: "evidence_required",
      scopeDisposition: "in_slice",
      requiredTool: { name: "read_file", args: { path: targetPath } },
      targetFiles: [targetPath],
    };
  }
  const symbol = String(
    finding.symbol || finding.ownerSymbol || finding.missingSymbol || ""
  ).trim();
  if (symbol) {
    return {
      status: "evidence_required",
      scopeDisposition: "in_slice",
      requiredTool: {
        name: "unreal_symbol_lookup",
        args: { query: symbol, access: "read" },
      },
      targetFiles: [],
    };
  }
  const rawLog = String(
    finding.buildLogPath || finding.logPath || finding.logFile || ""
  ).trim();
  const diagnosticSource = String(finding.diagnosticSource || "").toLowerCase();
  if (rawLog || ["build", "automation", "ubt", "uat", "log"].includes(diagnosticSource)) {
    const logArgs = {
      mode: "first_error",
      maxFiles: 1,
      maxLines: 200,
      summaryOnly: true,
    };
    if (rawLog) logArgs.fileName = path.posix.basename(rawLog.replace(/\\/g, "/"));
    return {
      status: "evidence_required",
      scopeDisposition: "infrastructure",
      requiredTool: { name: "read_unreal_logs", args: logArgs },
      targetFiles: [],
    };
  }
  return {
    status: "checkpoint_rebase_required",
    scopeDisposition: "in_slice",
    requiredTool: {
      name: "unreal_task_checkpoint",
      args: {
        action: "rebase",
        acceptCurrentFiles: true,
        includeGitChanges: false,
      },
    },
    targetFiles: [],
  };
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
  let noToolsForSynthesis = false;

  if (status === "completed") disposition = "complete";
  else if (["cancelled", "failed", "cancellation_uncertain"].includes(status)) disposition = "workflow_stop";
  else if (["pending_approval", "awaiting_approval"].includes(status)) disposition = "await_user";
  else if (status === "running") {
    const recoveryObligation = state.recoveryObligation
      && typeof state.recoveryObligation === "object"
      ? state.recoveryObligation
      : {};
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
    const recoveryStatus = String(recoveryObligation.status || "").trim().toLowerCase();
    const recoveryTool = recoveryObligation.requiredTool
      && typeof recoveryObligation.requiredTool === "object"
      ? recoveryObligation.requiredTool
      : {};
    const recoveryToolName = String(recoveryTool.name || "").trim();
    const recoveryToolArgs = recoveryTool.args && typeof recoveryTool.args === "object"
      ? { ...recoveryTool.args }
      : {};
    const recoveryFingerprint = String(recoveryObligation.fingerprint || "");
    const pendingGate = String(pendingGates[0] || "");
    const failedGateAttempt = pendingGate
      ? failedGateAttemptForCurrentScope(state, pendingGate)
      : {};
    const repeatedGateBlocker = Boolean(
      pendingGate
      && recoveryToolName === pendingGate
      && Number(failedGateAttempt.attemptCount || 0) >= 2
      && !failedGateAttempt.recoverySatisfiedAt
    );
    if (repeatedGateBlocker) {
      disposition = "rediscover";
      retryValue = "forbidden";
      blocker = {
        code: "REPEATED_GATE_BLOCKER",
        fingerprint: String(failedGateAttempt.fingerprint || ""),
      };
    } else if (["external_blocker", "await_user"].includes(recoveryStatus)) {
      disposition = "await_user";
      retryValue = "forbidden";
      blocker = {
        code: String(recoveryObligation.errorCode || "RECOVERY_EXTERNAL_BLOCKER"),
        fingerprint: recoveryFingerprint,
      };
    } else if (recoveryStatus === "evidence_complete") {
      // Read-only evidence exhaustion is not an infrastructure failure.  Keep
      // the conversation available for a source-backed final answer while
      // removing every tool from the current route so it cannot loop.
      disposition = "continue";
      retryValue = "forbidden";
      noToolsForSynthesis = true;
      blocker = {
        code: String(recoveryObligation.errorCode || "EVIDENCE_STAGNATION"),
        fingerprint: recoveryFingerprint,
      };
    } else if (recoveryStatus === "environment_recovery") {
      const attemptCount = nonNegativeInt(recoveryObligation.attemptCount);
      if (recoveryToolName && attemptCount <= 1) {
        requiredName = recoveryToolName;
        requiredArgs = recoveryToolArgs;
        retryValue = "once";
      } else {
        disposition = "await_user";
        retryValue = "forbidden";
        blocker = {
          code: String(recoveryObligation.errorCode || "RECOVERY_ENVIRONMENT_BLOCKED"),
          fingerprint: recoveryFingerprint,
        };
      }
    } else if ([
      "evidence_required",
      "repair_planning_required",
      "revalidate_required",
      "checkpoint_rebase_required",
      "phase_budget_checkpoint_required",
    ].includes(recoveryStatus)) {
      if (recoveryToolName) {
        requiredName = recoveryToolName;
        requiredArgs = recoveryToolArgs;
        retryValue = "once";
      } else {
        disposition = "await_user";
        retryValue = "forbidden";
        blocker = { code: "RECOVERY_REQUIRED_TOOL_MISSING", fingerprint: recoveryFingerprint };
      }
    } else if (recoveryStatus === "repair_required") {
      // A repair may only mutate while its current-scope sketch approval is
      // still valid. Gate expiry repopulates pendingGates atomically; that fact
      // must outrank the otherwise-ready recovery mutation or the public
      // control would require a mutation that authorization rejects.
      if (pendingGate) {
        requiredName = pendingGate;
        retryValue = "allowed";
      } else {
        requiredName = mutationToolForState(state, route);
        retryValue = "once";
        if (!requiredName) {
          disposition = "await_user";
          retryValue = "forbidden";
          blocker = { code: "RECOVERY_MUTATION_SCOPE_MISSING", fingerprint: recoveryFingerprint };
        }
      }
    } else if (state.slicePlanningRequired === true) {
      discoveryOnly = true;
    } else if (String(buildRecovery.status || "") === "evidence_required") {
      requiredName = String(buildRecovery.requiredNextTool || "").trim();
      requiredArgs = buildRecovery.requiredNextToolArgs && typeof buildRecovery.requiredNextToolArgs === "object"
        ? { ...buildRecovery.requiredNextToolArgs }
        : {};
    } else if (String(buildVerification.status || "") === "pending_automation") {
      requiredName = "run_unreal_automation_tests";
      const testFilters = Array.isArray(buildVerification.testFilters)
        ? buildVerification.testFilters.map(String).map((item) => item.trim()).filter(Boolean)
        : [];
      const testFilter = String(buildVerification.testFilter || "").trim();
      requiredArgs = testFilters.length
        ? { testFilters }
        : (testFilter ? { testFilter } : {});
    } else if (initialCompileDiagnostic) {
      requiredName = "build_unreal_project";
    } else if (preGateReadPath) {
      requiredName = "read_file";
      requiredArgs = { path: preGateReadPath };
    } else if (pendingGates.length) {
      const gate = pendingGates[0];
      const attempt = failedGateAttemptForCurrentScope(state, gate);
      const recoverySatisfied = Boolean(attempt.recoverySatisfiedAt);
      if (Number(attempt.attemptCount || 0) >= 2 && !recoverySatisfied) {
        disposition = "rediscover";
        retryValue = "forbidden";
        blocker = { code: "REPEATED_GATE_BLOCKER", fingerprint: String(attempt.fingerprint || "") };
      } else {
        const recoveryTool = recoverySatisfied ? "" : String(attempt.nextAction || "").trim();
        requiredName = activeTools.includes(recoveryTool) ? recoveryTool : gate;
        if (requiredName === recoveryTool && attempt.nextActionArgs && typeof attempt.nextActionArgs === "object") {
          requiredArgs = { ...attempt.nextActionArgs };
        }
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
          const fallback = validationFindingRecovery(validation.firstFinding);
          requiredName = String(fallback.requiredTool?.name || "");
          requiredArgs = fallback.requiredTool?.args
            && typeof fallback.requiredTool.args === "object"
            ? { ...fallback.requiredTool.args }
            : {};
        }
      } else if (currentMutationCheckpoint) {
        requiredName = "static_validate_project";
      } else if (checkpointAction && activeTools.includes(checkpointAction)) {
        requiredName = checkpointAction;
      }
    }
    if (requiredName === "static_validate_project") {
      const projectRoot = authoritativeProjectRoot(state);
      if (projectRoot) requiredArgs = { projectRoot, fullAudit: false };
    }
    if (requiredName === "build_unreal_project") {
      const projectFile = authoritativeProjectFile(state);
      if (projectFile) {
        requiredArgs = {
          ...requiredArgs,
          project: projectFile,
          allowAbsoluteProject: true,
          allowEngineFallback: false,
        };
      }
      const buildContract = state.buildContract && typeof state.buildContract === "object"
        ? state.buildContract
        : {};
      for (const key of ["engineRoot", "target", "platform", "configuration"]) {
        const value = String(buildContract[key] || "").trim();
        if (value) requiredArgs[key] = value;
      }
    }
    if (requiredName === "run_unreal_automation_tests") {
      const projectFile = authoritativeProjectFile(state);
      if (projectFile) requiredArgs = { ...requiredArgs, project: projectFile };
      const engineRoot = String(buildVerification.engineRoot || "").trim();
      if (engineRoot) requiredArgs = { ...requiredArgs, engineRoot: path.resolve(engineRoot) };
    }
    if (requiredName) disposition = requiredName === "unreal_task_checkpoint" ? "checkpoint" : "require_tool";
  }

  const allowedTools = requiredName
    ? [requiredName]
    : ["complete", "workflow_stop", "await_user"].includes(disposition)
      ? []
      : noToolsForSynthesis
        ? []
      : disposition === "rediscover" || discoveryOnly
        ? activeTools.filter((name) => (
          (blocker?.code === "REPEATED_GATE_BLOCKER"
            ? REPEATED_GATE_REDISCOVERY_TOOLS.has(name)
            : DISCOVERY_TOOLS.has(name))
          || (discoveryOnly && name === String(pendingGates[0] || ""))
        ))
        : activeTools;
  return {
    version: 2,
    authoritative: true,
    taskSessionId: String(state.taskSessionId || ""),
    taskMode: String(state.mode || "").trim().toLowerCase(),
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
  const fingerprint = canonicalHash(control);
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
  failedGateAttemptForCurrentScope,
  mutationToolForState,
  preGateSourceReadPath,
  transitionPathIdentity,
  validationFindingRecovery,
  authoritativeProjectFile,
  authoritativeProjectRoot,
};
