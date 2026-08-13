"use strict";

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { compactTaskAuthorization } = require("./public-contract.js");
const { taskStateDir, resolveAgentStateRoot, ensureStateRootLayout } = require("./state-root");
const { atomicWriteJson } = require("./atomic-io");
const {
  tryAcquireCrossProcessLock,
  releaseCrossProcessLock,
} = require("./write-locks");
const {
  getMcpConnectionId,
  getMcpClientInstanceId,
  taskOwnsActiveToolRoute,
  taskConnectionMatches,
  taskIsForeignHealthy,
} = require("./mcp-connection");
const { spawnSync } = require("child_process");
const { recoveryAction } = require("./route-recovery-policy");
const { stripProjectNamePrefix } = require("./read-path-resolver");

const TASK_SESSION_ID_RE = /^[A-Za-z0-9_-]{8,64}$/;

function sanitizeTaskSessionId(taskSessionId) {
  const value = String(taskSessionId || "").trim();
  if (!value) {
    return { ok: false, error: "taskSessionId is required" };
  }
  if (value.includes("..") || value.includes("/") || value.includes("\\")) {
    return { ok: false, error: "taskSessionId must not contain path separators or traversal" };
  }
  if (!TASK_SESSION_ID_RE.test(value)) {
    return {
      ok: false,
      error: "taskSessionId must match [A-Za-z0-9_-]{8,64}",
    };
  }
  return { ok: true, taskSessionId: value };
}

function taskDir(workspaceRoot, taskSessionId, stateRoot = resolveAgentStateRoot(workspaceRoot)) {
  ensureStateRootLayout(stateRoot);
  return taskStateDir(taskSessionId, stateRoot);
}

function readTaskStateResult(_workspaceRoot, taskSessionId, stateRoot = null) {
  stateRoot = stateRoot || resolveAgentStateRoot(_workspaceRoot);
  let dir;
  try {
    dir = taskDir(_workspaceRoot, taskSessionId, stateRoot);
  } catch {
    return { state: null, errorCode: "TASK_STATE_UNAVAILABLE" };
  }
  const statePath = path.join(dir, "state.json");
  let selectedPath = statePath;
  if (!fs.existsSync(statePath)) {
    const legacyRoot = path.resolve(_workspaceRoot || process.cwd());
    const legacyPath = path.join(legacyRoot, ".agent", "tasks", taskSessionId, "state.json");
    if (fs.existsSync(legacyPath)) {
      selectedPath = legacyPath;
    } else {
      return { state: null, errorCode: "TASK_STATE_MISSING" };
    }
  }
  try {
    const state = JSON.parse(fs.readFileSync(selectedPath, "utf8"));
    if (!state || typeof state !== "object" || Array.isArray(state)) {
      return { state: null, errorCode: "TASK_STATE_CORRUPT" };
    }
    return { state, errorCode: "", statePath: selectedPath };
  } catch {
    return { state: null, errorCode: "TASK_STATE_CORRUPT" };
  }
}

function readTaskState(_workspaceRoot, taskSessionId, stateRoot = null) {
  return readTaskStateResult(_workspaceRoot, taskSessionId, stateRoot).state;
}

function requiredFields(args = {}) {
  const nested = args.taskAuthorization && typeof args.taskAuthorization === "object"
    ? args.taskAuthorization
    : (args.task_authorization && typeof args.task_authorization === "object" ? args.task_authorization : {});
  const fields = {
    taskSessionId: String(args.taskSessionId || args.task_session_id || nested.taskSessionId || nested.task_session_id || "").trim(),
    authToken: String(args.authToken || args.auth_token || args.token || nested.authToken || nested.auth_token || nested.token || "").trim(),
    planId: String(args.planId || args.plan_id || nested.planId || nested.plan_id || "").trim(),
    planRevision: String(args.planRevision || args.plan_revision || nested.planRevision || nested.plan_revision || "").trim(),
    activeSliceId: String(args.activeSliceId || args.active_slice_id || nested.activeSliceId || nested.active_slice_id || "").trim(),
  };
  const routeHash = String(args.routeHash || args.route_hash || nested.routeHash || nested.route_hash || "").trim();
  const routePhase = String(args.routePhase || args.route_phase || nested.routePhase || nested.route_phase || "").trim();
  if (routeHash || routePhase) {
    fields.routeHash = routeHash;
    fields.routePhase = routePhase;
  }
  return fields;
}

function requestedMutationPaths(args = {}, state = {}) {
  const raw = [];
  if (args.path) raw.push(args.path);
  for (const item of Array.isArray(args.files) ? args.files : []) {
    if (item && item.path) raw.push(item.path);
  }
  for (const item of Array.isArray(args.patches) ? args.patches : []) {
    if (item && item.path) raw.push(item.path);
  }
  const projectFile = String(state.projectFile || "").trim();
  const projectRoot = projectFile.toLowerCase().endsWith(".uproject")
    ? path.dirname(projectFile)
    : projectFile;
  return [...new Set(raw.map((value) => {
    const text = String(value || "").trim();
    const normalized = path.isAbsolute(text)
      ? text
      : stripProjectNamePrefix(text, projectRoot);
    return path.resolve(
      path.isAbsolute(normalized)
        ? normalized
        : path.join(projectRoot || process.cwd(), normalized)
    );
  }))];
}

function sha1File(filePath) {
  return crypto.createHash("sha1").update(fs.readFileSync(filePath)).digest("hex");
}

function stableStringify(value) {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) {
    return `[${value.map((item) => stableStringify(item)).join(",")}]`;
  }
  const keys = Object.keys(value).sort();
  return `{${keys.map((key) => `${JSON.stringify(key)}:${stableStringify(value[key])}`).join(",")}}`;
}

function featureIntentTargetHash(snapshots = []) {
  const clean = (value, limit) => String(value || "")
    .replace(/\s+/gu, " ")
    .trim()
    .slice(0, limit);
  const normalized = (Array.isArray(snapshots) ? snapshots : [])
    .filter((item) => item && typeof item === "object" && !Array.isArray(item))
    .map((item) => ({
      path: clean(item.path, 1200),
      absolutePath: clean(item.absolutePath, 2000),
      exists: Boolean(item.exists),
      fileHash: clean(item.fileHash, 128),
    }))
    .sort((left, right) => {
      const absolute = left.absolutePath < right.absolutePath
        ? -1
        : (left.absolutePath > right.absolutePath ? 1 : 0);
      if (absolute !== 0) return absolute;
      return left.path < right.path ? -1 : (left.path > right.path ? 1 : 0);
    });
  return crypto.createHash("sha256").update(stableStringify(normalized)).digest("hex");
}

const SCOPE_AUTHORITATIVE_GATES = new Set([
  "unreal_code_sketch_claim_validate",
  "unreal_feature_intent_resolve",
]);
const SCOPE_AUTHORITY_PRIORITY = [
  "unreal_feature_intent_resolve",
  "unreal_code_sketch_claim_validate",
];

function resolveScopeAuthorityGate(requiredGates = []) {
  const required = new Set((requiredGates || []).map(String));
  for (const gate of SCOPE_AUTHORITY_PRIORITY) {
    if (required.has(gate)) return gate;
  }
  return "";
}

function validateCompletedGates(state, args = {}) {
  const writeGate = state.writeGate && typeof state.writeGate === "object" ? state.writeGate : {};
  const required = Array.isArray(state.requiredBeforeWrite)
    ? state.requiredBeforeWrite.map(String)
    : (Array.isArray(writeGate.requiredBeforeWrite) ? writeGate.requiredBeforeWrite.map(String) : []);
  if (!required.length) return { ok: true };
  const completed = state.completedGates && typeof state.completedGates === "object"
    ? state.completedGates
    : {};
  const missing = required.filter((gate) => {
    const record = completed[gate];
    return !record || record.status !== "completed";
  });
  if (missing.length) {
    return {
      ok: false,
      error: `Required pre-write gates are incomplete: ${missing.join(", ")}`,
      errorCode: "REQUIRED_GATE_INCOMPLETE",
      pendingGates: missing,
    };
  }
  const expectedGateSetHash = String(state.requiredGateSetHash || "");
  const now = Date.now();
  for (const gate of required) {
    const record = completed[gate];
    if (!record || String(record.gateSetHash || "") !== expectedGateSetHash) {
      return {
        ok: false,
        error: `Required pre-write gate is stale for the active plan: ${gate}`,
        errorCode: "REQUIRED_GATE_STALE",
      };
    }
    const expiresAt = Date.parse(String(record.expiresAt || ""));
    if (!Number.isFinite(expiresAt) || expiresAt <= now) {
      return {
        ok: false,
        error: `Required pre-write gate has expired: ${gate}`,
        errorCode: "REQUIRED_GATE_EXPIRED",
      };
    }
  }

  if (required.includes("unreal_feature_intent_resolve")) {
    const record = completed.unreal_feature_intent_resolve;
    const featureIntent = state.featureIntent
      && typeof state.featureIntent === "object"
      && !Array.isArray(state.featureIntent)
      ? state.featureIntent
      : null;
    if (!featureIntent || featureIntent.status !== "resolved") {
      return {
        ok: false,
        error: "Feature intent state is missing or unresolved.",
        errorCode: "FEATURE_INTENT_STATE_MISSING",
      };
    }
    const continuity = state.continuity
      && typeof state.continuity === "object"
      ? state.continuity
      : {};
    const checkpoint = continuity.checkpoint
      && typeof continuity.checkpoint === "object"
      ? continuity.checkpoint
      : {};
    const currentCheckpointHash = String(
      checkpoint.checkpointHash || continuity.planIdentityHash || ""
    );
    const snapshots = Array.isArray(record.targetSnapshots)
      ? record.targetSnapshots
      : [];
    const computedTargetHash = featureIntentTargetHash(snapshots);
    const fieldsMatch = Boolean(
      String(record.selectedIntentId || "")
      && String(record.selectedIntentId || "") === String(state.selectedIntentId || "")
      && String(record.selectedIntentId || "") === String(featureIntent.selectedIntentId || "")
      && String(record.intentContractHash || "")
      && String(record.intentContractHash || "") === String(state.intentContractHash || "")
      && String(record.intentContractHash || "") === String(featureIntent.intentContractHash || "")
      && String(record.acceptanceOracleHash || "")
      && String(record.acceptanceOracleHash || "") === String(featureIntent.acceptanceOracleHash || "")
      && String(record.planRevision || "") === String(state.planRevision || "")
      && String(record.planRevision || "") === String(featureIntent.planRevision || "")
      && String(record.checkpointHash || "")
      && String(record.checkpointHash || "") === currentCheckpointHash
      && String(record.checkpointHash || "") === String(featureIntent.checkpointHash || "")
      && String(record.targetSnapshotHash || "")
      && String(record.targetSnapshotHash || "") === computedTargetHash
      && String(record.targetSnapshotHash || "") === String(featureIntent.targetSnapshotHash || "")
    );
    if (!fieldsMatch) {
      return {
        ok: false,
        error: "Feature intent selection is stale or does not match the active plan/checkpoint/targets.",
        errorCode: "FEATURE_INTENT_BINDING_STALE",
      };
    }
  }

  const requestedPaths = requestedMutationPaths(args, state);
  // Only the single scope-authority gate constrains mutation targets.
  const authorityGate = resolveScopeAuthorityGate(required);
  const snapshotGates = authorityGate
    ? [{
      gate: authorityGate,
      snapshots: completed[authorityGate]?.targetSnapshots,
    }].filter((item) => Array.isArray(item.snapshots) && item.snapshots.length)
    : [];
  if (requestedPaths.length && snapshotGates.length) {
    const caseFold = (value) => process.platform === "win32" ? value.toLowerCase() : value;
    for (const snapshotGate of snapshotGates) {
      const byPath = new Map(
        snapshotGate.snapshots
          .filter((item) => item && item.absolutePath)
          .map((item) => [caseFold(path.resolve(String(item.absolutePath))), item])
      );
      for (const requestedPath of requestedPaths) {
        const snapshot = byPath.get(caseFold(requestedPath));
        if (!snapshot) {
          return {
            ok: false,
            error: `Mutation target was not covered by ${snapshotGate.gate} evidence: ${requestedPath}`,
            errorCode: "GATE_TARGET_MISMATCH",
          };
        }
        const existsNow = fs.existsSync(requestedPath);
        if (Boolean(snapshot.exists) !== existsNow) {
          return {
            ok: false,
            error: `Mutation target changed since ${snapshotGate.gate} validation: ${requestedPath}`,
            errorCode: "GATE_TARGET_STALE",
          };
        }
        if (existsNow && snapshot.fileHash) {
          let currentHash;
          try {
            currentHash = sha1File(requestedPath);
          } catch {
            return {
              ok: false,
              error: `Mutation target could not be re-read: ${requestedPath}`,
              errorCode: "GATE_TARGET_STALE",
            };
          }
          if (currentHash !== String(snapshot.fileHash)) {
            return {
              ok: false,
              error: `Mutation target content changed since ${snapshotGate.gate} validation: ${requestedPath}`,
              errorCode: "GATE_TARGET_STALE",
            };
          }
        }
      }
    }
  }
  return { ok: true };
}

const ROUTE_MUTATION_TOOLS = new Set([
  "write_file",
  "replace_in_file",
  "delete_file",
  "apply_edit_bundle",
]);

function stableValue(value) {
  if (Array.isArray(value)) return value.map(stableValue);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value).sort().map((key) => [key, stableValue(value[key])])
    );
  }
  return value;
}

function canonicalHash(value) {
  return crypto
    .createHash("sha256")
    .update(JSON.stringify(stableValue(value)), "utf8")
    .digest("hex");
}

function normalizeRoutePath(value) {
  let result = String(value || "").trim().replace(/\\/g, "/");
  while (result.startsWith("./")) result = result.slice(2);
  if (result.toLowerCase().startsWith("project://")) result = result.slice("project://".length);
  return result.replace(/^\/+|\/+$/g, "");
}

function normalizedSelectionSnapshots(values) {
  if (!Array.isArray(values)) return [];
  return values
    .filter((item) => item && typeof item === "object")
    .map((item) => ({
      path: normalizeRoutePath(item.path || item.relativePath),
      exists: Boolean(item.exists),
      fileHash: String(item.fileHash || ""),
    }))
    .filter((item) => item.path)
    .sort((left, right) => left.path.toLowerCase().localeCompare(right.path.toLowerCase()));
}

function selectionBindingForState(state) {
  const continuity = state.continuity && typeof state.continuity === "object"
    ? state.continuity
    : {};
  const checkpoint = continuity.checkpoint && typeof continuity.checkpoint === "object"
    ? continuity.checkpoint
    : {};
  const binding = {
    planRevision: String(state.planRevision || ""),
    activeSliceId: String(state.activeSliceId || ""),
    checkpointHash: String(
      checkpoint.checkpointHash
      || continuity.planIdentityHash
      || canonicalHash(checkpoint)
    ),
    targetSnapshotsHash: canonicalHash(
      normalizedSelectionSnapshots(
        (Array.isArray(state.selectedTargetSnapshots) && state.selectedTargetSnapshots.length)
          ? state.selectedTargetSnapshots
          : state.featureTargetSnapshots
      )
    ),
    selectedHypothesisId: String(state.selectedHypothesisId || ""),
    selectedCandidateId: String(state.selectedCandidateId || ""),
    selectedIntentId: String(state.selectedIntentId || ""),
    intentContractHash: String(state.intentContractHash || ""),
  };
  if (Object.prototype.hasOwnProperty.call(state, "selectedTargetSliceId")) {
    binding.targetSnapshotSliceId = String(
      state.selectedTargetSliceId || state.activeSliceId || ""
    );
  }
  binding.bindingHash = canonicalHash(binding);
  return binding;
}

function validateSelectionState(state) {
  const session = state.runtimeDebugSession && typeof state.runtimeDebugSession === "object"
    ? state.runtimeDebugSession
    : {};
  const comparison = session.patchCandidateComparison
    && typeof session.patchCandidateComparison === "object"
    ? session.patchCandidateComparison
    : {};
  const patchEvidence = session.patchEvidence && typeof session.patchEvidence === "object"
    ? session.patchEvidence
    : {};
  const topHypothesis = String(state.selectedHypothesisId || "");
  const nestedHypothesis = String(session.selectedHypothesisId || "");
  const topCandidate = String(state.selectedCandidateId || "");
  const nestedCandidate = String(comparison.selectedCandidateId || "");
  const appliedCandidate = String(patchEvidence.selectedPatchCandidateId || "");
  if (topHypothesis !== nestedHypothesis) {
    return {
      ok: false,
      errorCode: "TASK_SELECTION_STATE_MISMATCH",
      error: "Top-level selectedHypothesisId disagrees with runtimeDebugSession.",
    };
  }
  if (topCandidate !== nestedCandidate) {
    return {
      ok: false,
      errorCode: "TASK_SELECTION_STATE_MISMATCH",
      error: "Top-level selectedCandidateId disagrees with patchCandidateComparison.",
    };
  }
  if (appliedCandidate && appliedCandidate !== topCandidate) {
    return {
      ok: false,
      errorCode: "TASK_SELECTION_STATE_MISMATCH",
      error: "Patch evidence disagrees with selectedCandidateId.",
    };
  }
  const storedBinding = state.selectionBinding && typeof state.selectionBinding === "object"
    ? state.selectionBinding
    : {};
  if (storedBinding.bindingHash) {
    const expected = selectionBindingForState(state);
    if (
      String(storedBinding.bindingHash || "") !== expected.bindingHash
      || String(storedBinding.checkpointHash || "") !== expected.checkpointHash
      || String(storedBinding.targetSnapshotsHash || "") !== expected.targetSnapshotsHash
    ) {
      return {
        ok: false,
        errorCode: "TASK_SELECTION_BINDING_STALE",
        error: "Runtime selection binding is stale for the plan, slice, checkpoint, or targets.",
      };
    }
  }
  return { ok: true };
}

function routePathMatches(requestedAbsolute, state, selectedFile) {
  const projectFile = String(state.projectFile || "").trim();
  const projectRoot = projectFile.toLowerCase().endsWith(".uproject")
    ? path.dirname(projectFile)
    : projectFile;
  const requested = normalizeRoutePath(
    projectRoot
      ? path.relative(path.resolve(projectRoot), path.resolve(requestedAbsolute))
      : requestedAbsolute
  ).toLowerCase();
  const selected = normalizeRoutePath(selectedFile).toLowerCase();
  return Boolean(requested && selected && requested === selected);
}

function taskAuthorizationForState(state) {
  const route = state?.toolRoute && typeof state.toolRoute === "object"
    ? state.toolRoute
    : {};
  return {
    taskSessionId: String(state?.taskSessionId || ""),
    authToken: String(state?.authToken || ""),
    ownerCapability: String(state?.ownerCapability || ""),
    conversationId: String(state?.conversationId || ""),
    planId: String(state?.planId || ""),
    planRevision: String(state?.planRevision || ""),
    activeSliceId: String(state?.activeSliceId || ""),
    routeHash: String(route.routeHash || ""),
    routePhase: String(route.phase || ""),
  };
}

function authRefreshFailure(result, state, mismatchedFields = null) {
  if (
    !state
    || !result
    || (result.errorCode !== "TASK_ROUTE_STALE"
      && result.errorCode !== "TASK_AUTH_MISMATCH")
  ) {
    return result;
  }
  if (result.errorCode === "TASK_AUTH_MISMATCH") {
    const context = {
      taskSessionId: String(state.taskSessionId || ""),
      planId: String(state.planId || ""),
      planRevision: String(state.planRevision || ""),
      activeSliceId: String(state.activeSliceId || ""),
    };
    if (Array.isArray(mismatchedFields) && mismatchedFields.length) {
      context.mismatchedFields = mismatchedFields.map(String);
    }
    const recovery = recoveryAction(result.errorCode);
    const payload = {
      ...result,
      authorizationContext: context,
      taskAuthorization: {
        taskSessionId: context.taskSessionId,
        planId: context.planId,
        planRevision: context.planRevision,
        activeSliceId: context.activeSliceId,
      },
      nextAction: recovery.action,
      nextActionIsTool: recovery.isTool,
    };
    if (Array.isArray(mismatchedFields) && mismatchedFields.length) {
      payload.mismatchedFields = mismatchedFields.map(String);
    }
    return payload;
  }
  const recovery = recoveryAction(result.errorCode);
  return {
    ...result,
    taskAuthorization: taskAuthorizationForState(state),
    nextAction: recovery.action,
    nextActionIsTool: recovery.isTool,
  };
}

function checkpointConflictFailure(state, conflicts, error = "Task checkpoint conflicts with current files.") {
  const taskAuthorization = taskAuthorizationForState(state);
  return {
    ok: false,
    errorCode: "TASK_CHECKPOINT_CONFLICT",
    error,
    conflicts: Array.isArray(conflicts) ? conflicts : [],
    taskAuthorization,
    nextAction: "unreal_task_checkpoint",
    nextActionIsTool: true,
    nextActionArgs: {
      action: "rebase",
      acceptCurrentFiles: true,
      includeGitChanges: false,
      taskAuthorization,
    },
    nextActions: ["unreal_task_checkpoint", "unreal_task_status"],
    retryable: false,
    stopCurrentWorkflow: false,
    recoveryActionRequired: true,
    agentInstruction: (
      "Call unreal_task_checkpoint exactly once with nextActionArgs to rebase the same task. "
      + "Do not cancel, quarantine, or create a new task for an ordinary checkpoint conflict."
    ),
  };
}

function validateToolRoute(state, fields, args, toolName) {
  const route = effectiveToolRouteForState(state);
  if (
    route
    && String(route.routeHash || "")
    !== String(state.toolRoute?.routeHash || "")
  ) {
    state.toolRoute = route;
    state.toolRouteUsage = {
      routeHash: String(route.routeHash || ""),
      phase: String(route.phase || ""),
      roleSession: String(route.roleSession || ""),
      count: 0,
      calls: [],
      resetReason: "gate_ttl_expired",
    };
  }
  const activeRoute = route && typeof route === "object"
    ? state.toolRoute
    : null;
  if (!activeRoute) return { ok: true, legacy: true };
  const requiredFirstTool = String(activeRoute.requiredFirstTool || "").trim();
  const completion = state.routeFacts?.requiredFirstToolAttempt;
  const requiredFirstToolCompleted = Boolean(
    completion
    && typeof completion === "object"
    && String(completion.tool || "") === requiredFirstTool
    && String(completion.planRevision || "") === String(state.planRevision || "")
  );
  if (
    toolName
    && requiredFirstTool
    && !requiredFirstToolCompleted
    && toolName !== requiredFirstTool
  ) {
    const authorization = taskAuthorizationForState(state);
    return {
      ok: false,
      errorCode: "TASK_REQUIRED_FIRST_TOOL",
      error: `${requiredFirstTool} must run before other tools in this plan.`,
      toolRoute: activeRoute,
      taskAuthorization: authorization,
      nextAction: requiredFirstTool,
      nextActionArgs: { taskAuthorization: compactTaskAuthorization(authorization) },
      retryable: true,
      agentInstruction: `Call ${requiredFirstTool} now with the returned taskAuthorization. Do not inspect or edit files first.`,
    };
  }
  const activeTools = new Set(Array.isArray(activeRoute.activeTools) ? activeRoute.activeTools.map(String) : []);
  // A stale authorization must not instruct the model to retry a tool that the
  // refreshed route cannot execute. Surface the real phase/gate action first.
  if (toolName && !activeTools.has(toolName)) {
    const pending = Array.isArray(activeRoute.pendingGates)
      ? activeRoute.pendingGates.map(String).filter(Boolean)
      : [];
    return {
      ok: false,
      errorCode: "TASK_TOOL_NOT_ACTIVE",
      error: `${toolName} is not active in route phase ${String(activeRoute.phase || "")}.`,
      toolRoute: activeRoute,
      taskAuthorization: taskAuthorizationForState(state),
      nextAction: pending[0] || "use_active_route_tool",
    };
  }
  if (
    !fields.routeHash
    || !fields.routePhase
    || fields.routeHash !== String(activeRoute.routeHash || "")
    || fields.routePhase !== String(activeRoute.phase || "")
  ) {
    return {
      ok: false,
      errorCode: "TASK_ROUTE_STALE",
      error: "taskAuthorization routeHash/routePhase is missing or stale.",
      toolRoute: activeRoute,
    };
  }
  if (toolName && ROUTE_MUTATION_TOOLS.has(toolName)) {
    if (String(activeRoute.roleSession || "") !== "executor") {
      return {
        ok: false,
        errorCode: "TASK_TOOL_NOT_ACTIVE",
        error: `${toolName} requires the executor role session.`,
        toolRoute: activeRoute,
      };
    }
    const selectedSlice = activeRoute.selectedSlice && typeof activeRoute.selectedSlice === "object"
      ? activeRoute.selectedSlice
      : {};
    const selectedFiles = Array.isArray(selectedSlice.files)
      ? selectedSlice.files.map(String).filter(Boolean)
      : [];
    const requestedPaths = requestedMutationPaths(args, state);
    const maxFiles = Math.max(1, Math.min(4, Number(activeRoute.maxFilesPerSlice || 2)));
    if (!selectedFiles.length || !requestedPaths.length) {
      return {
        ok: false,
        errorCode: "TASK_SLICE_SCOPE_REQUIRED",
        error: "Mutation requires a non-empty server-selected slice.",
        toolRoute: activeRoute,
        taskAuthorization: taskAuthorizationForState(state),
        nextAction: "unreal_code_sketch_claim_validate",
        nextActionArgs: { taskAuthorization: compactTaskAuthorization(taskAuthorizationForState(state)) },
        maxFilesPerSlice: maxFiles,
      };
    }
    if (requestedPaths.length > maxFiles) {
      return {
        ok: false,
        errorCode: "TASK_ROUTE_SCOPE_EXCEEDED",
        error: `Mutation file count exceeds active slice limit (${requestedPaths.length} > ${maxFiles}).`,
        toolRoute: activeRoute,
        taskAuthorization: taskAuthorizationForState(state),
        nextAction: "unreal_code_sketch_claim_validate",
        nextActionArgs: { taskAuthorization: compactTaskAuthorization(taskAuthorizationForState(state)) },
        maxFilesPerSlice: maxFiles,
      };
    }
    const outsideSlice = requestedPaths.filter(
      (requested) => !selectedFiles.some(
        (selected) => routePathMatches(requested, state, selected)
      )
    );
    if (outsideSlice.length) {
      return {
        ok: false,
        errorCode: "TASK_SLICE_TARGET_MISMATCH",
        error: `Mutation target is outside selected slice: ${outsideSlice[0]}`,
        toolRoute: activeRoute,
        taskAuthorization: taskAuthorizationForState(state),
        nextAction: "unreal_code_sketch_claim_validate",
        nextActionArgs: { taskAuthorization: compactTaskAuthorization(taskAuthorizationForState(state)) },
        maxFilesPerSlice: maxFiles,
      };
    }
  }
  return { ok: true, route: activeRoute };
}

const ROUTE_RESERVATION_TTL_BY_TOOL_MS = Object.freeze({
  read_file: 120_000,
  read_file_range: 120_000,
  read_symbol: 120_000,
  list_directory: 300_000,
  search_files: 600_000,
});
const ROUTE_RESERVATION_TTL_DEFAULT_MS = 180_000;

function reservationTtlMs(toolName) {
  const key = String(toolName || "");
  return ROUTE_RESERVATION_TTL_BY_TOOL_MS[key] || ROUTE_RESERVATION_TTL_DEFAULT_MS;
}

function renewContinuityLeaseForActivity(state, nowMs = Date.now()) {
  const continuity = state?.continuity && typeof state.continuity === "object"
    ? { ...state.continuity }
    : null;
  const lease = continuity?.lease && typeof continuity.lease === "object"
    ? { ...continuity.lease }
    : null;
  if (!lease || String(lease.status || "") !== "active") return false;
  const expiryMs = Date.parse(String(lease.expiresAt || ""));
  if (!Number.isFinite(expiryMs) || expiryMs <= nowMs) return false;
  const rawTtl = Number(lease.ttlSeconds || 1800);
  const ttlSeconds = Math.max(
    60,
    Math.min(86400, Number.isFinite(rawTtl) ? rawTtl : 1800)
  );
  const heartbeatAt = new Date(nowMs).toISOString();
  continuity.lease = {
    ...lease,
    status: "active",
    ttlSeconds,
    heartbeatAt,
    expiresAt: new Date(nowMs + ttlSeconds * 1000).toISOString(),
    renewalReason: "route_tool_activity",
  };
  if (!continuity.lease.acquiredAt) continuity.lease.acquiredAt = heartbeatAt;
  state.continuity = continuity;
  return true;
}

function purgeExpiredReservations(usage, nowMs = Date.now()) {
  const list = Array.isArray(usage.reservations)
    ? usage.reservations.filter((entry) => {
      if (!entry || typeof entry !== "object") return false;
      const expiresAt = Date.parse(String(entry.expiresAt || ""));
      if (Number.isFinite(expiresAt) && expiresAt > nowMs) return true;
      // Same-process grace: keep briefly if heartbeat is fresher than hard expiry.
      const ownerPid = Number(entry.ownerPid || 0);
      const heartbeatAt = Date.parse(String(entry.lastHeartbeatAt || entry.createdAt || ""));
      if (
        ownerPid === process.pid
        && Number.isFinite(heartbeatAt)
        && nowMs - heartbeatAt < reservationTtlMs(entry.tool)
      ) {
        return true;
      }
      return false;
    })
    : [];
  usage.reservations = list;
  usage.reserved = list.length;
  return list;
}

function mutateRouteBudget(
  workspaceRoot,
  taskSessionId,
  fields,
  args,
  toolName,
  mode = "consume",
  reservationId = ""
) {
  if (!toolName) return { ok: true };
  const stateRoot = resolveAgentStateRoot(workspaceRoot);
  const dir = taskDir(workspaceRoot, taskSessionId, stateRoot);
  const statePath = path.join(dir, "state.json");
  const acquired = tryAcquireCrossProcessLock(statePath, "task_route_call", stateRoot);
  if (!acquired.ok) {
    return {
      ok: false,
      errorCode: "TASK_STATE_LOCKED",
      error: "Task route call ledger is busy.",
    };
  }
  try {
    const currentResult = readTaskStateResult(workspaceRoot, taskSessionId, stateRoot);
    const current = currentResult.state;
    if (!current) {
      return {
        ok: false,
        errorCode: currentResult.errorCode || "TASK_STATE_MISSING",
        error: "Task state disappeared before route call recording.",
      };
    }
    const routeCheck = validateToolRoute(current, fields, args, toolName);
    if (!routeCheck.ok) return routeCheck;
    if (routeCheck.legacy) return { ok: true };
    const route = routeCheck.route;
    let usage = current.toolRouteUsage && typeof current.toolRouteUsage === "object"
      ? { ...current.toolRouteUsage }
      : {};
    if (String(usage.routeHash || "") !== String(route.routeHash || "")) {
      usage = {
        routeHash: String(route.routeHash || ""),
        phase: String(route.phase || ""),
        roleSession: String(route.roleSession || ""),
        count: 0,
        reserved: 0,
        reservations: [],
        calls: [],
      };
    }
    // Drop legacy counter-only reservations after crash; they have no TTL/ID.
    if (!Array.isArray(usage.reservations) && Number(usage.reserved || 0) > 0) {
      usage.reservations = [];
      usage.reserved = 0;
    }
    const reservations = purgeExpiredReservations(usage);
    const count = Number(usage.count || 0);
    const reserved = reservations.length;
    // Keep the write server aligned with the Python route contract. The
    // current planner/executor cap is eight, while narrower analysis/verifier
    // routes may advertise lower limits. Never silently clamp a server-issued
    // route to a different budget than the route returned to the model.
    const limit = Math.max(2, Math.min(8, Number(route.maxToolCallsPerPhase || 2)));
    // A routed tool call is active task work. Renew the cross-platform task
    // lease in the same atomic state write as its route-budget mutation.
    renewContinuityLeaseForActivity(current);
    if (mode === "rollback") {
      const targetId = String(reservationId || "").trim();
      if (!targetId) {
        return {
          ok: false,
          errorCode: "TASK_RESERVATION_NOT_FOUND",
          error: "Reservation id is required to rollback a route budget slot.",
        };
      }
      const next = reservations.filter((entry) => String(entry.reservationId || "") !== targetId);
      if (next.length === reservations.length) {
        return {
          ok: false,
          errorCode: "TASK_RESERVATION_NOT_FOUND",
          error: `Unknown reservation id: ${targetId}`,
        };
      }
      usage.reservations = next;
      usage.reserved = next.length;
      current.toolRouteUsage = usage;
      current.updatedAt = new Date().toISOString();
      atomicWriteJson(statePath, current);
      return { ok: true, state: current, toolRoute: route, toolRouteUsage: usage };
    }
    if (mode === "commit") {
      const targetId = String(reservationId || "").trim();
      if (!targetId) {
        return {
          ok: false,
          errorCode: "TASK_RESERVATION_NOT_FOUND",
          error: "Reservation id is required to commit a route budget slot.",
        };
      }
      const next = reservations.filter((entry) => String(entry.reservationId || "") !== targetId);
      if (next.length === reservations.length) {
        return {
          ok: false,
          errorCode: "TASK_RESERVATION_NOT_FOUND",
          error: `Unknown reservation id: ${targetId}`,
        };
      }
      const calls = Array.isArray(usage.calls) ? usage.calls.map(String) : [];
      calls.push(toolName);
      usage.reservations = next;
      usage.reserved = next.length;
      usage.count = count + 1;
      usage.calls = calls.slice(-limit);
      current.toolRouteUsage = usage;
      current.updatedAt = new Date().toISOString();
      atomicWriteJson(statePath, current);
      return { ok: true, state: current, toolRoute: route, toolRouteUsage: usage };
    }
    if (mode === "heartbeat") {
      const targetId = String(reservationId || "").trim();
      if (!targetId) {
        return {
          ok: false,
          errorCode: "TASK_RESERVATION_NOT_FOUND",
          error: "Reservation id is required to heartbeat a route budget slot.",
        };
      }
      const idx = reservations.findIndex(
        (entry) => String(entry.reservationId || "") === targetId
      );
      if (idx < 0) {
        return {
          ok: false,
          errorCode: "TASK_RESERVATION_NOT_FOUND",
          error: `Unknown reservation id: ${targetId}`,
        };
      }
      const now = Date.now();
      const ttl = reservationTtlMs(reservations[idx].tool || toolName);
      const next = reservations.map((entry, entryIdx) => {
        if (entryIdx !== idx) return entry;
        return {
          ...entry,
          lastHeartbeatAt: new Date(now).toISOString(),
          expiresAt: new Date(now + ttl).toISOString(),
          ownerPid: process.pid,
        };
      });
      usage.reservations = next;
      usage.reserved = next.length;
      current.toolRouteUsage = usage;
      current.updatedAt = new Date().toISOString();
      atomicWriteJson(statePath, current);
      return {
        ok: true,
        state: current,
        toolRoute: route,
        toolRouteUsage: usage,
        reservationId: targetId,
      };
    }
    if (count + reserved >= limit) {
      const checkpointAuthorization = taskAuthorizationForState(current);
      return {
        ok: false,
        errorCode: "TASK_PHASE_TOOL_BUDGET_EXHAUSTED",
        error: `Phase tool-call budget exhausted (${count + reserved}/${limit}).`,
        toolRoute: route,
        toolRouteUsage: usage,
        taskAuthorization: checkpointAuthorization,
        nextAction: "unreal_task_checkpoint",
        nextActionArgs: {
          action: "record",
          phase: String(route.phase || "working"),
          requiredNextAction: String(toolName || ""),
          includeGitChanges: false,
          taskAuthorization: {
            taskSessionId: checkpointAuthorization.taskSessionId,
            ownerCapability: checkpointAuthorization.ownerCapability,
          },
        },
        nextActions: [
          "unreal_task_checkpoint",
          "unreal_task_status",
          "unreal_task_cancel",
        ],
        agentInstruction:
          "Call unreal_task_checkpoint exactly once with nextActionArgs (action=record). "
          + "action=status only inspects state and does not renew the work-call budget. "
          + "Then continue requiredNextAction with the returned taskAuthorization.",
      };
    }
    if (mode === "reserve") {
      const id = crypto.randomUUID();
      const createdAt = new Date().toISOString();
      const ttl = reservationTtlMs(toolName);
      const expiresAt = new Date(Date.now() + ttl).toISOString();
      const entry = {
        reservationId: id,
        tool: String(toolName),
        routeHash: String(route.routeHash || ""),
        ownerPid: process.pid,
        createdAt,
        lastHeartbeatAt: createdAt,
        expiresAt,
      };
      const next = [...reservations, entry];
      usage.reservations = next;
      usage.reserved = next.length;
      current.toolRouteUsage = usage;
      // Calling the required diagnostic once is enough to release its
      // server-directed recovery path. The build can legitimately stop at a
      // pre-build validation gate and require static_validate_project; waiting
      // for a successful/committed build would deadlock that recovery.
      if (String(route.requiredFirstTool || "") === String(toolName || "")) {
        const routeFacts = current.routeFacts && typeof current.routeFacts === "object"
          ? { ...current.routeFacts }
          : {};
        routeFacts.requiredFirstToolAttempt = {
          tool: String(toolName),
          planRevision: String(current.planRevision || ""),
          attemptedAt: new Date().toISOString(),
        };
        current.routeFacts = routeFacts;
      }
      current.updatedAt = new Date().toISOString();
      atomicWriteJson(statePath, current);
      return {
        ok: true,
        state: current,
        toolRoute: route,
        toolRouteUsage: usage,
        reservationId: id,
      };
    }
    const calls = Array.isArray(usage.calls) ? usage.calls.map(String) : [];
    calls.push(toolName);
    usage.count = count + 1;
    usage.calls = calls.slice(-limit);
    usage.reserved = reserved;
    usage.reservations = reservations;
    current.toolRouteUsage = usage;
    current.updatedAt = new Date().toISOString();
    atomicWriteJson(statePath, current);
    return { ok: true, state: current, toolRoute: route, toolRouteUsage: usage };
  } finally {
    releaseCrossProcessLock(statePath);
  }
}

function consumeRouteCall(workspaceRoot, taskSessionId, fields, args, toolName) {
  return mutateRouteBudget(workspaceRoot, taskSessionId, fields, args, toolName, "consume");
}

function reserveRouteCall(workspaceRoot, taskSessionId, fields, args, toolName) {
  return mutateRouteBudget(workspaceRoot, taskSessionId, fields, args, toolName, "reserve");
}

function commitRouteReservation(workspaceRoot, taskSessionId, fields, args, toolName, reservationId = "") {
  return mutateRouteBudget(
    workspaceRoot,
    taskSessionId,
    fields,
    args,
    toolName,
    "commit",
    reservationId
  );
}

function rollbackRouteReservation(workspaceRoot, taskSessionId, fields, args, toolName, reservationId = "") {
  return mutateRouteBudget(
    workspaceRoot,
    taskSessionId,
    fields,
    args,
    toolName,
    "rollback",
    reservationId
  );
}

function heartbeatRouteReservation(workspaceRoot, taskSessionId, fields, args, toolName, reservationId = "") {
  return mutateRouteBudget(
    workspaceRoot,
    taskSessionId,
    fields,
    args,
    toolName,
    "heartbeat",
    reservationId
  );
}

const SAFE_ROUTE_RECOVERY_TOOLS = new Set([
  "get_workspace_info",
  "get_active_project",
  "list_active_tasks",
  "cancel_active_task",
  "quarantine_corrupt_task",
]);

function canonicalWorkspaceRoot(value) {
  const resolved = path.resolve(String(value || ""));
  return process.platform === "win32" ? resolved.toLowerCase() : resolved;
}

function canonicalProjectIdentity(value, workspaceRoot = "") {
  const raw = String(value || "").trim();
  if (!raw) return "";
  const resolved = path.resolve(
    path.isAbsolute(raw) ? raw : path.join(workspaceRoot || process.cwd(), raw)
  );
  return process.platform === "win32" ? resolved.toLowerCase() : resolved;
}

function validateTaskRouteScope(
  state,
  workspaceRoot,
  activeProject = ""
) {
  const routeScope = state?.routeScope
    && typeof state.routeScope === "object"
    && !Array.isArray(state.routeScope)
    ? state.routeScope
    : {};
  const stateProject = canonicalProjectIdentity(
    routeScope.projectFile || state?.projectFile || "",
    workspaceRoot
  );
  if (stateProject) {
    const currentProject = canonicalProjectIdentity(activeProject, workspaceRoot);
    if (!currentProject || currentProject !== stateProject) {
      return {
        ok: false,
        errorCode: "TASK_PROJECT_MISMATCH",
        error: "Task authorization belongs to a different active Unreal project.",
        expectedProject: stateProject,
        activeProject: currentProject,
      };
    }
    return { ok: true };
  }
  const rawWorkspace = String(
    routeScope.workspaceRoot || state?.workspaceRoot || ""
  ).trim();
  if (
    rawWorkspace
    && canonicalWorkspaceRoot(rawWorkspace)
    !== canonicalWorkspaceRoot(workspaceRoot)
  ) {
    return {
      ok: false,
      errorCode: "TASK_ROUTE_SCOPE_MISMATCH",
      error: "Task authorization belongs to a different workspace route scope.",
    };
  }
  return { ok: true };
}

function effectiveToolRouteForState(state, nowMs = Date.now()) {
  let route = state?.toolRoute && typeof state.toolRoute === "object"
    ? state.toolRoute
    : null;
  if (!route) return null;
  for (let index = 0; index < 64; index += 1) {
    const transition = route.expiryTransition
      && typeof route.expiryTransition === "object"
      ? route.expiryTransition
      : {};
    const fallback = transition.route && typeof transition.route === "object"
      ? transition.route
      : null;
    const expiresAt = Date.parse(String(transition.at || ""));
    if (!fallback || !Number.isFinite(expiresAt) || expiresAt > nowMs) {
      return route;
    }
    route = fallback;
  }
  return route;
}

function discoverActiveTaskContext(workspaceRoot, activeProject = "", options = {}) {
  const ownerCapability = String(
    options.ownerCapability || options.owner_capability || ""
  ).trim();
  const conversationId = String(
    options.conversationId || options.conversation_id || ""
  ).trim();
  // CallTool authorization must prove ownership. ListTools/watchers may list a
  // single project task's tools without the secret (execution still gated).
  const requireOwnerCapability = options.requireOwnerCapability === true;
  let stateRoot;
  let tasksRoot;
  try {
    stateRoot = ensureStateRootLayout(resolveAgentStateRoot(workspaceRoot));
    tasksRoot = path.join(stateRoot, "tasks");
  } catch (error) {
    return {
      status: "blocked",
      errorCode: "TASK_STATE_ROOT_UNAVAILABLE",
      error: `Task state root is unavailable: ${error && error.message ? error.message : error}`,
    };
  }
  const currentWorkspace = canonicalWorkspaceRoot(workspaceRoot);
  const currentProject = canonicalProjectIdentity(activeProject, workspaceRoot);
  let entries;
  try {
    entries = fs.readdirSync(tasksRoot, { withFileTypes: true })
      .filter((entry) => entry.isDirectory())
      .sort((left, right) => left.name.localeCompare(right.name));
  } catch (error) {
    return {
      status: "blocked",
      errorCode: "TASK_STATE_ROOT_UNAVAILABLE",
      error: `Task state root is unreadable: ${error && error.message ? error.message : error}`,
    };
  }
  const running = [];
  const unprovenCandidates = [];
  let scopedClaimants = 0;
  let unmatchedLegacyClaimants = 0;
  for (const entry of entries) {
    const entryDir = path.join(tasksRoot, entry.name);
    const ownerPath = path.join(entryDir, "workspace-root.txt");
    const scopePath = path.join(entryDir, "route-scope.json");
    let ownerHint = "";
    let scopeHint = {};
    if (fs.existsSync(ownerPath)) {
      try {
        ownerHint = canonicalWorkspaceRoot(
          fs.readFileSync(ownerPath, "utf8").trim()
        );
      } catch {
        ownerHint = "";
      }
    }
    if (fs.existsSync(scopePath)) {
      try {
        const parsedScope = JSON.parse(fs.readFileSync(scopePath, "utf8"));
        if (parsedScope && typeof parsedScope === "object" && !Array.isArray(parsedScope)) {
          scopeHint = parsedScope;
        }
      } catch {
        scopeHint = {};
      }
    }
    const hintedProject = canonicalProjectIdentity(
      scopeHint.projectFile || "",
      workspaceRoot
    );
    const hintedWorkspace = String(scopeHint.workspaceRoot || ownerHint).trim()
      ? canonicalWorkspaceRoot(scopeHint.workspaceRoot || ownerHint)
      : "";
    const hintClaimsCurrent = Boolean(
      (hintedProject && currentProject && hintedProject === currentProject)
      || (!hintedProject && hintedWorkspace === currentWorkspace)
    );
    const result = readTaskStateResult(workspaceRoot, entry.name, stateRoot);
    if (result.errorCode === "TASK_STATE_CORRUPT") {
      if (!hintClaimsCurrent) continue;
      return {
        status: "ambiguous_or_corrupt",
        errorCode: "TASK_STATE_CORRUPT",
        error: `Task state is corrupt: ${entry.name}.`,
      };
    }
    const routeScope = result.state?.routeScope
      && typeof result.state.routeScope === "object"
      && !Array.isArray(result.state.routeScope)
      ? result.state.routeScope
      : {};
    const stateProject = canonicalProjectIdentity(
      routeScope.projectFile || result.state?.projectFile || "",
      workspaceRoot
    );
    const rawStateOwner = String(
      routeScope.workspaceRoot || result.state?.workspaceRoot || ""
    ).trim();
    const stateOwner = rawStateOwner
      ? canonicalWorkspaceRoot(rawStateOwner)
      : hintedWorkspace;
    if (!stateProject && !stateOwner) {
      // Legacy states cannot claim arbitrary workspaces through discovery.
      // Explicit task authorization remains supported by direct state lookup.
      continue;
    }
    if (hintedProject && stateProject && hintedProject !== stateProject) {
      if (hintedProject === currentProject || stateProject === currentProject) {
        return {
          status: "ambiguous_or_corrupt",
          errorCode: "TASK_SCOPE_MISMATCH",
          error: `Task project ownership mismatch: ${entry.name}.`,
        };
      }
      continue;
    }
    if (
      !stateProject
      && hintedWorkspace
      && stateOwner
      && hintedWorkspace !== stateOwner
    ) {
      if (hintedWorkspace === currentWorkspace || stateOwner === currentWorkspace) {
        return {
          status: "ambiguous_or_corrupt",
          errorCode: "TASK_OWNER_HINT_MISMATCH",
          error: `Task workspace ownership mismatch: ${entry.name}.`,
        };
      }
      continue;
    }
    const ownsCurrent = Boolean(
      (stateProject && currentProject && stateProject === currentProject)
      || (!stateProject && stateOwner === currentWorkspace)
    );
    if (!ownsCurrent) continue;
    const effectiveRoute = effectiveToolRouteForState(result.state);
    if (
      effectiveRoute
      && String(effectiveRoute.routeHash || "")
      !== String(result.state.toolRoute?.routeHash || "")
    ) {
      result.state.toolRoute = effectiveRoute;
      result.state.toolRouteUsage = {
        routeHash: String(effectiveRoute.routeHash || ""),
        phase: String(effectiveRoute.phase || ""),
        roleSession: String(effectiveRoute.roleSession || ""),
        count: 0,
        calls: [],
        resetReason: "gate_ttl_expired",
      };
      const statePath = result.statePath;
      const acquired = statePath
        ? tryAcquireCrossProcessLock(
          statePath,
          "task_route_expiry",
          stateRoot
        )
        : { ok: false };
      if (acquired.ok) {
        try {
          const currentResult = readTaskStateResult(
            workspaceRoot,
            entry.name,
            stateRoot
          );
          const currentState = currentResult.state;
          const currentEffective = effectiveToolRouteForState(currentState);
          if (
            currentState
            && currentEffective
            && String(currentEffective.routeHash || "")
            !== String(currentState.toolRoute?.routeHash || "")
          ) {
            currentState.toolRoute = currentEffective;
            currentState.toolRouteUsage = {
              routeHash: String(currentEffective.routeHash || ""),
              phase: String(currentEffective.phase || ""),
              roleSession: String(currentEffective.roleSession || ""),
              count: 0,
              calls: [],
              resetReason: "gate_ttl_expired",
            };
            atomicWriteJson(statePath, currentState);
            result.state = currentState;
          }
        } finally {
          releaseCrossProcessLock(statePath);
        }
      }
    }
    if (result.state && String(result.state.status || "") === "running") {
      if (!result.state.toolRoute || typeof result.state.toolRoute !== "object") {
        // Legacy/orphan running tasks without toolRoute cannot own a route.
        // Hard-failing here blocked list_directory/plan/writes for the whole project.
        continue;
      }
      const mode = String(result.state.mode || "").trim().toLowerCase();
      const routeEligible = mode !== "plan_only" && mode !== "detached";
      if (!routeEligible) {
        continue;
      }
      const candidate = {
        taskSessionId: String(result.state.taskSessionId || entry.name),
        state: result.state,
        route: result.state.toolRoute,
      };
      if (!taskOwnsActiveToolRoute(result.state, conversationId, ownerCapability)) {
        if (
          String(result.state.conversationId || "").trim()
          || String(result.state.ownerCapability || "").trim()
        ) {
          scopedClaimants += 1;
        } else if (ownerCapability) {
          // Capability was claimed but this legacy task did not match.
          unmatchedLegacyClaimants += 1;
        }
        if (!requireOwnerCapability) {
          const continuity = result.state.continuity
            && typeof result.state.continuity === "object"
            ? result.state.continuity
            : {};
          const lease = continuity.lease && typeof continuity.lease === "object"
            ? continuity.lease
            : null;
          const recovery = continuity.recovery && typeof continuity.recovery === "object"
            ? continuity.recovery
            : {};
          const supervisor = result.state.autonomySupervisor
            && typeof result.state.autonomySupervisor === "object"
            ? result.state.autonomySupervisor
            : {};
          const leaseExpiry = lease ? Date.parse(String(lease.expiresAt || "")) : NaN;
          if (
            (lease && (
              String(lease.status || "") !== "active"
              || !Number.isFinite(leaseExpiry)
              || leaseExpiry <= Date.now()
            ))
            || (Array.isArray(recovery.conflicts) && recovery.conflicts.length)
            || (Array.isArray(supervisor.blockers) && supervisor.blockers.length)
          ) {
            return {
              status: "blocked",
              taskSessionId: String(result.state.taskSessionId || entry.name),
              state: result.state,
              route: result.state.toolRoute,
              errorCode: "TASK_ROUTE_BLOCKED",
              error: `Running task is blocked by lease or recovery state: ${entry.name}.`,
            };
          }
          unprovenCandidates.push(candidate);
        }
        continue;
      }
      const continuity = result.state.continuity
        && typeof result.state.continuity === "object"
        ? result.state.continuity
        : {};
      const lease = continuity.lease && typeof continuity.lease === "object"
        ? continuity.lease
        : null;
      const recovery = continuity.recovery && typeof continuity.recovery === "object"
        ? continuity.recovery
        : {};
      const supervisor = result.state.autonomySupervisor
        && typeof result.state.autonomySupervisor === "object"
        ? result.state.autonomySupervisor
        : {};
      const leaseExpiry = lease ? Date.parse(String(lease.expiresAt || "")) : NaN;
      if (
        (lease && (
          String(lease.status || "") !== "active"
          || !Number.isFinite(leaseExpiry)
          || leaseExpiry <= Date.now()
        ))
        || (Array.isArray(recovery.conflicts) && recovery.conflicts.length)
        || (Array.isArray(supervisor.blockers) && supervisor.blockers.length)
      ) {
        return {
          status: "blocked",
          taskSessionId: String(result.state.taskSessionId || entry.name),
          state: result.state,
          route: result.state.toolRoute,
          errorCode: "TASK_ROUTE_BLOCKED",
          error: `Running task is blocked by lease or recovery state: ${entry.name}.`,
        };
      }
      running.push(candidate);
    }
  }
  if (running.length === 1 && unprovenCandidates.length === 0) {
    return { status: "active", ...running[0] };
  }
  if (running.length > 1 || (running.length >= 1 && unprovenCandidates.length >= 1)) {
    return {
      status: "ambiguous_or_corrupt",
      errorCode: "MULTIPLE_HEALTHY_ROUTE_TASKS",
      error: "More than one running task owns an active tool route.",
      healthyRoutes: [...running, ...unprovenCandidates],
    };
  }
  if (requireOwnerCapability) {
    if (ownerCapability && (scopedClaimants > 0 || unmatchedLegacyClaimants > 0)) {
      return {
        status: "ambiguous_or_corrupt",
        errorCode: "TASK_ROUTE_CAPABILITY_MISMATCH",
        error: (
          "ownerCapability was provided but did not match any running task; "
          + "use the matching capability or omit it for legacy connection ownership."
        ),
        healthyRoutes: unprovenCandidates,
      };
    }
    if (scopedClaimants > 0) {
      return {
        status: "ambiguous_or_corrupt",
        errorCode: "TASK_ROUTE_OWNERSHIP_REQUIRED",
        error: "Running conversation-scoped task(s) require taskAuthorization.ownerCapability.",
        healthyRoutes: unprovenCandidates,
      };
    }
    return { status: "none" };
  }
  // ListTools / watcher: expose tools when exactly one project task is running.
  if (unprovenCandidates.length === 1) {
    return { status: "active", ...unprovenCandidates[0] };
  }
  if (unprovenCandidates.length > 1 || scopedClaimants > 1) {
    return {
      status: "ambiguous_or_corrupt",
      errorCode: "MULTIPLE_HEALTHY_ROUTE_TASKS",
      error: "More than one running task owns an active tool route.",
      healthyRoutes: unprovenCandidates,
    };
  }
  return { status: "none" };
}

function routeRecoveryNextAction(errorCode = "") {
  return recoveryAction(errorCode).action;
}

function listRunningTasksForProject(workspaceRoot, activeProject = "", options = {}) {
  const ownerCapability = String(
    options.ownerCapability || options.owner_capability || ""
  ).trim();
  const conversationId = String(
    options.conversationId || options.conversation_id || ""
  ).trim();
  let stateRoot;
  let tasksRoot;
  let entries = [];
  try {
    stateRoot = ensureStateRootLayout(resolveAgentStateRoot(workspaceRoot));
    tasksRoot = path.join(stateRoot, "tasks");
    entries = fs.readdirSync(tasksRoot, { withFileTypes: true })
      .filter((entry) => entry.isDirectory());
  } catch (error) {
    const err = new Error(
      `Task state root is unavailable: ${error && error.message ? error.message : error}`
    );
    err.errorCode = "TASK_STATE_ROOT_UNAVAILABLE";
    throw err;
  }
  const currentWorkspace = canonicalWorkspaceRoot(workspaceRoot);
  const currentProject = canonicalProjectIdentity(activeProject, workspaceRoot);
  const tasks = [];
  for (const entry of entries) {
    const entryDir = path.join(tasksRoot, entry.name);
    const statePath = path.join(entryDir, "state.json");
    if (!fs.existsSync(statePath)) continue;
    let ownerHint = "";
    let hintedProject = "";
    try {
      const ownerPath = path.join(entryDir, "workspace-root.txt");
      if (fs.existsSync(ownerPath)) {
        ownerHint = canonicalWorkspaceRoot(fs.readFileSync(ownerPath, "utf8").trim());
      }
      const scopePath = path.join(entryDir, "route-scope.json");
      if (fs.existsSync(scopePath)) {
        const rawScope = JSON.parse(fs.readFileSync(scopePath, "utf8"));
        if (rawScope && typeof rawScope === "object") {
          ownerHint = canonicalWorkspaceRoot(rawScope.workspaceRoot || ownerHint) || ownerHint;
          hintedProject = canonicalProjectIdentity(rawScope.projectFile || "", workspaceRoot);
        }
      }
    } catch {
      // fall through with whatever hints we have
    }
    let state = null;
    try {
      state = JSON.parse(fs.readFileSync(statePath, "utf8"));
    } catch {
      const claims = Boolean(
        (hintedProject && currentProject && hintedProject === currentProject)
        || (!hintedProject && ownerHint && ownerHint === currentWorkspace)
      );
      if (claims) {
        tasks.push({
          taskSessionId: entry.name,
          status: "corrupt",
          recoverable: false,
          availableActions: ["quarantine_corrupt_task"],
          error: `task state is corrupt: ${statePath}`,
          ownsActiveToolRoute: false,
          mcpConnectionId: "",
          updatedAt: "",
        });
      }
      continue;
    }
    if (!state || typeof state !== "object" || Array.isArray(state)) {
      const claims = Boolean(
        (hintedProject && currentProject && hintedProject === currentProject)
        || (!hintedProject && ownerHint && ownerHint === currentWorkspace)
      );
      if (claims) {
        tasks.push({
          taskSessionId: entry.name,
          status: "corrupt",
          recoverable: false,
          availableActions: ["quarantine_corrupt_task"],
          error: `task state is not an object: ${statePath}`,
          ownsActiveToolRoute: false,
          mcpConnectionId: "",
          updatedAt: "",
        });
      }
      continue;
    }
    const routeScope = state.routeScope
      && typeof state.routeScope === "object"
      && !Array.isArray(state.routeScope)
      ? state.routeScope
      : {};
    const stateProject = canonicalProjectIdentity(
      routeScope.projectFile || state.projectFile || "",
      workspaceRoot
    );
    const stateOwner = canonicalWorkspaceRoot(
      routeScope.workspaceRoot || state.workspaceRoot || ownerHint || ""
    );
    const ownsCurrent = Boolean(
      (stateProject && currentProject && stateProject === currentProject)
      || (!stateProject && stateOwner && stateOwner === currentWorkspace)
    );
    if (!ownsCurrent) continue;
    if (String(state.status || "") !== "running") continue;
    const routeMissing = !(state.toolRoute && typeof state.toolRoute === "object");
    const route = state.toolRoute && typeof state.toolRoute === "object"
      ? state.toolRoute
      : {};
    const pendingGates = Array.isArray(route.pendingGates)
      ? route.pendingGates.map(String).filter(Boolean)
      : (Array.isArray(state.pendingGates)
        ? state.pendingGates.map(String).filter(Boolean)
        : []);
    const routeNextAction = routeMissing
      ? "unreal_task_status"
      : (pendingGates[0] || "continue_with_current_tool_route");
    const connectionMatches = taskConnectionMatches(
      state,
      conversationId,
      ownerCapability
    );
    const summary = {
      taskSessionId: String(state.taskSessionId || entry.name),
      status: String(state.status || ""),
      mode: String(state.mode || ""),
      request: String(state.request || "").slice(0, 240),
      planId: String(state.planId || ""),
      planRevision: String(state.planRevision || ""),
      writesAllowed: state.writesAllowed === true
        || (state.writeGate && state.writeGate.writesAllowed === true),
      mcpConnectionId: String(state.mcpConnectionId || ""),
      conversationId: String(state.conversationId || ""),
      routePhase: String(route.phase || ""),
      routeMissing,
      pendingGates,
      routeNextAction,
      routeNextActionIsTool: Boolean(routeMissing || pendingGates.length),
      ownsActiveToolRoute: taskOwnsActiveToolRoute(
        state,
        conversationId,
        ownerCapability
      ),
      foreignHealthy: taskIsForeignHealthy(
        state,
        conversationId,
        ownerCapability
      ),
      connectionMatches,
      updatedAt: String(state.updatedAt || ""),
      activeJobId: String(state.activeJobId || ""),
      recoverable: true,
      availableActions: ["cancel_active_task"],
    };
    if (connectionMatches !== true) {
      delete summary.conversationId;
      summary.mcpConnectionId = "";
      summary.request = "";
    }
    delete summary.ownerCapability;
    tasks.push(summary);
  }
  return tasks;
}

const CATALOG_UNION_ERROR_CODES = new Set([
  "MULTIPLE_HEALTHY_ROUTE_TASKS",
  "TASK_ROUTE_OWNERSHIP_REQUIRED",
]);

function unionFromHealthyRoutes(healthyRoutes = []) {
  const tools = new Set();
  const routeParts = [];
  let taskCount = 0;
  for (const item of healthyRoutes) {
    const state = item && item.state ? item.state : item;
    if (!state || typeof state !== "object") continue;
    const route = item.route
      || effectiveToolRouteForState(state)
      || state.toolRoute
      || {};
    if (!route || typeof route !== "object") continue;
    taskCount += 1;
    for (const name of Array.isArray(route.activeTools) ? route.activeTools : []) {
      if (name) tools.add(String(name));
    }
    routeParts.push(
      `${String(state.taskSessionId || item.taskSessionId || "")}:${String(route.routeHash || "")}:${String(route.phase || "")}`
    );
  }
  const sortedTools = [...tools].sort();
  return {
    tools: sortedTools,
    fingerprint: `${taskCount}:${routeParts.join("|")}:${sortedTools.join(",")}`,
    taskCount,
  };
}

function collectProjectActiveToolUnion(workspaceRoot, activeProject = "", options = {}) {
  if (Array.isArray(options.healthyRoutes) && options.healthyRoutes.length) {
    return unionFromHealthyRoutes(options.healthyRoutes);
  }
  const stateRoot = ensureStateRootLayout(resolveAgentStateRoot(workspaceRoot));
  const tasksRoot = path.join(stateRoot, "tasks");
  let entries = [];
  try {
    entries = fs.readdirSync(tasksRoot, { withFileTypes: true })
      .filter((entry) => entry.isDirectory())
      .sort((left, right) => left.name.localeCompare(right.name));
  } catch {
    return { tools: [], fingerprint: "none", taskCount: 0 };
  }
  const currentWorkspace = canonicalWorkspaceRoot(workspaceRoot);
  const currentProject = canonicalProjectIdentity(activeProject, workspaceRoot);
  const healthyRoutes = [];
  for (const entry of entries) {
    const result = readTaskStateResult(workspaceRoot, entry.name, stateRoot);
    if (!result.state || typeof result.state !== "object") continue;
    const state = result.state;
    if (String(state.status || "") !== "running") continue;
    const mode = String(state.mode || "").trim().toLowerCase();
    if (mode === "plan_only" || mode === "detached") continue;
    const routeScope = state.routeScope
      && typeof state.routeScope === "object"
      && !Array.isArray(state.routeScope)
      ? state.routeScope
      : {};
    const stateProject = canonicalProjectIdentity(
      routeScope.projectFile || state.projectFile || "",
      workspaceRoot
    );
    const stateOwner = String(
      routeScope.workspaceRoot || state.workspaceRoot || ""
    ).trim()
      ? canonicalWorkspaceRoot(routeScope.workspaceRoot || state.workspaceRoot)
      : "";
    const ownsCurrent = Boolean(
      (stateProject && currentProject && stateProject === currentProject)
      || (!stateProject && stateOwner && stateOwner === currentWorkspace)
    );
    if (!ownsCurrent) continue;
    const route = effectiveToolRouteForState(state) || state.toolRoute || {};
    if (!route || typeof route !== "object") continue;
    healthyRoutes.push({
      taskSessionId: String(state.taskSessionId || entry.name),
      state,
      route,
    });
  }
  return unionFromHealthyRoutes(healthyRoutes);
}

function listToolsRouteContext(workspaceRoot, activeProject = "") {
  const context = discoverActiveTaskContext(workspaceRoot, activeProject);
  if (context.status !== "ambiguous_or_corrupt") {
    return context;
  }
  // Corrupt / scope mismatch must not enter multi-route catalogMode union.
  // Advertised tools/list remains profile-stable; CallTool still fail-closes.
  if (!CATALOG_UNION_ERROR_CODES.has(String(context.errorCode || ""))) {
    return context;
  }
  const union = collectProjectActiveToolUnion(workspaceRoot, activeProject, {
    healthyRoutes: context.healthyRoutes,
  });
  if (!union.tools.length) {
    return context;
  }
  return {
    ...context,
    catalogMode: "route_union",
    taskSessionId: String(context.taskSessionId || "multi"),
    route: {
      routeHash: union.fingerprint,
      phase: "union",
      activeTools: union.tools,
    },
  };
}

function invokePythonTaskApi(workspaceRoot, callExpression, extraArgs = [], options = {}) {
  const scriptsDir = path.resolve(__dirname, "../../scripts");
  const fallbackPython = process.platform === "win32" ? "python" : "python3";
  const python = String(process.env.PYTHON_EXE || process.env.PYTHON || fallbackPython).trim()
    || fallbackPython;
  const stdinPayload = options.stdinPayload && typeof options.stdinPayload === "object"
    ? options.stdinPayload
    : null;
  // Node and Python are two halves of one MCP bridge. Pass the already-resolved
  // instance identity into the child so Python never performs its own Windows
  // parent/WMI discovery and cannot rotate ownership mid-call.
  const bridgeEnv = {
    ...process.env,
    MCP_CLIENT_INSTANCE_ID: getMcpClientInstanceId(),
    // Python otherwise inherits a locale-dependent Windows console encoding
    // (commonly CP949). The bridge decodes stdout as UTF-8, so non-ASCII
    // structured fields such as userMessageKo became U+FFFD in LM Studio.
    PYTHONIOENCODING: "utf-8",
    PYTHONUTF8: "1",
  };
  const code = [
    "import json, sys",
    "from pathlib import Path",
    `sys.path.insert(0, ${JSON.stringify(scriptsDir)})`,
    "from task_api import task_cancel, task_cancel_active, task_checkpoint, task_quarantine_corrupt, task_record_build_recovery, task_mark_build_recovery_evidence, task_complete_after_successful_build, task_require_automation_after_build",
    stdinPayload
      ? "_stdin = json.load(sys.stdin)"
      : "_stdin = {}",
    callExpression,
    "print(json.dumps(payload, ensure_ascii=False))",
  ].join("; ");
  const result = spawnSync(
    python,
    ["-c", code, String(workspaceRoot), ...extraArgs.map(String)],
    {
      encoding: "utf8",
      env: bridgeEnv,
      windowsHide: true,
      timeout: 120000,
      killSignal: "SIGKILL",
      input: stdinPayload ? JSON.stringify(stdinPayload) : undefined,
    }
  );
  if (result.error) {
    return {
      ok: false,
      errorCode: "TASK_PYTHON_BRIDGE_FAILED",
      error: `Failed to invoke Python task API: ${result.error.message}`,
    };
  }
  if (result.status !== 0) {
    return {
      ok: false,
      errorCode: "TASK_PYTHON_BRIDGE_FAILED",
      error: String(result.stderr || result.stdout || "Python task API failed").slice(0, 800),
    };
  }
  try {
    return JSON.parse(String(result.stdout || "").trim());
  } catch (error) {
    return {
      ok: false,
      errorCode: "TASK_PYTHON_BRIDGE_FAILED",
      error: `Invalid Python task API payload: ${error.message}`,
    };
  }
}

function cancelTaskViaPython(workspaceRoot, taskSessionId, options = {}) {
  const activeProject = String(options.activeProject || "");
  const force = options.force === true ? "1" : "0";
  const conversationId = String(options.conversationId || "");
  const ownerCapability = String(options.ownerCapability || "");
  // Keep ownerCapability off argv (process list / crash telemetry); pass via stdin JSON.
  return invokePythonTaskApi(
    workspaceRoot,
    (
      "payload = task_cancel_active(Path(sys.argv[1]), active_project=sys.argv[2], "
      + "task_session_id=sys.argv[3], force=sys.argv[4]=='1', "
      + "conversation_id=str((_stdin or {}).get('conversationId') or ''), "
      + "owner_capability=str((_stdin or {}).get('ownerCapability') or ''))"
    ),
    [activeProject, taskSessionId, force],
    {
      stdinPayload: {
        conversationId,
        ownerCapability,
      },
    }
  );
}

function checkpointMutationViaPython(workspaceRoot, args, modifiedFiles, options = {}) {
  const nested = args?.taskAuthorization && typeof args.taskAuthorization === "object"
    ? args.taskAuthorization
    : (args?.task_authorization && typeof args.task_authorization === "object"
      ? args.task_authorization
      : {});
  return invokePythonTaskApi(
    workspaceRoot,
    (
      "payload = task_checkpoint(Path(sys.argv[1]), "
      + "task_authorization=dict((_stdin or {}).get('taskAuthorization') or {}), "
      + "action='record', phase='executor', "
      + "modified_files=list((_stdin or {}).get('modifiedFiles') or []), "
      + "required_next_action=str((_stdin or {}).get('requiredNextAction') or ''), "
      + "validation=dict((_stdin or {}).get('validation') or {}), "
      + "note=str((_stdin or {}).get('note') or ''), "
      + "preserve_route_usage=True, include_git_changes=False, "
      + "advance_gate_snapshots=True)"
    ),
    [],
    {
      stdinPayload: {
        taskAuthorization: nested,
        modifiedFiles: Array.isArray(modifiedFiles) ? modifiedFiles.map(String) : [],
        requiredNextAction: String(options.requiredNextAction || "continue_active_slice"),
        validation: options.validation && typeof options.validation === "object"
          ? options.validation
          : {},
        note: String(options.note || "automatic checkpoint after successful mutation"),
      },
    }
  );
}

function recordBuildRecoveryViaPython(workspaceRoot, args, recovery) {
  const nested = args?.taskAuthorization && typeof args.taskAuthorization === "object"
    ? args.taskAuthorization
    : {};
  if (!nested.taskSessionId || !recovery || typeof recovery !== "object") {
    return { ok: true, active: false };
  }
  return invokePythonTaskApi(
    workspaceRoot,
    (
      "payload = task_record_build_recovery(Path(sys.argv[1]), "
      + "task_authorization=dict((_stdin or {}).get('taskAuthorization') or {}), "
      + "recovery=dict((_stdin or {}).get('recovery') or {}))"
    ),
    [],
    { stdinPayload: { taskAuthorization: nested, recovery } }
  );
}

function completeTaskAfterBuildViaPython(workspaceRoot, args, buildEvidence = {}) {
  const nested = args?.taskAuthorization && typeof args.taskAuthorization === "object"
    ? args.taskAuthorization
    : {};
  if (!nested.taskSessionId) {
    return { ok: true, active: false };
  }
  return invokePythonTaskApi(
    workspaceRoot,
    (
      "payload = task_complete_after_successful_build(Path(sys.argv[1]), "
      + "task_authorization=dict((_stdin or {}).get('taskAuthorization') or {}), "
      + "proof_level=str((_stdin or {}).get('proofLevel') or ''), "
      + "mutation_generation=int((_stdin or {}).get('mutationGeneration') or 0), "
      + "build_log_path=str((_stdin or {}).get('buildLogPath') or ''), "
      + "proof_kind=str((_stdin or {}).get('proofKind') or 'build'))"
    ),
    [],
    {
      stdinPayload: {
        taskAuthorization: nested,
        proofLevel: String(buildEvidence.proofLevel || "Built"),
        mutationGeneration: Number(buildEvidence.mutationGeneration || 0),
        buildLogPath: String(buildEvidence.buildLogPath || ""),
      },
    }
  );
}

function requireAutomationAfterBuildViaPython(workspaceRoot, args, buildEvidence = {}) {
  const nested = args?.taskAuthorization && typeof args.taskAuthorization === "object"
    ? args.taskAuthorization
    : {};
  if (!nested.taskSessionId) {
    return { ok: true, active: false };
  }
  return invokePythonTaskApi(
    workspaceRoot,
    (
      "payload = task_require_automation_after_build(Path(sys.argv[1]), "
      + "task_authorization=dict((_stdin or {}).get('taskAuthorization') or {}), "
      + "mutation_generation=int((_stdin or {}).get('mutationGeneration') or 0), "
      + "build_log_path=str((_stdin or {}).get('buildLogPath') or ''), "
      + "test_filter=str((_stdin or {}).get('testFilter') or ''), "
      + "declared_tests=list((_stdin or {}).get('declaredTests') or []))"
    ),
    [],
    {
      stdinPayload: {
        taskAuthorization: nested,
        mutationGeneration: Number(buildEvidence.mutationGeneration || 0),
        buildLogPath: String(buildEvidence.buildLogPath || ""),
        proofKind: String(buildEvidence.proofKind || "build"),
        testFilter: String(buildEvidence.testFilter || ""),
        declaredTests: Array.isArray(buildEvidence.declaredTests)
          ? buildEvidence.declaredTests.map(String)
          : [],
      },
    }
  );
}

function markBuildRecoveryEvidenceViaPython(workspaceRoot, args, targetFile) {
  const nested = args?.taskAuthorization && typeof args.taskAuthorization === "object"
    ? args.taskAuthorization
    : {};
  if (!nested.taskSessionId || !String(targetFile || "").trim()) {
    return { ok: true, active: false };
  }
  return invokePythonTaskApi(
    workspaceRoot,
    (
      "payload = task_mark_build_recovery_evidence(Path(sys.argv[1]), "
      + "task_authorization=dict((_stdin or {}).get('taskAuthorization') or {}), "
      + "target_file=str((_stdin or {}).get('targetFile') or ''))"
    ),
    [],
    { stdinPayload: { taskAuthorization: nested, targetFile: String(targetFile) } }
  );
}

function quarantineCorruptTask(workspaceRoot, activeProject = "", taskSessionId = "") {
  // Delegate to Python so linked background jobs are cancelled before move.
  return invokePythonTaskApi(
    workspaceRoot,
    "payload = task_quarantine_corrupt(Path(sys.argv[1]), active_project=sys.argv[2], task_session_id=sys.argv[3])",
    [activeProject || "", taskSessionId || ""]
  );
}

function cancelRunningTaskSession(workspaceRoot, taskSessionId, options = {}) {
  const sanitized = sanitizeTaskSessionId(taskSessionId);
  if (!sanitized.ok) {
    return { ok: false, error: sanitized.error, errorCode: "TASK_SESSION_INVALID" };
  }
  return cancelTaskViaPython(workspaceRoot, sanitized.taskSessionId, options);
}

function listActiveTasks(workspaceRoot, activeProject = "", options = {}) {
  try {
    const tasks = listRunningTasksForProject(workspaceRoot, activeProject, options);
    const corrupt = tasks.filter((item) => item.status === "corrupt");
    const owned = tasks.filter((item) => (
      item.status === "running"
      && item.connectionMatches === true
      && item.ownsActiveToolRoute === true
    ));
    const activeProjectSelected = Boolean(String(activeProject || "").trim());
    const nextAction = corrupt.length
      ? "quarantine_corrupt_task"
      : (owned.length === 1
        ? String(owned[0].routeNextAction || "continue_with_current_tool_route")
        : (tasks.length
          ? "active_task_requires_explicit_user_decision"
        : (activeProjectSelected
          ? "enable_or_call_unreal_agent_plan"
          : "get_active_project")));
    const nextActionIsTool = Boolean(
      corrupt.length
      || (owned.length === 1 && owned[0].routeNextActionIsTool === true)
      || (!activeProjectSelected && !tasks.length)
    );
    return {
      ok: true,
      count: tasks.length,
      runningCount: tasks.filter((item) => item.status === "running").length,
      corruptCount: corrupt.length,
      tasks,
      nextAction,
      nextActionIsTool,
      ...(tasks.length && !corrupt.length && owned.length !== 1 ? {
        agentInstruction: (
          "Task listing is diagnostic. A healthy task is never cancelled automatically. "
          + "Resume or cancel only after explicit ownership and user intent are established."
        ),
      } : {}),
      ...(!tasks.length && !corrupt.length && activeProjectSelected ? {
        requiredProvider: "mcp/unreal-rag",
        requiredTool: "unreal_agent_plan",
        doNotFabricateTaskAuthorization: true,
        agentInstruction: (
          "No server-owned task route exists for the active project. Ensure mcp/unreal-rag is enabled, "
          + "then call unreal_agent_plan with the original request. This response does not grant write authority."
        ),
      } : {}),
    };
  } catch (error) {
    if (error && error.errorCode === "TASK_STATE_ROOT_UNAVAILABLE") {
      return {
        ok: false,
        errorCode: "TASK_STATE_ROOT_UNAVAILABLE",
        error: String(error.message || error),
        tasks: [],
        nextAction: routeRecoveryNextAction("TASK_STATE_ROOT_UNAVAILABLE"),
      };
    }
    throw error;
  }
}

function cancelActiveTask(
  workspaceRoot,
  activeProject = "",
  taskSessionId = "",
  force = false,
  options = {}
) {
  try {
    return cancelActiveTaskInner(
      workspaceRoot,
      activeProject,
      taskSessionId,
      force,
      options
    );
  } catch (error) {
    if (error && error.errorCode === "TASK_STATE_ROOT_UNAVAILABLE") {
      return {
        ok: false,
        errorCode: "TASK_STATE_ROOT_UNAVAILABLE",
        error: String(error.message || error),
        nextAction: routeRecoveryNextAction("TASK_STATE_ROOT_UNAVAILABLE"),
      };
    }
    throw error;
  }
}

function cancelActiveTaskInner(
  workspaceRoot,
  activeProject = "",
  taskSessionId = "",
  force = false,
  options = {}
) {
  const ownerCapability = String(
    options.ownerCapability || options.owner_capability || ""
  ).trim();
  const conversationId = String(
    options.conversationId || options.conversation_id || ""
  ).trim();
  const ownership = { ownerCapability, conversationId };
  const tasks = listRunningTasksForProject(workspaceRoot, activeProject, ownership)
    .filter((item) => item.status === "running");
  const explicit = String(taskSessionId || "").trim();
  let target = null;
  if (explicit) {
    target = tasks.find((item) => item.taskSessionId === explicit) || null;
    if (!target) {
      const corrupt = listRunningTasksForProject(workspaceRoot, activeProject, ownership)
        .find((item) => item.taskSessionId === explicit && item.status === "corrupt");
      if (corrupt) {
        return {
          ok: false,
          errorCode: "TASK_STATE_CORRUPT",
          error: "Task state is corrupt; call quarantine_corrupt_task.",
          task: corrupt,
          nextAction: "quarantine_corrupt_task",
        };
      }
      return {
        ok: false,
        errorCode: "TASK_NOT_ACTIVE",
        error: `Task is not an active running session: ${explicit}`,
        tasks,
      };
    }
  } else {
    const owned = tasks.filter((item) => item.connectionMatches === true);
    if (owned.length === 1) {
      target = owned[0];
    } else if (owned.length > 1) {
      return {
        ok: false,
        errorCode: "TASK_AMBIGUOUS_ACTIVE",
        error: "Multiple running tasks owned by this conversation; pass taskSessionId explicitly.",
        tasks: owned,
      };
    } else if (tasks.length === 1 && (force || !tasks[0].foreignHealthy)) {
      target = tasks[0];
    } else if (!tasks.length) {
      const listed = listActiveTasks(workspaceRoot, activeProject, ownership);
      if (listed.corruptCount) {
        return {
          ok: false,
          errorCode: "TASK_STATE_CORRUPT",
          error: "Corrupt task state is blocking recovery; quarantine it first.",
          tasks: listed.tasks,
          nextAction: "quarantine_corrupt_task",
        };
      }
      return {
        ok: false,
        errorCode: "TASK_NONE_ACTIVE",
        error: "No running tasks for the active project/workspace.",
        tasks: [],
      };
    } else {
      return {
        ok: false,
        errorCode: "TASK_AMBIGUOUS_ACTIVE",
        error: "Multiple running tasks; pass taskSessionId and taskAuthorization.ownerCapability.",
        tasks,
      };
    }
  }
  if (!force && target.foreignHealthy === true && target.connectionMatches !== true) {
    return {
      ok: false,
      errorCode: "TASK_OWNED_BY_ANOTHER_CONNECTION",
      error: "Active task belongs to another healthy MCP connection. Pass force=true only after explicit user confirmation.",
      task: target,
    };
  }
  return cancelRunningTaskSession(workspaceRoot, target.taskSessionId, {
    activeProject,
    force,
    conversationId,
    ownerCapability,
  });
}

function discoverSingleActiveTask(workspaceRoot, activeProject = "") {
  const context = discoverActiveTaskContext(workspaceRoot, activeProject);
  return context.status === "active" ? context : null;
}

function discoverSingleActiveToolRoute(workspaceRoot, activeProject = "") {
  const active = discoverSingleActiveTask(workspaceRoot, activeProject);
  return active ? active.route : null;
}

function authorizeActiveRouteTool(workspaceRoot, toolName, args = {}, options = {}) {
  const auth = args.taskAuthorization && typeof args.taskAuthorization === "object"
    ? args.taskAuthorization
    : args.task_authorization && typeof args.task_authorization === "object"
      ? args.task_authorization
      : {};
  const ownerCapability = String(
    options.ownerCapability
    || auth.ownerCapability
    || auth.owner_capability
    || args.ownerCapability
    || args.owner_capability
    || ""
  ).trim();
  const conversationId = String(
    options.conversationId
    || auth.conversationId
    || auth.conversation_id
    || args.conversationId
    || args.conversation_id
    || ""
  ).trim();
  const hasExplicitOwnership = Boolean(ownerCapability || conversationId);
  const active = discoverActiveTaskContext(
    workspaceRoot,
    String(options.activeProject || ""),
    {
      ownerCapability,
      conversationId,
      // LM Studio does not reliably echo opaque auth objects on every tool
      // call.  When no ownership selector was supplied, an exact single
      // project-scoped route is safe to bind server-side. Ambiguous routes
      // still fail closed, while an explicitly supplied selector must match.
      requireOwnerCapability: hasExplicitOwnership,
    }
  );
  if (active.status === "none") return { ok: true, legacy: true };
  if (SAFE_ROUTE_RECOVERY_TOOLS.has(toolName)) {
    return {
      ok: true,
      controlSurface: true,
      recoveryOnly: active.status !== "active",
      routeStatus: active.status,
      errorCode: active.errorCode || undefined,
      toolRoute: active.route,
    };
  }
  if (active.status !== "active") {
    const errorCode = String(active.errorCode || "TASK_ROUTE_AMBIGUOUS_OR_CORRUPT");
    const ownershipRetry = errorCode === "TASK_ROUTE_OWNERSHIP_REQUIRED";
    return {
      ok: false,
      errorCode,
      error: active.error || "Task route is ambiguous, corrupt, or blocked.",
      routeStatus: active.status,
      toolRoute: active.route,
      nextAction: ownershipRetry ? toolName : routeRecoveryNextAction(errorCode),
      ...(ownershipRetry ? {
        retryable: true,
        requiredArgument: "taskAuthorization",
        agentInstruction: (
          "Retry the same tool once with the complete taskAuthorization previously "
          + "returned by the plan, gate, or checkpoint. Do not recover or cancel the task."
        ),
      } : {}),
    };
  }
  const route = active.route;
  const activeTools = new Set(
    Array.isArray(route.activeTools) ? route.activeTools.map(String) : []
  );
  if (!activeTools.has(toolName)) {
    return {
      ok: false,
      errorCode: "TASK_TOOL_NOT_ACTIVE",
      error: `${toolName} is not active in route phase ${String(route.phase || "")}.`,
      toolRoute: route,
    };
  }
  if (options.consumeBudget === false) {
    return {
      ok: true,
      taskSessionId: active.taskSessionId,
      toolRoute: route,
      taskAuthorization: taskAuthorizationForState(active.state),
      authorizationBinding: hasExplicitOwnership ? "explicit" : "single_active_route",
    };
  }
  return consumeRouteCall(
    workspaceRoot,
    active.taskSessionId,
    {
      routeHash: String(route.routeHash || ""),
      routePhase: String(route.phase || ""),
    },
    args,
    toolName
  );
}

function expandCompactTaskAuthorization(workspaceRoot, toolName, args = {}, options = {}) {
  const auth = args.taskAuthorization && typeof args.taskAuthorization === "object"
    ? args.taskAuthorization
    : args.task_authorization && typeof args.task_authorization === "object"
      ? args.task_authorization
      : {};
  const taskSessionId = String(
    auth.taskSessionId || auth.task_session_id || args.taskSessionId || args.task_session_id || ""
  ).trim();
  const ownerCapability = String(
    auth.ownerCapability || auth.owner_capability || args.ownerCapability || args.owner_capability || ""
  ).trim();
  const fields = requiredFields(args);
  const complete = [
    "taskSessionId",
    "authToken",
    "planId",
    "planRevision",
    "activeSliceId",
    "routeHash",
    "routePhase",
  ].every((key) => String(fields[key] || "").trim());
  if (complete || !taskSessionId || !ownerCapability) {
    return { ok: true, expanded: false, args };
  }
  const authorized = authorizeActiveRouteTool(
    workspaceRoot,
    toolName,
    args,
    {
      ...options,
      ownerCapability,
      consumeBudget: false,
    }
  );
  if (!authorized.ok) return authorized;
  if (String(authorized.taskSessionId || "") !== taskSessionId) {
    return {
      ok: false,
      errorCode: "TASK_ROUTE_CAPABILITY_MISMATCH",
      error: "Compact taskAuthorization taskSessionId does not match ownerCapability.",
    };
  }
  if (!authorized.taskAuthorization || typeof authorized.taskAuthorization !== "object") {
    return {
      ok: false,
      errorCode: "TASK_AUTH_REFRESH_UNAVAILABLE",
      error: "Server could not expand compact taskAuthorization.",
    };
  }
  return {
    ok: true,
    expanded: true,
    args: {
      ...args,
      taskAuthorization: authorized.taskAuthorization,
    },
    taskSessionId,
    taskAuthorization: authorized.taskAuthorization,
    authorizationBinding: "compact_owner_capability",
  };
}

function discardTaskAuthorizationWithoutActiveRoute(
  workspaceRoot,
  args = {},
  options = {}
) {
  const context = listToolsRouteContext(
    workspaceRoot,
    String(options.activeProject || "")
  );
  if (context.status !== "none") {
    return { args, discarded: false, routeStatus: context.status };
  }
  const sanitized = { ...args };
  for (const key of [
    "taskAuthorization",
    "task_authorization",
    "taskSessionId",
    "task_session_id",
    "ownerCapability",
    "owner_capability",
    "conversationId",
    "conversation_id",
  ]) {
    delete sanitized[key];
  }
  return { args: sanitized, discarded: true, routeStatus: "none" };
}

function authorizeTaskRouteTool(
  workspaceRoot,
  toolName,
  args = {},
  options = {}
) {
  const fields = requiredFields(args);
  const required = [
    "taskSessionId",
    "authToken",
    "planId",
    "planRevision",
    "activeSliceId",
  ];
  const missing = required.filter((key) => !String(fields[key] || ""));
  if (missing.length) {
    return {
      ok: false,
      errorCode: "TASK_AUTH_INCOMPLETE",
      error: `Task authorization missing required fields: ${missing.join(", ")}`,
    };
  }
  const sanitized = sanitizeTaskSessionId(fields.taskSessionId);
  if (!sanitized.ok) {
    return {
      ok: false,
      error: sanitized.error,
      errorCode: "TASK_AUTH_INVALID_FORMAT",
    };
  }
  const stateResult = readTaskStateResult(workspaceRoot, sanitized.taskSessionId);
  const state = stateResult.state;
  if (!state) {
    return {
      ok: false,
      errorCode: stateResult.errorCode || "TASK_STATE_MISSING",
      error: `Task state is unavailable: ${sanitized.taskSessionId}`,
    };
  }
  if (String(state.taskSessionId || "") !== sanitized.taskSessionId) {
    return {
      ok: false,
      errorCode: "TASK_STATE_ID_MISMATCH",
      error: `Task state identity mismatch: ${sanitized.taskSessionId}`,
    };
  }
  if (Object.prototype.hasOwnProperty.call(options, "activeProject")) {
    const scopeValidation = validateTaskRouteScope(
      state,
      workspaceRoot,
      String(options.activeProject || "")
    );
    if (!scopeValidation.ok) return scopeValidation;
  }
  const mismatches = [];
  for (const key of ["authToken", "planId", "planRevision", "activeSliceId"]) {
    if (String(state[key] || "") !== String(fields[key] || "")) {
      mismatches.push(key);
    }
  }
  if (mismatches.length) {
    return authRefreshFailure({
      ok: false,
      errorCode: "TASK_AUTH_MISMATCH",
      error: `Task authorization mismatch: ${mismatches.join(", ")}`,
    }, state, mismatches);
  }
  const routeValidation = validateToolRoute(state, fields, args, toolName);
  if (!routeValidation.ok) {
    return authRefreshFailure(routeValidation, state);
  }
  if (String(state.status || "") !== "running") {
    return {
      ok: false,
      errorCode: "TASK_NOT_WRITABLE",
      error: "Task is not running.",
    };
  }
  const continuity = state.continuity && typeof state.continuity === "object"
    ? state.continuity
    : {};
  const lease = continuity.lease && typeof continuity.lease === "object"
    ? continuity.lease
    : null;
  const leaseExpiry = lease ? Date.parse(String(lease.expiresAt || "")) : NaN;
  if (
    lease
    && (
      String(lease.status || "") !== "active"
      || !Number.isFinite(leaseExpiry)
      || leaseExpiry <= Date.now()
    )
  ) {
    return {
      ok: false,
      errorCode: "TASK_LEASE_EXPIRED",
      error: "Task continuity lease is inactive or expired.",
    };
  }
  const recovery = continuity.recovery && typeof continuity.recovery === "object"
    ? continuity.recovery
    : {};
  if (Array.isArray(recovery.conflicts) && recovery.conflicts.length) {
    return checkpointConflictFailure(state, recovery.conflicts);
  }
  const supervisor = state.autonomySupervisor
    && typeof state.autonomySupervisor === "object"
    ? state.autonomySupervisor
    : {};
  if (Array.isArray(supervisor.blockers) && supervisor.blockers.length) {
    return {
      ok: false,
      errorCode: "TASK_AUTONOMY_BLOCKED",
      error: "Task autonomy supervisor is blocked.",
    };
  }
  const activeJobId = String(state.activeJobId || "").trim();
  if (activeJobId) {
    return {
      ok: false,
      errorCode: "TASK_JOB_IN_PROGRESS",
      error: `Task has an active background job: ${activeJobId}`,
      activeJobId,
    };
  }
  const selectionValidation = validateSelectionState(state);
  if (!selectionValidation.ok) return selectionValidation;
  if (
    routeValidation.route
    && toolName
    && options.consumeBudget !== false
  ) {
    return consumeRouteCall(
      workspaceRoot,
      sanitized.taskSessionId,
      fields,
      args,
      toolName
    );
  }
  return {
    ok: true,
    taskSessionId: sanitized.taskSessionId,
    state,
    toolRoute: routeValidation.route,
  };
}

function validateMutationAuth(workspaceRoot, args = {}, options = {}) {
  const requireAll = options.requireAll !== false;
  const fields = requiredFields(args);
  const missing = Object.entries(fields).filter(([, value]) => !value).map(([key]) => key);
  if (requireAll && missing.length) {
    return {
      ok: false,
      error: `Task authorization missing required fields: ${missing.join(", ")}`,
      errorCode: "TASK_AUTH_INCOMPLETE",
    };
  }
  if (!fields.taskSessionId) {
    return { ok: false, error: "taskSessionId is required", errorCode: "TASK_SESSION_REQUIRED" };
  }
  const sanitized = sanitizeTaskSessionId(fields.taskSessionId);
  if (!sanitized.ok) {
    return {
      ok: false,
      error: sanitized.error,
      errorCode: "TASK_AUTH_INVALID_FORMAT",
    };
  }
  const stateResult = readTaskStateResult(workspaceRoot, sanitized.taskSessionId);
  const state = stateResult.state;
  if (!state) {
    if (stateResult.errorCode === "TASK_STATE_CORRUPT") {
      return {
        ok: false,
        error: `Task state is unreadable or malformed: ${sanitized.taskSessionId}`,
        errorCode: "TASK_STATE_CORRUPT",
        taskSessionId: sanitized.taskSessionId,
      };
    }
    return {
      ok: false,
      error: `Unknown task session: ${sanitized.taskSessionId}`,
      errorCode: stateResult.errorCode || "TASK_STATE_MISSING",
      taskSessionId: sanitized.taskSessionId,
    };
  }
  if (String(state.taskSessionId || "").trim() !== sanitized.taskSessionId) {
    return {
      ok: false,
      error: `Task state identity mismatch: ${sanitized.taskSessionId}`,
      errorCode: "TASK_STATE_ID_MISMATCH",
      taskSessionId: sanitized.taskSessionId,
    };
  }
  const nestedAuthorization = args.taskAuthorization && typeof args.taskAuthorization === "object"
    ? args.taskAuthorization
    : args.task_authorization && typeof args.task_authorization === "object"
      ? args.task_authorization
      : {};
  const suppliedOwnerCapability = String(
    args.ownerCapability
    || args.owner_capability
    || nestedAuthorization.ownerCapability
    || nestedAuthorization.owner_capability
    || ""
  ).trim();
  const stateOwnerCapability = String(state.ownerCapability || "").trim();
  if (
    suppliedOwnerCapability
    && stateOwnerCapability
    && suppliedOwnerCapability !== stateOwnerCapability
  ) {
    return {
      ok: false,
      error: "taskAuthorization.ownerCapability does not own this task session.",
      errorCode: "TASK_ROUTE_CAPABILITY_MISMATCH",
      taskSessionId: sanitized.taskSessionId,
    };
  }
  if (Object.prototype.hasOwnProperty.call(options, "activeProject")) {
    const scopeValidation = validateTaskRouteScope(
      state,
      workspaceRoot,
      String(options.activeProject || "")
    );
    if (!scopeValidation.ok) return scopeValidation;
  }
  const mismatches = [];
  for (const [key, expected] of Object.entries(fields)) {
    if (["taskSessionId", "routeHash", "routePhase"].includes(key) || !expected) continue;
    const actual = String(state[key] || state[key.charAt(0).toLowerCase() + key.slice(1)] || "");
    if (actual !== expected) {
      mismatches.push(key);
    }
  }
  if (mismatches.length) {
    return authRefreshFailure({
      ok: false,
      error: `Task authorization mismatch: ${mismatches.join(", ")}`,
      errorCode: "TASK_AUTH_MISMATCH",
      taskSessionId: sanitized.taskSessionId,
    }, state, mismatches);
  }
  if (state.slicePlanningRequired === true) {
    const taskAuthorization = taskAuthorizationForState(state);
    return {
      ok: false,
      errorCode: "SLICE_PLAN_REQUIRED",
      error: "Concrete executable slices must be registered before project mutation.",
      taskSessionId: sanitized.taskSessionId,
      taskAuthorization,
      nextAction: "unreal_task_define_slices",
      nextActionArgs: { taskAuthorization: compactTaskAuthorization(taskAuthorization) },
      retryable: true,
    };
  }
  const toolName = String(options.toolName || "");
  const routeValidation = validateToolRoute(state, fields, args, toolName);
  if (!routeValidation.ok) {
    return authRefreshFailure({
      ...routeValidation,
      taskSessionId: sanitized.taskSessionId,
    }, state);
  }
  const status = String(state.status || "");
  if (status !== "running") {
    return {
      ok: false,
      error: `Task session is not writable in status '${status || "unknown"}'`,
      errorCode: status === "cancelled" ? "TASK_CANCELLED" : "TASK_NOT_WRITABLE",
    };
  }
  const continuity = state.continuity && typeof state.continuity === "object"
    ? state.continuity
    : null;
  if (continuity) {
    const lease = continuity.lease && typeof continuity.lease === "object"
      ? continuity.lease
      : null;
    if (lease) {
      const expiresAt = Date.parse(String(lease.expiresAt || ""));
      if (
        String(lease.status || "") !== "active"
        || !Number.isFinite(expiresAt)
        || expiresAt <= Date.now()
      ) {
        return {
          ok: false,
          error: "Task continuity lease is inactive or expired; heartbeat/recovery is required.",
          errorCode: "TASK_LEASE_EXPIRED",
          taskSessionId: sanitized.taskSessionId,
        };
      }
    }
    const recovery = continuity.recovery && typeof continuity.recovery === "object"
      ? continuity.recovery
      : {};
    const conflicts = Array.isArray(recovery.conflicts) ? recovery.conflicts : [];
    if (
      String(recovery.status || "") === "blocked_by_checkpoint_conflict"
      || conflicts.length > 0
    ) {
      return checkpointConflictFailure(
        state,
        conflicts,
        "Task checkpoint conflicts with current files; explicitly rebase first."
      );
    }
  }
  const autonomySupervisor = state.autonomySupervisor
    && typeof state.autonomySupervisor === "object"
    ? state.autonomySupervisor
    : null;
  if (autonomySupervisor) {
    const blockers = Array.isArray(autonomySupervisor.blockers)
      ? autonomySupervisor.blockers
      : [];
    if (blockers.length > 0) {
      const nextAction = String(
        autonomySupervisor.nextAction || "replan_autonomous_strategy"
      );
      return {
        ok: false,
        error: "Autonomous retry budget is exhausted; strategy replan is required.",
        errorCode: "TASK_AUTONOMY_BLOCKED",
        taskSessionId: sanitized.taskSessionId,
        blockers,
        nextAction,
      };
    }
  }
  const activeJobId = String(state.activeJobId || "").trim();
  if (activeJobId) {
    return {
      ok: false,
      error: `Task has an active background job: ${activeJobId}`,
      errorCode: "TASK_JOB_IN_PROGRESS",
      taskSessionId: sanitized.taskSessionId,
      activeJobId,
    };
  }
  const writeGate = state.writeGate;
  const writesAllowed = writeGate && typeof writeGate === "object"
    ? writeGate.writesAllowed
    : (writeGate !== undefined ? writeGate : state.writesAllowed);
  if (writesAllowed !== true && writesAllowed !== "true") {
    return { ok: false, error: "Task writeGate denies writes", errorCode: "WRITE_GATE_DENIED" };
  }
  const gateValidation = validateCompletedGates(state, args);
  if (!gateValidation.ok) {
    return {
      ...gateValidation,
      taskSessionId: sanitized.taskSessionId,
    };
  }
  const selectionValidation = validateSelectionState(state);
  if (!selectionValidation.ok) {
    return {
      ...selectionValidation,
      taskSessionId: sanitized.taskSessionId,
    };
  }
  if (
    routeValidation.route
    && toolName
    && options.consumeBudget !== false
  ) {
    const consumed = consumeRouteCall(
      workspaceRoot,
      sanitized.taskSessionId,
      fields,
      args,
      toolName
    );
    if (!consumed.ok) {
      return {
        ...consumed,
        taskSessionId: sanitized.taskSessionId,
      };
    }
  }
  return {
    ok: true,
    taskSessionId: sanitized.taskSessionId,
    state,
    maxFilesPerEdit: Number(state.maxFilesPerEdit || writeGate?.maxFilesPerEdit || 2),
  };
}

module.exports = {
  TASK_SESSION_ID_RE,
  sanitizeTaskSessionId,
  taskDir,
  readTaskState,
  validateMutationAuth,
  validateCompletedGates,
  requestedMutationPaths,
  featureIntentTargetHash,
  requiredFields,
  taskAuthorizationForState,
  authRefreshFailure,
  getMcpConnectionId,
  taskOwnsActiveToolRoute,
  validateToolRoute,
  validateSelectionState,
  selectionBindingForState,
  discoverActiveTaskContext,
  discoverSingleActiveToolRoute,
  authorizeActiveRouteTool,
  expandCompactTaskAuthorization,
  discardTaskAuthorizationWithoutActiveRoute,
  authorizeTaskRouteTool,
  effectiveToolRouteForState,
  validateTaskRouteScope,
  SAFE_ROUTE_RECOVERY_TOOLS,
  consumeRouteCall,
  reserveRouteCall,
  commitRouteReservation,
  rollbackRouteReservation,
  heartbeatRouteReservation,
  requiredFields,
  listActiveTasks,
  cancelActiveTask,
  quarantineCorruptTask,
  checkpointMutationViaPython,
  completeTaskAfterBuildViaPython,
  requireAutomationAfterBuildViaPython,
  recordBuildRecoveryViaPython,
  markBuildRecoveryEvidenceViaPython,
  listToolsRouteContext,
  collectProjectActiveToolUnion,
};
