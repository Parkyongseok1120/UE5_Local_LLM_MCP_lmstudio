#!/usr/bin/env node
"use strict";

/**
 * LM Studio Unreal Agent MCP
 *
 * Safe-ish local tools for using a local LLM as a coding agent.
 *
 * Security model:
 * - Reads are restricted to WORKSPACE_ROOT and the selected active project.
 * - Writes are disabled unless ALLOW_WRITE=1.
 * - Command execution is disabled unless ALLOW_COMMANDS=1.
 * - Commands are allowlisted.
 * - Unreal build command is separately gated by ALLOW_UNREAL_BUILD=1.
 */

const fs = require("fs");
const fsp = fs.promises;
const path = require("path");
const cp = require("child_process");
const os = require("os");
const crypto = require("crypto");
const { AsyncLocalStorage } = require("async_hooks");
const {
  sanitizeModelPayload,
  withCompactTaskAuthorization,
} = require("./public-contract.js");
const {
  attachControlEnvelope,
  modelVisibleControlText,
} = require("./control-envelope.js");
const { REDIRECT_CODES } = require("./route-recovery-policy.js");
const {
  routeAuthorizationFailureOptions,
} = require("./route-authorization-failure-options.js");
const { attachCommittedToolOutcomeControl } = require("./post-read-route-control.js");
const { verifyRuntimeComponent } = require("./runtime-identity.js");
const { deriveValidationScope } = require("./validation-scope.js");
const {
  absolutePathIsWithin,
  filesystemPathIdentity,
} = require("./filesystem-path-identity.js");
const { getMcpIdentityStatus } = require("./mcp-connection.js");
const { allowedCommandBase, parseAllowedCommand } = require("./command-policy.js");

const {
  Server
} = require("@modelcontextprotocol/sdk/server/index.js");

const {
  StdioServerTransport
} = require("@modelcontextprotocol/sdk/server/stdio.js");

const {
  CallToolRequestSchema,
  ListToolsRequestSchema
} = require("@modelcontextprotocol/sdk/types.js");

const {
  discoverProjects,
  resolveBuildPlan,
  resolveProjectSelection,
  findEngineInstalls,
  defaultPlatform,
  getActiveProject,
  setActiveProject,
  listUnrealProjects,
  buildProjectBrowsePaths,
  resolveAgentWorkspaceRoot,
} = require("./unreal-detect.js");
const {
  scanSymbolImpact,
  validateRefactorPlan
} = require("./refactor-tools.js");
const {
  resolveReadPath,
  assertReadChildContained,
  displayPath,
  pathMetadata
} = require("./read-path-resolver.js");
const {
  validateAfterWrite,
  validateAfterDelete,
  runStaticValidation,
  resolveValidateOnWrite,
  VALIDATE_ON_WRITE_TIMEOUT_MS,
  clearValidated,
  isValidationInfrastructureFailure,
} = require("./validate-write.js");
const { validateMutationSemanticText, probeMutationSemanticGuard } = require("./mutation-semantic-guard.js");
const {
  requireCleanOrFail,
  requireValidationProofOrOverride,
  getDirtyState,
} = require("./validation-dirty");
const {
  authorizeActiveRouteTool,
  expandCompactTaskAuthorization,
  discardTaskAuthorizationWithoutActiveRoute,
  authorizeTaskRouteTool,
  discoverActiveTaskContext,
  listToolsRouteContext,
  SAFE_ROUTE_RECOVERY_TOOLS,
  validateMutationAuth,
  reserveRouteCall,
  commitRouteReservation,
  rollbackRouteReservation,
  heartbeatRouteReservation,
  readTaskState,
  taskAuthorizationForState,
  authoritativeTaskProjectFile,
  canonicalProjectIdentity,
  validateResolvedTaskProject,
  requiredFields,
  listActiveTasks,
  cancelActiveTask,
  quarantineCorruptTask,
  checkpointMutationViaPython,
  checkpointRollbackViaPython,
  completeTaskAfterBuildViaPython,
  requireAutomationAfterBuildViaPython,
  recordBuildRecoveryViaPython,
  recordRecoveryObligationViaPython,
  recordControlEventViaPython,
  bindBuildContractViaPython,
  markRecoveryEvidenceViaPython,
  scopedAbsentEvidencePath,
  inspectionReadPolicyForState,
} = require("./task-auth");
const {
  MAX_AUTOMATION_FILTERS,
  discoverAutomationTests,
  runAutomationTests,
} = require("./automation-executor");
const {
  activeRouteFingerprint,
  startActiveRouteWatcher,
} = require("./route-watcher");
const {
  applyBundleTransaction,
  finalizeTransaction,
  rollbackJournal,
  DEFAULT_MAX_FILES_PER_EDIT,
} = require("./edit-bundle");
const {
  recoverIncompleteJournals,
  prepareSingleFileJournal,
  markJournalAwaitingBuild,
  listPendingJournals,
  finalizePendingJournals,
  markPendingBuildFailed,
  archiveJournal,
  beginMutationJournal,
  commitMutationJournal,
  abandonMutationJournal,
  pendingBuildJournals,
  finalizePendingBuildJournals,
  saveJournal,
  armAtomicMutationJournal,
  stageMutationCompensation,
  markMutationStateRecorded,
  completeMutationJournalCheckpoint,
  armMutationRollback,
  completeMutationRollback,
} = require("./transaction-journal");
const { resolveAgentStateRoot, ensureStateRootLayout } = require("./state-root");
const { recordToolResult, recordToolStarted } = require("./agent-run-report.js");
const { createProgressHeartbeat } = require("./progress-heartbeat.js");
const {
  validateWriteTarget,
  shouldRollback,
  isDeleteAllowedPath,
  deletionCandidateIdentity,
  isPatchOnlyExistingFile: isPatchOnlyFile
} = require("./write-guards.js");
const {
  tryAcquirePathLock,
  releasePathLock
} = require("./write-locks.js");
const {
  checkMutationDuplicate,
  recordMutation: recordMutationAttempt,
  duplicateMutationMessage,
  clearMutationHistory
} = require("./mutation-history.js");
const {
  applyBuildRecoveryScopeBinding,
  buildResponsePayload,
  buildToolDisposition,
  compactLogPayload,
  compactMcpContent,
  compactValidationPayload,
  errorPayload,
  firstErrorCluster,
  formatSessionHandoff,
  isInterestingLogLine,
  missingReadTargetRecovery,
  resolveAgentResultMaxChars,
  slimWriteSuccessPayload,
  writeDisciplineOptions,
  writeTextArtifact
} = require("./context-ux.js");
const {
  callableAgentToolNames,
  phaseVisibleAgentToolNames,
  toolNotCallablePayload,
  projectSwitchGuidance,
} = require("./tool-exposure");
const { atomicWriteText, atomicWriteJson } = require("./atomic-io");
const {
  createExclusive,
  replaceWithCAS,
  sha256Buffer,
  sha256Text,
} = require("./safe-write");
const {
  boundedRecoveryRead,
  exactMutationCallGuard,
  bundleFailureRecovery,
} = require("./mutation-recovery.js");
const {
  beginToolCall,
  checkToolRepeatBlocked,
  recordToolFailure,
  toolRepeatBlockedMessage,
  clearToolFailureHistory,
} = require("./tool-failure-history");
const {
  exactRecoveryLogObligation,
  recoveryLogSource,
} = require("./recovery-log-contract");
const {
  checkReadRepeat,
  recordReadSuccess,
  recordReadStagnation,
  normalizeReadToolArgs,
  cachedReadInstruction,
  clearReadSuccessHistory,
  getFileCoverage,
} = require("./tool-read-history");

// Optional diagnostics. Production MCP runs must not write repository-root
// logs or POST to a hard-coded localhost collector unless explicitly enabled.
const AGENT_DEBUG_LOG = String(process.env.MCP_DEBUG_LOG || "").trim();
const AGENT_DEBUG_INGEST = String(process.env.MCP_DEBUG_INGEST_URL || "").trim();
const AGENT_DEBUG_ENABLED = /^(1|true|yes|on)$/i.test(String(process.env.MCP_DEBUG_TRACE || ""));
function agentDebugLog(hypothesisId, location, message, data) {
  if (!AGENT_DEBUG_ENABLED) return;
  const payload = {
    sessionId: String(process.env.MCP_DEBUG_SESSION_ID || "mcp-debug"),
    runId: String(process.env.MCP_DEBUG_RUN_ID || "manual"),
    hypothesisId,
    location,
    message,
    data: data || {},
    timestamp: Date.now(),
  };
  if (AGENT_DEBUG_LOG) {
    try { fs.appendFileSync(AGENT_DEBUG_LOG, `${JSON.stringify(payload)}\n`, "utf8"); } catch { /* ignore */ }
  }
  if (!AGENT_DEBUG_INGEST) return;
  try {
    fetch(AGENT_DEBUG_INGEST, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Debug-Session-Id": payload.sessionId },
      body: JSON.stringify(payload),
    }).catch(() => {});
  } catch { /* ignore */ }
}
/** path -> { reason, at } — guides stagnation messages during mutation recovery */
const mutationRecoveryHints = new Map();
function markMutationRecoveryHint(fileAbsPath, reason) {
  if (!fileAbsPath) return;
  mutationRecoveryHints.set(path.resolve(String(fileAbsPath)), {
    reason: String(reason || ""),
    at: Date.now(),
  });
}
function consumeMutationRecoveryHint(fileAbsPath) {
  if (!fileAbsPath) return null;
  const key = path.resolve(String(fileAbsPath));
  const hint = mutationRecoveryHints.get(key) || null;
  if (hint && Date.now() - hint.at > 10 * 60 * 1000) {
    mutationRecoveryHints.delete(key);
    return null;
  }
  if (hint) mutationRecoveryHints.delete(key);
  return hint;
}
// #endregion
const { runUnrealBuildFromPlan } = require("./build-executor");
const { readUtf8Range, readUtf8Tail } = require("./bounded-read");
const { createListDirectoryBudget } = require("./list-directory-budget");
const {
  beginBuild,
  finishBuild,
  beginValidation,
  finishValidationAndClear,
  recordMutation,
  recordDeletion,
  recordMutationBatch,
  compensateMutationBatch,
  reconcileMutationPathsFromDisk,
  readMutationState,
} = require("./mutation-generation");
const {
  recordValidationFailure,
  recordValidationSuccess,
  recordBuildGateFailure,
  beginBuildAttempt,
  finishBuildAttempt,
  cancelBuildAttempt,
  recordBuildBookkeepingPending,
  completeBuildBookkeeping,
  recordBuildRecoveryContract,
  recordRecoveryEvidenceCall,
  markRecoveryEvidenceSatisfied,
} = require("./workflow-loop-guard");
const { resolveProjectRootForFile } = require("./validate-write");

function numberEnv(name, fallback, min = 0) {
  const value = Number(process.env[name]);
  return Number.isFinite(value) ? Math.max(min, value) : fallback;
}

function escapeRegExp(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

const WORKSPACE_ROOT = path.resolve(process.env.WORKSPACE_ROOT || process.cwd());
const CONFIG_PATH = path.resolve(
  process.env.AGENT_MCP_CONFIG
  || path.join(__dirname, "..", "config", "agent-mcp.json")
);
const LIST_DIRECTORY_BUDGET = createListDirectoryBudget({
  windowMs: numberEnv("LIST_DIRECTORY_WINDOW_MS", 5 * 60 * 1000, 10_000),
  maxCallsPerWindow: numberEnv("LIST_DIRECTORY_MAX_CALLS", 24, 1),
  maxCallsPerPath: numberEnv("LIST_DIRECTORY_MAX_PER_PATH", 2, 1),
});
const ALLOW_WRITE = process.env.ALLOW_WRITE === "1" || process.env.ALLOW_WRITE === "true";
const ALLOW_COMMANDS = process.env.ALLOW_COMMANDS === "1" || process.env.ALLOW_COMMANDS === "true";
const ALLOW_UNREAL_BUILD = process.env.ALLOW_UNREAL_BUILD === "1" || process.env.ALLOW_UNREAL_BUILD === "true";
const EXISTING_SOURCE_WRITE_OVERRIDE_REQUESTED = ["1", "true", "yes", "on"].includes(
  String(process.env.ALLOW_EXISTING_SOURCE_WRITE || "").trim().toLowerCase()
);
if (EXISTING_SOURCE_WRITE_OVERRIDE_REQUESTED) {
  // stderr only: stdout carries the MCP stdio protocol.
  console.error(
    "[unreal-agent] ALLOW_EXISTING_SOURCE_WRITE is deprecated and ignored. "
    + "Existing files require a bounded read_file_range followed by replace_in_file."
  );
}
const MAX_READ_BYTES = Math.min(
  Math.trunc(numberEnv("MAX_READ_BYTES", 64 * 1024, 4 * 1024)),
  32 * 1024 * 1024
);
const FILE_CACHE_MAX_ENTRIES = numberEnv("FILE_CACHE_MAX_ENTRIES", 20, 0);
const FILE_CACHE_MAX_BYTES = numberEnv("FILE_CACHE_MAX_BYTES", MAX_READ_BYTES, 0);
const WORKSPACE_INFO_CACHE_TTL_MS = numberEnv("WORKSPACE_INFO_CACHE_TTL_MS", 60 * 1000, 0);
const CODE_DETAIL_READ_BYTES = {
  compact: 16 * 1024,
  medium: 32 * 1024,
  large: MAX_READ_BYTES,
  full: MAX_READ_BYTES
};
const CODE_DETAIL_LINE_CAP = {
  compact: 150,
  medium: 400,
  large: 1200,
  full: 2000
};

function resolveCodeDetail(raw) {
  const key = String(raw || "compact").trim().toLowerCase();
  return Object.prototype.hasOwnProperty.call(CODE_DETAIL_READ_BYTES, key) ? key : "compact";
}
const MAX_OUTPUT_BYTES = Math.min(
  Math.trunc(numberEnv("MAX_OUTPUT_BYTES", 1024 * 256, 16 * 1024)),
  8 * 1024 * 1024
);
const MCP_AGENT_RESULT_MAX_CHARS = resolveAgentResultMaxChars();
const BUILD_VERBOSE_OUTPUT = ["1", "true", "yes", "on"].includes(
  String(process.env.BUILD_VERBOSE_OUTPUT || "").trim().toLowerCase()
);
const COMMAND_TIMEOUT_MS = Math.min(
  Math.trunc(numberEnv("COMMAND_TIMEOUT_MS", 1000 * 60 * 10, 1000)),
  1000 * 60 * 60
);
const SEARCH_MAX_FILES = Math.min(
  Math.trunc(numberEnv("SEARCH_MAX_FILES", 5000, 1)),
  50_000
);
const LOG_READ_MAX_BYTES = Math.min(
  numberEnv("LOG_READ_MAX_BYTES", 4 * 1024 * 1024, 64 * 1024),
  32 * 1024 * 1024
);
const LOG_FIRST_ERROR_SCAN_MAX_BYTES = Math.min(
  numberEnv("LOG_FIRST_ERROR_SCAN_MAX_BYTES", 32 * 1024 * 1024, LOG_READ_MAX_BYTES),
  256 * 1024 * 1024
);
// Python task state persists these as 256-item execution batches. Keep the
// total contract aligned while exposing only the active batch to the tool.
const MAX_AUTOMATION_FILTERS_TOTAL = 4096;
const ALLOW_SOURCE_DELETE = ["1", "true", "yes", "on"].includes(
  String(process.env.ALLOW_SOURCE_DELETE || "").trim().toLowerCase()
);
const VALIDATE_ON_WRITE = resolveValidateOnWrite();
const MCP_ESSENTIAL_TOOLS = ["1", "true", "yes", "on"].includes(
  String(process.env.MCP_ESSENTIAL_TOOLS || "").trim().toLowerCase()
);
const MCP_EXTENDED_TOOLS = ["1", "true", "yes", "on"].includes(
  String(process.env.MCP_EXTENDED_TOOLS || "").trim().toLowerCase()
);
const CONTROL_PLANE_TOOLS = ["1", "true", "yes", "on"].includes(
  String(process.env.ALLOW_CONTROL_PLANE_TOOLS || "").trim().toLowerCase()
);
const REQUIRE_TASK_AUTH_FOR_WRITES = !["0", "false", "no", "off"].includes(
  String(process.env.MCP_REQUIRE_PLAN_AUTH ?? "1").trim().toLowerCase()
);
const ROUTE_MUTATION_TOOLS = new Set([
  "write_file",
  "replace_in_file",
  "delete_file",
  "apply_edit_bundle",
]);
const UNROUTED_INSPECTION_TOOLS = new Set([
  "get_workspace_info",
  "list_directory",
  "read_file",
  "read_file_range",
  "read_symbol",
  "search_files",
]);
// Tool arguments share the model's generation budget.  Full-file old/new
// payloads routinely truncate before LM Studio can dispatch the call, so the
// server contract keeps mutations small enough to be generated reliably.
const MAX_PATCH_ARGUMENT_CHARS = 12_000;
const MAX_PATCH_CHANGED_LINES = 60;
const MAX_NEW_FILE_ARGUMENT_CHARS = 12_000;
const MAX_NEW_FILE_LINES = 160;

/** Tracks shared activeProject so history clears when rag or agent switches projects. */
let lastSeenActiveProjectKey = null;

function clearLoopHistoriesOnProjectChange(force = false) {
  const current = String(getActiveProject(CONFIG_PATH) || "");
  if (!force && lastSeenActiveProjectKey !== null && current === lastSeenActiveProjectKey) {
    return false;
  }
  const changed = lastSeenActiveProjectKey !== null && current !== lastSeenActiveProjectKey;
  if (force || changed) {
    clearReadSuccessHistory();
    clearToolFailureHistory();
    clearMutationHistory();
    readEvidence.clear();
    fileCache.clear();
  }
  lastSeenActiveProjectKey = current;
  return force || changed;
}
const PATCH_ONLY_EXISTING_EXTENSIONS = new Set([".h", ".hpp", ".cpp", ".c", ".cc", ".cxx", ".cs"]);
const fileCache = new Map();
const readEvidence = new Map();
let workspaceInfoCache = null;
let runtimeComponentStatus = null;

const SERVER_VERSION = (() => {
  try {
    return String(require("../package.json").version || "unknown");
  } catch {
    return "unknown";
  }
})();

const server = new Server(
  {
    name: "lmstudio-unreal-agent-mcp",
    version: SERVER_VERSION
  },
  {
    capabilities: {
      tools: { listChanged: true },
      logging: {}
    }
  }
);
const toolCallContext = new AsyncLocalStorage();
let lastObservedRouteFingerprint = "";

function launchProjectPicker(explorer = false) {
  if (process.platform !== "win32") {
    return {
      ok: false,
      error: "project_picker_windows_only",
      message: "The project picker requires Windows (PowerShell). Use rag.ps1 pick-project manually or set activeProject in the shared config."
    };
  }
  const ragRoot = resolveAgentWorkspaceRoot();
  const script = path.join(ragRoot, "scripts", "pick_active_project.ps1");
  const args = ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script];
  if (explorer) {
    args.push("-Explorer");
  }
  const child = cp.spawn("powershell.exe", args, {
    detached: true,
    stdio: "ignore",
    windowsHide: false
  });
  child.unref();
  return {
    ok: true,
    message: explorer
      ? "Opened Windows file picker for .uproject on your desktop."
      : "Opened Windows project list picker on your desktop.",
    script
  };
}

function text(content) {
  let publicContent = String(content);
  let structuredContent = null;
  const context = toolCallContext.getStore() || {};
  try {
    structuredContent = sanitizeModelPayload(
      attachControlEnvelope(JSON.parse(publicContent), String(context.toolName || ""))
    );
    publicContent = modelVisibleControlText(
      structuredContent,
      process.env.MCP_FRONTEND || "",
      MCP_AGENT_RESULT_MAX_CHARS
    );
  } catch {
    // Plain text tool responses have no protocol object to sanitize.
  }
  const result = {
    content: [{
      type: "text",
      text: compactMcpContent(publicContent, MCP_AGENT_RESULT_MAX_CHARS)
    }]
  };
  if (structuredContent) {
    result.structuredContent = structuredContent;
    try {
      recordToolResult(WORKSPACE_ROOT, {
        toolName: String(context.toolName || "unknown"),
        arguments: context.arguments && typeof context.arguments === "object" ? context.arguments : {},
        structured: structuredContent,
        callId: String(context.callId || ""),
        durationMs: context.startedAtMs ? Date.now() - Number(context.startedAtMs) : 0,
      });
    } catch {
      // Telemetry is deliberately non-authoritative.
    }
  }
  try {
    context.progressHeartbeat?.finish();
  } catch {
    // Progress transport support is optional.
  }
  return result;
}

function fail(message, options = {}) {
  const payload = errorPayload(message, options);
  const result = text(JSON.stringify(payload, null, 2));
  result.isError = true;
  return result;
}

function isSemanticGuardSourcePath(filePath) {
  return [".h", ".hpp", ".cpp", ".c", ".cc", ".cxx", ".inl"]
    .includes(path.extname(String(filePath || "")).toLowerCase());
}

function mutationSemanticGuardFailure(guard, relPath) {
  const hits = Array.isArray(guard?.hits) ? guard.hits : [];
  const first = hits[0] || {};
  const reason = guard?.infrastructureError
    ? String(guard.reason || "semantic guard infrastructure failed")
    : String(first.message || "The prospective source contains a known-bad code pattern.");
  return fail(`Mutation semantic guard blocked ${relPath}: ${reason}`, {
    errorCode: guard?.infrastructureError
      ? "MUTATION_SEMANTIC_GUARD_UNAVAILABLE"
      : "MUTATION_SEMANTIC_GUARD_FAILED",
    retryable: true,
    stopCurrentWorkflow: false,
    nextAction: "unreal_code_sketch_claim_validate",
    nextActionIsTool: true,
    semanticGuard: guard,
    agentInstruction: (
      "The actual prospective file content failed the same semantic denylist used by the code-sketch gate. "
      + "Use the first replacement guidance, rerun unreal_code_sketch_claim_validate for this exact target, "
      +
      "then submit a corrected bounded patch. Do not weaken or omit the requested behavior."
    ),
  });
}

function agentRegisteredToolNames() {
  return allAgentTools().map((tool) => tool.name);
}

function mutationBookkeepingFailure(message, operation, relPath, rollback = null) {
  const retryToolByOperation = {
    create: "write_file",
    replace: "replace_in_file",
    delete: "delete_file",
    apply_edit_bundle: "apply_edit_bundle",
  };
  const doNotRetry = [retryToolByOperation[operation] || operation].filter(Boolean);
  const rolledBack = rollback?.rolledBack === true;
  return fail(String(message || "Mutation bookkeeping failed after write."), {
    errorCode: "MUTATION_LOCK_BUSY",
    path: relPath,
    operation,
    writeApplied: !rolledBack,
    rolledBack,
    rollbackIncomplete: rollback ? rollback.rollbackIncomplete === true : null,
    bookkeepingFailed: true,
    mutationGenerationNotRecorded: true,
    retryable: rolledBack,
    doNotRetry: rolledBack ? [] : doNotRetry,
    ...(rolledBack ? {
      nextAction: retryToolByOperation[operation] || operation,
      nextActionIsTool: true,
    } : {}),
    nextSteps: rolledBack
      ? ["The transaction was rolled back; refresh task control before retrying once."]
      : [
        `Do NOT retry ${doNotRetry.join(" or ")} — the file change may still be on disk.`,
        "Call read_file on the same path to confirm current content.",
        "Call static_validate_project (or build_unreal_project when appropriate) to recover validation state.",
      ],
    agentInstruction: rolledBack
      ? "Bookkeeping failed and the disk transaction was restored; refresh control before retrying."
      : "Bookkeeping failed after a successful write; verify disk state before any further edits.",
  });
}

async function bumpProjectMutationGeneration(targetPath, content, options = {}) {
  const uproject = getActiveProject(CONFIG_PATH);
  if (!uproject) return null;
  const projectDir = path.dirname(path.resolve(uproject));
  const projectRelativePath = path.relative(projectDir, path.resolve(targetPath)).replace(/\\/g, "/");
  if (!projectRelativePath || projectRelativePath.startsWith("../") || path.isAbsolute(projectRelativePath)) {
    const workspaceRelativePath = path.relative(WORKSPACE_ROOT, path.resolve(targetPath)).replace(/\\/g, "/");
    if (workspaceRelativePath === ".agent" || workspaceRelativePath.startsWith(".agent/")) {
      return null;
    }
    throw new Error(`mutation path outside active project: ${targetPath}`);
  }
  return await recordMutation(projectDir, projectRelativePath, content, options);
}

function mutationTaskSessionId(args = {}) {
  return String(requiredFields(args).taskSessionId || "").trim();
}

function inspectionReadPolicy(args = {}) {
  const taskSessionId = mutationTaskSessionId(args);
  if (!taskSessionId) return null;
  const state = readTaskState(WORKSPACE_ROOT, taskSessionId);
  return inspectionReadPolicyForState(state);
}

function mutationJournalLocation(targetPath, args = {}) {
  const absoluteTarget = path.resolve(targetPath);
  const activeProject = getActiveProject(CONFIG_PATH);
  let projectRoot = WORKSPACE_ROOT;
  if (activeProject) {
    const candidate = path.dirname(path.resolve(activeProject));
    const relative = path.relative(candidate, absoluteTarget);
    if (relative && !relative.startsWith("..") && !path.isAbsolute(relative)) {
      projectRoot = candidate;
    }
  }
  return {
    projectRoot,
    taskSessionId: mutationTaskSessionId(args),
    relativePath: path.relative(projectRoot, absoluteTarget).replace(/\\/g, "/"),
  };
}

function pendingMutationQuery(projectRoot, args, mutationGeneration = null) {
  return {
    projectRoot,
    taskSessionId: mutationTaskSessionId(args),
    mutationGeneration,
  };
}

function recordMutationEvidenceRecovery(args, options = {}) {
  const taskSessionId = mutationTaskSessionId(args);
  if (!taskSessionId) return { ok: true, active: false };
  const taskState = readTaskState(WORKSPACE_ROOT, taskSessionId);
  if (!taskState || String(taskState.status || "") !== "running") {
    return { ok: false, active: true, errorCode: "TASK_STATE_MISSING", error: "Active mutation task state is unavailable." };
  }
  const requiredArgs = options.requiredArgs && typeof options.requiredArgs === "object"
    ? options.requiredArgs
    : {};
  const targetFiles = Array.isArray(options.targetFiles)
    ? options.targetFiles.map((item) => String(item || "").replace(/\\/g, "/")).filter(Boolean).slice(0, 4)
    : [];
  return recordRecoveryObligationViaPython(
    WORKSPACE_ROOT,
    { taskAuthorization: taskAuthorizationForState(taskState) },
    {
      source: "mutation",
      status: "evidence_required",
      scopeDisposition: "in_slice",
      errorCode: String(options.errorCode || "MUTATION_EVIDENCE_REFRESH_REQUIRED"),
      mutationGeneration: Math.max(0, Number(taskState.mutationGeneration || 0)),
      requiredTool: { name: "read_file_range", args: requiredArgs },
      targetFiles,
      message: String(options.message || "Read the bounded current source range before constructing a new exact mutation call."),
      ...(options.failedCallFingerprint
        ? { failedCallFingerprint: String(options.failedCallFingerprint) }
        : {}),
    }
  );
}

function recordMutationFailureRecovery(args, options = {}) {
  const taskSessionId = mutationTaskSessionId(args);
  if (!taskSessionId) return { ok: true, active: false };
  const taskState = readTaskState(WORKSPACE_ROOT, taskSessionId);
  if (!taskState || String(taskState.status || "") !== "running") {
    return { ok: false, active: true, errorCode: "TASK_STATE_MISSING", error: "Active mutation task state is unavailable." };
  }
  const targetFiles = Array.isArray(options.targetFiles)
    ? options.targetFiles.map((item) => String(item || "").replace(/\\/g, "/")).filter(Boolean).slice(0, 4)
    : [];
  const reconciliationRequired = options.rollbackIncomplete === true || options.externalChange === true;
  const requiredTool = reconciliationRequired
    ? {
      name: "unreal_task_checkpoint",
      args: { action: "rebase", acceptCurrentFiles: true, includeGitChanges: false },
    }
    : {
      name: "unreal_code_sketch_claim_validate",
      args: targetFiles.length ? { targetFiles } : {},
    };
  return recordRecoveryObligationViaPython(
    WORKSPACE_ROOT,
    { taskAuthorization: taskAuthorizationForState(taskState) },
    {
      source: "mutation",
      status: reconciliationRequired ? "checkpoint_rebase_required" : "repair_planning_required",
      scopeDisposition: "in_slice",
      errorCode: String(options.errorCode || "MUTATION_VALIDATION_FAILED"),
      mutationGeneration: Math.max(0, Number(taskState.mutationGeneration || 0)),
      requiredTool,
      targetFiles,
      transactionId: String(options.transactionId || ""),
      journalPaths: targetFiles,
      message: String(options.message || (
        reconciliationRequired
          ? "The mutation rollback could not prove the pre-image. Rebase the exact task checkpoint against current files."
          : "The mutation was rolled back after validation. Validate a corrected bounded repair claim before writing again."
      )),
    }
  );
}

function promoteJournalRecoveryRequired(item, stateRoot = resolveAgentStateRoot()) {
  const taskSessionId = String(item?.taskSessionId || "").trim();
  if (!taskSessionId) {
    return { ok: true, active: false, idempotent: true, reason: "journal_not_task_bound" };
  }
  const taskState = readTaskState(WORKSPACE_ROOT, taskSessionId, stateRoot);
  if (!taskState || String(taskState.status || "") !== "running") {
    return {
      ok: false,
      active: true,
      errorCode: "TRANSACTION_RECOVERY_TASK_UNAVAILABLE",
      error: "The transaction journal is task-bound, but its running task state is unavailable.",
    };
  }
  const transactionId = String(item?.transactionId || "");
  const current = taskState.recoveryObligation && typeof taskState.recoveryObligation === "object"
    ? taskState.recoveryObligation
    : {};
  if (
    String(current.transactionId || "") === transactionId
    || (
      String(current.source || "") === "transaction_journal"
      && String(current.message || "").includes(transactionId)
    )
  ) {
    return { ok: true, active: true, idempotent: true, transactionId };
  }
  const targetFiles = Array.isArray(item?.paths)
    ? item.paths.map((value) => String(value || "").replace(/\\/g, "/")).filter(Boolean).slice(0, 4)
    : [];
  return recordRecoveryObligationViaPython(
    WORKSPACE_ROOT,
    { taskAuthorization: taskAuthorizationForState(taskState) },
    {
      source: "transaction_journal",
      status: "checkpoint_rebase_required",
      scopeDisposition: "in_slice",
      errorCode: "TRANSACTION_RECONCILIATION_REQUIRED",
      mutationGeneration: Math.max(0, Number(taskState.mutationGeneration || 0)),
      requiredTool: {
        name: "unreal_task_checkpoint",
        args: {
          action: "rebase",
          acceptCurrentFiles: true,
          includeGitChanges: false,
        },
      },
      targetFiles,
      transactionId,
      projectRoot: String(item?.projectRoot || ""),
      journalPaths: targetFiles,
      message: `Transaction ${transactionId} requires an exact checkpoint rebase before mutation work can continue.`,
    }
  );
}

function mutationCompensationOptions(journal, location) {
  return {
    prepareCompensation: async (receipt, pending = {}) => {
      stageMutationCompensation(journal, receipt, {
        ...location,
        mutationGeneration: pending.mutationGeneration,
        mutationRevision: pending.mutationRevision,
      });
    },
  };
}

async function finalizeDiskRollback(
  journal,
  projectRoot,
  diskRollback,
  receipt = null,
  reason = "mutation_failure"
) {
  const effectiveReceipt = receipt || journal?.mutationCompensationReceipt || null;
  let compensation = {
    compensated: !journal?.mutationStateRecorded && !effectiveReceipt,
    notRequired: !journal?.mutationStateRecorded && !effectiveReceipt,
    conflict: false,
  };
  if (diskRollback?.rolledBack === true && effectiveReceipt) {
    try {
      compensation = await compensateMutationBatch(projectRoot, effectiveReceipt);
    } catch (error) {
      compensation = {
        compensated: false,
        conflict: false,
        errorCode: String(error.errorCode || "MUTATION_COMPENSATION_FAILED"),
        error: String(error.message || error),
      };
    }
  }
  const fullyRestored = diskRollback?.rolledBack === true && compensation.compensated === true;
  if (fullyRestored) {
    await completeMutationRollback(journal, {
      strategy: effectiveReceipt ? "compensation_receipt" : "not_required",
      reason: String(reason || "mutation_failure"),
      compensation,
    });
  } else if (journal) {
    journal.status = "recovery_required";
    journal.rollbackReason = String(reason || "mutation_failure");
    journal.rollbackCompensation = compensation;
    journal.updatedAt = new Date().toISOString();
    saveJournal(journal);
  }
  return {
    rollback: {
      ...(diskRollback || {}),
      diskRolledBack: diskRollback?.rolledBack === true,
      rolledBack: fullyRestored,
      rollbackIncomplete: !fullyRestored,
    },
    compensation,
    fullyRestored,
  };
}

async function rollbackMutationJournals(journals, projectRoot, reason, args = {}) {
  const results = [];
  const checkpointArgs = { ...(args || {}) };
  let reconciliation = { reconciled: journals.length === 0 };
  let taskCheckpoint = {
    ok: true,
    skipped: true,
    reason: journals.length ? "not_attempted" : "no_rollback",
  };
  for (const journal of journals) {
    armMutationRollback(journal, { reason: String(reason || "workflow_rollback") });
    const rollback = await rollbackJournal(journal);
    const affectedPaths = [];
    const affectedAbsolutePaths = [];
    for (const entry of journal.entries || []) {
      if (entry?.canonicalAbsolutePath) invalidateFileCache(entry.canonicalAbsolutePath);
      if (entry?.relativePath) affectedPaths.push(entry.relativePath);
      if (entry?.canonicalAbsolutePath) affectedAbsolutePaths.push(entry.canonicalAbsolutePath);
    }
    if (rollback.rolledBack === true) {
      try {
        reconciliation = await reconcileMutationPathsFromDisk(projectRoot, affectedPaths);
      } catch (error) {
        reconciliation = {
          reconciled: false,
          errorCode: String(error.errorCode || "MUTATION_ROLLBACK_RECONCILIATION_FAILED"),
          error: String(error.message || error),
        };
      }
    } else {
      reconciliation = {
        reconciled: false,
        errorCode: "MUTATION_DISK_ROLLBACK_FAILED",
        error: "The journal post-image could not be restored to its pre-image.",
      };
    }
    const journalTaskSessionId = String(journal.taskSessionId || "").trim();
    if (reconciliation.reconciled === true) {
      if (
        REQUIRE_TASK_AUTH_FOR_WRITES
        && journalTaskSessionId
        && !mutationTaskSessionId(checkpointArgs)
      ) {
        taskCheckpoint = {
          ok: false,
          errorCode: "ROLLBACK_TASK_AUTHORIZATION_MISSING",
          error: "A task-bound rollback requires an authoritative task checkpoint.",
        };
      } else {
        taskCheckpoint = recordAutomaticContinuityCheckpoint(
          checkpointArgs,
          [...new Set(affectedAbsolutePaths.map((item) => path.resolve(item)))],
          {
            status: "pending",
            proofLevel: "NeedsStaticValidation",
            rollback: {
              reason: String(reason || "workflow_rollback"),
              restoredPaths: [...new Set(affectedPaths)],
            },
          },
          reconciliation.mutationGeneration
        );
      }
    } else {
      taskCheckpoint = {
        ok: false,
        skipped: true,
        errorCode: "ROLLBACK_RECONCILIATION_INCOMPLETE",
        error: "Rollback reconciliation did not reach the task checkpoint.",
      };
    }
    if (taskCheckpoint?.taskAuthorization) {
      checkpointArgs.taskAuthorization = taskCheckpoint.taskAuthorization;
      delete checkpointArgs.task_authorization;
    }
    const taskCheckpointCommitted = taskCheckpoint?.ok === true;
    const diskRolledBack = rollback.rolledBack === true;
    const fullyRolledBack = (
      diskRolledBack
      && reconciliation.reconciled === true
      && taskCheckpointCommitted
    );
    if (fullyRolledBack) {
      await completeMutationRollback(journal, {
        strategy: "disk_reconciliation",
        reason: String(reason || "workflow_rollback"),
        mutationGeneration: reconciliation.mutationGeneration,
        mutationRevision: reconciliation.mutationRevision,
        checkpointCommitted: true,
        checkpointHash: String(taskCheckpoint.checkpointHash || ""),
      });
    } else {
      journal.status = diskRolledBack ? "rollback_state_pending" : "recovery_required";
      journal.rollbackReason = String(reason || "workflow_rollback");
      journal.rollbackReconciliation = reconciliation;
      journal.rollbackCheckpointFailure = taskCheckpointCommitted ? null : taskCheckpoint;
      journal.updatedAt = new Date().toISOString();
      saveJournal(journal);
    }
    results.push({
      transactionId: journal.transactionId,
      ...rollback,
      diskRolledBack,
      mutationStateReconciled: reconciliation.reconciled === true,
      taskCheckpointCommitted,
      rolledBack: fullyRolledBack,
      rollbackIncomplete: !fullyRolledBack,
      reconciliation,
      taskCheckpoint,
    });
    if (!fullyRolledBack) break;
  }
  const rollbackIncomplete = (
    results.some((item) => item.rollbackIncomplete)
    || results.length !== journals.length
  );
  return {
    attempted: results.length > 0,
    rolledBack: results.length > 0 && !rollbackIncomplete,
    rollbackIncomplete,
    reason: String(reason || "workflow_rollback"),
    reconciliation,
    taskCheckpoint,
    transactions: results,
  };
}

async function markPendingMutationJournals(query, status, metadata = {}) {
  const marked = [];
  for (const journal of pendingBuildJournals(query)) {
    journal.status = status;
    journal.updatedAt = new Date().toISOString();
    journal.lifecycle = {
      ...(journal.lifecycle && typeof journal.lifecycle === "object" ? journal.lifecycle : {}),
      ...metadata,
    };
    saveJournal(journal);
    marked.push(journal.transactionId);
  }
  return marked;
}

async function rollbackPendingMutationJournals(query, reason, args = {}) {
  const failedJournals = pendingBuildJournals({
    ...query,
    statuses: ["validation_failed", "build_failed"],
  }).reverse();
  // Roll back the newest post-image first.  For A -> B -> C edits on one
  // file, this preserves each CAS chain (C -> B, then B -> A).
  return rollbackMutationJournals(
    failedJournals,
    query.projectRoot,
    reason || "terminal_validation_failure",
    args
  );
}

function recordAutomaticContinuityCheckpoint(args, modifiedFiles, validation = null, mutationGeneration = 0) {
  // Plan-auth off: skip Python continuity checkpoint. Models often still pass a
  // fabricated taskSessionId; task_api would then fail every write for any project.
  if (!REQUIRE_TASK_AUTH_FOR_WRITES) {
    agentDebugLog("H9", "server.js:recordAutomaticContinuityCheckpoint", "skip continuity checkpoint (plan-auth disabled)", {
      modifiedCount: Array.isArray(modifiedFiles) ? modifiedFiles.length : 0,
      hadTaskSessionId: Boolean(requiredFields(args || {}).taskSessionId),
    });
    return {
      ok: true,
      skipped: true,
      reason: "plan_auth_disabled",
      checkpointHash: "",
      phase: "executor",
      modifiedFiles: Array.isArray(modifiedFiles) ? modifiedFiles : [],
    };
  }
  const checkpoint = checkpointMutationViaPython(WORKSPACE_ROOT, args, modifiedFiles, {
    requiredNextAction: "static_validate_project",
    validation: validation || {},
    mutationGeneration,
  });
  if (!checkpoint || checkpoint.ok !== true) {
    return {
      ok: false,
      errorCode: String(checkpoint?.errorCode || "CONTINUITY_CHECKPOINT_FAILED"),
      error: String(checkpoint?.error || "Automatic continuity checkpoint failed."),
      ...(checkpoint?.taskAuthorization && typeof checkpoint.taskAuthorization === "object"
        ? { taskAuthorization: checkpoint.taskAuthorization }
        : {}),
      ...(checkpoint?.toolRoute && typeof checkpoint.toolRoute === "object"
        ? { toolRoute: checkpoint.toolRoute }
        : {}),
      ...(Number.isInteger(Number(checkpoint?.controlEpoch))
        ? { controlEpoch: Math.max(0, Number(checkpoint.controlEpoch)) }
        : {}),
      ...(checkpoint?.control && typeof checkpoint.control === "object"
        ? { control: checkpoint.control }
        : {}),
    };
  }
  const continuity = checkpoint.continuity && typeof checkpoint.continuity === "object"
    ? checkpoint.continuity
    : {};
  const recorded = continuity.checkpoint && typeof continuity.checkpoint === "object"
    ? continuity.checkpoint
    : {};
  return {
    ok: true,
    checkpointHash: String(recorded.checkpointHash || ""),
    phase: String(recorded.phase || "executor"),
    modifiedFiles: Array.isArray(recorded.modifiedFiles) ? recorded.modifiedFiles : [],
    ...(checkpoint.taskAuthorization && typeof checkpoint.taskAuthorization === "object"
      ? { taskAuthorization: checkpoint.taskAuthorization }
      : {}),
    ...(checkpoint.toolRoute && typeof checkpoint.toolRoute === "object"
      ? { toolRoute: checkpoint.toolRoute }
      : {}),
    ...(Number.isInteger(Number(checkpoint.controlEpoch))
      ? { controlEpoch: Math.max(0, Number(checkpoint.controlEpoch)) }
      : {}),
    ...(checkpoint.control && typeof checkpoint.control === "object"
      ? { control: checkpoint.control }
      : {}),
  };
}

function recoverRollbackContinuityCheckpoint({
  journal,
  reconciliation,
  absolutePaths,
  stateRoot,
}) {
  if (!REQUIRE_TASK_AUTH_FOR_WRITES) {
    return recordAutomaticContinuityCheckpoint(
      {},
      absolutePaths,
      {
        status: "pending",
        proofLevel: "NeedsStaticValidation",
        rollback: { reason: String(journal?.rollbackIntent?.reason || "startup_recovery") },
      },
      reconciliation.mutationGeneration
    );
  }
  const taskSessionId = String(journal?.taskSessionId || "").trim();
  if (!taskSessionId || !readTaskState(WORKSPACE_ROOT, taskSessionId, stateRoot)) {
    return {
      ok: false,
      errorCode: "ROLLBACK_TASK_STATE_MISSING",
      error: "The rollback journal is task-bound but its task state is unavailable.",
    };
  }
  return checkpointRollbackViaPython(WORKSPACE_ROOT, {
    transactionId: String(journal?.transactionId || ""),
    taskSessionId,
    projectRoot: String(journal?.projectRoot || ""),
    modifiedFiles: absolutePaths,
    mutationGeneration: reconciliation.mutationGeneration,
    validation: {
      status: "pending",
      proofLevel: "NeedsStaticValidation",
      rollback: { reason: String(journal?.rollbackIntent?.reason || "startup_recovery") },
    },
  });
}

function validationScopeForTask(args, mutationGeneration) {
  const taskSessionId = requiredFields(args || {}).taskSessionId;
  const taskState = taskSessionId
    ? readTaskState(WORKSPACE_ROOT, taskSessionId)
    : null;
  return deriveValidationScope(taskState, mutationGeneration, {
    fullAudit: args?.fullAudit === true,
    taskBound: Boolean(taskSessionId),
  });
}

function automationScopeForTask(args, mutationGeneration) {
  const validationScope = validationScopeForTask(args, mutationGeneration);
  if (validationScope.kind === "task_scope_unavailable") return validationScope;
  const taskSessionId = requiredFields(args || {}).taskSessionId;
  const taskState = taskSessionId
    ? readTaskState(WORKSPACE_ROOT, taskSessionId)
    : null;
  const repairScope = taskState?.repairScope && typeof taskState.repairScope === "object"
    ? taskState.repairScope
    : {};
  const temporarySliceId = String(repairScope.temporarySliceId || "").trim();
  const activeRepairScope = (
    String(repairScope.status || "") === "active"
    && temporarySliceId
    && temporarySliceId === String(taskState?.activeSliceId || "").trim()
  );
  if (!activeRepairScope) return validationScope;
  const normalizeTarget = (value) => String(value || "")
    .replace(/\\/g, "/")
    .replace(/^\.\//, "")
    .replace(/^\/+/, "")
    .trim();
  const causalTargets = Array.isArray(repairScope.causalSliceFiles)
    ? repairScope.causalSliceFiles
    : [];
  const targets = [...new Set([
    ...(Array.isArray(validationScope.targets) ? validationScope.targets : []),
    ...causalTargets,
    repairScope.targetFile,
  ].map(normalizeTarget).filter(Boolean))];
  return {
    ...validationScope,
    targets,
    repairCoverage: {
      temporarySliceId,
      supersededSliceId: String(repairScope.supersededSliceId || ""),
      causalTargetCount: causalTargets.length,
    },
  };
}

function recordValidationContinuityCheckpoint(args, validation, passed, mutationGeneration = 0) {
  if (!REQUIRE_TASK_AUTH_FOR_WRITES || !requiredFields(args || {}).taskSessionId) {
    return { ok: true, skipped: true, reason: "no_active_task_authorization" };
  }
  const findings = Array.isArray(validation?.findings) ? validation.findings : [];
  const isBlockingFinding = (item) => (
    item?.blocking === true
    || (
      item?.blocking === undefined
      && String(item?.severity || "").toLowerCase() === "error"
    )
  );
  const firstBlocking = findings.find((item) => item?.blocking === true)
    || findings.find((item) => String(item?.severity || "").toLowerCase() === "error")
    || findings[0]
    || null;
  const firstFinding = firstBlocking ? {
    severity: String(firstBlocking.severity || ""),
    code: String(firstBlocking.code || ""),
    path: String(firstBlocking.path || ""),
    line: Number(firstBlocking.line || 0),
    message: String(firstBlocking.message || ""),
  } : null;
  const findingFingerprint = firstFinding
    ? crypto.createHash("sha256").update(JSON.stringify({
      mutationGeneration: Number(mutationGeneration || 0),
      ...firstFinding,
    })).digest("hex").slice(0, 24)
    : "";
  const compact = {
    status: passed ? "passed" : "failed",
    proofLevel: passed ? "StaticVerified" : "StaticFailed",
    findingCount: Number(validation?.findingCount || findings.length || 0),
    blockingErrorCount: findings.filter(isBlockingFinding).length,
    ...(firstFinding ? {
      firstFinding,
      ...(!passed ? {
        recovery: {
          status: "evidence_required",
          findingFingerprint,
          targetPath: firstFinding.path,
          mutationGeneration: Number(mutationGeneration || 0),
          failedAt: new Date().toISOString(),
        },
      } : {}),
    } : {}),
  };
  const checkpoint = checkpointMutationViaPython(WORKSPACE_ROOT, args, [], {
    requiredNextAction: passed ? "build_unreal_project" : "read_file",
    validation: compact,
    note: passed
      ? "Static validation passed; build is the next required proof."
      : "Static validation failed; read the first finding before editing or rebuilding.",
    mutationGeneration,
  });
  if (!checkpoint || checkpoint.ok !== true) {
    return {
      ok: false,
      errorCode: String(checkpoint?.errorCode || "VALIDATION_CHECKPOINT_FAILED"),
      error: String(checkpoint?.error || "Validation continuity checkpoint failed."),
    };
  }
  return {
    ok: true,
    checkpointHash: String(checkpoint.continuity?.checkpoint?.checkpointHash || ""),
    phase: String(checkpoint.continuity?.checkpoint?.phase || "executor"),
    ...(checkpoint.taskAuthorization && typeof checkpoint.taskAuthorization === "object"
      ? { taskAuthorization: checkpoint.taskAuthorization }
      : {}),
    ...(checkpoint.toolRoute && typeof checkpoint.toolRoute === "object"
      ? { toolRoute: checkpoint.toolRoute }
      : {}),
    ...(Number.isInteger(Number(checkpoint.controlEpoch))
      ? { controlEpoch: Math.max(0, Number(checkpoint.controlEpoch)) }
      : {}),
    ...(checkpoint.control && typeof checkpoint.control === "object"
      ? { control: checkpoint.control }
      : {}),
  };
}

function continuityCheckpointFailure(
  checkpoint,
  operation,
  paths,
  mutation = null,
  rollback = null,
  compensation = null
) {
  const restored = rollback?.rolledBack === true && compensation?.compensated === true;
  return fail(checkpoint.error || "Automatic continuity checkpoint failed after write.", {
    errorCode: "CONTINUITY_CHECKPOINT_FAILED",
    underlyingErrorCode: checkpoint.errorCode || "",
    operation,
    paths,
    writeApplied: !restored,
    rolledBack: rollback?.rolledBack ?? null,
    rollbackIncomplete: rollback?.rollbackIncomplete ?? null,
    mutationStateCompensated: compensation?.compensated ?? null,
    compensationConflict: compensation?.conflict ?? null,
    checkpointFailed: true,
    retryable: restored,
    doNotRetry: restored ? [] : [operation],
    nextAction: restored ? operation : "unreal_task_checkpoint",
    nextActionIsTool: true,
    ...(checkpoint.taskAuthorization ? { taskAuthorization: checkpoint.taskAuthorization } : {}),
    ...(checkpoint.toolRoute ? { toolRoute: checkpoint.toolRoute } : {}),
    ...(Number.isInteger(Number(checkpoint.controlEpoch))
      ? { controlEpoch: Math.max(0, Number(checkpoint.controlEpoch)) }
      : {}),
    ...(checkpoint.control ? { control: checkpoint.control } : {}),
    ...(mutation ? {
      attemptedMutationGeneration: mutation.mutationGeneration,
      ...(!restored ? { mutationGeneration: mutation.mutationGeneration } : {}),
    } : {}),
    nextSteps: restored
      ? ["The disk and mutation state were restored; refresh task control before retrying once."]
      : [
        "Do NOT retry the mutation until disk and mutation state are reconciled.",
        "Read the affected file(s), then call unreal_task_checkpoint to recover continuity.",
        "Run static_validate_project before build_unreal_project.",
      ],
  });
}

async function agentNotify(message, level = "info") {
  try {
    toolCallContext.getStore()?.progressHeartbeat?.setPhase(String(message));
  } catch {
    // Progress transport support is optional.
  }
  try {
    await server.notification({
      method: "notifications/message",
      params: { level, logger: "unreal-agent", data: String(message) }
    });
  } catch {
    // Client may not subscribe to logging notifications.
  }
}

async function enforceTaskAuth(args, options = {}) {
  if (!CONTROL_PLANE_TOOLS && !REQUIRE_TASK_AUTH_FOR_WRITES) {
    return null;
  }
  const requireSession = Boolean(options.requireSession);
  const taskAuthorization = args?.taskAuthorization && typeof args.taskAuthorization === "object"
    ? args.taskAuthorization
    : (args?.task_authorization && typeof args.task_authorization === "object" ? args.task_authorization : {});
  const taskSessionId = String(args?.taskSessionId || args?.task_session_id || taskAuthorization.taskSessionId || taskAuthorization.task_session_id || "").trim();
  if (requireSession && !taskSessionId) {
    return fail("taskAuthorization is required for project write tools.", {
      errorCode: "TASK_SESSION_REQUIRED",
      retryable: true,
      stopCurrentWorkflow: false,
      doNotCreateDuplicatePlan: true,
      nextSteps: [
        "If unreal_agent_plan already returned taskAuthorization in this chat, copy that complete object unchanged and retry this write once.",
        "Only when no plan exists yet, call unreal_agent_plan once with the original user request.",
      ],
      agentInstruction: "Reuse the existing unreal_agent_plan.taskAuthorization when available. Do not create another plan merely to refresh or recover omitted authorization.",
    });
  }
  if (!taskSessionId) {
    return null;
  }
  const auth = validateMutationAuth(
    WORKSPACE_ROOT,
    args || {},
    {
      requireAll: true,
      toolName: String(options.toolName || ""),
      activeProject: getActiveProject(CONFIG_PATH),
      // Budget is committed only after path/approval validation, right before execute.
      consumeBudget: options.consumeBudget === true,
    }
  );
  if (!auth.ok) {
    const terminalRollback = await rollbackPendingForTerminalBlock(auth, args);
    const routeStale = auth.errorCode === "TASK_ROUTE_STALE";
    const authMismatch = auth.errorCode === "TASK_AUTH_MISMATCH";
    const incomplete = auth.errorCode === "TASK_AUTH_INCOMPLETE";
    const invalidFormat = auth.errorCode === "TASK_AUTH_INVALID_FORMAT";
    const missingState = auth.errorCode === "TASK_STATE_MISSING";
    const toolInactive = auth.errorCode === "TASK_TOOL_NOT_ACTIVE";
    const budgetExhausted = auth.errorCode === "TASK_PHASE_TOOL_BUDGET_EXHAUSTED";
    const routeRedirect = REDIRECT_CODES.has(auth.errorCode);
    const recoveryActionRequired = (
      authMismatch
      || invalidFormat
      || missingState
      || toolInactive
      || budgetExhausted
      || routeRedirect
    );
    const canContinueWorkflow = incomplete || routeStale || recoveryActionRequired;
    const recoveryNextSteps = [];
    if (invalidFormat || missingState) {
      recoveryNextSteps.push(
        "Do not invent or repair taskAuthorization fields. Call unreal_agent_plan once with the original user request, then copy its server-issued taskAuthorization unchanged."
      );
    } else if (toolInactive) {
      recoveryNextSteps.push(
        `Do not retry ${String(options.toolName || "the write tool")}. Call ${String(auth.nextAction || "the active pending gate")} with the returned taskAuthorization.`
      );
    } else if (budgetExhausted) {
      recoveryNextSteps.push(
        "Do not retry the budgeted tool. Call unreal_task_checkpoint with nextActionArgs exactly as returned (action=record); action=status does not renew the budget. Then follow requiredNextAction."
      );
    } else if (routeRedirect) {
      recoveryNextSteps.push(
        `Do not retry ${String(options.toolName || "the mutation tool")}. Call ${String(auth.nextAction || "unreal_code_sketch_claim_validate")} with the returned taskAuthorization to bind a bounded target slice, then continue.`
      );
    }
    return fail(auth.error || "Task authorization failed.", {
      ...(terminalRollback ? { mutationRollback: terminalRollback } : {}),
      taskSessionId: auth.taskSessionId,
      errorCode: auth.errorCode || "TASK_AUTH_FAILED",
      retryable: incomplete || routeStale || routeRedirect,
      stopCurrentWorkflow: !canContinueWorkflow,
      recoveryActionRequired,
      taskAuthorizationSource: "server_only",
      doNotFabricateTaskAuthorization: true,
      authorizationRefreshRequired: routeStale || authMismatch,
      ...(recoveryNextSteps.length ? { nextSteps: recoveryNextSteps } : {}),
      ...(auth.taskAuthorization ? { taskAuthorization: auth.taskAuthorization } : {}),
      ...(Number.isInteger(Number(auth.controlEpoch))
        ? { controlEpoch: Math.max(0, Number(auth.controlEpoch)) }
        : {}),
      ...(auth.toolRoute && typeof auth.toolRoute === "object"
        ? { toolRoute: auth.toolRoute }
        : {}),
      // Authorization is a projection boundary, not a semantic reducer. If
      // task state already committed v2 control, carry it through unchanged.
      ...(auth.control && typeof auth.control === "object"
        ? { control: { ...auth.control } }
        : {}),
      ...(auth.nextAction ? { nextAction: auth.nextAction } : {}),
      ...(auth.nextActionIsTool !== undefined
        ? { nextActionIsTool: Boolean(auth.nextActionIsTool) }
        : {}),
      ...(auth.nextActionArgs && typeof auth.nextActionArgs === "object"
        ? { nextActionArgs: auth.nextActionArgs }
        : {}),
      ...(incomplete ? {
        doNotCall: ["unreal_agent_plan"],
        agentInstruction: "Do not create another plan. Retry this write once with the complete taskAuthorization object returned by the latest gateCompletion or tool error.",
      } : {}),
      ...(routeStale ? {
        doNotCall: ["unreal_agent_plan"],
        agentInstruction: "Do not replan. Retry the same write tool once with taskAuthorization from this error response.",
      } : {}),
      ...(authMismatch ? {
        doNotRetry: [String(options.toolName || "write_tool")],
        nextAction: "unreal_agent_plan",
        nextActionIsTool: true,
        suggestedToolCalls: [{ tool: "unreal_agent_plan", args: { request: "<original user request>" } }],
        agentInstruction: "Plan identity changed and this error intentionally does not expose a live authToken. Do not copy its incomplete taskAuthorization or retry the write. Call unreal_agent_plan once with the original request; the active task will replan or return the current server-owned route.",
      } : {}),
      ...(invalidFormat || missingState ? {
        doNotRetry: [String(options.toolName || "write_tool")],
        nextAction: "unreal_agent_plan",
        nextActionIsTool: true,
        suggestedToolCalls: [{ tool: "unreal_agent_plan", args: { request: "<original user request>" } }],
        agentInstruction: "The supplied taskAuthorization was not server-issued or no longer exists. Never fabricate authorization. Call unreal_agent_plan once, then continue with the returned route.",
      } : {}),
      ...(toolInactive ? {
        doNotRetry: [String(options.toolName || "write_tool")],
        agentInstruction: `The write tool is not active in this phase. Call ${String(auth.nextAction || "the pending gate")} and continue; do not present manual paste-ready code.`,
      } : {}),
      ...(budgetExhausted ? {
        doNotRetry: [String(options.toolName || "work_tool")],
        suggestedToolCalls: auth.nextActionArgs && typeof auth.nextActionArgs === "object"
          ? [{ tool: "unreal_task_checkpoint", args: auth.nextActionArgs }]
          : [],
        agentInstruction: "The phase budget requires a recorded checkpoint. Call unreal_task_checkpoint with nextActionArgs exactly as returned (action=record); do not use action=status, stop, or fall back to manual code.",
      } : {}),
      ...(routeRedirect ? {
        doNotRetry: [String(options.toolName || "mutation_tool")],
        agentInstruction: `The mutation target is outside the active bounded slice. Call ${String(auth.nextAction || "unreal_code_sketch_claim_validate")} with concrete code and the returned taskAuthorization, then continue the same task. Do not stop, cancel, recover, or replan.`,
      } : {}),
    });
  }
  // Success must return null so write_file/replace_in_file proceed (not return the auth object).
  return null;
}

function pendingJournalsForWorkflowRollback(taskSessionId, projectPath) {
  if (!projectPath) return { projectRoot: "", journals: [] };
  const projectRoot = projectPath ? path.dirname(path.resolve(projectPath)) : "";
  const modern = projectRoot
    ? pendingBuildJournals({ taskSessionId, projectRoot })
    : [];
  const legacy = listPendingJournals({ taskSessionId, projectPath });
  const journals = [...modern, ...legacy].filter((journal, index, rows) => (
    rows.findIndex((item) => item.transactionId === journal.transactionId) === index
  ));
  journals.sort((left, right) => String(right.createdAt || "").localeCompare(String(left.createdAt || "")));
  return { projectRoot, journals };
}

async function rollbackPendingForTerminalBlock(result = {}, args = {}) {
  if (String(result.errorCode || "") !== "TASK_AUTONOMY_BLOCKED") return null;
  const fields = requiredFields(args || {});
  const taskSessionId = String(result.taskSessionId || fields.taskSessionId || "");
  const projectPath = String(getActiveProject(CONFIG_PATH) || "");
  const { projectRoot, journals } = pendingJournalsForWorkflowRollback(taskSessionId, projectPath);
  if (!journals.length) return null;
  const rollback = await rollbackMutationJournals(
    journals,
    projectRoot,
    "TASK_AUTONOMY_BLOCKED",
    args
  );
  return {
    ok: rollback.rolledBack,
    status: rollback.rolledBack ? "ROLLED_BACK_BLOCKED" : "ROLLBACK_INCOMPLETE_BLOCKED",
    taskSessionId,
    reconciliation: rollback.reconciliation,
    transactions: rollback.transactions,
  };
}

const BUILD_PROOF_EXTENSIONS = new Set([
  ".h", ".hpp", ".hh", ".inl", ".cpp", ".c", ".cc", ".cxx", ".cs",
  ".uplugin", ".uproject", ".ini",
]);

function requiresBuildTransaction(targetPath, projectPath) {
  if (!targetPath || !projectPath) return false;
  const root = path.dirname(path.resolve(projectPath));
  const absolute = path.resolve(targetPath);
  const relative = path.relative(root, absolute);
  return Boolean(
    relative
    && !relative.startsWith("..")
    && !path.isAbsolute(relative)
    && BUILD_PROOF_EXTENSIONS.has(path.extname(absolute).toLowerCase())
  );
}

async function rollbackPendingForWorkflowStop(args = {}, reason = "WORKFLOW_STOPPED") {
  const fields = requiredFields(args || {});
  const taskSessionId = String(fields.taskSessionId || "");
  const projectPath = String(getActiveProject(CONFIG_PATH) || "");
  const { projectRoot, journals } = pendingJournalsForWorkflowRollback(taskSessionId, projectPath);
  if (!journals.length) return null;
  const rollback = await rollbackMutationJournals(journals, projectRoot, reason, args);
  return {
    ok: rollback.rolledBack,
    status: rollback.rolledBack ? "ROLLED_BACK_BLOCKED" : "ROLLBACK_INCOMPLETE_BLOCKED",
    reason,
    taskSessionId,
    reconciliation: rollback.reconciliation,
    transactions: rollback.transactions,
  };
}

function validationToolResult(summary, validation, options = {}) {
  const base = options.ok === false
    ? {
      summary,
      ok: false,
      path: options.path || null,
      operation: options.operation || null,
      replacements: options.replacements ?? null,
      rolledBack: options.rolledBack ?? null,
      rollbackIncomplete: options.rollbackIncomplete ?? null,
      restoredPaths: options.restoredPaths ?? null,
      unrestoredPaths: options.unrestoredPaths ?? null,
      externalChangeDetected: options.externalChangeDetected ?? null,
      rollbackErrors: options.rollbackErrors ?? null,
      conflict: options.conflict ?? null,
      error: options.error || null,
      validation: compactValidationPayload(validation),
      nextSteps: options.nextSteps || [],
    }
    : slimWriteSuccessPayload(summary, validation, options);
  const passthrough = [
    "mutationGeneration", "validatedGeneration", "validationStale", "proofLevel",
    "validationPassed", "validationStatus", "validationBlockingErrorCount",
    "commandSucceeded", "proofSatisfied", "recoveryRequired", "errorCode",
    "retryable", "doNotRetry", "stopCurrentWorkflow", "suggestedToolCalls",
    "validationOverrideAvailable", "buildAllowedForValidatedGeneration", "requiredNextTool",
    "requiredNextToolArgs", "validationScope", "transactionId",
    "failedCallFingerprint", "forbiddenCallFingerprints", "forbiddenCalls",
    "continuityCheckpoint", "taskAuthorization", "toolRoute", "controlEpoch", "control",
  ];
  for (const key of passthrough) {
    if (options[key] !== undefined) base[key] = options[key];
  }
  if (options.continuityCheckpoint && typeof options.continuityCheckpoint === "object") {
    if (options.continuityCheckpoint.taskAuthorization) {
      base.taskAuthorization = options.continuityCheckpoint.taskAuthorization;
    }
    if (options.continuityCheckpoint.toolRoute) {
      base.toolRoute = options.continuityCheckpoint.toolRoute;
    }
    if (Number.isInteger(Number(options.continuityCheckpoint.controlEpoch))) {
      base.controlEpoch = Math.max(0, Number(options.continuityCheckpoint.controlEpoch));
    }
    if (options.continuityCheckpoint.control) {
      base.control = options.continuityCheckpoint.control;
    }
  }
  const result = text(JSON.stringify(base, null, 2));
  if (options.isError) result.isError = true;
  return result;
}

function bindAuthoritativeLifecycleControl(payload, lifecycleResult) {
  if (!payload || typeof payload !== "object" || !lifecycleResult || typeof lifecycleResult !== "object") {
    return payload;
  }
  for (const key of ["taskAuthorization", "toolRoute", "controlEpoch", "control"]) {
    if (lifecycleResult[key] !== undefined) payload[key] = lifecycleResult[key];
  }
  const required = lifecycleResult.control?.requiredTool;
  if (required && typeof required === "object" && String(required.name || "").trim()) {
    payload.requiredNextTool = String(required.name);
    payload.requiredNextToolArgs = required.args && typeof required.args === "object"
      ? { ...required.args }
      : {};
    payload.nextAction = payload.requiredNextTool;
    payload.nextActionArgs = { ...payload.requiredNextToolArgs };
    payload.nextActionIsTool = true;
  } else if (lifecycleResult.control?.authoritative === true) {
    delete payload.requiredNextTool;
    delete payload.requiredNextToolArgs;
    if (
      String(lifecycleResult.control?.phase || "").toLowerCase() === "synthesis"
      && String(lifecycleResult.control?.disposition || "").toLowerCase() === "continue"
    ) {
      payload.nextAction = "synthesize_current_evidence";
      payload.nextActionArgs = {};
      payload.nextActionIsTool = false;
    } else if (String(lifecycleResult.control?.disposition || "").toLowerCase() === "await_user") {
      payload.nextAction = "await_user";
      payload.nextActionArgs = lifecycleResult.control.requiredUserInput || {};
      payload.nextActionIsTool = false;
    } else {
      delete payload.nextAction;
      delete payload.nextActionArgs;
      delete payload.nextActionIsTool;
    }
  }
  return payload;
}

function bindCommittedMutationControl(payload, committedBudget, checkpoint = {}) {
  const state = committedBudget?.state && typeof committedBudget.state === "object"
    ? committedBudget.state
    : null;
  if (!state) return payload;
  return bindAuthoritativeLifecycleControl(payload, {
    taskAuthorization: checkpoint?.taskAuthorization || taskAuthorizationForState(state),
    toolRoute: state.toolRoute && typeof state.toolRoute === "object" ? state.toolRoute : {},
    controlEpoch: Math.max(0, Number(state.controlEpoch || 0)),
    control: state.controlState && typeof state.controlState === "object" ? state.controlState : {},
  });
}

function taskProofLifecycle(state) {
  if (!state || typeof state !== "object") return null;
  return {
    taskAuthorization: taskAuthorizationForState(state),
    toolRoute: state.toolRoute && typeof state.toolRoute === "object"
      ? state.toolRoute
      : undefined,
    controlEpoch: Number.isInteger(Number(state.controlEpoch))
      ? Math.max(0, Number(state.controlEpoch))
      : undefined,
    control: state.controlState && typeof state.controlState === "object"
      ? state.controlState
      : undefined,
  };
}

function validateTaskProofProject(args, resolvedProject) {
  const taskSessionId = requiredFields(args).taskSessionId;
  if (!taskSessionId) return { ok: true, active: false, state: null, lifecycle: null };
  const state = readTaskState(WORKSPACE_ROOT, taskSessionId);
  if (!state) {
    return {
      ok: false,
      active: true,
      errorCode: "TASK_STATE_MISSING",
      error: "Task-bound Unreal proof could not load its authoritative task state.",
      state: null,
      lifecycle: null,
    };
  }
  const validation = validateResolvedTaskProject(
    state,
    WORKSPACE_ROOT,
    resolvedProject,
    { requireBoundProject: true }
  );
  return {
    ...validation,
    active: true,
    state,
    lifecycle: taskProofLifecycle(state),
  };
}

function taskProofFailure(binding, fallbackMessage) {
  const payload = {
    errorCode: String(binding?.errorCode || "TASK_PROJECT_PROOF_MISMATCH"),
    retryable: true,
    expectedProject: String(binding?.expectedProject || ""),
    observedProject: String(binding?.observedProject || ""),
  };
  bindAuthoritativeLifecycleControl(payload, binding?.lifecycle);
  return fail(String(binding?.error || fallbackMessage || "Task proof scope mismatch."), payload);
}

function taskRequiredToolArgs(state) {
  const required = state?.controlState?.requiredTool;
  return required && typeof required === "object" && required.args && typeof required.args === "object"
    ? required.args
    : {};
}

function taskEngineProofMismatch(binding, observedEngineRoot, expectedEngineRoot, errorCode) {
  const payload = {
    errorCode: String(errorCode || "TASK_ENGINE_PROOF_MISMATCH"),
    retryable: true,
    expectedEngineRoot: String(expectedEngineRoot || ""),
    observedEngineRoot: String(observedEngineRoot || ""),
  };
  bindAuthoritativeLifecycleControl(payload, binding?.lifecycle);
  return fail("Build/Automation engine does not match the authoritative task proof scope.", payload);
}

function normalizeForToken(value) {
  return String(value || "").replace(/\r\n/g, "\n").trim();
}

function requireDeletionText(value, fieldName) {
  const textValue = normalizeForToken(value);
  if (textValue.length < 12) {
    throw new Error(`${fieldName} must be a concrete sentence of at least 12 characters.`);
  }
  return textValue;
}

function deletionApprovalToken({ relPath, completedEditsSummary, reason, ifNotDeleted, ifDeleted }) {
  const payload = JSON.stringify({
    relPath: normalizeForToken(relPath).replace(/\\/g, "/"),
    completedEditsSummary: normalizeForToken(completedEditsSummary),
    reason: normalizeForToken(reason),
    ifNotDeleted: normalizeForToken(ifNotDeleted),
    ifDeleted: normalizeForToken(ifDeleted),
  });
  return crypto.createHash("sha256").update(payload).digest("hex").slice(0, 24);
}

async function buildDeletionProposal(rawFiles, completedEditsSummary, activeProject) {
  const summary = requireDeletionText(completedEditsSummary, "completedEditsSummary");
  if (!Array.isArray(rawFiles) || rawFiles.length === 0) {
    throw new Error("files must contain at least one deletion candidate.");
  }
  if (rawFiles.length > 20) {
    throw new Error("files may contain at most 20 deletion candidates.");
  }

  const seen = new Set();
  const files = [];
  for (const item of rawFiles) {
    const resolution = await resolveReadToolPath(String(item && item.path || ""));
    const target = resolution.absolutePath;
    const guard = isDeleteAllowedPath(target, WORKSPACE_ROOT, activeProject);
    if (!guard.ok) {
      throw new Error(guard.message);
    }
    const delStat = await statSafe(target);
    if (!delStat || !delStat.isFile()) {
      throw new Error(`not found or not file: ${item && item.path}`);
    }
    const relPath = displayPath(resolution);
    const relKey = deletionCandidateIdentity(relPath);
    if (seen.has(relKey)) {
      throw new Error(`duplicate deletion candidate: ${relPath}`);
    }
    seen.add(relKey);

    const reason = requireDeletionText(item.reason, `reason for ${relPath}`);
    const ifNotDeleted = requireDeletionText(item.ifNotDeleted, `ifNotDeleted for ${relPath}`);
    const ifDeleted = requireDeletionText(item.ifDeleted, `ifDeleted for ${relPath}`);
    files.push({
      path: relPath,
      fileName: path.basename(target),
      sizeBytes: delStat.size,
      reason,
      ifNotDeleted,
      ifDeleted,
      approvalToken: deletionApprovalToken({
        relPath,
        completedEditsSummary: summary,
        reason,
        ifNotDeleted,
        ifDeleted,
      }),
    });
  }

  return {
    fileCount: files.length,
    completedEditsSummary: summary,
    files,
    deleted: false,
    instruction: "No files were deleted. Explain this plan to the user and wait for explicit approval before calling delete_file with the matching per-file approvalToken.",
  };
}

async function resolveReadToolPath(p) {
  return resolveReadPath(p, {
    workspaceRoot: WORKSPACE_ROOT,
    activeProject: getActiveProject(CONFIG_PATH)
  });
}

async function resolveWriteToolPath(p) {
  const resolution = await resolveReadToolPath(p);
  if (resolution.resolvedRootType !== "active_project") {
    const rel = path.relative(WORKSPACE_ROOT, resolution.absolutePath).replace(/\\/g, "/");
    if (!rel.startsWith(".agent/")) {
      throw new Error(`write blocked outside active project and .agent/: ${p}`);
    }
    return resolution;
  }
  const rel = String(resolution.projectRelativePath || "").replace(/\\/g, "/");
  const allowed = rel.startsWith("Source/")
    || rel.startsWith("Config/")
    || /^Plugins\/[^/]+\/(?:Source\/|[^/]+\.uplugin$)/i.test(rel);
  if (!allowed) {
    throw new Error(`project write blocked outside Source/Config/Plugins source: ${p}`);
  }
  return resolution;
}

function normalizeRelPath(p) {
  if (!p || typeof p !== "string") {
    throw new Error("path must be a non-empty string");
  }
  const workspace = path.resolve(WORKSPACE_ROOT);
  const resolved = path.resolve(workspace, p);

  // Primary check: resolved path must start with workspace + separator (or equal workspace).
  // This is more robust than relative().startsWith("..") on Windows with symlinks.
  if (resolved !== workspace && !resolved.startsWith(workspace + path.sep)) {
    throw new Error(`path escapes WORKSPACE_ROOT: ${p}`);
  }

  return resolved;
}

async function exists(p) {
  try {
    await fsp.access(p);
    return true;
  } catch {
    return false;
  }
}

async function statSafe(p) {
  try {
    return await fsp.stat(p);
  } catch {
    return null;
  }
}

function isPatchOnlyExistingFile(p) {
  return isPatchOnlyFile(p);
}

function validationFailed(validation) {
  return Boolean(validation && validation.ok === false);
}

function durableGuardScopeForArgs(args = {}, overrides = {}) {
  const stateRoot = ensureStateRootLayout(resolveAgentStateRoot());
  const taskSessionId = String(
    overrides.taskSessionId || requiredFields(args || {}).taskSessionId || ""
  ).trim();
  if (!taskSessionId) return { stateRoot };
  let taskState = null;
  try {
    taskState = readTaskState(WORKSPACE_ROOT, taskSessionId, stateRoot);
  } catch {
    taskState = null;
  }
  let projectRoot = String(overrides.projectRoot || "").trim();
  if (!projectRoot && taskState) {
    const projectFile = authoritativeTaskProjectFile(taskState, WORKSPACE_ROOT);
    if (projectFile) projectRoot = path.dirname(projectFile);
  }
  const requestedGeneration = Number(overrides.mutationGeneration);
  const stateGeneration = Number(taskState?.mutationGeneration);
  const mutationGeneration = Number.isFinite(requestedGeneration)
    ? Math.max(0, Math.floor(requestedGeneration))
    : (Number.isFinite(stateGeneration) ? Math.max(0, Math.floor(stateGeneration)) : null);
  if (!projectRoot || mutationGeneration === null) {
    return { taskSessionId, stateRoot };
  }
  return {
    taskSessionId,
    projectRoot,
    mutationGeneration,
    stateRoot,
  };
}

function exactCheckpointRebaseTool() {
  return {
    name: "unreal_task_checkpoint",
    args: {
      action: "rebase",
      acceptCurrentFiles: true,
      includeGitChanges: false,
    },
  };
}

function taskStateForToolArgs(args = {}) {
  const taskSessionId = requiredFields(args || {}).taskSessionId;
  if (!taskSessionId) return null;
  try {
    return readTaskState(WORKSPACE_ROOT, taskSessionId);
  } catch {
    return null;
  }
}

function authoritativeBuildRecoveryArgs(taskState, invocationArgs = {}) {
  const contract = taskState?.buildContract && typeof taskState.buildContract === "object"
    ? taskState.buildContract
    : {};
  const source = Object.keys(contract).length ? contract : invocationArgs;
  const result = {
    project: String(source.project || source.projectFile || taskState?.projectFile || "").trim(),
    engineRoot: String(source.engineRoot || "").trim(),
    target: String(source.target || "").trim(),
    platform: String(source.platform || "").trim(),
    configuration: String(source.configuration || "").trim(),
    allowAbsoluteProject: true,
    allowEngineFallback: false,
  };
  return Object.values(result).every((value) => value !== "") ? result : null;
}

function authoritativeAutomationRecoveryArgs(taskState, invocationArgs = {}) {
  const verification = taskState?.buildVerification
    && typeof taskState.buildVerification === "object"
    ? taskState.buildVerification
    : {};
  const filters = Array.isArray(verification.testFilters) && verification.testFilters.length
    ? verification.testFilters.map(String).map((value) => value.trim()).filter(Boolean)
    : (Array.isArray(invocationArgs.testFilters)
      ? invocationArgs.testFilters.map(String).map((value) => value.trim()).filter(Boolean)
      : [String(invocationArgs.testFilter || "").trim()].filter(Boolean));
  if (!filters.length) return null;
  const project = String(
    verification.projectFile || invocationArgs.project || taskState?.projectFile || ""
  ).trim();
  const engineRoot = String(verification.engineRoot || invocationArgs.engineRoot || "").trim();
  return {
    testFilters: filters.slice(0, MAX_AUTOMATION_FILTERS),
    ...(project ? { project } : {}),
    ...(engineRoot ? { engineRoot } : {}),
  };
}

function recoveryGateAfterMissingDiagnostic(taskState, priorRecovery, invocationArgs = {}) {
  const source = recoveryLogSource(priorRecovery, priorRecovery?.requiredTool?.args?.fileName);
  if (source === "automation") {
    const automationArgs = authoritativeAutomationRecoveryArgs(taskState, invocationArgs);
    if (automationArgs) {
      return {
        status: "environment_recovery",
        requiredTool: { name: "run_unreal_automation_tests", args: automationArgs },
      };
    }
  } else {
    const buildArgs = authoritativeBuildRecoveryArgs(taskState, invocationArgs);
    if (buildArgs) {
      return {
        status: "environment_recovery",
        requiredTool: { name: "build_unreal_project", args: buildArgs },
      };
    }
  }
  return {
    status: "checkpoint_rebase_required",
    requiredTool: exactCheckpointRebaseTool(),
  };
}

function boundedAutomationRetryGate(args = {}, taskState = taskStateForToolArgs(args)) {
  const automationArgs = authoritativeAutomationRecoveryArgs(taskState, args);
  return automationArgs
    ? {
      status: "environment_recovery",
      requiredTool: { name: "run_unreal_automation_tests", args: automationArgs },
    }
    : {
      status: "checkpoint_rebase_required",
      requiredTool: exactCheckpointRebaseTool(),
    };
}

function boundedBuildRetryGate(args = {}, taskState = taskStateForToolArgs(args)) {
  const buildArgs = authoritativeBuildRecoveryArgs(taskState, args);
  return buildArgs
    ? {
      status: "environment_recovery",
      requiredTool: { name: "build_unreal_project", args: buildArgs },
    }
    : {
      status: "checkpoint_rebase_required",
      requiredTool: exactCheckpointRebaseTool(),
    };
}

function exposureProfileName() {
  return MCP_EXTENDED_TOOLS ? "extended" : "essential";
}

function filterAgentTools(tools, context = null) {
  const allowed = callableAgentToolNames(tools.map((tool) => tool.name));
  const visible = phaseVisibleAgentToolNames(allowed, context || {});
  return tools.filter((tool) => visible.has(tool.name)).map((tool) => {
    if (context?.status !== "none" || !UNROUTED_INSPECTION_TOOLS.has(tool.name)) {
      return tool;
    }
    const schema = tool.inputSchema && typeof tool.inputSchema === "object"
      ? tool.inputSchema
      : null;
    if (!schema?.properties?.taskAuthorization) return tool;
    const properties = { ...schema.properties };
    delete properties.taskAuthorization;
    return { ...tool, inputSchema: { ...schema, properties } };
  });
}

function buildToolCatalogDiagnostics(tools, context = null) {
  const registered = Array.isArray(tools) ? tools : [];
  const resolved = context || listToolsRouteContext(
    WORKSPACE_ROOT,
    getActiveProject(CONFIG_PATH) || ""
  );
  const advertised = filterAgentTools(registered, resolved);
  return {
    profile: exposureProfileName(),
    registeredCount: registered.length,
    advertisedCount: advertised.length,
    routeContextStatus: String(resolved.status || "none"),
    routeErrorCode: String(resolved.errorCode || ""),
    stateRoot: ensureStateRootLayout(resolveAgentStateRoot()),
    identity: getMcpIdentityStatus(),
  };
}

let catalogInitializedDiagnosticEmitted = false;
async function emitCatalogInitializedDiagnostic(context = null) {
  if (catalogInitializedDiagnosticEmitted) return;
  catalogInitializedDiagnosticEmitted = true;
  const tools = allAgentTools();
  const catalog = buildToolCatalogDiagnostics(tools, context);
  await agentNotify(JSON.stringify({
    event: "mcp_catalog_initialized",
    server: "unreal-agent",
    profile: catalog.profile,
    registeredToolCount: catalog.registeredCount,
    advertisedToolCount: catalog.advertisedCount,
    routeContextStatus: catalog.routeContextStatus,
    routeErrorCode: catalog.routeErrorCode,
    stateRoot: catalog.stateRoot,
    activeProject: getActiveProject(CONFIG_PATH) || "",
    mcpIdentity: catalog.identity,
    runtimeComponent: runtimeComponentStatus?.running || null,
    bundleIntegrityVerified: runtimeComponentStatus?.bundleIntegrityVerified === true,
    installedGitCommit: runtimeComponentStatus?.installedGitCommit || "",
    expectedGitCommit: runtimeComponentStatus?.expectedGitCommit || "",
    sourceHeadMatched: runtimeComponentStatus?.sourceHeadMatched ?? null,
    runtimeStale: runtimeComponentStatus?.runtimeStale === true,
    runtimeVerified: runtimeComponentStatus?.runtimeVerified === true,
  }), "info");
}
function requiredArgumentCheck(tool, args) {
  const required = Array.isArray(tool?.inputSchema?.required) ? tool.inputSchema.required : [];
  if (!args || typeof args !== "object" || Array.isArray(args)) {
    return { required, missing: required, invalidShape: true, provided: [] };
  }
  const missing = required.filter((key) => {
    const value = args[key];
    return !(key in args) || value == null || (typeof value === "string" && !value.trim());
  });
  return { required, missing, invalidShape: false, provided: Object.keys(args).sort() };
}


function truncateOutput(s, maxBytes = MAX_OUTPUT_BYTES) {
  const buf = Buffer.from(String(s), "utf8");
  if (buf.length <= maxBytes) return String(s);
  return buf.subarray(0, maxBytes).toString("utf8") + `\n\n[TRUNCATED: output exceeded ${maxBytes} bytes]`;
}

function isTextLikely(buffer) {
  if (!buffer || buffer.length === 0) return true;
  const sample = buffer.subarray(0, Math.min(buffer.length, 4096));
  let zeros = 0;
  for (const b of sample) {
    if (b === 0) zeros++;
  }
  return zeros === 0;
}

function execCommand(commandLine, cwd = WORKSPACE_ROOT, timeoutMs = COMMAND_TIMEOUT_MS) {
  const parsed = parseAllowedCommand(commandLine);
  if (!parsed) {
    return Promise.resolve({
      ok: false,
      exitCode: 1,
      signal: null,
      stdout: "",
      stderr: "",
      error: `command not allowlisted or blocked: ${commandLine}`,
      timedOut: false,
      processTreeKilled: false,
      fullLogPath: null,
    });
  }

  return new Promise((resolve) => {
    const logPath = path.join(os.tmpdir(), `unreal-agent-cmd-${process.pid}-${Date.now()}.log`);
    let logStream;
    try {
      logStream = fs.createWriteStream(logPath, { flags: "a" });
    } catch {
      logStream = null;
    }

    const child = cp.spawn(parsed.file, parsed.args, {
      cwd,
      shell: parsed.shell === true,
      windowsHide: true,
    });
    let stdout = "";
    let stderr = "";
    let timedOut = false;
    let killIssued = false;
    const timer = setTimeout(() => {
      timedOut = true;
      killIssued = true;
      if (process.platform === "win32" && child.pid) {
        cp.exec(`taskkill /PID ${child.pid} /T /F`, { windowsHide: true }, () => {});
      } else {
        child.kill("SIGKILL");
      }
    }, timeoutMs);
    child.stdout?.on("data", (chunk) => {
      const textChunk = String(chunk || "");
      stdout += textChunk;
      logStream?.write(textChunk);
    });
    child.stderr?.on("data", (chunk) => {
      const textChunk = String(chunk || "");
      stderr += textChunk;
      logStream?.write(textChunk);
    });
    child.on("close", (code, signal) => {
      clearTimeout(timer);
      logStream?.end();
      resolve({
        ok: !timedOut && code === 0,
        exitCode: typeof code === "number" ? code : 1,
        signal: signal || null,
        stdout: truncateOutput(stdout || ""),
        stderr: truncateOutput(stderr || ""),
        error: timedOut ? `Process timed out after ${timeoutMs}ms` : "",
        timedOut,
        processTreeKilled: timedOut ? killIssued : null,
        fullLogPath: logStream ? logPath : null,
      });
    });
    child.on("error", (error) => {
      clearTimeout(timer);
      logStream?.end();
      resolve({
        ok: false,
        exitCode: 1,
        signal: null,
        stdout: truncateOutput(stdout || ""),
        stderr: truncateOutput(stderr || ""),
        error: String(error.message || error),
        timedOut: false,
        processTreeKilled: false,
        fullLogPath: logStream ? logPath : null,
      });
    });
  });
}

function makeJsonSchema(properties, required = []) {
  return {
    type: "object",
    properties,
    required,
    additionalProperties: false
  };
}
function taskAuthSchemaProperties() {
  return {
    taskAuthorization: {
      type: "object",
      description: "Stable task ownership handle. The server resolves every rotating route field from current task state.",
      properties: {
        taskSessionId: { type: "string" },
        ownerCapability: {
          type: "string",
          description: "Secret ownership token from task_start. Not a conversationId.",
        },
      },
      required: [
        "taskSessionId",
        "ownerCapability",
      ],
      additionalProperties: false,
    },
  };
}

function routeOwnershipFromArgs(args = {}) {
  const auth = args.taskAuthorization && typeof args.taskAuthorization === "object"
    ? args.taskAuthorization
    : args.task_authorization && typeof args.task_authorization === "object"
      ? args.task_authorization
      : {};
  return {
    ownerCapability: String(
      auth.ownerCapability
      || auth.owner_capability
      || args.ownerCapability
      || args.owner_capability
      || ""
    ).trim(),
    conversationId: String(
      auth.conversationId
      || auth.conversation_id
      || args.conversationId
      || args.conversation_id
      || ""
    ).trim(),
    taskSessionId: String(
      auth.taskSessionId
      || auth.task_session_id
      || args.taskSessionId
      || args.task_session_id
      || ""
    ).trim(),
  };
}


function fileStatSignature(stat) {
  return `${stat.size}:${stat.mtimeMs}`;
}

function durableReadCoverageEntry(taskState, resolution) {
  const files = taskState?.sourceEvidence?.files;
  if (!files || typeof files !== "object") return null;
  const candidatePath = String(resolution?.projectRelativePath || "").replace(/\\/g, "/");
  const candidateKey = candidatePath ? filesystemPathIdentity(candidatePath) : "";
  for (const [rawKey, entry] of Object.entries(files)) {
    if (!entry || typeof entry !== "object") continue;
    const entryPath = String(entry.path || rawKey || "").replace(/\\/g, "/");
    if (candidateKey && filesystemPathIdentity(entryPath) === candidateKey) return entry;
  }
  return null;
}

function readContinuationForTask(taskState, resolution, args, durableEntry) {
  const currentPath = String(resolution?.projectRelativePath || args?.path || "")
    .replace(/\\/g, "/");
  const currentKey = currentPath ? filesystemPathIdentity(currentPath) : "";
  const entryIsCurrent = durableEntry && (
    !durableEntry.fileSignature
    || String(durableEntry.fileSignature) === String(resolution?.fileSignature || "")
  );
  if (entryIsCurrent && durableEntry?.wholeFileComplete !== true && durableEntry?.nextUnreadLine) {
    const startLine = Math.max(1, Number(durableEntry.nextUnreadLine || 1));
    const lineCount = Math.max(0, Number(durableEntry.lineCount || 0));
    return {
      requiredNextTool: "read_file_range",
      requiredNextToolArgs: {
        path: String(args?.path || resolution?.projectRelativePath || ""),
        startLine,
        endLine: lineCount >= startLine ? lineCount : startLine + 199,
      },
      reason: "truncated_read_continuation",
    };
  }
  const progress = taskState?.inspectionProgress && typeof taskState.inspectionProgress === "object"
    ? taskState.inspectionProgress
    : {};
  const frontier = Array.isArray(progress.remainingFrontier)
    ? progress.remainingFrontier.map((value) => String(value || "").replace(/\\/g, "/")).filter(Boolean)
    : [];
  const accepted = new Set(Object.values(taskState?.sourceEvidence?.files || {})
    .map((entry) => filesystemPathIdentity(String(entry?.path || "")))
    .filter(Boolean));
  const next = frontier.find((candidate) => {
    const key = filesystemPathIdentity(candidate);
    return key && key !== currentKey && !accepted.has(key);
  });
  return next
    ? {
      requiredNextTool: "read_file",
      requiredNextToolArgs: { path: next },
      reason: "durable_frontier_continuation",
    }
    : null;
}

async function resolveMutationGenerationForRead(resolution, targetPath) {
  try {
    const activeProject = resolution.activeProject || getActiveProject(CONFIG_PATH);
    if (!activeProject) return 0;
    const projectRoot = await resolveProjectRootForFile(targetPath, () => activeProject);
    if (!projectRoot) return 0;
    const state = await readMutationState(projectRoot);
    return Number(state.mutationGeneration || 0);
  } catch {
    return 0;
  }
}

function buildReadEvidenceContext(target, stat, resolution, options = {}) {
  const taskSessionId = String(options.taskSessionId || "");
  const taskState = taskSessionId ? readTaskState(WORKSPACE_ROOT, taskSessionId) : null;
  const localEvidence = target ? readEvidence.get(path.resolve(target)) : null;
  const fileSignature = stat ? fileStatSignature(stat) : null;
  const durableEntry = durableReadCoverageEntry(taskState, resolution);
  const contentHash = String(
    options.contentHash
    || (localEvidence?.signature === fileSignature ? localEvidence.contentHash : "")
    || (durableEntry?.fileSignature === fileSignature ? durableEntry.contentHash : "")
    || ""
  ).trim().toLowerCase() || null;
  const resolved = {
    ...resolution,
    fileSignature,
  };
  return {
    fileAbsPath: target ? path.resolve(target) : null,
    fileSignature,
    contentHash,
    mutationGeneration: options.mutationGeneration ?? 0,
    scopeSignature: options.scopeSignature || null,
    evidenceHash: options.evidenceHash || null,
    taskSessionId,
    evidenceSessionId: String(options.evidenceSessionId || ""),
    taskAuthorization: options.taskAuthorization && typeof options.taskAuthorization === "object"
      ? options.taskAuthorization
      : null,
    detachedReadOnlyObservation: options.detachedReadOnlyObservation === true,
    activeProject: resolution?.activeProject || getActiveProject(CONFIG_PATH) || null,
    taskState,
    durableCoverage: durableEntry,
    coverageContinuation: readContinuationForTask(taskState, resolved, options, durableEntry),
  };
}

function summarizeCachedRead(content) {
  const source = String(content || "");
  const lines = source.split(/\r?\n/);
  const anchors = [];
  for (let index = 0; index < lines.length && anchors.length < 16; index += 1) {
    const line = lines[index].trim().replace(/^\d+\|/, "").trim().replace(/\s+/g, " ");
    if (!line || line.startsWith("//")) continue;
    if (
      /^U(?:CLASS|STRUCT|ENUM|INTERFACE|FUNCTION|PROPERTY)\b/.test(line)
      || /^(?:class|struct|enum(?:\s+class)?)\s+[A-Za-z_]/.test(line)
      || /^[A-Za-z_][\w:<>,*&\s]*::[~A-Za-z_]\w*\s*\(/.test(line)
      || /IMPLEMENT_[A-Z0-9_]*AUTOMATION_TEST|(?:BEGIN_)?DEFINE_SPEC|\bTEST(?:\s*\(|_CLASS(?:_WITH_(?:ASSERTS|BASE|FLAGS|BASE_AND_FLAGS))?\s*\()|Describe\s*\(|It\s*\(/.test(line)
      || /DOREPLIFETIME|HasAuthority\s*\(|_Implementation\s*\(|OnRep_|Server[A-Za-z_]*\s*\(/.test(line)
    ) {
      anchors.push(`L${index + 1}: ${line.slice(0, 220)}`);
    }
  }
  return {
    evidenceHash: crypto.createHash("sha256").update(source, "utf8").digest("hex"),
    cachedContentBytes: Buffer.byteLength(source, "utf8"),
    cachedLineCount: lines.length,
    semanticAnchors: anchors,
  };
}

function evidenceSessionSchemaProperty() {
  return {
    sessionId: {
      type: "string",
      description:
        "Optional conversation evidence scope. The context-compactor injects this automatically; omit it in ordinary model calls.",
    },
  };
}

function cachedReadSuccess(content, options = {}) {
  const source = String(content || "");
  const summary = summarizeCachedRead(source);
  const includeContent = options.includeContent === true && source.length <= 24_000;
  const defaultInstruction = cachedReadInstruction("READ_CACHE_HIT");
  const payload = {
    ok: true,
    resultKind: "cache_hit",
    cached: true,
    evidenceProgressed: false,
    workflowProgressed: false,
    evidenceStatus: "cached",
    repeatDetected: true,
    doNotRepeatRead: true,
    // Same-path cache hit must not abort multi-file investigations.
    stopCurrentWorkflow: options.stopCurrentWorkflow === true,
    retryable: false,
    phase: "evidence_cached",
    userMessage: options.userMessage || defaultInstruction,
    agentInstruction: options.agentInstruction || (
      options.includeContent === true && !includeContent
        ? `${defaultInstruction} The cached range is too large to resend; request one narrower exact range if edit text is missing.`
        : defaultInstruction
    ),
    contentSuppressed: !includeContent,
    ...summary,
    readAttempts: options.readAttempts || 2,
  };
  if (includeContent) payload.content = source;
  if (options.includeContent === true && !includeContent) payload.cachedContentTruncated = true;
  if (options.readCount != null) payload.readCount = options.readCount;
  if (options.fullyCovered) payload.fullyCovered = true;
  if (options.coveredBy) payload.coveredBy = options.coveredBy;
  if (options.coverage) payload.coverage = options.coverage;
  if (options.continuation && typeof options.continuation === "object") {
    const continuation = options.continuation;
    if (continuation.requiredNextTool) {
      payload.requiredNextTool = String(continuation.requiredNextTool);
      payload.requiredNextToolArgs = continuation.requiredNextToolArgs
        && typeof continuation.requiredNextToolArgs === "object"
        ? { ...continuation.requiredNextToolArgs }
        : {};
      payload.nextAction = payload.requiredNextTool;
      payload.nextActionArgs = { ...payload.requiredNextToolArgs };
      payload.nextActionIsTool = true;
    } else if (continuation.nextAction) {
      payload.nextAction = String(continuation.nextAction);
      payload.nextActionArgs = continuation.nextActionArgs || {};
      payload.nextActionIsTool = continuation.nextActionIsTool === true;
    }
  }
  const taskState = options.taskState && typeof options.taskState === "object"
    ? options.taskState
    : null;
  if (taskState) {
    payload.taskAuthorization = taskAuthorizationForState(taskState);
    payload.taskSessionId = String(taskState.taskSessionId || "");
    payload.controlEpoch = Math.max(0, Number(taskState.controlEpoch || 0));
    payload.toolRoute = taskState.toolRoute && typeof taskState.toolRoute === "object"
      ? taskState.toolRoute
      : undefined;
    payload.sourceEvidence = taskState.sourceEvidence;
    payload.inspectionProgress = taskState.inspectionProgress;
    payload.synthesisReadiness = taskState.synthesisReadiness;
    const currentControl = taskState.controlState && typeof taskState.controlState === "object"
      ? taskState.controlState
      : {};
    bindAuthoritativeLifecycleControl(payload, {
      taskAuthorization: payload.taskAuthorization,
      toolRoute: taskState.toolRoute,
      controlEpoch: Math.max(0, Number(currentControl.epoch ?? taskState.controlEpoch ?? 0)),
      control: currentControl,
    });
  }
  return text(JSON.stringify(payload, null, 2));
}

function readRepeatBlocked(tool, guard, context = {}) {
  const continuation = guard?.continuation
    || context.coverageContinuation
    || null;
  const payload = {
    ok: false,
    resultKind: "repeat_blocked",
    errorCode: "READ_REPEAT_BLOCKED",
    cached: false,
    evidenceProgressed: false,
    workflowProgressed: false,
    retryable: false,
    stopCurrentWorkflow: false,
    doNotRetry: [String(tool || "")],
    agentInstruction: cachedReadInstruction("READ_REPEAT_BLOCKED"),
    userMessage: cachedReadInstruction("READ_REPEAT_BLOCKED"),
    readAttempts: Number(guard?.attempts || 1),
  };
  if (guard?.coverage) payload.coverage = guard.coverage;
  if (continuation?.requiredNextTool) {
    payload.requiredNextTool = String(continuation.requiredNextTool);
    payload.requiredNextToolArgs = continuation.requiredNextToolArgs
      && typeof continuation.requiredNextToolArgs === "object"
      ? { ...continuation.requiredNextToolArgs }
      : {};
    payload.nextAction = payload.requiredNextTool;
    payload.nextActionArgs = { ...payload.requiredNextToolArgs };
    payload.nextActionIsTool = true;
    payload.nextSteps = [`Call ${payload.requiredNextTool} exactly once with requiredNextToolArgs.`];
  } else if (context.taskSessionId) {
    payload.requiredNextTool = "unreal_agent_plan";
    payload.requiredNextToolArgs = { request: "Continue the bounded source-evidence task from retained coverage." };
    payload.nextAction = payload.requiredNextTool;
    payload.nextActionArgs = { ...payload.requiredNextToolArgs };
    payload.nextActionIsTool = true;
    payload.nextSteps = ["Call unreal_agent_plan once to obtain the next bounded server-owned evidence action."];
  } else {
    payload.nextAction = "synthesize_current_evidence";
    payload.nextActionIsTool = false;
    payload.nextSteps = ["Use the retained evidence; no further read of this unchanged path is allowed."];
  }
  let taskState = context.taskState
    || (context.taskSessionId ? readTaskState(WORKSPACE_ROOT, context.taskSessionId) : null);
  let lifecycle = null;
  if (taskState && String(taskState.status || "").toLowerCase() === "running"
      && context.detachedReadOnlyObservation !== true
      && context.taskAuthorization && typeof context.taskAuthorization === "object") {
    const requiredTool = payload.nextActionIsTool
      ? {
        name: String(payload.requiredNextTool || ""),
        args: payload.requiredNextToolArgs && typeof payload.requiredNextToolArgs === "object"
          ? { ...payload.requiredNextToolArgs }
          : {},
      }
      : {};
    lifecycle = recordRecoveryObligationViaPython(
      WORKSPACE_ROOT,
      { taskAuthorization: taskAuthorizationForState(taskState) },
      {
        source: "evidence",
        status: payload.nextActionIsTool
          ? (requiredTool.name === "unreal_agent_plan"
            ? "phase_budget_replan_required"
            : "evidence_required")
          : "evidence_complete",
        scopeDisposition: "in_slice",
        errorCode: "READ_REPEAT_BLOCKED",
        mutationGeneration: Math.max(0, Number(
          taskState.mutationGeneration ?? context.mutationGeneration ?? 0
        )),
        requiredTool,
        targetFiles: evidenceStagnationTargetFiles(taskState, context),
        message: payload.nextActionIsTool
          ? `Semantic duplicate blocked; continue with the authoritative ${requiredTool.name} action exactly once.`
          : "Semantic duplicate blocked; use retained evidence without another read.",
      },
    );
    if (lifecycle?.ok !== true) {
      const failurePayload = {
        errorCode: String(lifecycle?.errorCode || "EVIDENCE_RECOVERY_RECORD_FAILED"),
        retryable: false,
        stopCurrentWorkflow: true,
        doNotRetry: ["read_file", "read_file_range", "read_symbol", "search_files"],
        agentInstruction: "Do not retry the evidence read. The authoritative task continuation could not be committed.",
      };
      bindAuthoritativeLifecycleControl(failurePayload, lifecycle || {});
      return fail(
        String(lifecycle?.error || "Could not commit the authoritative evidence continuation."),
        failurePayload,
      );
    }
    taskState = readTaskState(WORKSPACE_ROOT, String(taskState.taskSessionId || "")) || taskState;
  }
  if (taskState) {
    payload.taskAuthorization = taskAuthorizationForState(taskState);
    payload.toolRoute = taskState.toolRoute && typeof taskState.toolRoute === "object"
      ? taskState.toolRoute
      : undefined;
    payload.controlEpoch = Math.max(0, Number(taskState.controlEpoch || 0));
    const currentControl = taskState.controlState && typeof taskState.controlState === "object"
      ? taskState.controlState
      : {};
    bindAuthoritativeLifecycleControl(payload, {
      taskAuthorization: payload.taskAuthorization,
      toolRoute: taskState.toolRoute,
      controlEpoch: Math.max(0, Number(currentControl.epoch ?? taskState.controlEpoch ?? 0)),
      control: currentControl,
    });
    if (lifecycle?.ok === true) payload.taskRecoveryRecorded = true;
  }
  return fail(`Repeated ${tool} read is a semantic duplicate.`, payload);
}

function evidenceStagnationTargetFiles(taskState, context = {}) {
  const selected = taskState?.toolRoute?.selectedSlice;
  const selectedFiles = Array.isArray(selected?.files)
    ? selected.files.map((value) => String(value || "").replace(/\\/g, "/").replace(/^\/+/, "").trim())
      .filter(Boolean)
      .slice(0, 4)
    : [];
  if (selectedFiles.length) return selectedFiles;
  const activeProject = String(context.activeProject || "").trim();
  const fileAbsPath = String(context.fileAbsPath || "").trim();
  if (!activeProject || !fileAbsPath) return [];
  try {
    const projectRoot = path.dirname(path.resolve(activeProject));
    const relative = path.relative(projectRoot, path.resolve(fileAbsPath));
    if (relative && !relative.startsWith("..") && !path.isAbsolute(relative)) {
      return [relative.replace(/\\/g, "/")];
    }
  } catch {
    // An evidence guard must not fail merely because a stale context cannot be
    // converted to a project-relative repair target.
  }
  return [];
}

function recordTaskBoundEvidenceStagnation(context = {}, errorCode, recoveryHint = null) {
  const authorization = context.taskAuthorization && typeof context.taskAuthorization === "object"
    ? context.taskAuthorization
    : null;
  const taskSessionId = String(authorization?.taskSessionId || context.taskSessionId || "").trim();
  if (!authorization || !taskSessionId || context.detachedReadOnlyObservation === true) return null;
  const taskState = readTaskState(WORKSPACE_ROOT, taskSessionId);
  if (!taskState || String(taskState.status || "").toLowerCase() !== "running") return null;
  const targetFiles = evidenceStagnationTargetFiles(taskState, context);
  // The read route was already authorized above. Persist with the freshly
  // loaded server-owned credential instead of echoing a model-provided compact
  // handle back into the Python transaction; that handle may intentionally
  // omit rotating route fields and must not downgrade this P0 transition.
  const lifecycle = recordControlEventViaPython(
    WORKSPACE_ROOT,
    { taskAuthorization: taskAuthorizationForState(taskState) },
    {
      kind: "EVIDENCE_STAGNATION",
      errorCode: String(errorCode || "EVIDENCE_STAGNATION"),
      targetFiles,
      mutationGeneration: Number(context.mutationGeneration || 0),
      recoveryHint: recoveryHint && typeof recoveryHint === "object" ? recoveryHint : null,
    },
  );
  if (!lifecycle || typeof lifecycle !== "object") {
    return {
      ok: false,
      active: true,
      errorCode: "EVIDENCE_RECOVERY_RECORD_FAILED",
      error: "Task-bound evidence recovery returned no lifecycle result.",
    };
  }
  // Any record attempt for an existing active task is authoritative.  Do not
  // silently fall through to a v1 blocker just because a bridge error omitted
  // its optional `active` flag.
  return { ...lifecycle, active: lifecycle.active !== false };
}

function evidenceStagnationFail(tool, guard, options = {}) {
  const errorCode = guard.reason || "EVIDENCE_STAGNATION";
  recordReadStagnation(tool, guard.normalizedArgs, options.context || {});
  const recoveryHint = consumeMutationRecoveryHint(options.context?.fileAbsPath);
  // #region agent log
  const cov = getFileCoverage(options.context || {});
  agentDebugLog("H2", "server.js:evidenceStagnationFail", "evidence stagnation hard-stop", {
    tool,
    errorCode,
    attempts: guard.attempts,
    pingPong: Boolean(guard.pingPong),
    fullyCovered: Boolean(guard.fullyCovered),
    path: String(guard.normalizedArgs?.path || options.context?.fileAbsPath || "").slice(-120),
    recoveryHint: recoveryHint || null,
    coverage: cov ? {
      rangeCount: (cov.ranges || []).length,
      ranges: (cov.ranges || []).slice(0, 6),
      nonRangeCount: cov.nonRangeCount || 0,
      coveredRepeatCount: cov.coveredRepeatCount || 0,
      stagnationCount: cov.stagnationCount || 0,
    } : null,
  });
  // #endregion
  const lifecycle = recordTaskBoundEvidenceStagnation(options.context || {}, errorCode, recoveryHint);
  const lifecycleFailed = lifecycle
    && lifecycle.active === true
    && lifecycle.ok !== true;
  if (lifecycleFailed) {
    const payload = {
      errorCode: String(lifecycle.errorCode || "EVIDENCE_RECOVERY_RECORD_FAILED"),
      retryable: false,
      stopCurrentWorkflow: true,
      doNotRetry: ["read_file", "read_file_range", "read_symbol", "search_files"],
      agentInstruction: "Do not retry an evidence read. The task recovery state could not be committed; report the control error.",
    };
    bindAuthoritativeLifecycleControl(payload, lifecycle);
    return fail(String(lifecycle.error || "Could not commit task-bound evidence recovery."), payload);
  }
  const repairRecoveryActive = Boolean(
    recoveryHint
    && lifecycle?.control?.requiredTool
    && String(lifecycle.control.requiredTool.name || "") === "unreal_code_sketch_claim_validate"
  );
  if (repairRecoveryActive) {
    const payload = {
      ...(lifecycle?.ok === true ? {
        taskRecoveryRecorded: true,
        recoveryDisposition: String(lifecycle?.control?.disposition || ""),
      } : {}),
      errorCode,
      retryable: lifecycle?.control?.disposition === "require_tool",
      stopCurrentWorkflow: false,
      stopCurrentPhase: true,
      phaseBoundary: "evidence",
      doNotRetry: ["read_file", "read_file_range", "read_symbol", "search_files"],
      nextAction: "replace_in_file",
      nextActionIsTool: true,
      agentInstruction:
        "Do not call another evidence tool. Reuse the file content already in context and apply a bounded "
        + `replace_in_file (newText <= ${MAX_PATCH_CHANGED_LINES} lines, oldText+newText <= ${MAX_PATCH_ARGUMENT_CHARS} chars).`,
      userMessage:
        "Re-read blocked. Continue with a smaller replace_in_file using evidence already returned.",
      nextSteps: [
        "Do not call another evidence tool.",
        "Apply one bounded replace_in_file using exact text already in context.",
      ],
      readAttempts: guard.attempts,
      pingPong: Boolean(guard.pingPong),
      recoveryReason: recoveryHint.reason,
    };
    bindAuthoritativeLifecycleControl(payload, lifecycle);
    return fail(
      "Evidence re-read blocked after a bounded mutation rejection. Use existing evidence and emit a smaller replace_in_file.",
      payload
    );
  }
  const payload = {
    ...(lifecycle?.ok === true ? {
      taskRecoveryRecorded: true,
      recoveryDisposition: String(lifecycle?.control?.disposition || ""),
    } : {}),
    errorCode,
    retryable: lifecycle?.control?.disposition === "require_tool",
    doNotRetry: ["read_file", "read_file_range", "read_symbol", "search_files"],
    // Evidence exhaustion is a phase boundary, not a reason to abandon an
    // implementation request. Mutations and validation remain available.
    stopCurrentWorkflow: false,
    stopCurrentPhase: true,
    phaseBoundary: "evidence",
    agentInstruction: cachedReadInstruction(errorCode),
    userMessage: cachedReadInstruction(errorCode),
    nextSteps: [
      "Do not call another evidence tool.",
      "For implementation, continue with a supported write/validation step; for analysis-only work, answer from retained evidence.",
    ],
    readAttempts: guard.attempts,
    pingPong: Boolean(guard.pingPong),
  };
  bindAuthoritativeLifecycleControl(payload, lifecycle);
  return fail(
    errorCode === "EVIDENCE_STAGNATION_REPEAT"
      ? `identical ${tool} evidence call blocked after stagnation.`
      : "Evidence read stagnating — no new line coverage or soft budget exhausted.",
    payload
  );
}

function prepareReadGuard(tool, args, context) {
  const normalizedArgs = normalizeReadToolArgs(tool, args);
  const taskSessionId = String(context?.taskSessionId || requiredFields(args || {}).taskSessionId || "");
  if (taskSessionId) {
    const taskState = readTaskState(WORKSPACE_ROOT, taskSessionId);
    const requiredName = String(taskState?.controlState?.requiredTool?.name || "");
    const recoveryStatus = String(taskState?.recoveryObligation?.status || "");
    if (requiredName === String(tool || "") && recoveryStatus) {
      // A server-issued recovery read is a state transition, not optional
      // evidence gathering. Let the exact authorized call reach the route
      // reservation commit even when its line range was materialized earlier.
      return {
        normalizedArgs,
        action: "allow",
        repeat: false,
        serverRequiredRecoveryRead: true,
      };
    }
  }
  const decision = checkReadRepeat(tool, normalizedArgs, context);
  return { normalizedArgs, decision, ...decision };
}

function applyBuildRecoveryEvidenceGuard(tool, context = {}, toolArgs = {}) {
  // Detached observations answer a separate user question while the write
  // task is suspended; they cannot satisfy or consume its recovery evidence.
  if (context.detachedReadOnlyObservation === true) return null;
  const activeProject = String(context.activeProject || getActiveProject(CONFIG_PATH) || "");
  if (!activeProject) return null;
  const taskSessionId = String(context.taskSessionId || "");
  const recoveryBudget = taskSessionId
    ? numberEnv("MCP_BUILD_RECOVERY_EVIDENCE_BUDGET", 4, 1)
    : numberEnv("MCP_PRE_TASK_BUILD_RECOVERY_EVIDENCE_BUDGET", 8, 1);
  const recovery = recordRecoveryEvidenceCall(
    path.dirname(activeProject),
    context.mutationGeneration,
    // A realistic compile fix commonly needs the owning header, the failing
    // source range, one exact project search, and the matching declaration.
    // Two reads forced models to guess before a validator-directed lookup.
    {
      // Before a plan exists, compact models commonly inspect several files
      // to find the owner of the first parallel compiler diagnostic. The
      // tighter four-call limit begins once a bounded task exists.
      budget: recoveryBudget,
      // Reads before a plan must not consume the bounded recovery budget of a
      // later server-issued task. Checkpoints stay in the same scope, so they
      // still cannot be abused to reset evidence wandering.
      scopeKey: taskSessionId || "pre_task",
      tool,
      fileAbsPath: context.fileAbsPath || "",
      toolArgs,
      commitEvidence: false,
    },
    {
      taskSessionId,
      stateRoot: ensureStateRootLayout(resolveAgentStateRoot()),
    }
  );
  if (!recovery.blocked) return null;
  const contract = recovery.recoveryContract || {};
  if (recovery.reason === "build_recovery_required_tool_mismatch"
      || recovery.reason === "build_recovery_target_mismatch"
      || recovery.reason === "build_recovery_required_args_mismatch") {
    const requiredNextToolArgs = withCompactTaskAuthorization(
      contract.requiredNextToolArgs || {},
      context.taskAuthorization
    );
    return fail("Build recovery requires the first compiler diagnostic's exact source range.", {
      errorCode: "BUILD_RECOVERY_REQUIRED_EVIDENCE",
      retryable: true,
      stopCurrentWorkflow: false,
      doNotRetry: [tool],
      requiredNextTool: contract.requiredNextTool || "read_file_range",
      requiredNextToolArgs,
      nextAction: contract.requiredNextTool || "read_file_range",
      nextActionArgs: requiredNextToolArgs,
      nextActionIsTool: true,
      agentInstruction:
        "Do not inspect another file or the whole source file. Call requiredNextTool exactly once with requiredNextToolArgs, then move to the bounded repair plan.",
      nextSteps: [
        "Use the exact first-error range returned by the server.",
        "Do not batch-read parallel compiler errors before fixing the first one.",
      ],
    });
  }
  if (recovery.reason === "build_recovery_evidence_complete") {
    const nextAction = taskSessionId
      ? "unreal_code_sketch_claim_validate"
      : "unreal_agent_plan";
    const nextActionArgs = taskSessionId
      ? withCompactTaskAuthorization(
        { targetFiles: [contract.targetFile].filter(Boolean) },
        context.taskAuthorization
      )
      : {
        request: `Fix the first compiler error in ${contract.targetFile || "the reported source file"} and rebuild until successful.`,
        mode: "compile_fix",
      };
    return fail("The required first-error range is already available; further evidence reads are blocked until repair planning.", {
      errorCode: "BUILD_RECOVERY_EVIDENCE_COMPLETE",
      retryable: true,
      stopCurrentWorkflow: false,
      doNotRetry: ["unreal_rag_search", "search_files", "read_file", "read_file_range", "read_symbol"],
      nextAction,
      nextActionArgs,
      agentInstruction: taskSessionId
        ? "Do not read more files. Validate a bounded sketch for the reported target file, then apply the smallest mutation."
        : "Do not read more files. Start a compile-fix plan for the reported target file, then validate its bounded sketch and mutate.",
      nextSteps: ["Proceed to bounded repair planning; the first-error source evidence is complete."],
    });
  }
  return fail("Build-recovery evidence budget exhausted without a source mutation.", {
    errorCode: "BUILD_RECOVERY_EVIDENCE_BUDGET_EXHAUSTED",
    retryable: false,
    stopCurrentWorkflow: true,
    doNotRetry: ["unreal_rag_search", "search_files", "read_file", "read_file_range", "read_symbol"],
    evidenceReadCount: recovery.count,
    evidenceReadBudget: recovery.budget,
    buildFingerprint: recovery.buildFingerprint,
    agentInstruction:
      "Do not gather more evidence. Apply the smallest fix supported by the compiler error and existing reads, or report the blocker.",
    nextSteps: [
      "Patch the first actionable compiler error using evidence already returned.",
      "If no safe patch is supported, stop and report the exact unresolved error.",
    ],
  });
}

function commitBuildRecoveryEvidence(tool, context = {}, toolArgs = {}) {
  const activeProject = String(context.activeProject || getActiveProject(CONFIG_PATH) || "");
  if (!activeProject) return { ok: true, active: false };
  return markRecoveryEvidenceSatisfied(
    path.dirname(activeProject),
    context.mutationGeneration,
    {
      tool,
      fileAbsPath: context.fileAbsPath || "",
      toolArgs,
    },
    {
      taskSessionId: String(context.taskSessionId || ""),
      stateRoot: ensureStateRootLayout(resolveAgentStateRoot()),
    }
  );
}

function applyReadGuard(tool, guard, context) {
  if (!guard || guard.action === "allow" || !guard.repeat) return null;
  if (guard.action === "blocked" || guard.reason === "READ_REPEAT_BLOCKED") {
    return readRepeatBlocked(tool, guard, context);
  }
  if (
    guard.action === "stagnation"
    || guard.reason === "EVIDENCE_STAGNATION"
    || guard.reason === "EVIDENCE_STAGNATION_REPEAT"
  ) {
    return evidenceStagnationFail(tool, guard, { context });
  }
  // Identical / fully-covered range: return a total cache-hit result without
  // injecting a wider or wrong-range body.
  if (guard.action === "cache" || guard.action === "materialize" || guard.reason === "READ_CACHE_HIT") {
    return cachedReadSuccess(guard.cachedContent, {
      readAttempts: guard.attempts,
      fullyCovered: guard.fullyCovered,
      coveredBy: guard.coveredBy,
      includeContent: tool === "read_file_range",
      coverage: guard.coverage,
      continuation: context.coverageContinuation,
      taskState: context.taskState,
    });
  }
  return null;
}

/**
 * Truncate a UTF-8 text buffer on the last complete newline and describe line span.
 */
function truncateTextAtNewline(utf8Text, fileSize, bytesRead, detail) {
  let body = String(utf8Text || "");
  // Drop a trailing partial line when the raw byte read stopped mid-line.
  if (fileSize > bytesRead && body.length && !body.endsWith("\n")) {
    const lastNl = body.lastIndexOf("\n");
    if (lastNl >= 0) body = body.slice(0, lastNl + 1);
  }
  const lines = body.length ? body.replace(/\n$/, "").split("\n") : [];
  const startLine = 1;
  const endLine = Math.max(1, lines.length);
  const nextStartLine = endLine + 1;
  const nextDetail = detail === "compact" ? "medium" : detail === "medium" ? "large" : null;

  // Best-effort: last function-like signature that may be cut off.
  let detectedPartialSymbol = null;
  const sigRe = /\b((?:[A-Za-z_][\w:]*::)+[A-Za-z_]\w*)\s*\(/g;
  let match;
  while ((match = sigRe.exec(body)) !== null) {
    detectedPartialSymbol = match[1];
  }

  const meta = {
    truncated: fileSize > bytesRead,
    returnedLines: { start: startLine, end: endLine },
    nextStartLine,
    preferredNextTool: "read_symbol",
    detectedPartialSymbol,
    fileSizeBytes: fileSize,
    bytesRead,
    detailLevel: detail,
  };
  let footer = "";
  if (meta.truncated) {
    footer = `\n\n[TRUNCATED: file size ${fileSize} bytes, read ${bytesRead} bytes at detailLevel=${detail}. `
      + `Returned lines ${startLine}-${endLine}. Next unread line: ${nextStartLine}. `
      + `Prefer read_symbol for a named function`
      + (detectedPartialSymbol ? ` (partial symbol candidate: ${detectedPartialSymbol})` : "")
      + `. `
      + (nextDetail
        ? `Escalate once with detailLevel=${nextDetail} or read_file_range from line ${nextStartLine}.]`
        : `Use read_file_range from line ${nextStartLine} or read_symbol.]`);
  }
  return { body, footer, meta, endLine };
}

function rememberReadEvidence(target, stat, resolution, lineRange = null, contentHash = null) {
  const key = path.resolve(target);
  const existing = readEvidence.get(key);
  const ranges = new Set(existing && existing.signature === fileStatSignature(stat) ? existing.lineRanges || [] : []);
  if (lineRange) ranges.add(lineRange);
  readEvidence.set(key, {
    signature: fileStatSignature(stat),
    contentHash: contentHash || (existing && existing.signature === fileStatSignature(stat) ? existing.contentHash : null),
    path: pathMetadata(resolution),
    lineRanges: Array.from(ranges),
    readAt: Date.now()
  });
}

function hasFreshReadEvidence(target, stat) {
  const entry = readEvidence.get(path.resolve(target));
  return Boolean(entry && entry.signature === fileStatSignature(stat) && entry.contentHash);
}

function sourceEvidenceSummary(activeProject) {
  const projectDir = activeProject ? path.dirname(path.resolve(activeProject)) : null;
  const filesRead = [];
  for (const [absolutePath, entry] of readEvidence.entries()) {
    if (!projectDir || !absolutePathIsWithin(absolutePath, projectDir)) continue;
    if (![".h", ".hpp", ".cpp", ".c", ".cc", ".cs"].includes(path.extname(absolutePath).toLowerCase())) continue;
    filesRead.push({
      path: entry.path.projectRelativePath,
      lineRanges: entry.lineRanges,
      readAt: entry.readAt
    });
  }
  return {
    sourceReadSucceeded: filesRead.length > 0,
    filesRead,
    directSourceRequired: true
  };
}

function rememberCachedFile(target, stat, buffer) {
  if (FILE_CACHE_MAX_ENTRIES <= 0 || FILE_CACHE_MAX_BYTES <= 0 || buffer.length > FILE_CACHE_MAX_BYTES) {
    return;
  }
  const key = path.resolve(target);
  fileCache.delete(key);
  fileCache.set(key, {
    signature: fileStatSignature(stat),
    buffer
  });
  while (fileCache.size > FILE_CACHE_MAX_ENTRIES) {
    const oldest = fileCache.keys().next().value;
    fileCache.delete(oldest);
  }
}

async function readCachedBufferFile(target, stat) {
  if (FILE_CACHE_MAX_ENTRIES <= 0 || FILE_CACHE_MAX_BYTES <= 0 || stat.size > FILE_CACHE_MAX_BYTES) {
    return fsp.readFile(target);
  }
  const key = path.resolve(target);
  const signature = fileStatSignature(stat);
  const cached = fileCache.get(key);
  if (cached && cached.signature === signature && Buffer.isBuffer(cached.buffer)) {
    fileCache.delete(key);
    fileCache.set(key, cached);
    return cached.buffer;
  }
  const buffer = await fsp.readFile(target);
  rememberCachedFile(target, stat, buffer);
  return buffer;
}

async function readLeadingFileBuffer(target, stat, maxBytes) {
  if (stat.size <= FILE_CACHE_MAX_BYTES) {
    const raw = await readCachedBufferFile(target, stat);
    return raw.subarray(0, Math.min(maxBytes, raw.length));
  }
  const fd = await fsp.open(target, "r");
  try {
    const buffer = Buffer.alloc(Math.min(maxBytes, stat.size));
    await fd.read(buffer, 0, buffer.length, 0);
    return buffer;
  } finally {
    await fd.close();
  }
}

async function readCachedTextFile(target, stat) {
  const raw = await readCachedBufferFile(target, stat);
  if (!isTextLikely(raw.subarray(0, Math.min(raw.length, MAX_READ_BYTES)))) {
    const err = new Error("file appears binary");
    err.code = "BINARY_FILE";
    throw err;
  }
  return raw.toString("utf8");
}

function invalidateFileCache(target) {
  const key = path.resolve(target);
  fileCache.delete(key);
  readEvidence.delete(key);
}

function invalidateWorkspaceInfoCache() {
  workspaceInfoCache = null;
}

const {
  evaluateBootstrapCache,
  mergeBootstrapCache,
} = require("./bootstrap-cache");

function bootstrapCachePath() {
  return path.join(WORKSPACE_ROOT, ".agent", "session", "bootstrap_cache.json");
}

async function readBootstrapCache() {
  try {
    const raw = await fsp.readFile(bootstrapCachePath(), "utf8");
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

async function writeBootstrapCache(patch) {
  const cachePath = bootstrapCachePath();
  await fsp.mkdir(path.dirname(cachePath), { recursive: true });
  const existing = await readBootstrapCache();
  const next = mergeBootstrapCache(existing, patch);
  atomicWriteJson(cachePath, next);
  invalidateWorkspaceInfoCache();
  return next;
}

async function buildWorkspaceInfo() {
  const activeProject = getActiveProject(CONFIG_PATH);
  const cacheKey = `${WORKSPACE_ROOT}|${CONFIG_PATH}|${activeProject || ""}`;
  const now = Date.now();
  if (
    WORKSPACE_INFO_CACHE_TTL_MS > 0
    && workspaceInfoCache
    && workspaceInfoCache.key === cacheKey
    && now < workspaceInfoCache.expiresAt
  ) {
    return workspaceInfoCache.value;
  }

  const engines = await findEngineInstalls();
  const discovery = await discoverProjects(WORKSPACE_ROOT, CONFIG_PATH);
  let projectContext = null;
  if (activeProject) {
    projectContext = {
      ok: true,
      ...buildProjectBrowsePaths(activeProject, WORKSPACE_ROOT)
    };
  } else {
    const switchGuidance = projectSwitchGuidance(agentRegisteredToolNames());
    projectContext = {
      ok: false,
      error: switchGuidance.requiredNextTool
        ? "activeProject is not set. Call unreal_set_active_project on unreal-rag first."
        : "activeProject is not set. Call set_active_project first.",
      browseAvailable: false,
      ...switchGuidance
    };
  }
  const payload = {
    workspaceRoot: WORKSPACE_ROOT,
    stateRoot: ensureStateRootLayout(resolveAgentStateRoot()),
    configPath: CONFIG_PATH,
    serverEntry: __filename,
    serverVersion: SERVER_VERSION,
    activeProject,
    projectContext,
    sourceEvidence: sourceEvidenceSummary(activeProject),
    allowWrite: ALLOW_WRITE,
    requireTaskAuthForWrites: REQUIRE_TASK_AUTH_FOR_WRITES,
    allowCommands: ALLOW_COMMANDS,
    allowUnrealBuild: ALLOW_UNREAL_BUILD,
    validateOnWrite: VALIDATE_ON_WRITE,
    validateOnWriteTimeoutMs: VALIDATE_ON_WRITE_TIMEOUT_MS,
    allowExistingSourceWrite: false,
    allowSourceDelete: ALLOW_SOURCE_DELETE,
    mcpEssentialTools: MCP_ESSENTIAL_TOOLS,
    mcpExtendedTools: MCP_EXTENDED_TOOLS,
    toolCatalog: buildToolCatalogDiagnostics(
      allAgentTools(),
      listToolsRouteContext(WORKSPACE_ROOT, activeProject || "")
    ),
    maxReadBytes: MAX_READ_BYTES,
    maxOutputBytes: MAX_OUTPUT_BYTES,
    maxAgentResultChars: MCP_AGENT_RESULT_MAX_CHARS,
    commandTimeoutMs: COMMAND_TIMEOUT_MS,
    contextHygiene: {
      recommendedMaxTurnsPerChat: 12,
      freshSessionTriggers: [
        "request exceeds the available context size",
        "failed to restore kv cache",
        "Model failed to generate a tool call"
      ],
      toolBudgetDefaults: {
        readFileDetailLevel: "compact",
        readUnrealLogsMaxLines: 60,
        readUnrealLogsMaxFiles: 1,
        buildResponseMode: BUILD_VERBOSE_OUTPUT ? "verbose" : "compact"
      },
      handoffTemplatePath: "prompts/lmstudio_session_handoff.md",
      handoffArtifactPath: ".agent/handoff/latest.md"
    },
    defaultPlatform: defaultPlatform(),
    projectSearchRoots: discovery.roots,
    discoveredProjectCount: discovery.projects.length,
    installedEngines: engines.map((e) => e.engineRoot),
    recentProjects: discovery.projects.slice(0, 8).map((p) => ({
      projectFile: p.projectFile,
      projectPath: p.projectPath,
      preferredTarget: p.preferredTarget,
      allTargets: p.allTargets,
      engineAssociation: p.engineAssociation,
      modifiedAt: p.modifiedAt
    })),
  };
  if (activeProject) {
    payload.bootstrapCache = evaluateBootstrapCache(await readBootstrapCache(), activeProject);
  } else {
    payload.bootstrapCache = evaluateBootstrapCache(await readBootstrapCache(), activeProject);
  }
  if (WORKSPACE_INFO_CACHE_TTL_MS > 0) {
    workspaceInfoCache = {
      key: cacheKey,
      expiresAt: now + WORKSPACE_INFO_CACHE_TTL_MS,
      value: payload
    };
  }
  return payload;
}

function allAgentTools() {
  const tools = [
      {
        name: "get_workspace_info",
        description: "Show workspace root, safety flags, configured search roots, and recently discovered Unreal projects.",
        inputSchema: makeJsonSchema({})
      },
      {
        name: "list_unreal_projects",
        description: "List discovered Unreal projects and show which one is currently active.",
        inputSchema: makeJsonSchema({
          maxDepth: { type: "number", description: "Search depth for .uproject discovery. Default 4." }
        })
      },
      {
        name: "get_active_project",
        description: "Return the selected active Unreal project and projectDir. Use this instead of listing WORKSPACE_ROOT when activeProject is already set.",
        inputSchema: makeJsonSchema({})
      },
      {
        name: "list_active_tasks",
        description: "List running task sessions for the active project/workspace without requiring a known taskSessionId. Does not return authToken or ownerCapability. Foreign conversation metadata is redacted unless taskAuthorization.ownerCapability matches.",
        inputSchema: makeJsonSchema({
          taskAuthorization: taskAuthSchemaProperties().taskAuthorization,
        })
      },
      {
        name: "cancel_active_task",
        description: "Cancel the single active running task, or a named taskSessionId when multiple are present. Pass taskAuthorization.ownerCapability to cancel your own task without force. Foreign healthy tasks require force=true.",
        inputSchema: makeJsonSchema({
          taskSessionId: { type: "string", description: "Optional explicit taskSessionId when multiple running tasks exist." },
          force: { type: "boolean", description: "Force-cancel a healthy task owned by another MCP connection after user confirmation." },
          taskAuthorization: taskAuthSchemaProperties().taskAuthorization,
        })
      },
      {
        name: "quarantine_corrupt_task",
        description: "Archive corrupt task state that shrinks the tool list but cannot be cancelled normally. Moves the task directory under quarantine/ so route ownership is released.",
        inputSchema: makeJsonSchema({
          taskSessionId: { type: "string", description: "Optional explicit corrupt taskSessionId when multiple corrupt tasks exist." }
        })
      },
      {
        name: "set_active_project",
        description: "Choose the active Unreal project by .uproject path or one exact project name. Pass clear=true to unset; partial names are suggestions only.",
        inputSchema: makeJsonSchema({
          projectPath: { type: "string", description: "Absolute or workspace-relative .uproject path." },
          hint: { type: "string", description: "Exact project name or exact .uproject stem; partial/fuzzy names are never auto-selected." },
          clear: { type: "boolean", description: "If true, clear activeProject and return to free selection." }
        })
      },
      {
        name: "open_active_project_picker",
        description: "Open a Windows GUI to pick the active .uproject. Default shows a selectable project list; set explorer=true for a file dialog.",
        inputSchema: makeJsonSchema({
          explorer: { type: "boolean", description: "If true, open file explorer dialog instead of grid list." }
        })
      },
      {
        name: "refactor_impact_scan",
        description: "Scan the active or hinted Unreal project for references to a class/symbol. Use before R0-R4 refactors.",
        inputSchema: makeJsonSchema({
          symbol: { type: "string", description: "Class or symbol name to search for." },
          hint: { type: "string", description: "Optional project hint if activeProject is unset." },
          maxFiles: { type: "number", description: "Max matching files. Default 40." }
        }, ["symbol"])
      },
      {
        name: "refactor_plan_validate",
        description: "Validate an R0-R4 refactor plan against stage rules (SSOT, no code on R0, file limits).",
        inputSchema: makeJsonSchema({
          stage: { type: "string", description: "R0, R1, R2, R3, or R4." },
          planText: { type: "string", description: "The refactor plan markdown/text to validate." }
        }, ["stage", "planText"])
      },
      {
        name: "detect_unreal_project",
        description: "Detect Unreal .uproject files, editor targets, engine association, and default build settings. Use before build_unreal_project when project/target are unknown.",
        inputSchema: makeJsonSchema({
          hint: { type: "string", description: "Optional project folder or .uproject name fragment, e.g. JRPG or JRPG.uproject." },
          resolveBuildDefaults: { type: "boolean", description: "If true, also resolve engineRoot/target/platform/configuration. Default true." }
        })
      },
      {
        name: "read_unreal_logs",
        description: "Read a compact error-focused slice from the newest MCP build log or Unreal runtime log. Supports bounded tail, first-error scanning from byte zero, and cursor/range reads so oversized logs do not hide the original failure.",
        inputSchema: makeJsonSchema({
          mode: { type: "string", enum: ["tail", "first_error", "range"], description: "tail (default), first_error from byte zero, or range from cursorByte." },
          cursorByte: { type: "number", description: "Start byte for range mode. Use nextCursorByte to continue." },
          maxBytes: { type: "number", description: "Bytes per chunk: range accepts 1 KiB to LOG_READ_MAX_BYTES; tail/first_error use at least 64 KiB." },
          maxLines: { type: "number", description: "Max tail lines per log file. Default 60, max 500." },
          maxFiles: { type: "number", description: "Newest log files to inspect. Default 1, max 3." },
          fileName: { type: "string", description: "Optional exact log basename such as latest-build.log or latest-automation.log. Paths are not accepted." },
          filter: { type: "string", description: "Optional case-insensitive substring filter (Error, Assert, etc.)." },
          summaryOnly: { type: "boolean", description: "Return the first error cluster instead of the full tail. Default true." }
        })
      },
      {
        name: "write_session_handoff",
        description: "Save a compact cross-chat resume note to the fixed artifact path .agent/handoff/latest.md under WORKSPACE_ROOT. Safe-mode utility: does not require ALLOW_WRITE=1, overwrites only that artifact file, and never writes project source.",
        inputSchema: makeJsonSchema({
          summary: { type: "string", description: "One-sentence current task state." },
          changedFiles: {
            type: "array",
            items: { type: "string" },
            description: "Changed project-relative files, max 12."
          },
          openErrors: {
            type: "array",
            items: { type: "string" },
            description: "Remaining actionable errors, max 5."
          },
          nextSteps: {
            type: "array",
            items: { type: "string" },
            description: "Next steps in order, max 3."
          },
          avoidRepeating: {
            type: "array",
            items: { type: "string" },
            description: "Failed calls or approaches not to repeat, max 3."
          }
        }, ["summary"])
      },
      {
        name: "record_bootstrap_step",
        description: "Record completion of a bootstrap step in .agent/session/bootstrap_cache.json so a fresh chat can skip bootstrap when the cache is still valid.",
        inputSchema: makeJsonSchema({
          step: {
            type: "string",
            description: "One of unreal_get_active_project, unreal_rag_health, get_workspace_info.",
          },
          projectPath: { type: "string", description: "Active .uproject path when known." },
          ragHealthOk: { type: "boolean", description: "Set true after unreal_rag_health succeeds." },
        }, ["step"])
      },
      {
        name: "list_directory",
        description: "List immediate children of a workspace:// or project:// directory (non-recursive). Prefer shallow discovery; repeated/same-path listing is budget-limited. Prefer search_files for deep lookup.",
        inputSchema: makeJsonSchema({
          ...evidenceSessionSchemaProperty(),
          path: { type: "string", description: "Relative path inside workspace, e.g. '.', 'Source'." },
          maxEntries: { type: "number", description: "Max entries to show. Default 200." }
        }, ["path"])
      },
      {
        name: "read_file",
        description: "Read a UTF-8 file under workspace:// or project://. Active-project source may be outside WORKSPACE_ROOT. Required before writes; large source should use read_file_range.",
        inputSchema: makeJsonSchema({
          ...evidenceSessionSchemaProperty(),
          path: { type: "string", description: "Relative path inside workspace." },
          maxBytes: { type: "number", description: "Optional max bytes. Capped by detailLevel tier." },
          detailLevel: {
            type: "string",
            enum: ["compact", "medium", "large", "full"],
            description: "Read size tier: compact ~16 KiB, medium ~32 KiB, large/full up to 64 KiB."
          }
        }, ["path"])
      },
      {
        name: "read_file_range",
        description: "Read a line range under workspace:// or project://. Prefer this over read_file for large project sources. Line span is capped by detailLevel.",
        inputSchema: makeJsonSchema({
          ...evidenceSessionSchemaProperty(),
          path: { type: "string", description: "Relative path inside workspace." },
          startLine: { type: "number", description: "1-based start line (inclusive)." },
          endLine: { type: "number", description: "1-based end line (inclusive)." },
          detailLevel: {
            type: "string",
            enum: ["compact", "medium", "large", "full"],
            description: "Max lines per request: compact 150, medium 400, large 1200, full 2000."
          }
        }, ["path", "startLine", "endLine"])
      },
      {
        name: "read_symbol",
        description: "Read one C++ function body and record it as direct source evidence. Prefer this for function-level analysis.",
        inputSchema: makeJsonSchema({
          ...evidenceSessionSchemaProperty(),
          path: { type: "string", description: "Source file containing the function." },
          symbol: { type: "string", description: "Function or qualified function, e.g. UFoo::Tick or Tick." },
          contextLines: { type: "number", description: "Extra lines around the function. Default 3, max 30." }
        }, ["path", "symbol"])
      },
      {
        name: "write_file",
        description: "Create one brand-new UTF-8 file under the active project's Source/Config/Plugins source tree (or .agent/ under WORKSPACE_ROOT). Keep the first file body bounded (prefer <=8,000 characters); extend it later with replace_in_file if needed. Requires ALLOW_WRITE=1 and an active executor route; the server auto-binds the single active project task, while ambiguous tasks require the optional taskAuthorization selector. Pass concrete targetFiles and changeKind=new_file to unreal_code_sketch_claim_validate before write. Create-only: any file that already exists is blocked. Use replace_in_file to modify existing files. Do not retry write_file after a 'file already exists' error.",
        inputSchema: makeJsonSchema({
          ...taskAuthSchemaProperties(),
          path: { type: "string", description: "workspace:// or project:// path (active-project Source allowed even outside WORKSPACE_ROOT)." },
          content: { type: "string", description: "Full file content to write." },
          createDirs: { type: "boolean", description: "Create parent directories if needed. Default false." }
        }, ["path", "content"])
      },
      {
        name: "replace_in_file",
        description: "Safely replace one exact bounded region in an existing file under the active project's Source/Config/Plugins source tree (or .agent/ under WORKSPACE_ROOT). Use at most 60 changed lines and prefer <=8,000 combined oldText/newText characters; never duplicate a complete file as old/new text. Split larger work into multiple read_file_range + replace_in_file calls. Requires ALLOW_WRITE=1 and an active executor route; the server auto-binds the single active project task, while ambiguous tasks require the optional taskAuthorization selector. Read the target range first and set expectedOccurrences=1. Line endings (CRLF/LF) are normalized automatically. If oldText is not found, re-read a narrower range and correct it; never retry unchanged. Byte-identical repeat calls are rejected.",
        inputSchema: makeJsonSchema({
          ...taskAuthSchemaProperties(),
          path: { type: "string", description: "workspace:// or project:// path (active-project Source allowed even outside WORKSPACE_ROOT)." },
          oldText: { type: "string", description: "Exact text to replace." },
          newText: { type: "string", description: "Replacement text." },
          expectedOccurrences: { type: "number", description: "If set, replacement only proceeds when occurrence count matches." }
        }, ["path", "oldText", "newText"])
      },
      {
        name: "propose_file_deletions",
        description: "Create a structured deletion plan after edits are complete. Deletes nothing. Required before delete_file: list file count, path, file name, reason, impact if kept, and impact if deleted, then wait for explicit user approval.",
        inputSchema: makeJsonSchema({
          completedEditsSummary: { type: "string", description: "What edits/checks are already complete before considering deletion." },
          files: {
            type: "array",
            description: "Deletion candidates. Each item must include path, reason, ifNotDeleted, and ifDeleted.",
            items: {
              type: "object",
              properties: {
                path: { type: "string" },
                reason: { type: "string" },
                ifNotDeleted: { type: "string" },
                ifDeleted: { type: "string" }
              }
            }
          }
        }, ["completedEditsSummary", "files"])
      },
      {
        name: "delete_file",
        description: "Delete one file under the active project's Source/ tree only after propose_file_deletions returned a per-file approvalToken and the user approved that plan. Requires an active executor route, ALLOW_WRITE=1, and ALLOW_SOURCE_DELETE=1; the server auto-binds one active project task and requires the optional taskAuthorization selector only when routes are ambiguous. Extended mode only.",
        inputSchema: makeJsonSchema({
          ...taskAuthSchemaProperties(),
          path: { type: "string", description: "workspace:// or project:// path inside the active project's Source tree." },
          completedEditsSummary: { type: "string", description: "Same completedEditsSummary used in propose_file_deletions." },
          reason: { type: "string", description: "Specific reason this file must be deleted." },
          ifNotDeleted: { type: "string", description: "What concretely happens if this file is not deleted." },
          ifDeleted: { type: "string", description: "What concretely happens if this file is deleted." },
          approvalToken: { type: "string", description: "Per-file approvalToken returned by propose_file_deletions after user approval." },
          expectedContent: { type: "string", description: "Optional exact file content guard before delete." }
        }, ["path", "completedEditsSummary", "reason", "ifNotDeleted", "ifDeleted", "approvalToken"])
      },
      {
        name: "apply_edit_bundle",
        description: "Apply a small edit bundle atomically with pre-hash capture, scoped validation, and rollback on failure. For existing files use patches only, each covering at most 60 changed lines; multiple patches for the same file are allowed and applied in listed order. never put a complete existing file in files/content. The files form is only for bounded brand-new files. Requires ALLOW_WRITE=1 and an active executor route; the server auto-binds one active project task and requires the optional taskAuthorization selector only when routes are ambiguous.",
        inputSchema: makeJsonSchema({
          files: {
            type: "array",
            description: "brand-new files only; never use content to overwrite an existing path.",
            items: {
              type: "object",
              properties: {
                path: { type: "string" },
                content: { type: "string" }
              }
            }
          },
          patches: {
            type: "array",
            description: "Small exact patches for existing files; max 60 changed lines per patch and no full-file old/new payloads. Multiple entries may target the same path and are applied in listed order.",
            items: {
              type: "object",
              properties: {
                path: { type: "string" },
                oldText: { type: "string" },
                newText: { type: "string" },
                expectedOccurrences: { type: "number" }
              }
            }
          },
          ...taskAuthSchemaProperties()
        }, [])
      },
      {
        name: "static_validate_project",
        description: "Run static Unreal compile-readiness validation on the current task slice with related source pairs as advisory context. Set fullAudit=true only for an explicit project-wide audit. A failed scan is fresh evidence but is not a passing build proof; read the first finding, mutate the bounded slice, and validate again.",
        inputSchema: makeJsonSchema({
          projectRoot: { type: "string", description: "Optional project root or .uproject path. Defaults to active project." },
          fullAudit: { type: "boolean", description: "Run a project-wide audit instead of the server-owned current task slice. Default false." }
        })
      },
      {
        name: "search_files",
        description: "Search text under workspace:// or project://. For current Unreal code, scope to project://Source or project://Plugins and use direct source evidence. Filename-extension-shaped queries such as \\.cpp$ are also matched against basenames unless matchFileNames=false is explicit.",
        inputSchema: makeJsonSchema({
          ...evidenceSessionSchemaProperty(),
          query: { type: "string", description: "Regex or plain text to search." },
          path: { type: "string", description: "Relative directory/file to search. Default '.'." },
          regex: { type: "boolean", description: "Use query as regex. Default false." },
          matchFileNames: {
            type: "boolean",
            description: "Also return file paths whose basename matches query. Explicit true/false wins; when omitted, filename-extension-shaped queries are inferred as filename discovery."
          },
          maxResults: { type: "number", description: "Max matching lines. Default 100." }
        }, ["query"])
      },
      {
        name: "run_command",
        description: "Run a small allowlisted command in WORKSPACE_ROOT. Requires ALLOW_COMMANDS=1. Dangerous commands are blocked.",
        inputSchema: makeJsonSchema({
          command: { type: "string", description: "Command line. Allowlisted only." },
          cwd: { type: "string", description: "Relative cwd inside workspace. Default '.'." },
          timeoutMs: { type: "number", description: "Timeout. Default 10 minutes." }
        }, ["command"])
      },
      {
        name: "build_unreal_project",
        description: "Run the host Unreal build tool after C++ or Build.cs edits. Requires one completed static scan for the current mutation generation; remaining findings do not require an override. Returns compact errors and a project-local fullLogPath.",
        inputSchema: makeJsonSchema({
          hint: { type: "string", description: "Optional project folder or .uproject name fragment for auto-detection." },
          engineRoot: { type: "string", description: "Optional UE engine root. Auto-detected from EngineAssociation when omitted." },
          project: { type: "string", description: "Optional .uproject path relative to workspace or absolute inside workspace." },
          target: { type: "string", description: "Optional target name. Defaults to detected *Editor target." },
          platform: { type: "string", description: "Optional platform. Default Win64 on Windows." },
          configuration: { type: "string", description: "Optional configuration. Default Development." },
          allowAbsoluteProject: { type: "boolean", description: "Allow absolute .uproject path outside workspace. Default false." },
          timeoutMs: { type: "number", description: "Build timeout in ms. Default COMMAND_TIMEOUT_MS." },
          verboseOutput: { type: "boolean", description: "Include truncated stdout/stderr inline. Default false; prefer fullLogPath." },
          validationOverride: { type: "boolean", description: "Allow one audited build despite validation-dirty or stale-generation proof state." },
          validationOverrideNote: { type: "string", description: "Reason recorded when validationOverride=true." },
          allowEngineFallback: { type: "boolean", description: "When true, allow build when the resolved engine version differs from the active project's numeric EngineAssociation. Record an audit note in agent chat." }
        })
      },
      {
        name: "run_unreal_automation_tests",
        description: "Run declared project Automation tests through UnrealEditor-Cmd after a successful UHT/UBT build. Task-bound runs must use the server-owned ordered testFilters and exact project/engine proof scope returned by control. Manual unbound runs may use testFilter. The tool fails if zero tests execute and completes or advances a task slice only after every bound filter passes.",
        inputSchema: makeJsonSchema({
          testFilter: { type: "string", description: "Optional Automation name/prefix for manual, unbound use. A task-bound run must use the exact server-owned testFilters instead." },
          testFilters: {
            type: "array",
            items: { type: "string" },
            maxItems: MAX_AUTOMATION_FILTERS,
            description: "Exact server-bound ordered declaration filters for the active slice. Do not combine, broaden, omit, or reorder them."
          },
          engineRoot: { type: "string", description: "Optional UE engine root; defaults to the active project's resolved engine." },
          project: { type: "string", description: "Optional .uproject path; defaults to active project." },
          timeoutMs: { type: "number", description: "Automation timeout in ms. Default 30 minutes." },
          verboseOutput: { type: "boolean", description: "Include bounded stdout/stderr in the response." }
        })
      }
  ];
  const optionalTaskAuthorization = taskAuthSchemaProperties().taskAuthorization;
  for (const tool of tools) {
    const schema = tool.inputSchema;
    if (!schema || typeof schema !== "object") continue;
    const properties = schema.properties;
    if (!properties || typeof properties !== "object") continue;
    if (!properties.taskAuthorization) {
      properties.taskAuthorization = optionalTaskAuthorization;
    }
  }
  return tools;
}

server.setRequestHandler(ListToolsRequestSchema, async () => {
  const tools = allAgentTools();
  const context = listToolsRouteContext(
    WORKSPACE_ROOT,
    getActiveProject(CONFIG_PATH) || ""
  );
  lastObservedRouteFingerprint = activeRouteFingerprint(context);
  await emitCatalogInitializedDiagnostic(context);
  return {
    tools: filterAgentTools(tools, context)
  };
});

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const name = request.params.name;
  return toolCallContext.run({ toolName: name }, async () => {
    let args = request.params.arguments || {};
    let priorSeq = 0;
    let durableGuardScope = { stateRoot: ensureStateRootLayout(resolveAgentStateRoot()) };
    const context = toolCallContext.getStore();
    if (context) {
      context.arguments = args;
      context.startedAtMs = Date.now();
      context.callId = `${process.pid}:${context.startedAtMs}:${priorSeq}:${crypto.randomBytes(4).toString("hex")}`;
      const metadata = request.params?._meta && typeof request.params._meta === "object"
        ? request.params._meta
        : {};
      context.progressHeartbeat = createProgressHeartbeat({
        toolName: name,
        progressToken: metadata.progressToken,
        sendProgress: (params) => server.notification({
          method: "notifications/progress",
          params,
        }),
        sendMessage: (data) => server.notification({
          method: "notifications/message",
          params: { level: "info", logger: "unreal-agent", data },
        }),
      });
      try {
        recordToolStarted(WORKSPACE_ROOT, {
          toolName: name,
          arguments: args,
          callId: context.callId,
        });
      } catch {
        // Telemetry is deliberately non-authoritative.
      }
    }
    try {
    clearLoopHistoriesOnProjectChange(false);
    const activeProjectForRoute = getActiveProject(CONFIG_PATH) || "";
    const currentRouteContext = listToolsRouteContext(
      WORKSPACE_ROOT,
      activeProjectForRoute
    );
    const currentRouteFingerprint = activeRouteFingerprint(currentRouteContext);
    if (currentRouteFingerprint !== lastObservedRouteFingerprint) {
      lastObservedRouteFingerprint = currentRouteFingerprint;
      try {
        await server.sendToolListChanged();
      } catch (error) {
        // Route enforcement remains server-side, but a failed advisory refresh
        // must be visible during diagnosis instead of looking like success.
        console.warn(
          `[unreal-agent] TOOLS_LIST_CHANGED_NOTIFY_FAILED: ${String(error?.message || error || "unknown notification failure").slice(0, 500)}`
        );
      }
    }
    const toolDefinitions = allAgentTools();
    const allowed = callableAgentToolNames(toolDefinitions.map((tool) => tool.name));
    if (!allowed.has(name)) {
      const blocked = toolNotCallablePayload(name);
      return fail(blocked.error, {
        errorCode: blocked.errorCode,
        retryable: blocked.retryable,
        userMessage: blocked.userMessage,
        agentInstruction: blocked.agentInstruction,
      });
    }
    if (
      currentRouteContext.status === "none"
      && ROUTE_MUTATION_TOOLS.has(name)
    ) {
      return fail("A server-owned task route is required before project mutation.", {
        errorCode: "TASK_PLANNER_ROUTE_REQUIRED",
        retryable: false,
        stopCurrentWorkflow: false,
        recoveryActionRequired: true,
        doNotRetry: [name],
        doNotFabricateTaskAuthorization: true,
        requiredProvider: "mcp/unreal-rag",
        requiredTool: "unreal_agent_plan",
        nextAction: "enable_or_call_unreal_agent_plan",
        nextActionIsTool: false,
        agentInstruction: (
          "Do not retry this write and do not invent taskAuthorization. Ensure mcp/unreal-rag is enabled, "
          + "then call unreal_agent_plan once with the original user request. Continue only with its server-issued route."
        ),
      });
    }
    if (
      currentRouteContext.status === "none"
      && UNROUTED_INSPECTION_TOOLS.has(name)
    ) {
      args = discardTaskAuthorizationWithoutActiveRoute(
        WORKSPACE_ROOT,
        args,
        { activeProject: activeProjectForRoute }
      ).args;
    }
    let nestedTaskAuthorization = args.taskAuthorization
      && typeof args.taskAuthorization === "object"
      ? args.taskAuthorization
      : args.task_authorization
      && typeof args.task_authorization === "object"
      ? args.task_authorization
      : null;
    let hasExplicitTaskAuthorization = Boolean(
      nestedTaskAuthorization
      || String(args.taskSessionId || args.task_session_id || "").trim()
    );
    const requestedObservation = args.taskObservation && typeof args.taskObservation === "object"
      ? args.taskObservation
      : {};
    const detachedObservationCandidate = Boolean(
      String(requestedObservation.mode || "") === "detached_read_only"
      && UNROUTED_INSPECTION_TOOLS.has(name)
      && hasExplicitTaskAuthorization
    );
    if (
      hasExplicitTaskAuthorization
      && !SAFE_ROUTE_RECOVERY_TOOLS.has(name)
      && !detachedObservationCandidate
    ) {
      const compactExpansion = expandCompactTaskAuthorization(
        WORKSPACE_ROOT,
        name,
        args,
        { activeProject: activeProjectForRoute }
      );
      if (!compactExpansion.ok) {
        return fail(
          compactExpansion.error || "Compact task route authorization failed.",
          routeAuthorizationFailureOptions(compactExpansion, name)
        );
      }
      if (compactExpansion.expanded) {
        args = compactExpansion.args;
        nestedTaskAuthorization = args.taskAuthorization;
        hasExplicitTaskAuthorization = true;
      }
    }
    let detachedReadOnlyObservation = false;
    const observation = requestedObservation;
    if (
      String(observation.mode || "") === "detached_read_only"
      && UNROUTED_INSPECTION_TOOLS.has(name)
      && hasExplicitTaskAuthorization
    ) {
      const observationOwner = discoverActiveTaskContext(
        WORKSPACE_ROOT,
        activeProjectForRoute,
        {
          ...routeOwnershipFromArgs(args),
          requireOwnerCapability: true,
        }
      );
      const observationFields = requiredFields(args);
      detachedReadOnlyObservation = Boolean(
        observationOwner.status === "active"
        && String(observationOwner.taskSessionId || "") === observationFields.taskSessionId
        && /^[a-f0-9]{64}$/i.test(String(observation.requestHash || ""))
      );
      if (!detachedReadOnlyObservation) {
        return fail("Detached read-only observation could not be bound to the active task owner.", {
          errorCode: "DETACHED_OBSERVATION_AUTH_FAILED",
          retryable: false,
          stopCurrentWorkflow: false,
          agentInstruction: "Do not retry or fall back to task control tools. Resume the active task or ask the read-only question in a fresh chat.",
        });
      }
    }
    if (name === "build_unreal_project" && hasExplicitTaskAuthorization && ALLOW_UNREAL_BUILD) {
      const taskSessionId = requiredFields(args).taskSessionId;
      const taskState = taskSessionId ? readTaskState(WORKSPACE_ROOT, taskSessionId) : null;
      const required = taskState?.controlState?.requiredTool;
      if (required && required.name === "build_unreal_project") {
        let buildContract = taskState.buildContract && typeof taskState.buildContract === "object"
          ? { ...taskState.buildContract }
          : null;
        if (!buildContract) {
          const requiredArgs = required.args && typeof required.args === "object" ? required.args : {};
          const canonicalPlanArgs = {
            project: String(requiredArgs.project || taskState.projectFile || ""),
            allowAbsoluteProject: true,
            allowEngineFallback: false,
          };
          if (String(requiredArgs.engineRoot || "").trim()) {
            canonicalPlanArgs.engineRoot = String(requiredArgs.engineRoot);
          }
          const canonicalPlan = await resolveBuildPlan(WORKSPACE_ROOT, CONFIG_PATH, canonicalPlanArgs);
          if (!canonicalPlan.ok || !canonicalPlan.build) {
            return fail(canonicalPlan.error || "Could not resolve the authoritative task build plan.", {
              errorCode: "TASK_BUILD_CONTRACT_RESOLUTION_FAILED",
              retryable: false,
              stopCurrentWorkflow: false,
            });
          }
          buildContract = {
            project: String(canonicalPlan.build.projectPath || ""),
            engineRoot: String(canonicalPlan.build.engineRoot || ""),
            target: String(canonicalPlan.build.target || ""),
            platform: String(canonicalPlan.build.platform || ""),
            configuration: String(canonicalPlan.build.configuration || ""),
            allowAbsoluteProject: true,
            allowEngineFallback: false,
          };
          const binding = bindBuildContractViaPython(WORKSPACE_ROOT, args, buildContract);
          if (binding?.ok !== true) {
            return fail(binding?.error || "Could not bind the authoritative task build contract.", {
              ...routeAuthorizationFailureOptions(binding || {}, name),
              errorCode: String(binding?.errorCode || "TASK_BUILD_CONTRACT_BIND_FAILED"),
              retryable: false,
            });
          }
        }
        // Server-owned semantic fields overwrite model extras before both
        // route authorization and execution. Optional operational fields such
        // as timeoutMs remain caller-controlled.
        args = {
          ...args,
          ...buildContract,
          taskAuthorization: nestedTaskAuthorization,
        };
      }
    }
    let routePreflight = { ok: true };
    if (SAFE_ROUTE_RECOVERY_TOOLS.has(name)) {
      routePreflight = { ok: true, controlSurface: true };
    } else if (detachedReadOnlyObservation) {
      routePreflight = { ok: true, detachedReadOnlyObservation: true };
    } else if (hasExplicitTaskAuthorization) {
      if (!ROUTE_MUTATION_TOOLS.has(name)) {
        routePreflight = authorizeTaskRouteTool(
          WORKSPACE_ROOT,
          name,
          args,
          {
            consumeBudget: false,
            activeProject: activeProjectForRoute,
          }
        );
      }
    } else {
      routePreflight = authorizeActiveRouteTool(
        WORKSPACE_ROOT,
        name,
        args,
        {
          consumeBudget: false,
          activeProject: activeProjectForRoute,
        }
      );
    }
    if (!routePreflight.ok) {
      const terminalRollback = await rollbackPendingForTerminalBlock(routePreflight, args);
      return fail(
        routePreflight.error || "Task route authorization failed.",
        {
          ...routeAuthorizationFailureOptions(routePreflight, name),
          ...(terminalRollback ? { mutationRollback: terminalRollback } : {}),
        }
      );
    }
    if (
      !hasExplicitTaskAuthorization
      && routePreflight.taskAuthorization
      && typeof routePreflight.taskAuthorization === "object"
    ) {
      args = {
        ...args,
        taskAuthorization: routePreflight.taskAuthorization,
      };
      hasExplicitTaskAuthorization = true;
    }
    durableGuardScope = durableGuardScopeForArgs(args);
    priorSeq = beginToolCall(durableGuardScope);
    if (context) {
      context.arguments = args;
      context.durableGuardScope = durableGuardScope;
    }
    const earlyRepeatBlock = checkToolRepeatBlocked(name, args, priorSeq, durableGuardScope);
    if (earlyRepeatBlock.blocked) {
      return fail(toolRepeatBlockedMessage(name, earlyRepeatBlock), {
        errorCode: "TOOL_REPEAT_BLOCKED",
        retryable: false,
        stopCurrentWorkflow: true,
        doNotRetry: [name],
        agentInstruction: "Do not retry " + name + " with the same arguments. Stop the current workflow and report the MCP internal error.",
      });
    }

    const argumentCheck = requiredArgumentCheck(
      toolDefinitions.find((tool) => tool.name === name),
      args
    );
    if (argumentCheck.invalidShape) {
      recordToolFailure(name, args, "INVALID_TOOL_ARGUMENTS", durableGuardScope);
      return fail("Tool arguments must be a JSON object.", {
        errorCode: "INVALID_TOOL_ARGUMENTS",
        requiredArguments: argumentCheck.required,
        providedArguments: argumentCheck.provided,
        retryable: true,
        stopCurrentWorkflow: false,
        agentInstruction: "Retry this same tool once with arguments encoded as a JSON object.",
      });
    }
    const missingNonAuthorizationArgs = argumentCheck.missing.filter((key) => key !== "taskAuthorization");
    if (missingNonAuthorizationArgs.length) {
      recordToolFailure(name, args, "INVALID_TOOL_ARGUMENTS", durableGuardScope);
      return fail("Missing required argument(s): " + missingNonAuthorizationArgs.join(", "), {
        errorCode: "INVALID_TOOL_ARGUMENTS",
        requiredArguments: argumentCheck.required,
        providedArguments: argumentCheck.provided,
        retryable: true,
        stopCurrentWorkflow: false,
        agentInstruction: "Retry this same tool once with the missing required arguments. Do not create a new plan.",
      });
    }

    // Validation-heavy reads and every mutation reserve before I/O.  Mutation
    // reservations commit only after disk state, semantic/static validation,
    // mutation generation, and the durable continuity checkpoint all succeed.
    const DEFER_BUDGET_UNTIL_SUCCESS = new Set([
      "read_symbol",
      "read_file",
      "read_file_range",
      "search_files",
      "list_directory",
      "write_file",
      "replace_in_file",
      "delete_file",
      "apply_edit_bundle",
    ]);
    let pendingBudgetReservation = null;
    let committedDeferredBudget = null;
    const budgetFields = requiredFields(args || {});
    const runBudgetOp = (op, reservationId = "", callMetadata = null) => {
      if (hasExplicitTaskAuthorization) {
        return op(
          WORKSPACE_ROOT,
          budgetFields.taskSessionId,
          budgetFields,
          args,
          name,
          reservationId,
          callMetadata
        );
      }
      const active = discoverActiveTaskContext(
        WORKSPACE_ROOT,
        activeProjectForRoute,
        {
          ...routeOwnershipFromArgs(args),
          requireOwnerCapability: true,
        }
      );
      if (active.status !== "active") {
        return { ok: true, legacy: true };
      }
      return op(
        WORKSPACE_ROOT,
        active.taskSessionId,
        {
          routeHash: String(active.route?.routeHash || ""),
          routePhase: String(active.route?.phase || ""),
        },
        args,
        name,
        reservationId,
        callMetadata
      );
    };
    if (
      !SAFE_ROUTE_RECOVERY_TOOLS.has(name)
      && !detachedReadOnlyObservation
      && (!ROUTE_MUTATION_TOOLS.has(name) || REQUIRE_TASK_AUTH_FOR_WRITES)
      && (hasExplicitTaskAuthorization || routePreflight.taskSessionId)
    ) {
      if (DEFER_BUDGET_UNTIL_SUCCESS.has(name)) {
        const reserved = runBudgetOp(reserveRouteCall);
        if (!reserved.ok) {
          return fail(
            reserved.error || "Task route authorization failed.",
            routeAuthorizationFailureOptions(reserved, name)
          );
        }
        if (reserved.reservationId) {
          pendingBudgetReservation = { id: String(reserved.reservationId) };
        }
      } else {
        const budgetCommit = hasExplicitTaskAuthorization
          ? authorizeTaskRouteTool(
            WORKSPACE_ROOT,
            name,
            args,
            {
              consumeBudget: true,
              activeProject: activeProjectForRoute,
            }
          )
          : authorizeActiveRouteTool(
            WORKSPACE_ROOT,
            name,
            args,
            {
              consumeBudget: true,
              activeProject: activeProjectForRoute,
            }
          );
        if (!budgetCommit.ok) {
          return fail(
            budgetCommit.error || "Task route authorization failed.",
            routeAuthorizationFailureOptions(budgetCommit, name)
          );
        }
      }
    }
    const rollbackDeferredBudget = (callMetadata = null) => {
      if (!pendingBudgetReservation) return;
      const reservationId = String(pendingBudgetReservation.id || "");
      pendingBudgetReservation = null;
      if (reservationId) {
        runBudgetOp(rollbackRouteReservation, reservationId, callMetadata);
      }
    };
    const heartbeatDeferredBudget = () => {
      if (!pendingBudgetReservation || !pendingBudgetReservation.id) return;
      runBudgetOp(heartbeatRouteReservation, String(pendingBudgetReservation.id));
    };
    const commitDeferredBudgetOrFail = (callMetadata = null) => {
      if (!pendingBudgetReservation) return null;
      const reservationId = String(pendingBudgetReservation.id || "");
      pendingBudgetReservation = null;
      const committed = runBudgetOp(commitRouteReservation, reservationId, callMetadata);
      if (!committed.ok) {
        if (reservationId) {
          runBudgetOp(rollbackRouteReservation, reservationId);
        }
        return fail(
          committed.error || "Task route authorization failed.",
          routeAuthorizationFailureOptions(committed, name)
        );
      }
      committedDeferredBudget = committed;
      return null;
    };

    try {

    if (process.env.MCP_TEST_FORCE_TOOL_ERROR === name) {
      throw new Error(`test-forced internal error for ${name}`);
    }

    if (name === "get_workspace_info") {
      return text(JSON.stringify(await buildWorkspaceInfo(), null, 2));
    }

    if (name === "list_unreal_projects") {
      const payload = await listUnrealProjects(WORKSPACE_ROOT, CONFIG_PATH, {
        maxDepth: args.maxDepth
      });
      return text(JSON.stringify(payload, null, 2));
    }

    if (name === "get_active_project") {
      const activeProject = getActiveProject(CONFIG_PATH);
      let details = null;
      let projectContext = null;
      if (activeProject) {
        const selection = await resolveProjectSelection(WORKSPACE_ROOT, CONFIG_PATH, {
          hint: activeProject
        });
        details = selection.selected;
        projectContext = {
          ok: true,
          ...buildProjectBrowsePaths(activeProject, WORKSPACE_ROOT)
        };
      } else {
        projectContext = {
          ok: false,
          error: "activeProject is not set.",
          browseAvailable: false,
          requiredNextTool: { server: "unreal-rag", name: "unreal_set_active_project" },
          suggestedToolCalls: [{ tool: "unreal_set_active_project", args: {} }]
        };
      }
      return text(JSON.stringify({
        activeProject,
        details,
        projectContext,
        sourceEvidence: sourceEvidenceSummary(activeProject)
      }, null, 2));
    }

    if (name === "list_active_tasks") {
      return text(JSON.stringify(
        listActiveTasks(
          WORKSPACE_ROOT,
          getActiveProject(CONFIG_PATH) || "",
          routeOwnershipFromArgs(args)
        ),
        null,
        2
      ));
    }

    if (name === "cancel_active_task") {
      const ownership = routeOwnershipFromArgs(args);
      const payload = cancelActiveTask(
        WORKSPACE_ROOT,
        getActiveProject(CONFIG_PATH) || "",
        String(args.taskSessionId || ownership.taskSessionId || ""),
        args.force === true,
        ownership
      );
      if (payload.ok) {
        try {
          await server.sendToolListChanged();
        } catch {
          // Older clients may not accept list-changed notifications.
        }
      }
      return text(JSON.stringify(payload, null, 2));
    }

    if (name === "quarantine_corrupt_task") {
      const payload = quarantineCorruptTask(
        WORKSPACE_ROOT,
        getActiveProject(CONFIG_PATH) || "",
        String(args.taskSessionId || "")
      );
      if (payload.ok) {
        try {
          await server.sendToolListChanged();
        } catch {
          // Older clients may not accept list-changed notifications.
        }
      }
      return text(JSON.stringify(payload, null, 2));
    }

    if (name === "set_active_project") {
      if (!ALLOW_WRITE) {
        return fail("set_active_project blocked. Set ALLOW_WRITE=1 to update config/agent-mcp.json.");
      }
      const result = await setActiveProject(WORKSPACE_ROOT, CONFIG_PATH, {
        projectPath: args.projectPath,
        hint: args.hint,
        clear: args.clear === true
      });
      invalidateWorkspaceInfoCache();
      clearLoopHistoriesOnProjectChange(true);
      return text(JSON.stringify(result, null, 2));
    }

    if (name === "open_active_project_picker") {
      return text(JSON.stringify(launchProjectPicker(args.explorer === true), null, 2));
    }

    if (name === "refactor_impact_scan") {
      const payload = await scanSymbolImpact(WORKSPACE_ROOT, CONFIG_PATH, {
        symbol: args.symbol,
        hint: args.hint,
        maxFiles: args.maxFiles
      });
      return text(JSON.stringify(payload, null, 2));
    }

    if (name === "refactor_plan_validate") {
      const payload = validateRefactorPlan(args.stage, args.planText);
      return text(JSON.stringify(payload, null, 2));
    }

    if (name === "detect_unreal_project") {
      const resolveBuildDefaults = args.resolveBuildDefaults !== false;
      const selection = await resolveProjectSelection(WORKSPACE_ROOT, CONFIG_PATH, {
        hint: args.hint
      });

      const payload = {
        selectionReason: selection.selectionReason,
        searchRoots: selection.roots,
        selected: selection.selected
          ? {
            projectFile: selection.selected.projectFile,
            projectPath: selection.selected.projectPath,
            projectDir: selection.selected.projectDir,
            projectName: selection.selected.projectName,
            preferredTarget: selection.selected.preferredTarget,
            allTargets: selection.selected.allTargets,
            engineAssociation: selection.selected.engineAssociation,
            modifiedAt: selection.selected.modifiedAt
          }
          : null,
        candidates: selection.projects.slice(0, 12).map((p) => ({
          projectFile: p.projectFile,
          projectPath: p.projectPath,
          preferredTarget: p.preferredTarget,
          score: p.score || 0,
          modifiedAt: p.modifiedAt
        })),
        error: selection.error || null,
        suggestions: selection.suggestions || null
      };

      if (resolveBuildDefaults) {
        const plan = await resolveBuildPlan(WORKSPACE_ROOT, CONFIG_PATH, { hint: args.hint });
        payload.buildDefaults = plan.ok ? plan.build : null;
        payload.buildError = plan.ok ? null : plan.error || null;
      }

      return text(JSON.stringify(payload, null, 2));
    }

    if (name === "list_directory") {
      const resolution = await resolveReadToolPath(args.path || ".");
      const target = resolution.absolutePath;
      const inspectionPolicy = inspectionReadPolicy(args);
      const maxEntries = Math.max(1, Math.min(
        Number(args.maxEntries || 200),
        inspectionPolicy?.maxDirectoryEntries || 1000,
        1000,
      ));
      const relative = String(
        resolution.projectRelativePath
        || resolution.workspaceRelativePath
        || args.path
        || "."
      ).replace(/\\/g, "/").replace(/^\.\/+/, "").replace(/\/+$/, "");
      const activeProject = getActiveProject(CONFIG_PATH);
      const conversationScope = String(args.sessionId || "").trim();
      const taskScope = String(requiredFields(args).taskSessionId || "").trim();
      const budgetScope = `${taskScope || conversationScope || "unscoped"}\u0000${String(activeProject || WORKSPACE_ROOT || "workspace")}`;
      const budgetCheck = LIST_DIRECTORY_BUDGET.check(budgetScope, relative || ".");
      if (!budgetCheck.ok) {
        return fail(
          `list_directory blocked (${budgetCheck.errorCode}) for path=${budgetCheck.path}`,
          {
            errorCode: budgetCheck.errorCode,
            retryable: false,
            stopCurrentWorkflow: false,
            path: pathMetadata(resolution),
            calls: budgetCheck.calls,
            maxCallsPerWindow: budgetCheck.maxCallsPerWindow,
            pathCount: budgetCheck.pathCount,
            maxCallsPerPath: budgetCheck.maxCallsPerPath,
            budgetOwner: budgetCheck.budgetOwner,
            budgetKind: budgetCheck.budgetKind,
            budgetPersistence: budgetCheck.persistence,
            budgetResetRule: budgetCheck.resetRule,
            budgetResumeAction: budgetCheck.resumeAction,
            budgetScopeId: taskScope || conversationScope || "unscoped",
            agentInstruction: budgetCheck.agentInstruction,
            nextSteps: [
              "Call search_files with a focused query under this path.",
              "Or read specific files you already know about.",
            ],
          }
        );
      }
      const s = await statSafe(target);
      if (!s) return fail(`not found: ${args.path}`, { path: pathMetadata(resolution) });
      if (!s.isDirectory()) return fail(`not a directory: ${args.path}`, { path: pathMetadata(resolution) });

      const entries = await fsp.readdir(target, { withFileTypes: true });
      const rows = [];
      for (const e of entries.slice(0, maxEntries)) {
        const child = path.join(target, e.name);
        await assertReadChildContained(child, resolution);
        const st = await statSafe(child);
        rows.push({
          name: e.name,
          type: e.isDirectory() ? "dir" : e.isFile() ? "file" : "other",
          size: st ? st.size : null,
          modified: st ? st.mtime.toISOString() : null
        });
      }
      LIST_DIRECTORY_BUDGET.commit(budgetScope, relative || ".");
      const budgetFail = commitDeferredBudgetOrFail({
        inspectionDirectoryList: { entryCount: rows.length },
        inspectionDiscoveryCandidates: {
          paths: rows.filter((entry) => entry.type === "file")
            .map((entry) => `${relative}/${entry.name}`.replace(/^\/+/, "")),
        },
      });
      if (budgetFail) return budgetFail;
      return attachCommittedToolOutcomeControl(
        text(JSON.stringify({ path: pathMetadata(resolution), entries: rows }, null, 2)),
        committedDeferredBudget,
        "list_directory"
      );
    }

    if (name === "read_unreal_logs") {
      const logTaskSessionId = requiredFields(args).taskSessionId;
      const logTaskState = logTaskSessionId
        ? readTaskState(WORKSPACE_ROOT, logTaskSessionId)
        : null;
      const activeProject = authoritativeTaskProjectFile(logTaskState, WORKSPACE_ROOT)
        || getActiveProject(CONFIG_PATH);
      if (!activeProject) {
        const switchGuidance = projectSwitchGuidance(agentRegisteredToolNames());
        return fail(
          switchGuidance.requiredNextTool
            ? "activeProject is not set. Use unreal_set_active_project on unreal-rag first."
            : "activeProject is not set. Use set_active_project first.",
          {
            nextSteps: ["Select the target .uproject, then read logs again."],
            ...switchGuidance
          }
        );
      }
      const projectDir = path.dirname(path.resolve(activeProject));
      const candidateLogDirs = [
        path.join(projectDir, ".agent", "logs"),
        path.join(projectDir, "Saved", "Logs"),
      ];
      const logsDirs = [];
      for (const candidate of candidateLogDirs) {
        if (await exists(candidate)) logsDirs.push(candidate);
      }
      const maxLines = Math.max(20, Math.min(Number(args.maxLines || 60), 500));
      const maxFiles = Math.max(1, Math.min(Number(args.maxFiles || 1), 3));
      const summaryOnly = args.summaryOnly !== false;
      const readMode = ["tail", "first_error", "range"].includes(String(args.mode || "").toLowerCase())
        ? String(args.mode).toLowerCase()
        : "tail";
      const rangeCursorByte = Math.max(0, Math.trunc(Number(args.cursorByte || 0)));
      const requestedChunkBytes = Math.min(
        Math.trunc(Number(args.maxBytes || LOG_READ_MAX_BYTES)),
        LOG_READ_MAX_BYTES,
      );
      const chunkBytes = Math.max(
        readMode === "range" ? 1024 : 64 * 1024,
        requestedChunkBytes,
      );
      const filterText = String(args.filter || "").toLowerCase();
      const requestedLogFile = String(args.fileName || "").trim();
      if (
        requestedLogFile
        && (
          path.basename(requestedLogFile) !== requestedLogFile
          || !requestedLogFile.toLowerCase().endsWith(".log")
        )
      ) {
        return fail("fileName must be one exact .log basename without directory segments.", {
          errorCode: "LOG_FILENAME_INVALID",
          retryable: true,
        });
      }
      const observedRecoveryLogArgs = {
        mode: readMode,
        maxFiles,
        maxLines,
        summaryOnly,
        ...(requestedLogFile ? { fileName: requestedLogFile } : {}),
        ...(readMode === "range" ? {
          cursorByte: rangeCursorByte,
          maxBytes: chunkBytes,
        } : {}),
        ...(filterText ? { filter: String(args.filter || "") } : {}),
      };
      const recoveryFields = requiredFields(args);
      const recoveryTaskState = recoveryFields.taskSessionId
        ? readTaskState(WORKSPACE_ROOT, recoveryFields.taskSessionId)
        : null;
      const recoveryLogObligation = exactRecoveryLogObligation(
        recoveryTaskState,
        observedRecoveryLogArgs
      );
      if (logsDirs.length === 0) {
        if (recoveryLogObligation.matched) {
          const priorRecovery = recoveryLogObligation.recovery;
          const gate = recoveryGateAfterMissingDiagnostic(
            recoveryTaskState,
            priorRecovery,
            args
          );
          const blocker = recordRecoveryObligationViaPython(WORKSPACE_ROOT, args, {
            source: recoveryLogSource(priorRecovery, requestedLogFile),
            status: gate.status,
            scopeDisposition: "infrastructure",
            errorCode: "RECOVERY_LOG_UNAVAILABLE",
            mutationGeneration: Number(
              priorRecovery.mutationGeneration || recoveryTaskState?.mutationGeneration || 0
            ),
            requiredTool: gate.requiredTool,
            targetFiles: Array.isArray(priorRecovery.targetFiles) ? priorRecovery.targetFiles : [],
            message: `The required recovery log directory is unavailable under: ${projectDir}`,
          });
          const payload = {
            ok: false,
            errorCode: "RECOVERY_LOG_UNAVAILABLE",
            retryable: true,
            doNotRetry: ["read_unreal_logs"],
            requiredNextTool: gate.requiredTool.name,
            requiredNextToolArgs: gate.requiredTool.args,
            projectDir,
            logsDirs: candidateLogDirs,
            nextSteps: [
              `Call ${gate.requiredTool.name} exactly once to regenerate or rebase the missing diagnostic evidence.`,
            ],
          };
          bindAuthoritativeLifecycleControl(payload, blocker);
          return fail(`Required recovery log directories are unavailable under: ${projectDir}`, payload);
        }
        return fail(`logs directories not found under: ${projectDir}`, {
          nextSteps: ["Run build_unreal_project or launch the project once to create a log."]
        });
      }
      const logFiles = [];
      for (const logsDir of logsDirs) {
        let entries;
        try {
          entries = await fsp.readdir(logsDir, { withFileTypes: true });
        } catch (error) {
          if (error && ["ENOENT", "EACCES", "EPERM"].includes(error.code)) continue;
          throw error;
        }
        for (const entry of entries) {
          if (!entry.isFile() || !entry.name.toLowerCase().endsWith(".log")) continue;
          if (requestedLogFile && entry.name !== requestedLogFile) continue;
          const logPath = path.join(logsDir, entry.name);
          const logStat = await statSafe(logPath);
          if (logStat?.isFile()) {
            logFiles.push({ path: logPath, mtimeMs: logStat.mtimeMs });
          }
        }
      }
      logFiles.sort((a, b) => b.mtimeMs - a.mtimeMs);
      const picked = logFiles.slice(0, maxFiles);
      const chunks = [];
      let skippedLogReadCount = 0;
      for (const logEntry of picked) {
        const logPath = logEntry.path;
        let bounded;
        let firstErrorFound = null;
        let scanTruncated = false;
        try {
          if (readMode === "range") {
            bounded = await readUtf8Range(
              logPath,
              rangeCursorByte,
              chunkBytes,
              {
                preservePartialLeading: true,
                maxLines,
              },
            );
          } else if (readMode === "first_error") {
            let cursor = 0;
            let bytesScanned = 0;
            let priorLines = [];
            let partialLine = "";
            let foundLines = [];
            let sourceBytes = 0;
            let nextCursorByte = 0;
            while (bytesScanned < LOG_FIRST_ERROR_SCAN_MAX_BYTES) {
              const scan = await readUtf8Range(
                logPath,
                cursor,
                chunkBytes,
                { preservePartialLeading: true },
              );
              sourceBytes = scan.sourceBytes;
              nextCursorByte = scan.nextCursorByte;
              bytesScanned += scan.bytesRead;
              const currentLines = scan.content.split(/\r?\n/);
              currentLines[0] = `${partialLine}${currentLines[0] || ""}`;
              partialLine = scan.hasMore ? (currentLines.pop() || "") : "";
              const combined = [...priorLines, ...currentLines];
              const matchIndex = combined.findIndex((line) => (
                filterText
                  ? String(line).toLowerCase().includes(filterText)
                  : isInterestingLogLine(line)
              ));
              if (matchIndex >= 0) {
                const start = Math.max(0, matchIndex - 4);
                foundLines = combined.slice(start, Math.min(combined.length, matchIndex + 5));
                firstErrorFound = true;
                break;
              }
              priorLines = combined.slice(-4);
              cursor = scan.nextCursorByte;
              if (!scan.hasMore || scan.bytesRead <= 0) break;
            }
            scanTruncated = nextCursorByte < sourceBytes;
            if (firstErrorFound !== true) {
              firstErrorFound = false;
              foundLines = priorLines;
            }
            bounded = {
              content: foundLines.join("\n"),
              sourceBytes,
              bytesRead: bytesScanned,
              requestedStartByte: 0,
              contentStartByte: 0,
              nextCursorByte,
              hasMore: scanTruncated,
              sourceTruncated: scanTruncated,
            };
          } else {
            bounded = await readUtf8Tail(logPath, chunkBytes);
          }
        } catch (error) {
          if (error && ["ENOENT", "EACCES", "EPERM"].includes(error.code)) {
            skippedLogReadCount += 1;
            continue;
          }
          throw error;
        }
        const lines = bounded.content.split(/\r?\n/);
        if (
          readMode === "range"
          && lines.length > 1
          && lines[lines.length - 1] === ""
          && /\r?\n$/u.test(bounded.content)
        ) {
          lines.pop();
        }
        let filtered = filterText
          ? lines.filter((line) => line.toLowerCase().includes(filterText))
          : lines;
        if (readMode === "first_error") {
          filtered = lines.slice(0, maxLines);
        } else if (readMode === "range") {
          filtered = filtered.slice(0, maxLines);
          firstErrorFound = filtered.some((line) => (
            filterText
              ? String(line).toLowerCase().includes(filterText)
              : isInterestingLogLine(line)
          ));
        } else if (summaryOnly) {
          filtered = firstErrorCluster(filtered, 4, 30);
        } else {
          filtered = filtered.slice(-maxLines);
        }
        chunks.push({
          file: path.relative(projectDir, logPath).replace(/\\/g, "/"),
          lineCount: filtered.length,
          lines: filtered,
          sourceBytes: bounded.sourceBytes,
          bytesRead: bounded.bytesRead,
          bytesReturned: bounded.bytesReturned ?? bounded.bytesRead,
          sourceTruncated: bounded.sourceTruncated,
          mode: readMode,
          cursorByte: bounded.requestedStartByte ?? null,
          contentStartByte: bounded.contentStartByte ?? null,
          contentEndByte: bounded.contentEndByte ?? bounded.nextCursorByte ?? null,
          nextCursorByte: bounded.nextCursorByte ?? null,
          hasMore: Boolean(bounded.hasMore),
          lineLimited: Boolean(bounded.lineLimited),
          firstErrorFound,
          scanTruncated,
        });
      }
      const truncatedSourceCount = chunks.filter((chunk) => chunk.sourceTruncated).length;
      const firstLine = chunks.flatMap((chunk) => chunk.lines).find((line) => String(line).trim()) || "";
      const payload = compactLogPayload({
        summary: chunks.length
          ? `LOGS READY — ${chunks.length} file(s), ${chunks.reduce((n, chunk) => n + chunk.lineCount, 0)} line(s)${firstLine ? `; first: ${firstLine}` : ""}`
          : "NO LOG FILES — project .agent/logs and Saved/Logs contain no .log files.",
        ok: chunks.length > 0,
        projectDir,
        logsDirs,
        responseMode: readMode === "tail" && summaryOnly ? "summary" : readMode,
        logReadMaxBytes: LOG_READ_MAX_BYTES,
        firstErrorScanMaxBytes: LOG_FIRST_ERROR_SCAN_MAX_BYTES,
        truncatedSourceCount,
        skippedLogReadCount,
        sourceReadNote: truncatedSourceCount
          ? readMode === "tail"
            ? "Only the bounded tail was scanned. Retry with mode=first_error for the original failure or mode=range with cursorByte for precise traversal."
            : "The bounded scan/range did not cover the full source. Continue from nextCursorByte when hasMore=true."
          : null,
        suggestedRagMode: filterText.includes("error") || filterText.includes("fatal")
          ? "compile_fix"
          : "runtime_debug",
        logs: chunks,
        requestedLogFile: requestedLogFile || null,
        nextSteps: chunks.length
          ? ["Use only the first actionable error or assertion for the next fix."]
          : ["Run the project or build once, then read logs again."]
      });
      const recoveryLogEvidenceSatisfied = !["first_error", "range"].includes(readMode)
        || chunks.some((chunk) => chunk.firstErrorFound === true);
      if (payload.ok === true && recoveryFields.taskSessionId && recoveryLogEvidenceSatisfied) {
        const recoveryEvidence = markRecoveryEvidenceViaPython(
          WORKSPACE_ROOT,
          args,
          "read_unreal_logs",
          observedRecoveryLogArgs,
          sha256Text(JSON.stringify(payload.logs || []))
        );
        if (recoveryEvidence?.ok === false) {
          payload.ok = false;
          payload.errorCode = String(
            recoveryEvidence.errorCode || "RECOVERY_EVIDENCE_COMMIT_FAILED"
          );
          payload.error = String(
            recoveryEvidence.error || "The task recovery evidence could not be committed."
          );
          payload.retryable = false;
          payload.doNotRetry = ["read_unreal_logs"];
          payload.recoveryEvidence = {
            ok: false,
            active: recoveryEvidence?.active === true,
            errorCode: payload.errorCode,
          };
          bindAuthoritativeLifecycleControl(payload, recoveryEvidence);
          return fail("Recovery log evidence was read but could not be committed.", payload);
        }
        if (recoveryEvidence?.active === true || recoveryEvidence?.ok === false) {
          payload.recoveryEvidence = {
            ok: recoveryEvidence?.ok === true,
            active: recoveryEvidence?.active === true,
            errorCode: String(recoveryEvidence?.errorCode || ""),
          };
          bindAuthoritativeLifecycleControl(payload, recoveryEvidence);
        }
      } else if (
        payload.ok === true
        && ["first_error", "range"].includes(readMode)
        && recoveryLogObligation.matched
      ) {
        const priorRecovery = recoveryLogObligation.recovery;
        const continuation = chunks.find((chunk) => (
          chunk.firstErrorFound !== true
          && chunk.hasMore === true
          && Number.isFinite(Number(chunk.nextCursorByte))
        ));
        const nextRangeArgs = continuation
          ? {
            mode: "range",
            ...(requestedLogFile ? { fileName: requestedLogFile } : {}),
            cursorByte: Number(continuation.nextCursorByte),
            maxBytes: chunkBytes,
            maxFiles: 1,
            maxLines,
            summaryOnly: false,
            ...(filterText ? { filter: String(args.filter || "") } : {}),
          }
          : null;
        const gate = nextRangeArgs
          ? {
            status: "evidence_required",
            requiredTool: { name: "read_unreal_logs", args: nextRangeArgs },
          }
          : recoveryGateAfterMissingDiagnostic(recoveryTaskState, priorRecovery, args);
        const blocker = recordRecoveryObligationViaPython(WORKSPACE_ROOT, args, {
          source: recoveryLogSource(priorRecovery, requestedLogFile),
          status: gate.status,
          scopeDisposition: nextRangeArgs ? "in_slice" : "infrastructure",
          errorCode: nextRangeArgs
            ? "RECOVERY_LOG_SCAN_CONTINUATION_REQUIRED"
            : "RECOVERY_ERROR_EVIDENCE_NOT_FOUND",
          mutationGeneration: Number(
            priorRecovery.mutationGeneration || recoveryTaskState?.mutationGeneration || 0
          ),
          requiredTool: gate.requiredTool,
          targetFiles: Array.isArray(priorRecovery.targetFiles) ? priorRecovery.targetFiles : [],
          message: nextRangeArgs
            ? "The bounded log window had no actionable error; continue from the exact persisted byte cursor."
            : "The complete authoritative log scan had no actionable error; rerun the owning gate once to regenerate evidence.",
        });
        payload.ok = false;
        payload.errorCode = nextRangeArgs
          ? "RECOVERY_LOG_SCAN_CONTINUATION_REQUIRED"
          : "RECOVERY_ERROR_EVIDENCE_NOT_FOUND";
        payload.retryable = true;
        payload.doNotRetry = ["read_unreal_logs"];
        payload.requiredNextTool = gate.requiredTool.name;
        payload.requiredNextToolArgs = gate.requiredTool.args;
        payload.recoveryEvidence = {
          ok: false,
          active: blocker?.active === true,
          errorCode: payload.errorCode,
        };
        payload.nextSteps = [
          nextRangeArgs
            ? "Continue from requiredNextToolArgs.cursorByte; do not restart the scan from byte zero."
            : `Call ${gate.requiredTool.name} exactly once; do not plan a speculative source edit.`,
        ];
        bindAuthoritativeLifecycleControl(payload, blocker);
        return fail(
          nextRangeArgs
            ? "No actionable error was found in this bounded log window; continue from the persisted cursor."
            : "The complete recovery log contains no actionable error; the owning gate must regenerate evidence.",
          payload
        );
      } else if (payload.ok !== true && recoveryLogObligation.matched) {
        const priorRecovery = recoveryLogObligation.recovery;
        const gate = recoveryGateAfterMissingDiagnostic(
          recoveryTaskState,
          priorRecovery,
          args
        );
        const blocker = recordRecoveryObligationViaPython(WORKSPACE_ROOT, args, {
          source: recoveryLogSource(priorRecovery, requestedLogFile),
          status: gate.status,
          scopeDisposition: "infrastructure",
          errorCode: "RECOVERY_LOG_UNAVAILABLE",
          mutationGeneration: Number(
            priorRecovery.mutationGeneration || recoveryTaskState?.mutationGeneration || 0
          ),
          requiredTool: gate.requiredTool,
          targetFiles: Array.isArray(priorRecovery.targetFiles) ? priorRecovery.targetFiles : [],
          message: `The required recovery log is unavailable: ${requestedLogFile || "<latest>"}`,
        });
        payload.ok = false;
        payload.errorCode = "RECOVERY_LOG_UNAVAILABLE";
        payload.retryable = true;
        payload.doNotRetry = ["read_unreal_logs"];
        payload.requiredNextTool = gate.requiredTool.name;
        payload.requiredNextToolArgs = gate.requiredTool.args;
        bindAuthoritativeLifecycleControl(payload, blocker);
        return fail(
          `Required recovery log is unavailable: ${requestedLogFile || "latest project log"}`,
          payload
        );
      }
      return text(JSON.stringify(payload, null, 2));
    }

    if (name === "write_session_handoff") {
      const handoff = formatSessionHandoff(args);
      const artifactPath = await writeTextArtifact(
        WORKSPACE_ROOT,
        path.join(".agent", "handoff", "latest.md"),
        handoff
      );
      return text(JSON.stringify({
        summary: `HANDOFF SAVED — ${artifactPath}`,
        ok: true,
        artifactPath,
        writeMode: "artifact_only",
        overwritten: true,
        safeModeAllowed: true,
        note: "Writes only the fixed .agent/handoff/latest.md artifact under WORKSPACE_ROOT. Project source files are never modified.",
        lineCount: handoff.trimEnd().split(/\r?\n/).length,
        nextSteps: [
          "Start a fresh LM Studio chat.",
          "Paste prompts/lmstudio_session_bootstrap.md.",
          `Ask the model to read ${artifactPath} and continue from the smallest next step.`
        ]
      }, null, 2));
    }

    if (name === "record_bootstrap_step") {
      const step = String(args.step || "").trim();
      const allowed = new Set(["unreal_get_active_project", "unreal_rag_health", "get_workspace_info"]);
      if (!allowed.has(step)) {
        return fail(`unsupported bootstrap step: ${step}`, {
          nextSteps: [`Use one of: ${Array.from(allowed).join(", ")}`],
        });
      }
      const cache = await writeBootstrapCache({
        projectPath: args.projectPath || getActiveProject(CONFIG_PATH) || "",
        stepsCompleted: [step],
        ragHealthOk: args.ragHealthOk === true ? true : undefined,
      });
      const bootstrapCache = evaluateBootstrapCache(cache, getActiveProject(CONFIG_PATH));
      return text(JSON.stringify({
        summary: `BOOTSTRAP STEP RECORDED — ${step}`,
        ok: true,
        step,
        bootstrapCache,
      }, null, 2));
    }

    if (name === "read_file") {
      const resolution = await resolveReadToolPath(args.path);
      const target = resolution.absolutePath;
      const s = await statSafe(target);
      if (!s) {
        rollbackDeferredBudget({
          absentEvidence: {
            projectRelativePath: resolution.projectRelativePath || String(args.path || ""),
            query: path.basename(String(args.path || "")),
            scopePath: displayPath(resolution),
            searchComplete: false,
          },
        });
        return fail(
          `not found: ${args.path}`,
          {
            ...missingReadTargetRecovery("read_file", args.path, resolution.resolvedRootType),
            nextSteps: ["Search for the basename inside the active project before guessing a new path."],
            suggestedToolCalls: [{
              tool: "search_files",
              args: {
                query: path.basename(String(args.path || "")),
                path: resolution.resolvedRootType === "active_project" ? "project://Source" : "workspace://",
              },
            }],
          }
        );
      }
      if (!s.isFile()) return fail(`not a file: ${args.path}`, {
        path: pathMetadata(resolution),
        suggestedToolCalls: [{ tool: "list_directory", args: { path: displayPath(resolution) } }]
      });

      const mutationGeneration = await resolveMutationGenerationForRead(resolution, target);
      const readContext = buildReadEvidenceContext(target, s, resolution, {
        mutationGeneration,
        taskSessionId: requiredFields(args).taskSessionId,
        evidenceSessionId: args.sessionId,
        taskAuthorization: args.taskAuthorization,
        detachedReadOnlyObservation,
      });
      const guard = prepareReadGuard("read_file", args, readContext);
      const blocked = applyReadGuard("read_file", guard, readContext);
      if (blocked) return blocked;
      const recoveryBlocked = applyBuildRecoveryEvidenceGuard("read_file", readContext, args);
      if (recoveryBlocked) return recoveryBlocked;

      const detail = resolveCodeDetail(args.detailLevel);
      const tierCap = CODE_DETAIL_READ_BYTES[detail];
      const inspectionPolicy = inspectionReadPolicy(args);
      const maxBytes = Math.max(
        1,
        Math.min(
          Number(args.maxBytes || tierCap),
          tierCap,
          MAX_READ_BYTES,
          inspectionPolicy?.maxBytesPerRead || MAX_READ_BYTES,
        )
      );
      const buffer = await readLeadingFileBuffer(target, s, maxBytes);
      if (!isTextLikely(buffer)) return fail(`file appears binary: ${args.path}`);
      const hasCRLF = buffer.includes(Buffer.from("\r\n"));
      // Normalize line endings so model's copy-paste into oldText matches replace_in_file
      const rawOut = buffer.toString("utf8").replace(/\r\n/g, "\n");
      const truncated = truncateTextAtNewline(rawOut, s.size, buffer.length, detail);
      let out = truncated.body;
      if (hasCRLF) {
        out = `[line-endings: CRLF — replace_in_file normalizes automatically]\n` + out;
      }
      out += truncated.footer;
      const metadataHeader = `[path-metadata: ${JSON.stringify(pathMetadata(resolution))}]\n`
        + (truncated.meta.truncated ? `[read-truncation: ${JSON.stringify(truncated.meta)}]\n` : "");
      const output = metadataHeader + out;
      // Finish all I/O (including full-file evidence hash) before committing budget.
      const contentHash = sha256Buffer(await fsp.readFile(target));
      const directSourceEvidence = {
        projectRelativePath: resolution.projectRelativePath,
        contentHash,
        fileSignature: fileStatSignature(s),
        mutationGeneration,
        lineRange: `1-${truncated.endLine}`,
        lineCount: truncated.meta.truncated ? 0 : truncated.endLine,
        characterCount: rawOut.length,
        bytesReturned: buffer.length,
        detailLevel: detail,
        truncated: truncated.meta.truncated === true,
        wholeFileComplete: truncated.meta.truncated !== true,
        completeRead: truncated.meta.truncated !== true,
        nextUnreadLine: truncated.meta.nextStartLine,
        semanticAnchors: summarizeCachedRead(rawOut).semanticAnchors,
        includePaths: Array.from(rawOut.matchAll(/^\s*#\s*include\s*[<"]([^>"]+)[>"]/gmu))
          .map((match) => String(match[1] || "").replace(/\\/gu, "/"))
          .filter(Boolean)
          .slice(0, 32),
        supportingExcerpt: {
          startLine: 1,
          endLine: Math.min(
            truncated.endLine,
            Math.max(1, rawOut.slice(0, 4000).split("\n").length),
          ),
          text: rawOut.slice(0, 4000),
        },
      };
      const budgetFail = commitDeferredBudgetOrFail({
        directSourceEvidence,
      });
      if (budgetFail) return budgetFail;
      rememberReadEvidence(
        target,
        s,
        resolution,
        `1-${truncated.endLine}`,
        contentHash
      );
      recordReadSuccess("read_file", guard.normalizedArgs, {
        ...readContext,
        contentHash,
        evidenceHash: sha256Text(out),
      }, output, {
        lineRange: { start: 1, end: truncated.endLine },
        lineCount: directSourceEvidence.lineCount,
        bytesReturned: buffer.length,
        detailLevel: detail,
        truncated: directSourceEvidence.truncated,
        wholeFileComplete: directSourceEvidence.wholeFileComplete,
        nextUnreadLine: directSourceEvidence.nextUnreadLine,
        semanticAnchors: directSourceEvidence.semanticAnchors,
      });
      commitBuildRecoveryEvidence("read_file", readContext, args);
      return attachCommittedToolOutcomeControl(
        text(output),
        committedDeferredBudget,
        "read_file"
      );
    }

    if (name === "read_file_range") {
      const resolution = await resolveReadToolPath(args.path);
      const target = resolution.absolutePath;
      const s = await statSafe(target);
      if (!s) {
        rollbackDeferredBudget({
          absentEvidence: {
            projectRelativePath: resolution.projectRelativePath || String(args.path || ""),
            query: path.basename(String(args.path || "")),
            scopePath: displayPath(resolution),
            searchComplete: false,
          },
        });
        return fail(
          `not found: ${args.path}`,
          missingReadTargetRecovery("read_file_range", args.path, resolution.resolvedRootType)
        );
      }
      if (!s.isFile()) return fail(`not a file: ${args.path}`);

      const mutationGeneration = await resolveMutationGenerationForRead(resolution, target);
      const readContext = buildReadEvidenceContext(target, s, resolution, {
        mutationGeneration,
        taskSessionId: requiredFields(args).taskSessionId,
        evidenceSessionId: args.sessionId,
        taskAuthorization: args.taskAuthorization,
        detachedReadOnlyObservation,
      });
      const guard = prepareReadGuard("read_file_range", args, readContext);
      const blocked = applyReadGuard("read_file_range", guard, readContext);
      if (blocked) return blocked;
      const recoveryBlocked = applyBuildRecoveryEvidenceGuard("read_file_range", readContext, args);
      if (recoveryBlocked) return recoveryBlocked;
      const normalizedArgs = guard.normalizedArgs;

      const detail = resolveCodeDetail(args.detailLevel);
      const inspectionPolicy = inspectionReadPolicy(args);
      const lineCap = Math.min(
        CODE_DETAIL_LINE_CAP[detail],
        inspectionPolicy?.maxLinesPerRead || CODE_DETAIL_LINE_CAP[detail],
      );
      const startLine = Math.max(1, Number(args.startLine || 1));
      let endLine = Math.max(startLine, Number(args.endLine || startLine));
      if (!Number.isFinite(startLine) || !Number.isFinite(endLine)) {
        return fail("startLine and endLine must be numbers");
      }
      if (endLine - startLine + 1 > lineCap) {
        endLine = startLine + lineCap - 1;
      }
      if (endLine - startLine > 2000) {
        return fail("line range too large (max 2000 lines per request)");
      }

      let content = "";
      try {
        content = await readCachedTextFile(target, s);
      } catch (err) {
        if (err && err.code === "BINARY_FILE") return fail(`file appears binary: ${args.path}`);
        throw err;
      }
      const lines = content.split(/\r?\n/);
      const slice = lines.slice(startLine - 1, endLine);
      const charCap = inspectionPolicy?.maxCharactersPerRead || Number.POSITIVE_INFINITY;
      const delivered = [];
      let deliveredCharacters = 0;
      for (let idx = 0; idx < slice.length; idx += 1) {
        const row = `${startLine + idx}|${slice[idx]}`;
        const added = row.length + (delivered.length > 0 ? 1 : 0);
        if (deliveredCharacters + added > charCap) break;
        delivered.push(row);
        deliveredCharacters += added;
      }
      const completeEndLine = delivered.length > 0 ? startLine + delivered.length - 1 : startLine - 1;
      const rangeTruncated = delivered.length < slice.length;
      const numbered = delivered.length > 0
        ? delivered.join("\n")
        : `${startLine}|${String(slice[0] || "").slice(0, Math.max(0, charCap - String(startLine).length - 1))}`;
      const output = `File: ${displayPath(resolution)}\nPath-Metadata: ${JSON.stringify(pathMetadata(resolution))}\nLines: ${startLine}-${Math.max(startLine, completeEndLine)} of ${lines.length}`
        + (rangeTruncated ? `\n[read-truncation: character budget ${charCap}; last line is not evidence-complete]` : "")
        + `\n\n${numbered}`;
      const contentHash = sha256Text(content);
      const wholeFileComplete = startLine === 1
        && completeEndLine >= lines.length
        && rangeTruncated !== true;
      const directSourceEvidence = {
        projectRelativePath: resolution.projectRelativePath,
        contentHash,
        fileSignature: fileStatSignature(s),
        mutationGeneration,
        lineRange: completeEndLine >= startLine ? `${startLine}-${completeEndLine}` : "0-0",
        lineCount: lines.length,
        characterCount: numbered.length,
        bytesReturned: Buffer.byteLength(numbered, "utf8"),
        detailLevel: detail,
        truncated: rangeTruncated,
        wholeFileComplete,
        completeRead: wholeFileComplete,
        nextUnreadLine: completeEndLine >= startLine ? completeEndLine + 1 : startLine,
        includePaths: Array.from(content.matchAll(/^\s*#\s*include\s*[<"]([^>"]+)[>"]/gmu))
          .map((match) => String(match[1] || "").replace(/\\/gu, "/"))
          .filter(Boolean)
          .slice(0, 32),
        supportingExcerpt: {
          startLine,
          endLine: Math.min(
            Math.max(startLine, completeEndLine),
            startLine + Math.max(0, delivered.slice(0, 120).length - 1),
          ),
          text: delivered.slice(0, 120).map((row) => row.replace(/^\d+\|/u, "")).join("\n").slice(0, 4000),
        },
      };
      const budgetFail = commitDeferredBudgetOrFail({
        directSourceEvidence,
      });
      if (budgetFail) return budgetFail;
      rememberReadEvidence(
        target,
        s,
        resolution,
        completeEndLine >= startLine ? `${startLine}-${completeEndLine}` : "0-0",
        contentHash
      );
      recordReadSuccess("read_file_range", normalizedArgs, {
        ...readContext,
        contentHash,
        evidenceHash: sha256Text(content),
      }, output, {
        lineRange: completeEndLine >= startLine
          ? { start: startLine, end: completeEndLine }
          : null,
        lineCount: lines.length,
        bytesReturned: Buffer.byteLength(numbered, "utf8"),
        detailLevel: detail,
        truncated: rangeTruncated,
        wholeFileComplete,
        nextUnreadLine: directSourceEvidence.nextUnreadLine,
      });
      commitBuildRecoveryEvidence("read_file_range", readContext, args);
      return attachCommittedToolOutcomeControl(
        text(output),
        committedDeferredBudget,
        "read_file_range"
      );
    }

    if (name === "read_symbol") {
      const resolution = await resolveReadToolPath(args.path);
      const target = resolution.absolutePath;
      const stat = await statSafe(target);
      if (!stat) {
        return fail(
          `not found: ${args.path}`,
          missingReadTargetRecovery("read_symbol", args.path, resolution.resolvedRootType)
        );
      }
      if (!stat.isFile()) return fail(`not a file: ${args.path}`);

      const mutationGeneration = await resolveMutationGenerationForRead(resolution, target);
      const readContext = buildReadEvidenceContext(target, stat, resolution, {
        mutationGeneration,
        taskSessionId: requiredFields(args).taskSessionId,
        evidenceSessionId: args.sessionId,
        taskAuthorization: args.taskAuthorization,
        detachedReadOnlyObservation,
      });
      const guard = prepareReadGuard("read_symbol", args, readContext);
      const blocked = applyReadGuard("read_symbol", guard, readContext);
      if (blocked) return blocked;
      const recoveryBlocked = applyBuildRecoveryEvidenceGuard("read_symbol", readContext, args);
      if (recoveryBlocked) return recoveryBlocked;
      const normalizedArgs = guard.normalizedArgs;

      let content;
      try { content = await readCachedTextFile(target, stat); }
      catch (err) {
        if (err && err.code === "BINARY_FILE") return fail(`file appears binary: ${args.path}`);
        throw err;
      }
      const symbol = String(args.symbol || "").trim();
      const parts = symbol.split("::");
      const leaf = parts[parts.length - 1];
      if (!leaf || !/^[A-Za-z_][A-Za-z0-9_]*$/.test(leaf)) return fail("invalid C++ symbol");
      const escapedParts = parts.map((part) => escapeRegExp(part));
      const pattern = new RegExp(`\\b${escapedParts.join("\\s*::\\s*")}\\s*\\(`, "m");
      const fallback = new RegExp(`\\b${escapeRegExp(leaf)}\\s*\\(`, "m");
      const match = pattern.exec(content) || fallback.exec(content);
      if (!match) return fail(`symbol not found: ${symbol}`, {
        suggestedToolCalls: [{ tool: "search_files", args: { query: leaf, path: "project://Source" } }]
      });
      const braceStart = content.indexOf("{", match.index + match[0].length);
      const semicolon = content.indexOf(";", match.index + match[0].length);
      if (braceStart < 0 || (semicolon >= 0 && semicolon < braceStart)) {
        return fail(`symbol body not found: ${symbol}`, {
          nextSteps: ["Search for the qualified definition in .cpp files."]
        });
      }
      let depth = 0;
      let braceEnd = -1;
      let quote = "";
      let escaped = false;
      for (let i = braceStart; i < content.length; i += 1) {
        const ch = content[i];
        if (quote) {
          if (escaped) escaped = false;
          else if (ch === "\\") escaped = true;
          else if (ch === quote) quote = "";
          continue;
        }
        if (ch === "\"" || ch === "'") { quote = ch; continue; }
        if (ch === "{") depth += 1;
        else if (ch === "}" && --depth === 0) { braceEnd = i; break; }
      }
      if (braceEnd < 0) return fail(`unbalanced symbol body: ${symbol}`);
      const lines = content.split(/\r?\n/);
      const lineAt = (offset) => content.slice(0, offset).split(/\r?\n/).length;
      const context = Math.max(0, Math.min(30, Number(args.contextLines ?? 3)));
      const startLine = Math.max(1, lineAt(match.index) - context);
      const endLine = Math.min(lines.length, lineAt(braceEnd) + context);
      const numbered = lines.slice(startLine - 1, endLine).map((line, idx) => `${startLine + idx}|${line}`).join("\n");
      const output = `File: ${displayPath(resolution)}\nSymbol: ${symbol}\nPath-Metadata: ${JSON.stringify(pathMetadata(resolution))}\nLines: ${startLine}-${endLine} of ${lines.length}\n\n${numbered}`;
      const contentHash = sha256Text(content);
      const directSourceEvidence = {
        projectRelativePath: resolution.projectRelativePath,
        contentHash,
        fileSignature: fileStatSignature(stat),
        mutationGeneration,
        lineRange: `${startLine}-${endLine}`,
        lineCount: lines.length,
        characterCount: numbered.length,
        bytesReturned: Buffer.byteLength(numbered, "utf8"),
        detailLevel: "symbol",
        truncated: false,
        wholeFileComplete: false,
        completeRead: false,
        nextUnreadLine: endLine + 1,
        includePaths: Array.from(content.matchAll(/^\s*#\s*include\s*[<"]([^>"]+)[>"]/gmu))
          .map((match) => String(match[1] || "").replace(/\\/gu, "/"))
          .filter(Boolean)
          .slice(0, 32),
        supportingExcerpt: {
          startLine,
          endLine,
          text: lines.slice(startLine - 1, endLine).join("\n").slice(0, 4000),
        },
      };
      const budgetFail = commitDeferredBudgetOrFail({ directSourceEvidence });
      if (budgetFail) return budgetFail;
      rememberReadEvidence(
        target,
        stat,
        resolution,
        `${startLine}-${endLine}`,
        contentHash
      );
      recordReadSuccess("read_symbol", normalizedArgs, {
        ...readContext,
        contentHash,
        evidenceHash: contentHash,
      }, output, {
        lineRange: { start: startLine, end: endLine },
        lineCount: lines.length,
        bytesReturned: Buffer.byteLength(numbered, "utf8"),
        detailLevel: "symbol",
        truncated: false,
        wholeFileComplete: false,
        nextUnreadLine: endLine + 1,
      });
      commitBuildRecoveryEvidence("read_symbol", readContext, args);
      return attachCommittedToolOutcomeControl(
        text(output),
        committedDeferredBudget,
        "read_symbol"
      );
    }

    if (name === "write_file") {
      if (!ALLOW_WRITE) return fail("write_file blocked. Set ALLOW_WRITE=1 to enable.");
      const authFail = await enforceTaskAuth(args, {
        requireSession: REQUIRE_TASK_AUTH_FOR_WRITES,
        toolName: "write_file",
      });
      if (authFail) return authFail;
      const writeResolution = await resolveWriteToolPath(args.path);
      const target = writeResolution.absolutePath;
      const parent = path.dirname(target);
      const activeProject = getActiveProject(CONFIG_PATH);
      const guard = await validateWriteTarget({
        targetAbsPath: target,
        workspaceRoot: WORKSPACE_ROOT,
        activeProjectPath: activeProject,
        createDirs: Boolean(args.createDirs),
        fileExists: async (p) => exists(p),
        allowExistingWrite: false
      });
      if (!guard.ok) {
        const rel = displayPath(writeResolution);
        const fileExists = await exists(target);
        const discipline = writeDisciplineOptions(fileExists, {
          path: rel,
          startLine: 1,
          endLine: 120,
        });
        const callGuard = fileExists ? exactMutationCallGuard("write_file", args) : {};
        let lifecycle = null;
        if (fileExists) {
          rollbackDeferredBudget({
            mutationFailure: { errorCode: "FILE_ALREADY_EXISTS", fingerprint: callGuard.failedCallFingerprint },
          });
          lifecycle = recordMutationEvidenceRecovery(args, {
            errorCode: "FILE_ALREADY_EXISTS",
            requiredArgs: discipline.requiredNextToolArgs,
            targetFiles: [writeResolution.projectRelativePath || rel],
            failedCallFingerprint: callGuard.failedCallFingerprint,
            message: "write_file is create-only for this existing path. Read the bounded current range before constructing a new exact replacement call.",
          });
        }
        const payload = {
          ...discipline,
          ...callGuard,
          suggestedToolCalls: discipline.suggestedToolCalls,
        };
        bindAuthoritativeLifecycleControl(payload, lifecycle);
        return fail(guard.message, payload);
      }
      const requestedContent = String(args.content || "");
      const requestedLineCount = requestedContent.split(/\r?\n/).length;
      if (
        requestedContent.length > MAX_NEW_FILE_ARGUMENT_CHARS
        || requestedLineCount > MAX_NEW_FILE_LINES
      ) {
        return fail("write_file payload is too large for a reliable LM Studio tool call.", {
          errorCode: "BOUNDED_NEW_FILE_REQUIRED",
          retryable: true,
          stopCurrentWorkflow: false,
          nextAction: "write_file",
          nextActionIsTool: true,
          limits: {
            maxContentChars: MAX_NEW_FILE_ARGUMENT_CHARS,
            maxLines: MAX_NEW_FILE_LINES,
          },
          agentInstruction: "Create a smaller compilable file first, then extend it with bounded read_file_range + replace_in_file calls. Do not stop, cancel, or paste the file manually.",
        });
      }
      const lock = tryAcquirePathLock(target, "write_file");
      if (!lock.ok) {
        return fail("previous write still in progress on this path; verify file state with read_file before retrying.");
      }
      try {
        if (args.createDirs) await fsp.mkdir(parent, { recursive: true });
        if (!(await exists(parent))) return fail(`parent directory not found: ${path.relative(WORKSPACE_ROOT, parent)}`);
        const rel = displayPath(writeResolution);
        const mutationPayload = String(args.content || "");
        const mutationGuardScope = durableGuardScopeForArgs(args);
        const repeat = checkMutationDuplicate(
          "write_file",
          target,
          mutationPayload,
          mutationGuardScope
        );
        if (repeat.duplicate) {
          const callGuard = exactMutationCallGuard("write_file", args);
          rollbackDeferredBudget({
            mutationFailure: { errorCode: "MUTATION_REPEAT_BLOCKED", fingerprint: callGuard.failedCallFingerprint },
          });
          const lifecycle = recordMutationFailureRecovery(args, {
            errorCode: "MUTATION_REPEAT_BLOCKED",
            targetFiles: [writeResolution.projectRelativePath || rel],
            message: "The exact create call was already attempted. Validate a corrected bounded repair before constructing a new write call.",
          });
          const payload = {
            errorCode: "MUTATION_REPEAT_BLOCKED",
            retryable: true,
            stopCurrentWorkflow: false,
            ...callGuard,
          };
          bindAuthoritativeLifecycleControl(payload, lifecycle);
          return fail(duplicateMutationMessage("write_file", rel, repeat), payload);
        }
        const contentToWrite = mutationPayload;
        if (isSemanticGuardSourcePath(target)) {
          const semanticGuard = validateMutationSemanticText(contentToWrite);
          if (!semanticGuard.ok) {
            return mutationSemanticGuardFailure(semanticGuard, rel);
          }
        }
        const targetExists = await exists(target);
        const priorContent = targetExists
          ? await fsp.readFile(target, "utf8")
          : null;
        const journalLocation = mutationJournalLocation(target, args);
        const mutationJournal = beginMutationJournal({
          operation: "write_file",
          ...journalLocation,
          canonicalAbsolutePath: target,
          existedBefore: targetExists,
          preContent: priorContent,
          intendedPostContent: contentToWrite,
          checkpointRequired: REQUIRE_TASK_AUTH_FOR_WRITES && Boolean(journalLocation.taskSessionId),
        });
        try {
          await createExclusive(target, contentToWrite);
        } catch (err) {
          if (err && err.code === "EEXIST") {
            await abandonMutationJournal(mutationJournal, "aborted");
            const discipline = writeDisciplineOptions(true, {
              path: rel,
              startLine: 1,
              endLine: 120,
            });
            const callGuard = exactMutationCallGuard("write_file", args);
            rollbackDeferredBudget({
              mutationFailure: { errorCode: "FILE_ALREADY_EXISTS", fingerprint: callGuard.failedCallFingerprint },
            });
            const lifecycle = recordMutationEvidenceRecovery(args, {
              errorCode: "FILE_ALREADY_EXISTS",
              requiredArgs: discipline.requiredNextToolArgs,
              targetFiles: [writeResolution.projectRelativePath || rel],
              failedCallFingerprint: callGuard.failedCallFingerprint,
              message: "write_file lost a create race on this existing path. Read the bounded current range before constructing a new exact replacement call.",
            });
            const payload = {
              ...discipline,
              ...callGuard,
              suggestedToolCalls: discipline.suggestedToolCalls,
            };
            bindAuthoritativeLifecycleControl(payload, lifecycle);
            return fail(`write_file blocked because file already exists: ${rel}. Read the bounded current range before constructing a replace_in_file call. Do not replay the failed call fingerprint.`, payload);
          }
          throw err;
        }
        recordMutationAttempt("write_file", target, mutationPayload, mutationGuardScope);
        invalidateFileCache(target);
        heartbeatDeferredBudget();
        const validation = await validateAfterWrite(target, () => getActiveProject(CONFIG_PATH));
        heartbeatDeferredBudget();
        if (validationFailed(validation)) {
          // Stale-safe rollback: only revert if the file still holds exactly what this
          // request wrote. A newer operation's content must never be clobbered.
          let current = null;
          try { current = await fsp.readFile(target, "utf8"); } catch { current = null; }
          if (shouldRollback(current, contentToWrite)) {
            const rollback = await rollbackJournal(mutationJournal);
            invalidateFileCache(target);
            if (rollback.rolledBack) await abandonMutationJournal(mutationJournal, "rolled_back");
            rollbackDeferredBudget({
              mutationFailure: {
                errorCode: rollback.rolledBack ? "WRITE_STATIC_VALIDATION_FAILED" : "WRITE_ROLLBACK_INCOMPLETE",
                transactionId: mutationJournal.transactionId,
              },
            });
            const recovery = recordMutationFailureRecovery(args, {
              errorCode: rollback.rolledBack
                ? "WRITE_STATIC_VALIDATION_FAILED"
                : "WRITE_ROLLBACK_INCOMPLETE",
              targetFiles: [writeResolution.projectRelativePath || rel],
              transactionId: mutationJournal.transactionId,
              rollbackIncomplete: rollback.rolledBack !== true,
            });
            const failureOptions = {
              ok: false,
              path: rel,
              operation: "create",
              rolledBack: rollback.rolledBack,
              rollbackIncomplete: rollback.rolledBack !== true,
              transactionId: mutationJournal.transactionId,
              isError: true,
              errorCode: rollback.rolledBack
                ? "WRITE_STATIC_VALIDATION_FAILED"
                : "WRITE_ROLLBACK_INCOMPLETE",
              error: rollback.rolledBack
                ? "Static validation failed after create; the write was reverted."
                : "Static validation failed and the pre-image could not be fully restored.",
              nextSteps: rollback.rolledBack
                ? ["Validate a corrected bounded repair claim before writing again."]
                : ["Rebase the exact task checkpoint against current files before any new mutation."],
            };
            bindAuthoritativeLifecycleControl(failureOptions, recovery);
            return validationToolResult(
              `WRITE ROLLED BACK — ${rel} failed static validation.`,
              validation,
              failureOptions
            );
          }
          invalidateFileCache(target);
          mutationJournal.status = "recovery_required";
          mutationJournal.updatedAt = new Date().toISOString();
          saveJournal(mutationJournal);
          rollbackDeferredBudget({
            mutationFailure: { errorCode: "WRITE_ROLLBACK_CONFLICT", transactionId: mutationJournal.transactionId },
          });
          const recovery = recordMutationFailureRecovery(args, {
            errorCode: "WRITE_ROLLBACK_CONFLICT",
            targetFiles: [writeResolution.projectRelativePath || rel],
            transactionId: mutationJournal.transactionId,
            rollbackIncomplete: true,
            externalChange: true,
          });
          const failureOptions = {
            ok: false,
            path: rel,
            operation: "create",
            rolledBack: false,
            rollbackIncomplete: true,
            conflict: true,
            isError: true,
            errorCode: "WRITE_ROLLBACK_CONFLICT",
            error: "Another operation changed the file after this write.",
            nextSteps: ["Rebase the exact task checkpoint against current files before any new mutation."],
          };
          bindAuthoritativeLifecycleControl(failureOptions, recovery);
          return validationToolResult(
            `WRITE CONFLICT — ${rel} failed validation and rollback was skipped.`,
            validation,
            failureOptions
          );
        }
        let summary = `OK — ${rel} created.`;
        const nextSteps = ["Continue the planned edit set, then run build_unreal_project for C++/Build.cs changes."];
        if (validation && validation.skipped) {
          summary += validation.timedOut
            ? " Static validation exceeded its time budget."
            : " Static validation infrastructure was unavailable.";
          nextSteps.unshift("Run static_validate_project before build.");
        }
        let committedJournal = commitMutationJournal(mutationJournal, contentToWrite, {
          ...journalLocation,
          mutationGeneration: 0,
        });
        let mutation;
        try {
          mutation = await bumpProjectMutationGeneration(
            target,
            contentToWrite,
            mutationCompensationOptions(committedJournal, journalLocation)
          );
        } catch (err) {
          const diskRollback = await rollbackJournal(committedJournal);
          const restored = await finalizeDiskRollback(
            committedJournal,
            journalLocation.projectRoot,
            diskRollback,
            null,
            "mutation_bookkeeping_failed"
          );
          return mutationBookkeepingFailure(err.message || err, "create", rel, restored.rollback);
        }
        committedJournal = markMutationStateRecorded(committedJournal, {
          mutationGeneration: mutation?.mutationGeneration || 0,
          mutationRevision: mutation?.mutationRevision || 0,
          mutationStateRequired: Boolean(mutation),
        });
        const checkpoint = recordAutomaticContinuityCheckpoint(
          args, [target], validation, mutation?.mutationGeneration || 0
        );
        if (!checkpoint.ok) {
          const diskRollback = await rollbackJournal(committedJournal);
          const restored = await finalizeDiskRollback(
            committedJournal,
            journalLocation.projectRoot,
            diskRollback,
            mutation?.compensationReceipt,
            "continuity_checkpoint_failed"
          );
          return continuityCheckpointFailure(
            checkpoint, "write_file", [rel], mutation, restored.rollback, restored.compensation
          );
        }
        committedJournal = await completeMutationJournalCheckpoint(committedJournal, checkpoint);
        const budgetFail = commitDeferredBudgetOrFail({
          mutationCommit: {
            transactionId: committedJournal.transactionId,
            operation: "write_file",
            paths: [rel],
          },
        });
        if (budgetFail) return budgetFail;
        const successOptions = {
          path: rel,
          operation: "create",
          bytesWritten: Buffer.byteLength(contentToWrite, "utf8"),
          nextSteps,
          ...(mutation ? { mutationGeneration: mutation.mutationGeneration } : {}),
          transactionId: committedJournal.transactionId,
          buildValidationPending: committedJournal.status === "awaiting_build",
          continuityCheckpoint: checkpoint,
        };
        bindCommittedMutationControl(successOptions, committedDeferredBudget, checkpoint);
        return validationToolResult(summary, validation, successOptions);
      } finally {
        releasePathLock(target);
      }
    }

    if (name === "replace_in_file") {
      if (!ALLOW_WRITE) return fail("replace_in_file blocked. Set ALLOW_WRITE=1 to enable.");
      const authFail = await enforceTaskAuth(args, {
        requireSession: REQUIRE_TASK_AUTH_FOR_WRITES,
        toolName: "replace_in_file",
      });
      if (authFail) return authFail;
      const writeResolution = await resolveWriteToolPath(args.path);
      const target = writeResolution.absolutePath;
      const s = await statSafe(target);
      if (!s || !s.isFile()) {
        return fail(`not found or not file: ${args.path}. replace_in_file only edits existing files; to create a brand-new file, use write_file.`, {
          nextSteps: ["Search for the correct path. Use write_file only if this is intentionally a brand-new file."],
          suggestedToolCalls: [{
            tool: "search_files",
            args: { query: path.basename(String(args.path || "")), path: "." }
          }]
        });
      }
      if (PATCH_ONLY_EXISTING_EXTENSIONS.has(path.extname(target).toLowerCase()) && !hasFreshReadEvidence(target, s)) {
        return fail("replace_in_file blocked: direct read evidence for the current file version is required.", {
          sourceEvidence: sourceEvidenceSummary(getActiveProject(CONFIG_PATH)),
          suggestedToolCalls: [{ tool: "read_file_range", args: { path: displayPath(writeResolution), startLine: 1, endLine: 200 } }]
        });
      }
      const oldText = String(args.oldText ?? "");
      const newText = String(args.newText ?? "");
      if (!oldText) return fail("oldText must not be empty");
      const combinedPatchChars = oldText.length + newText.length;
      const changedLineCount = newText.split(/\r?\n/).length;
      if (
        combinedPatchChars > MAX_PATCH_ARGUMENT_CHARS
        || changedLineCount > MAX_PATCH_CHANGED_LINES
      ) {
        // #region agent log
        let priorCov = null;
        try {
          const mutationGeneration = await resolveMutationGenerationForRead(writeResolution, target);
          const patchEvidenceCtx = buildReadEvidenceContext(target, s, writeResolution, {
            mutationGeneration,
          });
          priorCov = getFileCoverage(patchEvidenceCtx);
        } catch { /* ignore coverage probe errors */ }
        const freshEvidence = hasFreshReadEvidence(target, s);
        const hasPriorEvidence = Boolean(
          freshEvidence
          || (
            priorCov
            && (
              (priorCov.nonRangeCount || 0) > 0
              || ((priorCov.ranges || []).length > 0)
              || (priorCov.coveredRepeatCount || 0) > 0
              || (priorCov.stagnationCount || 0) > 0
            )
          )
        );
        const display = displayPath(writeResolution);
        agentDebugLog("H1", "server.js:replace_in_file", "BOUNDED_PATCH_REQUIRED rejected", {
          path: display,
          combinedPatchChars,
          changedLineCount,
          maxCombinedPatchChars: MAX_PATCH_ARGUMENT_CHARS,
          maxChangedLines: MAX_PATCH_CHANGED_LINES,
          charsOver: Math.max(0, combinedPatchChars - MAX_PATCH_ARGUMENT_CHARS),
          linesOver: Math.max(0, changedLineCount - MAX_PATCH_CHANGED_LINES),
          priorCoverage: priorCov ? {
            rangeCount: (priorCov.ranges || []).length,
            ranges: (priorCov.ranges || []).slice(0, 6),
            nonRangeCount: priorCov.nonRangeCount || 0,
            coveredRepeatCount: priorCov.coveredRepeatCount || 0,
            stagnationCount: priorCov.stagnationCount || 0,
          } : null,
          freshEvidence,
          recoveryWillSuggestRead: !hasPriorEvidence,
          likelyDeadlock: hasPriorEvidence,
          recoveryMode: hasPriorEvidence ? "split_from_existing_evidence" : "narrow_read_then_split",
        });
        // #endregion
        // When the file was already read, directing the model back to read_file_range
        // causes EVIDENCE_STAGNATION / READ_REPEAT and a large-patch retry loop.
        if (hasPriorEvidence) {
          markMutationRecoveryHint(target, "BOUNDED_PATCH_REQUIRED");
          return fail("replace_in_file patch is too large for a reliable LM Studio tool call.", {
            errorCode: "BOUNDED_PATCH_REQUIRED",
            retryable: true,
            stopCurrentWorkflow: false,
            doNotRetry: ["read_file", "read_file_range"],
            nextAction: "replace_in_file",
            nextActionIsTool: true,
            nextActionArgs: {
              path: display,
              oldText: "<exact contiguous excerpt already in context, <=60 lines>",
              newText: "<replacement for that excerpt only>",
              expectedOccurrences: 1,
            },
            limits: {
              maxCombinedPatchChars: MAX_PATCH_ARGUMENT_CHARS,
              maxChangedLines: MAX_PATCH_CHANGED_LINES,
              observedCombinedPatchChars: combinedPatchChars,
              observedChangedLines: changedLineCount,
            },
            agentInstruction:
              "Do NOT re-read this file — evidence is already in context. "
              + "Emit a smaller replace_in_file now (newText <= " + String(MAX_PATCH_CHANGED_LINES) + " lines and "
              + "oldText+newText <= " + String(MAX_PATCH_ARGUMENT_CHARS) + " chars). "
              + "Split the remaining work across additional bounded patches. Do not stop or cancel.",
            nextSteps: [
              "Reuse exact text already returned by prior read_file/read_file_range.",
              "Apply one bounded replace_in_file, then continue with the next region.",
            ],
          });
        }
        return fail("replace_in_file patch is too large for a reliable LM Studio tool call.", {
          errorCode: "BOUNDED_PATCH_REQUIRED",
          retryable: true,
          stopCurrentWorkflow: false,
          nextAction: "read_file_range",
          nextActionIsTool: true,
          nextActionArgs: {
            path: display,
            startLine: 1,
            endLine: 120,
            detailLevel: "compact",
          },
          limits: {
            maxCombinedPatchChars: MAX_PATCH_ARGUMENT_CHARS,
            maxChangedLines: MAX_PATCH_CHANGED_LINES,
            observedCombinedPatchChars: combinedPatchChars,
            observedChangedLines: changedLineCount,
          },
          agentInstruction:
            "Read one narrower target range, then replace only that exact region. "
            + "Split the change across additional bounded patches; never duplicate the complete file as oldText/newText.",
        });
      }

      const lock = tryAcquirePathLock(target, "replace_in_file");
      if (!lock.ok) {
        return fail("previous write still in progress on this path; verify file state with read_file before retrying.");
      }
      try {
        const mutationPayload = `${oldText}\u0000${newText}\u0000${args.expectedOccurrences ?? ""}`;
        const mutationGuardScope = durableGuardScopeForArgs(args);
        const repeat = checkMutationDuplicate(
          "replace_in_file",
          target,
          mutationPayload,
          mutationGuardScope
        );
        if (repeat.duplicate) {
          const display = displayPath(writeResolution);
          let currentContent = "";
          try { currentContent = await fsp.readFile(target, "utf8"); } catch { currentContent = ""; }
          const nextActionArgs = boundedRecoveryRead(display, currentContent, oldText);
          const callGuard = exactMutationCallGuard("replace_in_file", args);
          rollbackDeferredBudget({
            mutationFailure: { errorCode: "MUTATION_REPEAT_BLOCKED", fingerprint: callGuard.failedCallFingerprint },
          });
          const lifecycle = recordMutationEvidenceRecovery(args, {
            errorCode: "MUTATION_REPEAT_BLOCKED",
            requiredArgs: nextActionArgs,
            targetFiles: [writeResolution.projectRelativePath || display],
            failedCallFingerprint: callGuard.failedCallFingerprint,
            message: "The exact mutation call was already attempted. Read the bounded current range and construct a new call rather than replaying it.",
          });
          const payload = {
            errorCode: "MUTATION_REPEAT_BLOCKED",
            retryable: true,
            stopCurrentWorkflow: false,
            ...callGuard,
            nextAction: "read_file_range",
            nextActionIsTool: true,
            nextActionArgs,
            requiredNextTool: "read_file_range",
            requiredNextToolArgs: nextActionArgs,
          };
          bindAuthoritativeLifecycleControl(payload, lifecycle);
          return fail(duplicateMutationMessage("replace_in_file", display, repeat), payload);
        }
        const raw = await readCachedBufferFile(target, s);
        const hasCRLF = raw.includes(Buffer.from("\r\n"));
        // Normalize to LF for matching; preserve original line endings in output
        const content = raw.toString("utf8");
        const contentNorm = content.replace(/\r\n/g, "\n");
        const oldTextNorm = oldText.replace(/\r\n/g, "\n");

        const occurrences = contentNorm.split(oldTextNorm).length - 1;
        if (occurrences === 0) {
          // Provide actionable diagnostic: show up to 3 lines around nearest partial match
          const firstLine = oldTextNorm.split("\n")[0].trim().slice(0, 60);
          const nearIdx = firstLine ? contentNorm.indexOf(firstLine) : -1;
          let hint = "";
          if (nearIdx !== -1) {
            const before = contentNorm.lastIndexOf("\n", nearIdx - 1);
            const snippetStart = Math.max(0, before);
            const snippet = contentNorm.slice(snippetStart, nearIdx + 200).split("\n").slice(0, 5).join("\n");
            hint = `\n\nNearest partial match context:\n${snippet}\n\nHint: read the file with read_file_range to get the exact text, then retry with the exact content shown.`;
          } else {
            hint = "\n\nHint: the first line of oldText was not found anywhere in the file. Use read_file or search_files to verify the exact content before retrying.";
          }
          const display = displayPath(writeResolution);
          const nextActionArgs = boundedRecoveryRead(display, contentNorm, oldTextNorm);
          const callGuard = exactMutationCallGuard("replace_in_file", args);
          rollbackDeferredBudget({
            mutationFailure: { errorCode: "OLD_TEXT_NOT_FOUND", fingerprint: callGuard.failedCallFingerprint },
          });
          const lifecycle = recordMutationEvidenceRecovery(args, {
            errorCode: "OLD_TEXT_NOT_FOUND",
            requiredArgs: nextActionArgs,
            targetFiles: [writeResolution.projectRelativePath || display],
            failedCallFingerprint: callGuard.failedCallFingerprint,
            message: "The exact replacement pre-image was not present. Read the nearest bounded current range, then construct a new call with new oldText.",
          });
          const payload = {
            errorCode: "OLD_TEXT_NOT_FOUND",
            retryable: true,
            stopCurrentWorkflow: false,
            ...callGuard,
            nextAction: "read_file_range",
            nextActionIsTool: true,
            nextActionArgs,
            requiredNextTool: "read_file_range",
            requiredNextToolArgs: nextActionArgs,
            agentInstruction: "Call read_file_range once with the exact bounded args. Then construct a new replace_in_file call from the returned current text; only the failed call fingerprint is forbidden.",
            nextSteps: ["Read the bounded current range, then construct a new exact replacement call."],
          };
          bindAuthoritativeLifecycleControl(payload, lifecycle);
          return fail(`oldText not found in ${args.path} (file uses ${hasCRLF ? "CRLF" : "LF"} line endings).${hint}`, payload);
        }
        const isSourcePath = [".h", ".hpp", ".cpp", ".c", ".cc", ".cs"].includes(path.extname(target).toLowerCase());
        const expectedOccurrences = args.expectedOccurrences !== undefined
          ? Number(args.expectedOccurrences)
          : (isSourcePath ? 1 : undefined);
        const occurrenceRecoveryFailure = (errorCode, message, extra = {}) => {
          const display = displayPath(writeResolution);
          const nextActionArgs = boundedRecoveryRead(display, contentNorm, oldTextNorm);
          const callGuard = exactMutationCallGuard("replace_in_file", args);
          rollbackDeferredBudget({
            mutationFailure: { errorCode, fingerprint: callGuard.failedCallFingerprint },
          });
          const lifecycle = recordMutationEvidenceRecovery(args, {
            errorCode,
            requiredArgs: nextActionArgs,
            targetFiles: [writeResolution.projectRelativePath || display],
            failedCallFingerprint: callGuard.failedCallFingerprint,
            message: "The replacement occurrence contract did not match current disk state. Read the bounded current range before constructing a new call.",
          });
          const payload = {
            errorCode,
            retryable: true,
            stopCurrentWorkflow: false,
            ...callGuard,
            nextAction: "read_file_range",
            nextActionIsTool: true,
            nextActionArgs,
            requiredNextTool: "read_file_range",
            requiredNextToolArgs: nextActionArgs,
            observedOccurrences: occurrences,
            agentInstruction: "Read the exact bounded current range, then construct a new replacement with a unique pre-image and a correct expectedOccurrences value. Only the failed call fingerprint is forbidden.",
            ...extra,
          };
          bindAuthoritativeLifecycleControl(payload, lifecycle);
          return fail(message, payload);
        };
        if (isSourcePath && args.expectedOccurrences === undefined && occurrences > 1) {
          const snippets = contentNorm.split("\n")
            .map((line, index) => ({ line, index }))
            .filter(({ line }) => line.includes(oldTextNorm.split("\n")[0]))
            .slice(0, 3)
            .map(({ line, index }) => `L${index + 1}: ${line.slice(0, 120)}`)
            .join("\n");
          return occurrenceRecoveryFailure(
            "AMBIGUOUS_REPLACE",
            `ambiguous replace in ${args.path}: found ${occurrences} matches; specify expectedOccurrences or narrow oldText.${snippets ? `\n\nMatches:\n${snippets}` : ""}`,
            { matchingSnippets: snippets }
          );
        }
        if (expectedOccurrences !== undefined && occurrences !== expectedOccurrences) {
          return occurrenceRecoveryFailure(
            "OCCURRENCE_MISMATCH",
            `occurrence mismatch: expected ${expectedOccurrences}, found ${occurrences}`,
            { expectedOccurrences }
          );
        }

        // Apply replacement on normalized content, then restore original line endings if needed
        const priorContent = content;
        const replacementNorm = newText.replace(/\r\n/g, "\n");
        const updatedNorm = expectedOccurrences === 1
          ? contentNorm.replace(oldTextNorm, replacementNorm)
          : contentNorm.split(oldTextNorm).join(replacementNorm);
        const prospectiveContent = hasCRLF ? updatedNorm.replace(/\n/g, "\r\n") : updatedNorm;
        if (isSemanticGuardSourcePath(target)) {
          const semanticGuard = validateMutationSemanticText(prospectiveContent);
          if (!semanticGuard.ok) {
            return mutationSemanticGuardFailure(semanticGuard, displayPath(writeResolution));
          }
        }
        const evidenceEntry = readEvidence.get(path.resolve(target));
        const journalLocation = mutationJournalLocation(target, args);
        const mutationJournal = beginMutationJournal({
          operation: "replace_in_file",
          ...journalLocation,
          canonicalAbsolutePath: target,
          existedBefore: true,
          preContent: priorContent,
          intendedPostContent: prospectiveContent,
          checkpointRequired: REQUIRE_TASK_AUTH_FOR_WRITES && Boolean(journalLocation.taskSessionId),
        });
        const casResult = await replaceWithCAS({
          targetPath: target,
          priorContent: content,
          oldText,
          newText,
          expectedOccurrences,
          readHash: evidenceEntry?.contentHash || null,
        });
        if (!casResult.ok) {
          await abandonMutationJournal(mutationJournal, "aborted");
          let currentContent = contentNorm;
          try {
            currentContent = String(await fsp.readFile(target, "utf8")).replace(/\r\n/g, "\n");
          } catch {
            // Keep the last successfully read content for a concrete recovery range.
          }
          const display = displayPath(writeResolution);
          const nextActionArgs = boundedRecoveryRead(display, currentContent, oldTextNorm);
          const callGuard = exactMutationCallGuard("replace_in_file", args);
          const errorCode = casResult.errorCode || "READ_HASH_CAS_MISMATCH";
          rollbackDeferredBudget({
            mutationFailure: { errorCode, fingerprint: callGuard.failedCallFingerprint },
          });
          const lifecycle = recordMutationEvidenceRecovery(args, {
            errorCode,
            requiredArgs: nextActionArgs,
            targetFiles: [writeResolution.projectRelativePath || display],
            failedCallFingerprint: callGuard.failedCallFingerprint,
            message: "The file changed after its read evidence. Read the bounded current range before constructing a new replacement call.",
          });
          const payload = {
            errorCode: casResult.errorCode || "READ_HASH_CAS_MISMATCH",
            retryable: true,
            stopCurrentWorkflow: false,
            ...callGuard,
            nextAction: "read_file_range",
            nextActionIsTool: true,
            nextActionArgs,
            requiredNextTool: "read_file_range",
            requiredNextToolArgs: nextActionArgs,
            nextSteps: ["Read the bounded current range, then construct a new call with current exact text."],
            agentInstruction: "Call read_file_range with the exact bounded args. Do not replay the failed call fingerprint; construct a new exact replacement from current disk evidence.",
          };
          bindAuthoritativeLifecycleControl(payload, lifecycle);
          return fail(casResult.error || "replace_in_file blocked by read-hash CAS.", payload);
        }
        recordMutationAttempt("replace_in_file", target, mutationPayload, mutationGuardScope);
        const updated = casResult.updated;
        invalidateFileCache(target);
        heartbeatDeferredBudget();
        const validation = await validateAfterWrite(target, () => getActiveProject(CONFIG_PATH));
        heartbeatDeferredBudget();
        const rel = displayPath(writeResolution);
        if (validationFailed(validation)) {
          // Stale-safe rollback: only restore if the file still holds exactly what this
          // request wrote; otherwise a newer operation owns the file — skip and warn.
          let current = null;
          try { current = await fsp.readFile(target, "utf8"); } catch { current = null; }
          if (shouldRollback(current, updated)) {
            const rollback = await rollbackJournal(mutationJournal);
            invalidateFileCache(target);
            if (rollback.rolledBack) await abandonMutationJournal(mutationJournal, "rolled_back");
            rollbackDeferredBudget({
              mutationFailure: {
                errorCode: rollback.rolledBack ? "PATCH_STATIC_VALIDATION_FAILED" : "PATCH_ROLLBACK_INCOMPLETE",
                transactionId: mutationJournal.transactionId,
              },
            });
            const recovery = recordMutationFailureRecovery(args, {
              errorCode: rollback.rolledBack
                ? "PATCH_STATIC_VALIDATION_FAILED"
                : "PATCH_ROLLBACK_INCOMPLETE",
              targetFiles: [writeResolution.projectRelativePath || rel],
              transactionId: mutationJournal.transactionId,
              rollbackIncomplete: rollback.rolledBack !== true,
            });
            const failureOptions = {
              ok: false,
              path: rel,
              operation: "replace",
              replacements: occurrences,
              rolledBack: rollback.rolledBack,
              rollbackIncomplete: rollback.rolledBack !== true,
              transactionId: mutationJournal.transactionId,
              isError: true,
              errorCode: rollback.rolledBack
                ? "PATCH_STATIC_VALIDATION_FAILED"
                : "PATCH_ROLLBACK_INCOMPLETE",
              error: rollback.rolledBack
                ? "Static validation failed after replace; the file was restored."
                : "Static validation failed and the pre-image could not be fully restored.",
              nextSteps: rollback.rolledBack
                ? ["Validate a corrected bounded repair claim before writing again."]
                : ["Rebase the exact task checkpoint against current files before any new mutation."],
            };
            bindAuthoritativeLifecycleControl(failureOptions, recovery);
            return validationToolResult(
              `PATCH ROLLED BACK — ${rel} failed static validation.`,
              validation,
              failureOptions
            );
          }
          invalidateFileCache(target);
          mutationJournal.status = "recovery_required";
          mutationJournal.updatedAt = new Date().toISOString();
          saveJournal(mutationJournal);
          rollbackDeferredBudget({
            mutationFailure: { errorCode: "PATCH_ROLLBACK_CONFLICT", transactionId: mutationJournal.transactionId },
          });
          const recovery = recordMutationFailureRecovery(args, {
            errorCode: "PATCH_ROLLBACK_CONFLICT",
            targetFiles: [writeResolution.projectRelativePath || rel],
            transactionId: mutationJournal.transactionId,
            rollbackIncomplete: true,
            externalChange: true,
          });
          const failureOptions = {
            ok: false,
            path: rel,
            operation: "replace",
            replacements: occurrences,
            rolledBack: false,
            rollbackIncomplete: true,
            conflict: true,
            isError: true,
            errorCode: "PATCH_ROLLBACK_CONFLICT",
            error: "Another operation changed the file after this patch.",
            nextSteps: ["Rebase the exact task checkpoint against current files before any new mutation."],
          };
          bindAuthoritativeLifecycleControl(failureOptions, recovery);
          return validationToolResult(
            `PATCH CONFLICT — ${rel} failed validation and rollback was skipped.`,
            validation,
            failureOptions
          );
        }
        let summary = `OK — ${rel} patched (${occurrences} replacement(s)).`;
        const nextSteps = ["Continue the plan, or run build_unreal_project when the C++/Build.cs edit set is complete."];
        if (validation && validation.skipped) {
          summary += validation.timedOut
            ? " Static validation exceeded its time budget."
            : " Static validation infrastructure was unavailable.";
          nextSteps.unshift("Run static_validate_project before build.");
        }
        let committedJournal = commitMutationJournal(mutationJournal, updated, {
          ...journalLocation,
          mutationGeneration: 0,
        });
        let mutation;
        try {
          mutation = await bumpProjectMutationGeneration(
            target,
            updated,
            mutationCompensationOptions(committedJournal, journalLocation)
          );
        } catch (err) {
          const diskRollback = await rollbackJournal(committedJournal);
          const restored = await finalizeDiskRollback(
            committedJournal,
            journalLocation.projectRoot,
            diskRollback,
            null,
            "mutation_bookkeeping_failed"
          );
          return mutationBookkeepingFailure(err.message || err, "replace", rel, restored.rollback);
        }
        committedJournal = markMutationStateRecorded(committedJournal, {
          mutationGeneration: mutation?.mutationGeneration || 0,
          mutationRevision: mutation?.mutationRevision || 0,
          mutationStateRequired: Boolean(mutation),
        });
        const checkpoint = recordAutomaticContinuityCheckpoint(
          args, [target], validation, mutation?.mutationGeneration || 0
        );
        if (!checkpoint.ok) {
          const diskRollback = await rollbackJournal(committedJournal);
          const restored = await finalizeDiskRollback(
            committedJournal,
            journalLocation.projectRoot,
            diskRollback,
            mutation?.compensationReceipt,
            "continuity_checkpoint_failed"
          );
          return continuityCheckpointFailure(
            checkpoint, "replace_in_file", [rel], mutation, restored.rollback, restored.compensation
          );
        }
        committedJournal = await completeMutationJournalCheckpoint(committedJournal, checkpoint);
        const budgetFail = commitDeferredBudgetOrFail({
          mutationCommit: {
            transactionId: committedJournal.transactionId,
            operation: "replace_in_file",
            paths: [rel],
          },
        });
        if (budgetFail) return budgetFail;
        const successOptions = {
          path: rel,
          operation: "replace",
          replacements: occurrences,
          nextSteps,
          ...(mutation ? { mutationGeneration: mutation.mutationGeneration } : {}),
          transactionId: committedJournal.transactionId,
          buildValidationPending: committedJournal.status === "awaiting_build",
          continuityCheckpoint: checkpoint,
        };
        bindCommittedMutationControl(successOptions, committedDeferredBudget, checkpoint);
        return validationToolResult(summary, validation, successOptions);
      } finally {
        releasePathLock(target);
      }
    }

    if (name === "propose_file_deletions") {
      try {
        const activeProject = getActiveProject(CONFIG_PATH);
        const plan = await buildDeletionProposal(args.files, args.completedEditsSummary, activeProject);
        return text(JSON.stringify(plan, null, 2));
      } catch (err) {
        const message = err && err.message ? String(err.message) : String(err);
        return fail(message, {
          errorCode: "VALIDATION_ERROR",
          retryable: false,
          userMessage: message,
          agentInstruction: "Fix propose_file_deletions arguments (summary/reasons must be concrete sentences) and retry once.",
        });
      }
    }

    if (name === "delete_file") {
      if (!ALLOW_WRITE) return fail("delete_file blocked. Set ALLOW_WRITE=1 to enable.");
      if (!ALLOW_SOURCE_DELETE) {
        return fail("delete_file blocked. Set ALLOW_SOURCE_DELETE=1 to enable source deletions.");
      }
      const authFail = await enforceTaskAuth(args, {
        requireSession: REQUIRE_TASK_AUTH_FOR_WRITES,
        toolName: "delete_file",
      });
      if (authFail && authFail.isError) return authFail;
      const resolution = await resolveReadToolPath(args.path);
      const target = resolution.absolutePath;
      const activeProject = getActiveProject(CONFIG_PATH);
      const guard = isDeleteAllowedPath(target, WORKSPACE_ROOT, activeProject);
      if (!guard.ok) {
        return fail(guard.message);
      }
      const rel = displayPath(resolution);
      const completedEditsSummary = requireDeletionText(args.completedEditsSummary, "completedEditsSummary");
      const reason = requireDeletionText(args.reason, "reason");
      const ifNotDeleted = requireDeletionText(args.ifNotDeleted, "ifNotDeleted");
      const ifDeleted = requireDeletionText(args.ifDeleted, "ifDeleted");
      const expectedToken = deletionApprovalToken({
        relPath: rel,
        completedEditsSummary,
        reason,
        ifNotDeleted,
        ifDeleted,
      });
      if (String(args.approvalToken || "") !== expectedToken) {
        return fail(
          "delete_file blocked: approvalToken does not match this deletion explanation. "
          + "Call propose_file_deletions after edits are complete, show the plan to the user, "
          + "and pass the matching per-file approvalToken only after approval."
        );
      }
      const lock = tryAcquirePathLock(target, "delete_file");
      if (!lock.ok) {
        return fail("previous write still in progress on this path; verify file state with read_file before retrying.");
      }
      try {
        const delStat = await statSafe(target);
        if (!delStat || !delStat.isFile()) return fail(`not found or not file: ${args.path}`);
        const priorContent = await fsp.readFile(target, "utf8");
        if (args.expectedContent !== undefined) {
          if (priorContent !== String(args.expectedContent)) {
            return fail("expectedContent mismatch; delete aborted.");
          }
        }
        const journalLocation = mutationJournalLocation(target, args);
        const mutationJournal = beginMutationJournal({
          operation: "delete_file",
          ...journalLocation,
          canonicalAbsolutePath: target,
          existedBefore: true,
          preContent: priorContent,
          deleteTarget: true,
          checkpointRequired: REQUIRE_TASK_AUTH_FOR_WRITES && Boolean(journalLocation.taskSessionId),
        });
        try {
          await fsp.unlink(target);
        } catch (error) {
          await abandonMutationJournal(mutationJournal, "aborted");
          throw error;
        }
        invalidateFileCache(target);
        heartbeatDeferredBudget();
        const validation = await validateAfterDelete(
          target,
          () => getActiveProject(CONFIG_PATH)
        );
        heartbeatDeferredBudget();
        if (validationFailed(validation)) {
          const rollback = await rollbackJournal(mutationJournal);
          invalidateFileCache(target);
          if (rollback.rolledBack) {
            await abandonMutationJournal(mutationJournal, "rolled_back");
          }
          const errorCode = rollback.rolledBack
            ? "DELETE_STATIC_VALIDATION_FAILED"
            : "DELETE_ROLLBACK_INCOMPLETE";
          rollbackDeferredBudget({
            mutationFailure: {
              errorCode,
              transactionId: mutationJournal.transactionId,
            },
          });
          const lifecycle = recordMutationFailureRecovery(args, {
            errorCode,
            targetFiles: [resolution.projectRelativePath || rel],
            transactionId: mutationJournal.transactionId,
            rollbackIncomplete: rollback.rolledBack !== true,
            externalChange: rollback.externalChangeDetected?.length > 0,
            message: rollback.rolledBack
              ? "The deletion failed post-mutation static validation and was rolled back. Validate a corrected bounded repair claim."
              : "The deletion failed validation and its pre-image could not be fully restored. Rebase the exact task checkpoint against current files.",
          });
          const failureOptions = {
            ok: false,
            path: rel,
            operation: "delete",
            rolledBack: rollback.rolledBack,
            rollbackIncomplete: rollback.rollbackIncomplete,
            restoredPaths: rollback.restoredPaths,
            unrestoredPaths: rollback.unrestoredPaths,
            externalChangeDetected: rollback.externalChangeDetected,
            rollbackErrors: rollback.rollbackErrors,
            transactionId: mutationJournal.transactionId,
            isError: true,
            errorCode,
            error: rollback.rolledBack
              ? "Static validation failed after deletion; the file was restored."
              : "Static validation failed and the deleted file could not be fully restored.",
            nextSteps: rollback.rolledBack
              ? ["Validate a corrected bounded repair claim before deleting again."]
              : ["Rebase the exact task checkpoint against current files before any new mutation."],
          };
          bindAuthoritativeLifecycleControl(failureOptions, lifecycle);
          return validationToolResult(
            rollback.rolledBack
              ? `DELETE ROLLED BACK — ${rel} failed static validation.`
              : `DELETE RECOVERY REQUIRED — ${rel} failed static validation.`,
            validation,
            failureOptions
          );
        }
        let committedJournal = commitMutationJournal(mutationJournal, null, {
          ...journalLocation,
          mutationGeneration: 0,
          deletedAfter: true,
        });
        const activeProjectForMutation = activeProject;
        let mutation = null;
        if (activeProjectForMutation) {
          const projectDir = path.dirname(path.resolve(activeProjectForMutation));
          const projectRelativePath = path.relative(projectDir, target).replace(/\\/g, "/");
          if (!projectRelativePath || projectRelativePath.startsWith("../") || path.isAbsolute(projectRelativePath)) {
            const diskRollback = await rollbackJournal(committedJournal);
            const restored = await finalizeDiskRollback(
              committedJournal,
              journalLocation.projectRoot,
              diskRollback,
              null,
              "mutation_path_outside_project"
            );
            return fail(`mutation path outside active project: ${target}`, {
              deleted: rel,
              writeApplied: !restored.fullyRestored,
              rolledBack: restored.fullyRestored,
              bookkeepingFailed: true,
              retryable: restored.fullyRestored,
            });
          }
          try {
            mutation = await recordDeletion(
              projectDir,
              projectRelativePath,
              mutationCompensationOptions(committedJournal, journalLocation)
            );
          } catch (error) {
            const diskRollback = await rollbackJournal(committedJournal);
            const restored = await finalizeDiskRollback(
              committedJournal,
              journalLocation.projectRoot,
              diskRollback,
              null,
              "mutation_bookkeeping_failed"
            );
            return mutationBookkeepingFailure(
              error.message || error,
              "delete",
              rel,
              restored.rollback
            );
          }
        }
        committedJournal = markMutationStateRecorded(committedJournal, {
          mutationGeneration: mutation?.mutationGeneration || 0,
          mutationRevision: mutation?.mutationRevision || 0,
          mutationStateRequired: Boolean(mutation),
        });
        const checkpoint = recordAutomaticContinuityCheckpoint(
          args, [target], null, mutation?.mutationGeneration || 0
        );
        if (!checkpoint.ok) {
          const diskRollback = await rollbackJournal(committedJournal);
          const restored = await finalizeDiskRollback(
            committedJournal,
            journalLocation.projectRoot,
            diskRollback,
            mutation?.compensationReceipt,
            "continuity_checkpoint_failed"
          );
          return continuityCheckpointFailure(
            checkpoint, "delete_file", [rel], mutation, restored.rollback, restored.compensation
          );
        }
        committedJournal = await completeMutationJournalCheckpoint(committedJournal, checkpoint);
        const budgetFail = commitDeferredBudgetOrFail({
          mutationCommit: {
            transactionId: committedJournal.transactionId,
            operation: "delete_file",
            paths: [rel],
          },
        });
        if (budgetFail) return budgetFail;
        const nextSteps = ["Continue the planned edit set, then run build_unreal_project for source deletions."];
        if (validation?.skipped) {
          nextSteps.unshift("Run static_validate_project before build.");
        }
        const successPayload = {
          ok: true,
          deleted: rel,
          fileName: path.basename(target),
          completedEditsSummary,
          reason,
          ifNotDeleted,
          ifDeleted,
          ...(mutation ? { mutationGeneration: mutation.mutationGeneration } : {}),
          transactionId: committedJournal.transactionId,
          buildValidationPending: committedJournal.status === "awaiting_build",
          continuityCheckpoint: checkpoint,
          validation: compactValidationPayload(validation),
          nextSteps,
          ...(checkpoint.taskAuthorization ? { taskAuthorization: checkpoint.taskAuthorization } : {}),
          ...(checkpoint.toolRoute ? { toolRoute: checkpoint.toolRoute } : {}),
          ...(Number.isInteger(Number(checkpoint.controlEpoch))
            ? { controlEpoch: Math.max(0, Number(checkpoint.controlEpoch)) }
            : {}),
          ...(checkpoint.control ? { control: checkpoint.control } : {}),
        };
        bindCommittedMutationControl(successPayload, committedDeferredBudget, checkpoint);
        return text(JSON.stringify(successPayload, null, 2));
      } finally {
        releasePathLock(target);
      }
    }

    if (name === "apply_edit_bundle") {
      if (!ALLOW_WRITE) return fail("apply_edit_bundle blocked. Set ALLOW_WRITE=1 to enable.");
      const authFail = await enforceTaskAuth(args, {
        requireSession: true,
        toolName: "apply_edit_bundle",
      });
      if (authFail) return authFail;
      const auth = validateMutationAuth(
        WORKSPACE_ROOT,
        args,
        {
          requireAll: true,
          toolName: "apply_edit_bundle",
          consumeBudget: false,
          activeProject: getActiveProject(CONFIG_PATH),
        }
      );
      if (!auth.ok) {
        return fail(
          auth.error || "Task authorization failed.",
          routeAuthorizationFailureOptions(auth, "apply_edit_bundle")
        );
      }
      await agentNotify("Applying edit bundle…");
      const bundle = {
        files: Array.isArray(args.files) ? args.files : [],
        patches: Array.isArray(args.patches) ? args.patches : []
      };
      if (!bundle.files.length && !bundle.patches.length) {
        return fail("apply_edit_bundle requires at least one file or patch entry.");
      }
      const activeProject = getActiveProject(CONFIG_PATH);
      if (!activeProject) {
        return fail("apply_edit_bundle requires an active project.", {
          suggestedToolCalls: [{ tool: "unreal_set_active_project", args: {} }],
        });
      }
      const projectRoot = path.dirname(path.resolve(activeProject));

      async function resolveBundlePath(relPath) {
        try {
          const resolution = await resolveWriteToolPath(relPath);
          return { ok: true, absolutePath: resolution.absolutePath };
        } catch (error) {
          return { ok: false, error: String(error.message || error) };
        }
      }

      // Reject full-file overwrite shapes before starting a transaction.  The
      // model must be able to finish generating the JSON call, and existing
      // files retain the safer exact-patch/CAS workflow.
      for (const entry of bundle.files) {
        const relPath = String(entry?.path || "");
        const content = String(entry?.content || "");
        const resolution = await resolveBundlePath(relPath);
        if (!resolution.ok) {
          return fail(resolution.error || `Invalid bundle path: ${relPath}`);
        }
        if (await exists(resolution.absolutePath)) {
          const discipline = writeDisciplineOptions(true, {
            path: relPath,
            startLine: 1,
            endLine: 120,
          });
          const callGuard = exactMutationCallGuard("apply_edit_bundle", args);
          rollbackDeferredBudget({
            mutationFailure: {
              errorCode: "BUNDLE_EXISTING_FILE_CONTENT_FORBIDDEN",
              fingerprint: callGuard.failedCallFingerprint,
            },
          });
          const lifecycle = recordMutationEvidenceRecovery(args, {
            errorCode: "BUNDLE_EXISTING_FILE_CONTENT_FORBIDDEN",
            requiredArgs: discipline.requiredNextToolArgs,
            targetFiles: [relPath],
            failedCallFingerprint: callGuard.failedCallFingerprint,
            message: "A bundle files[] entry targeted an existing file. Read its bounded current range before constructing a new exact replacement call.",
          });
          const payload = {
            errorCode: "BUNDLE_EXISTING_FILE_CONTENT_FORBIDDEN",
            retryable: true,
            stopCurrentWorkflow: false,
            ...callGuard,
            nextAction: "read_file_range",
            nextActionIsTool: true,
            nextActionArgs: discipline.requiredNextToolArgs,
            requiredNextTool: "read_file_range",
            requiredNextToolArgs: discipline.requiredNextToolArgs,
            suggestedToolCalls: discipline.suggestedToolCalls,
            agentInstruction: "Read the bounded current range first, then construct a new exact replace_in_file call. Never resend complete existing-file content in apply_edit_bundle.files.",
          };
          bindAuthoritativeLifecycleControl(payload, lifecycle);
          return fail(`apply_edit_bundle.files cannot overwrite existing file: ${relPath}`, payload);
        }
        if (
          content.length > MAX_NEW_FILE_ARGUMENT_CHARS
          || content.split(/\r?\n/).length > MAX_NEW_FILE_LINES
        ) {
          return fail(`apply_edit_bundle new-file payload is too large: ${relPath}`, {
            errorCode: "BOUNDED_NEW_FILE_REQUIRED",
            retryable: true,
            stopCurrentWorkflow: false,
            limits: {
              maxContentChars: MAX_NEW_FILE_ARGUMENT_CHARS,
              maxLines: MAX_NEW_FILE_LINES,
            },
            agentInstruction: "Create a smaller compilable file first, then extend it with bounded patches. Do not stop or cancel the task.",
          });
        }
      }
      for (const entry of bundle.patches) {
        const oldText = String(entry?.oldText || "");
        const newText = String(entry?.newText || "");
        if (
          oldText.length + newText.length > MAX_PATCH_ARGUMENT_CHARS
          || newText.split(/\r?\n/).length > MAX_PATCH_CHANGED_LINES
        ) {
          return fail(`apply_edit_bundle patch is too large: ${String(entry?.path || "")}`, {
            errorCode: "BOUNDED_PATCH_REQUIRED",
            retryable: true,
            stopCurrentWorkflow: false,
            nextAction: "read_file_range",
            nextActionIsTool: true,
            limits: {
              maxCombinedPatchChars: MAX_PATCH_ARGUMENT_CHARS,
              maxChangedLines: MAX_PATCH_CHANGED_LINES,
            },
            agentInstruction: "Split the bundle entry into bounded exact patches and continue. Do not send complete existing files or stop the task.",
          });
        }
      }

      const tx = await applyBundleTransaction(bundle, resolveBundlePath, {
        maxFilesPerEdit: auth.maxFilesPerEdit || DEFAULT_MAX_FILES_PER_EDIT,
        deferFinalization: true,
        projectRoot,
        taskSessionId: mutationTaskSessionId(args),
        checkpointRequired: REQUIRE_TASK_AUTH_FOR_WRITES && Boolean(mutationTaskSessionId(args)),
        transactionMetadata: {
          taskSessionId: requiredFields(args).taskSessionId,
          projectPath: String(activeProject || ""),
          projectRoot,
        },
        onCommitted: async (commit) => {
          armAtomicMutationJournal(commit.journal, {
            projectRoot,
            taskSessionId: mutationTaskSessionId(args),
            checkpointRequired: REQUIRE_TASK_AUTH_FOR_WRITES && Boolean(mutationTaskSessionId(args)),
          });
          const validationResults = [];
          for (const absPath of commit.writtenAbs) {
            heartbeatDeferredBudget();
            if (isSemanticGuardSourcePath(absPath)) {
              const prospectiveContent = await fsp.readFile(absPath, "utf8");
              const semanticGuard = validateMutationSemanticText(prospectiveContent);
              if (!semanticGuard.ok) {
                return {
                  ok: false,
                  error: "mutation semantic guard failed",
                  validation: { semanticGuard, path: absPath },
                  validationResults,
                };
              }
            }
            validationResults.push(await validateAfterWrite(absPath, () => getActiveProject(CONFIG_PATH)));
            heartbeatDeferredBudget();
          }
          const failed = validationResults.find((item) => validationFailed(item));
          if (failed) {
            return { ok: false, error: "static validation failed", validation: failed, validationResults };
          }
          return { ok: true, validationResults };
        },
      });
      if (!tx.ok) {
        let recoveryPlan = bundleFailureRecovery(tx, bundle);
        const patchFailure = tx.mutationFailure && typeof tx.mutationFailure === "object"
          ? tx.mutationFailure
          : null;
        const evidenceFailureCodes = new Set([
          "OLD_TEXT_NOT_FOUND",
          "OCCURRENCE_MISMATCH",
          "READ_HASH_CAS_MISMATCH",
          "PATCH_CAS_FAILED",
        ]);
        if (
          patchFailure
          && evidenceFailureCodes.has(String(patchFailure.errorCode || ""))
          && recoveryPlan.rollbackIncomplete !== true
        ) {
          const relativePath = String(patchFailure.relativePath || "").replace(/\\/g, "/");
          const resolved = await resolveBundlePath(relativePath);
          let currentContent = "";
          if (resolved.ok) {
            try {
              currentContent = await fsp.readFile(resolved.absolutePath, "utf8");
            } catch {
              currentContent = "";
            }
          }
          const requiredArgs = boundedRecoveryRead(
            relativePath,
            currentContent,
            String(patchFailure.oldText || "")
          );
          recoveryPlan = {
            errorCode: String(patchFailure.errorCode || "PATCH_CAS_FAILED"),
            status: "evidence_required",
            scopeDisposition: "in_slice",
            requiredTool: { name: "read_file_range", args: requiredArgs },
            targetFiles: relativePath ? [relativePath] : [],
            message: "The bundle patch pre-image or CAS evidence no longer matches. Read the nearest bounded current range, then construct a new exact bundle or replacement call.",
            rolledBack: recoveryPlan.rolledBack,
            rollbackIncomplete: false,
          };
        }
        if (recoveryPlan.rolledBack) {
          await archiveJournal(tx.transactionId);
        }
        const callGuard = exactMutationCallGuard("apply_edit_bundle", args);
        rollbackDeferredBudget({
          mutationFailure: {
            errorCode: recoveryPlan.errorCode,
            transactionId: String(tx.transactionId || ""),
            fingerprint: callGuard.failedCallFingerprint,
          },
        });
        const lifecycle = recordRecoveryObligationViaPython(WORKSPACE_ROOT, args, {
          source: "mutation",
          status: recoveryPlan.status,
          scopeDisposition: recoveryPlan.scopeDisposition,
          errorCode: recoveryPlan.errorCode,
          mutationGeneration: Math.max(0, Number(auth.state?.mutationGeneration || 0)),
          requiredTool: recoveryPlan.requiredTool,
          targetFiles: recoveryPlan.targetFiles,
          transactionId: String(tx.transactionId || ""),
          projectRoot,
          journalPaths: recoveryPlan.targetFiles,
          failedCallFingerprint: callGuard.failedCallFingerprint,
          message: recoveryPlan.message,
        });
        await agentNotify(`apply_edit_bundle failed: ${tx.error}`, "error");
        const payload = {
          errorCode: recoveryPlan.errorCode,
          retryable: true,
          stopCurrentWorkflow: false,
          ...callGuard,
          ...(tx.validation?.semanticGuard ? { semanticGuard: tx.validation.semanticGuard } : {}),
          nextAction: recoveryPlan.requiredTool.name,
          nextActionIsTool: true,
          nextActionArgs: recoveryPlan.requiredTool.args,
          requiredNextTool: recoveryPlan.requiredTool.name,
          requiredNextToolArgs: recoveryPlan.requiredTool.args,
          agentInstruction: recoveryPlan.message,
          transactionId: tx.transactionId,
          rolledBack: recoveryPlan.rolledBack,
          rollbackIncomplete: recoveryPlan.rollbackIncomplete,
          restoredPaths: tx.rollback?.restoredPaths || tx.restoredPaths || [],
          unrestoredPaths: tx.rollback?.unrestoredPaths || tx.unrestoredPaths || [],
          externalChangeDetected: tx.rollback?.externalChangeDetected || tx.externalChangeDetected || [],
          rollbackErrors: tx.rollback?.rollbackErrors || [],
          recoveryRequired: true,
        };
        bindAuthoritativeLifecycleControl(payload, lifecycle);
        return fail(`apply_edit_bundle failed: ${tx.error}`, payload);
      }

      const validationResults = Array.isArray(tx.validation?.validationResults)
        ? tx.validation.validationResults
        : [];
      const primaryValidation = validationResults.find((item) => item?.skipped)
        || validationResults[0]
        || null;
      let lastMutation = null;
      const bundleMutations = [];
      for (const absPath of tx.writtenAbs) {
        bundleMutations.push({
          relPath: path.relative(projectRoot, absPath).replace(/\\/g, "/"),
          content: await fsp.readFile(absPath, "utf8"),
        });
      }
      try {
        lastMutation = await recordMutationBatch(
          projectRoot,
          bundleMutations,
          mutationCompensationOptions(tx.journal, {
            projectRoot,
            taskSessionId: mutationTaskSessionId(args),
          })
        );
      } catch (error) {
        const diskRollback = await rollbackJournal(tx.journal);
        const restored = await finalizeDiskRollback(
          tx.journal,
          projectRoot,
          diskRollback,
          null,
          "mutation_bookkeeping_failed"
        );
        return mutationBookkeepingFailure(
          error.message,
          "apply_edit_bundle",
          bundleMutations.map((item) => item.relPath).join(", "),
          restored.rollback
        );
      }
      tx.journal = markMutationStateRecorded(tx.journal, {
        mutationGeneration: lastMutation?.mutationGeneration || 0,
        mutationRevision: lastMutation?.mutationRevision || 0,
        mutationStateRequired: true,
      });
      const checkpoint = recordAutomaticContinuityCheckpoint(
        args,
        tx.writtenAbs,
        primaryValidation,
        lastMutation?.mutationGeneration || 0
      );
      if (!checkpoint.ok) {
        const diskRollback = await rollbackJournal(tx.journal);
        const restored = await finalizeDiskRollback(
          tx.journal,
          projectRoot,
          diskRollback,
          lastMutation?.compensationReceipt,
          "continuity_checkpoint_failed"
        );
        return continuityCheckpointFailure(
          checkpoint,
          "apply_edit_bundle",
          tx.writtenAbs.map((item) => path.relative(projectRoot, item).replace(/\\/g, "/")),
          lastMutation,
          restored.rollback,
          restored.compensation
        );
      }
      tx.journal = await completeMutationJournalCheckpoint(tx.journal, checkpoint);
      const budgetFail = commitDeferredBudgetOrFail({
        mutationCommit: {
          transactionId: tx.journal.transactionId,
          operation: "apply_edit_bundle",
          paths: bundleMutations.map((item) => item.relPath),
        },
      });
      if (budgetFail) return budgetFail;
      const bundleNextSteps = ["Run build_unreal_project after C++ edits."];
      if (primaryValidation?.skipped) {
        bundleNextSteps.unshift("Run static_validate_project before build.");
      }
      const successOptions = {
        operation: "apply_edit_bundle",
        writtenCount: tx.writtenAbs.length,
        preChangeHashes: tx.preChangeHashes,
        transactionId: tx.transactionId,
        ...(lastMutation ? { mutationGeneration: lastMutation.mutationGeneration } : {}),
        buildValidationPending: tx.journal?.status === "awaiting_build",
        continuityCheckpoint: checkpoint,
        nextSteps: bundleNextSteps,
        phase: "editing",
        userMessage: `Applied ${tx.writtenAbs.length} file(s) from bundle`,
        cancellable: false,
      };
      bindCommittedMutationControl(successOptions, committedDeferredBudget, checkpoint);
      return validationToolResult(
        `OK — applied ${tx.writtenAbs.length} file(s) from bundle.`,
        primaryValidation,
        successOptions
      );
    }

    if (name === "static_validate_project") {
      await agentNotify("Running static validation…");
      const activeProject = getActiveProject(CONFIG_PATH);
      let projectRoot = String(args.projectRoot || "").trim();
      if (!projectRoot && activeProject) {
        projectRoot = path.dirname(path.resolve(activeProject));
      }
      if (!projectRoot) {
        const taskSessionId = String(requiredFields(args || {}).taskSessionId || "").trim();
        const taskState = taskSessionId
          ? readTaskState(WORKSPACE_ROOT, taskSessionId)
          : null;
        const taskProject = String(
          taskState?.routeScope?.projectFile || taskState?.projectFile || ""
        ).trim();
        if (taskProject) {
          const resolvedTaskProject = path.resolve(taskProject);
          projectRoot = path.extname(resolvedTaskProject).toLowerCase() === ".uproject"
            ? path.dirname(resolvedTaskProject)
            : resolvedTaskProject;
        }
      }
      if (!projectRoot) {
        const switchGuidance = projectSwitchGuidance(agentRegisteredToolNames());
        const requiredTool = exactCheckpointRebaseTool();
        const blocker = recordRecoveryObligationViaPython(WORKSPACE_ROOT, args, {
          source: "static",
          status: "checkpoint_rebase_required",
          scopeDisposition: "infrastructure",
          errorCode: "STATIC_PROJECT_UNAVAILABLE",
          mutationGeneration: 0,
          requiredTool,
          targetFiles: [],
          message: "Static validation requires an active Unreal project or an explicit projectRoot.",
        });
        const payload = {
          errorCode: "STATIC_PROJECT_UNAVAILABLE",
          retryable: true,
          requiredNextTool: requiredTool.name,
          requiredNextToolArgs: requiredTool.args,
          nextSteps: ["Select an active .uproject, then run static validation again."],
          ...switchGuidance
        };
        bindAuthoritativeLifecycleControl(payload, blocker);
        return fail("No active project and no projectRoot provided.", payload);
      }
      const resolved = path.resolve(projectRoot);
      if (resolved.toLowerCase().endsWith(".uproject")) {
        projectRoot = path.dirname(resolved);
      } else {
        projectRoot = resolved;
      }
      let validationStart;
      try {
        validationStart = await beginValidation(projectRoot);
      } catch (err) {
        if (err && err.errorCode === "MUTATION_STATE_CORRUPT") {
          return fail("Static validation blocked: mutation state corrupt.", {
            errorCode: "MUTATION_STATE_CORRUPT",
            nextSteps: ["Repair .agent/state/mutation.json, then run static_validate_project."],
          });
        }
        throw err;
      }
      const validationScope = validationScopeForTask(
        args,
        validationStart.startGeneration
      );
      if (validationScope.kind === "task_scope_unavailable") {
        const blocker = recordRecoveryObligationViaPython(WORKSPACE_ROOT, args, {
          source: "static",
          status: "checkpoint_rebase_required",
          scopeDisposition: "in_slice",
          errorCode: "STATIC_VALIDATION_SCOPE_UNAVAILABLE",
          mutationGeneration: validationStart.startGeneration,
          requiredTool: {
            name: "unreal_task_checkpoint",
            args: {
              action: "rebase",
              acceptCurrentFiles: true,
              includeGitChanges: false,
            },
          },
          targetFiles: [],
          message: "Static validation could not bind the current task slice; rebase the same task checkpoint.",
        });
        const payload = {
          errorCode: "STATIC_VALIDATION_SCOPE_UNAVAILABLE",
          validationScope,
          retryable: true,
          nextSteps: [
            "Refresh the current task authorization or record the mutation checkpoint.",
            "Use fullAudit=true only when a project-wide audit is explicitly intended.",
          ],
        };
        bindAuthoritativeLifecycleControl(payload, blocker);
        return fail("Static validation could not bind the current task slice.", payload);
      }
      const validation = await runStaticValidation(projectRoot, {
        scopeTargets: validationScope.targets,
      });
      if (validation?.timedOut === true || isValidationInfrastructureFailure(validation)) {
        const firstFinding = Array.isArray(validation?.findings)
          ? validation.findings[0] || {}
          : {};
        const recovery = recordRecoveryObligationViaPython(
          WORKSPACE_ROOT,
          args,
          {
            source: "static",
            status: "environment_recovery",
            scopeDisposition: "infrastructure",
            errorCode: String(firstFinding.code || (validation?.timedOut ? "VALIDATOR_TIMEOUT" : "VALIDATOR_EXEC_FAILED")),
            mutationGeneration: validationStart.startGeneration,
            requiredTool: {
              name: "static_validate_project",
              args: { projectRoot, fullAudit: args.fullAudit === true },
            },
            targetFiles: validationScope.targets || [],
            message: String(firstFinding.message || validation?.reason || "Static validation infrastructure failed."),
          }
        );
        const infrastructurePayload = {
          ok: false,
          operation: "static_validate",
          error: String(validation?.reason || firstFinding.message || "Static validation infrastructure failed."),
          errorCode: String(firstFinding.code || (validation?.timedOut ? "VALIDATOR_TIMEOUT" : "VALIDATOR_EXEC_FAILED")),
          retryable: recovery?.control?.disposition === "require_tool",
          validationScope,
        };
        bindAuthoritativeLifecycleControl(infrastructurePayload, recovery);
        return validationToolResult("STATIC VALIDATION INFRASTRUCTURE FAILURE", validation, infrastructurePayload);
      }
      const severityCounts = (validation.findings || []).reduce((counts, finding) => {
        const key = String(finding.severity || "unknown").toLowerCase();
        counts[key] = (counts[key] || 0) + 1;
        return counts;
      }, {});
      const blockingErrorCount = (validation.findings || []).filter((finding) => (
        finding?.blocking === true
        || (
          finding?.blocking === undefined
          && String(finding?.severity || "").toLowerCase() === "error"
        )
      )).length;
      const validationSummary = validationFailed(validation)
        ? `STATIC VALIDATION FAILED — ${blockingErrorCount} blocking error(s), ${severityCounts.warning || 0} warning(s)`
        : `STATIC VALIDATION PASSED — ${severityCounts.warning || 0} warning(s)`;
      if (validationFailed(validation)) {
        const loopState = recordValidationFailure(
          projectRoot,
          validationStart.startGeneration,
          validation,
          {
            taskSessionId: requiredFields(args).taskSessionId,
            stateRoot: ensureStateRootLayout(resolveAgentStateRoot()),
          }
        );
        if (loopState.blocked) {
          const targetFiles = Array.isArray(validationScope.targets)
            ? validationScope.targets.map(String).filter(Boolean).slice(0, 4)
            : [];
          const requiredTool = targetFiles.length
            ? {
              name: "unreal_code_sketch_claim_validate",
              args: { targetFiles },
            }
            : {
              name: "unreal_task_checkpoint",
              args: {
                action: "rebase",
                acceptCurrentFiles: true,
                includeGitChanges: false,
              },
            };
          const recovery = recordRecoveryObligationViaPython(WORKSPACE_ROOT, args, {
            source: "static",
            status: targetFiles.length
              ? "repair_planning_required"
              : "checkpoint_rebase_required",
            scopeDisposition: "in_slice",
            errorCode: "WORKFLOW_LOOP_BLOCKED",
            failureFingerprint: loopState.fingerprint,
            mutationGeneration: loopState.mutationGeneration,
            requiredTool,
            targetFiles,
            message: "The repeated static failure requires one bounded alternate repair strategy.",
          });
          const automaticReplan = ["require_tool", "checkpoint"].includes(
            String(recovery?.control?.disposition || "")
          );
          const mutationRollback = automaticReplan
            ? null
            : await rollbackPendingMutationJournals(
              pendingMutationQuery(projectRoot, args, loopState.mutationGeneration),
              "static_validation_recovery_exhausted",
              args
            );
          const recoveryGeneration = Number(
            mutationRollback?.reconciliation?.mutationGeneration
            ?? loopState.mutationGeneration
          );
          const blockedPayload = {
            ok: false,
            operation: "static_validate",
            isError: true,
            error: automaticReplan
              ? "Validation repeated; one bounded alternate repair strategy is required."
              : "Validation repeated after the alternate strategy; user direction is required.",
            errorCode: "WORKFLOW_LOOP_BLOCKED",
            retryable: automaticReplan,
            stopCurrentWorkflow: !automaticReplan,
            validationOverrideAvailable: false,
            mutationGeneration: recoveryGeneration,
            validationScope,
            ...(mutationRollback ? { mutationRollback } : {}),
            nextSteps: [
              automaticReplan
                ? `Call ${requiredTool.name} exactly once, use a different repair candidate, then mutate before validating again.`
                : "The failed mutation was rolled back. Review the exhausted strategy before resuming the same task.",
            ],
          };
          bindAuthoritativeLifecycleControl(blockedPayload, recovery);
          return validationToolResult(
            "WORKFLOW BLOCKED: same validation/build failure repeated without a file mutation.",
            validation,
            blockedPayload
          );
        }
        let finish;
        try {
          finish = await finishValidationAndClear(projectRoot, validationStart.startGeneration, {
            passed: false,
            blockingErrorCount,
            proofLevel: "StaticFailed",
          });
        } catch (error) {
          const recovery = recordRecoveryObligationViaPython(WORKSPACE_ROOT, args, {
            source: "static",
            status: "environment_recovery",
            scopeDisposition: "infrastructure",
            errorCode: String(error?.errorCode || "VALIDATION_STATE_CLEANUP_FAILED"),
            mutationGeneration: validationStart.startGeneration,
            requiredTool: { name: "static_validate_project", args: { projectRoot, fullAudit: args.fullAudit === true } },
            targetFiles: validationScope.targets || [],
            message: String(error?.message || "Static validation state cleanup failed."),
          });
          const payload = {
            ok: false,
            operation: "static_validate",
            error: String(error?.message || "Static validation state cleanup failed."),
            errorCode: String(error?.errorCode || "VALIDATION_STATE_CLEANUP_FAILED"),
            retryable: recovery?.control?.disposition === "require_tool",
            validationScope,
          };
          bindAuthoritativeLifecycleControl(payload, recovery);
          return validationToolResult("STATIC VALIDATION CLEANUP FAILURE", validation, payload);
        }
        if (finish.validationStale) {
          const recovery = recordRecoveryObligationViaPython(WORKSPACE_ROOT, args, {
            source: "static",
            status: "revalidate_required",
            scopeDisposition: "in_slice",
            errorCode: "VALIDATION_STALE",
            mutationGeneration: finish.mutationGeneration,
            requiredTool: {
              name: "static_validate_project",
              args: { projectRoot, fullAudit: args.fullAudit === true },
            },
            targetFiles: validationScope.targets || [],
            message: "The project mutated while static validation was running.",
          });
          const payload = {
            validationStale: true,
            mutationGeneration: finish.mutationGeneration,
            nextSteps: ["Re-run static_validate_project after edits settle."],
          };
          bindAuthoritativeLifecycleControl(payload, recovery);
          return fail("Validation stale: project mutated during validation.", payload);
        }
        const validationCheckpoint = recordValidationContinuityCheckpoint(
          args, validation, false, finish.mutationGeneration
        );
        if (validationCheckpoint?.ok !== true) {
          const recovery = recordRecoveryObligationViaPython(WORKSPACE_ROOT, args, {
            source: "static",
            status: "environment_recovery",
            scopeDisposition: "infrastructure",
            errorCode: String(validationCheckpoint?.errorCode || "VALIDATION_CHECKPOINT_FAILED"),
            mutationGeneration: finish.mutationGeneration,
            requiredTool: {
              name: "static_validate_project",
              args: { projectRoot, fullAudit: args.fullAudit === true },
            },
            targetFiles: validationScope.targets || [],
            message: String(validationCheckpoint?.error || "Validation continuity checkpoint failed."),
          });
          const checkpointFailure = {
            ok: false,
            operation: "static_validate",
            error: String(validationCheckpoint?.error || "Validation continuity checkpoint failed."),
            errorCode: String(validationCheckpoint?.errorCode || "VALIDATION_CHECKPOINT_FAILED"),
            isError: true,
            retryable: recovery?.control?.disposition === "require_tool",
            validationScope,
          };
          bindAuthoritativeLifecycleControl(checkpointFailure, recovery);
          return validationToolResult("STATIC VALIDATION CHECKPOINT FAILURE", validation, checkpointFailure);
        }
        const pendingMutationTransactions = await markPendingMutationJournals(
          pendingMutationQuery(projectRoot, args, finish.mutationGeneration),
          "validation_failed",
          {
            proofKind: "static_validation",
            errorCode: "STATIC_VALIDATION_FAILED",
            failedAt: new Date().toISOString(),
          }
        );
        await agentNotify(validationSummary);
        return validationToolResult(validationSummary, validation, {
          ok: false,
          operation: "static_validate",
          error: "Static validation found blocking errors; the scan is fresh but is not a passing build proof.",
          errorCode: "STATIC_VALIDATION_FAILED",
          retryable: false,
          doNotRetry: ["build_unreal_project"],
          stopCurrentWorkflow: false,
          validationOverrideAvailable: true,
          buildAllowedForValidatedGeneration: false,
          requiredNextTool: String(
            validationCheckpoint?.control?.requiredTool?.name || ""
          ) || undefined,
          requiredNextToolArgs: validationCheckpoint?.control?.requiredTool?.args
            && typeof validationCheckpoint.control.requiredTool.args === "object"
            ? { ...validationCheckpoint.control.requiredTool.args }
            : undefined,
          continuityCheckpoint: validationCheckpoint,
          validationPassed: finish.validationPassed,
          validationStatus: finish.validationStatus,
          validationBlockingErrorCount: finish.validationBlockingErrorCount,
          proofLevel: finish.validationProofLevel,
          validatedGeneration: finish.validatedGeneration,
          mutationGeneration: finish.mutationGeneration,
          validationScope,
          pendingMutationTransactions,
          nextSteps: [
            "Read the first blocking finding, then mutate the bounded task slice using the authoritative recovery tool.",
            "Run static_validate_project again only after the recovery mutation creates a new generation.",
            "Use validationOverride=true only with a concrete audit note when authoritative UBT evidence is explicitly required.",
          ],
        });
      }
      recordValidationSuccess(
        projectRoot,
        validationStart.startGeneration,
        durableGuardScopeForArgs(args, {
          projectRoot,
          mutationGeneration: validationStart.startGeneration,
        })
      );
      let finish;
      try {
        finish = await finishValidationAndClear(projectRoot, validationStart.startGeneration, {
          passed: true,
          blockingErrorCount: 0,
          proofLevel: "StaticVerified",
        });
      } catch (error) {
        const recovery = recordRecoveryObligationViaPython(WORKSPACE_ROOT, args, {
          source: "static",
          status: "environment_recovery",
          scopeDisposition: "infrastructure",
          errorCode: String(error?.errorCode || "VALIDATION_STATE_CLEANUP_FAILED"),
          mutationGeneration: validationStart.startGeneration,
          requiredTool: { name: "static_validate_project", args: { projectRoot, fullAudit: args.fullAudit === true } },
          targetFiles: validationScope.targets || [],
          message: String(error?.message || "Static validation state cleanup failed."),
        });
        const payload = {
          ok: false,
          operation: "static_validate",
          error: String(error?.message || "Static validation state cleanup failed."),
          errorCode: String(error?.errorCode || "VALIDATION_STATE_CLEANUP_FAILED"),
          retryable: recovery?.control?.disposition === "require_tool",
          validationScope,
        };
        bindAuthoritativeLifecycleControl(payload, recovery);
        return validationToolResult("STATIC VALIDATION CLEANUP FAILURE", validation, payload);
      }
      if (finish.validationStale) {
        const recovery = recordRecoveryObligationViaPython(WORKSPACE_ROOT, args, {
          source: "static",
          status: "revalidate_required",
          scopeDisposition: "in_slice",
          errorCode: "VALIDATION_STALE",
          mutationGeneration: finish.mutationGeneration,
          requiredTool: {
            name: "static_validate_project",
            args: { projectRoot, fullAudit: args.fullAudit === true },
          },
          targetFiles: validationScope.targets || [],
          message: "The project mutated while static validation was running.",
        });
        const payload = {
          validationStale: true,
          mutationGeneration: finish.mutationGeneration,
          nextSteps: ["Re-run static_validate_project after edits settle."],
        };
        bindAuthoritativeLifecycleControl(payload, recovery);
        return fail("Validation stale: project mutated during validation.", payload);
      }
      const validationCheckpoint = recordValidationContinuityCheckpoint(
        args, validation, true, finish.mutationGeneration
      );
      if (validationCheckpoint?.ok !== true) {
        const recovery = recordRecoveryObligationViaPython(WORKSPACE_ROOT, args, {
          source: "static",
          status: "environment_recovery",
          scopeDisposition: "infrastructure",
          errorCode: String(validationCheckpoint?.errorCode || "VALIDATION_CHECKPOINT_FAILED"),
          mutationGeneration: finish.mutationGeneration,
          requiredTool: {
            name: "static_validate_project",
            args: { projectRoot, fullAudit: args.fullAudit === true },
          },
          targetFiles: validationScope.targets || [],
          message: String(validationCheckpoint?.error || "Validation continuity checkpoint failed."),
        });
        const checkpointFailure = {
          ok: false,
          operation: "static_validate",
          error: String(validationCheckpoint?.error || "Validation continuity checkpoint failed."),
          errorCode: String(validationCheckpoint?.errorCode || "VALIDATION_CHECKPOINT_FAILED"),
          isError: true,
          retryable: recovery?.control?.disposition === "require_tool",
          validationScope,
        };
        bindAuthoritativeLifecycleControl(checkpointFailure, recovery);
        return validationToolResult("STATIC VALIDATION CHECKPOINT FAILURE", validation, checkpointFailure);
      }
      await agentNotify(validationSummary);
      return validationToolResult(validationSummary, validation, {
        operation: "static_validate",
        validationPassed: finish.validationPassed,
        validationStatus: finish.validationStatus,
        validationBlockingErrorCount: finish.validationBlockingErrorCount,
        proofLevel: finish.validationProofLevel,
        validatedGeneration: finish.validatedGeneration,
        mutationGeneration: finish.mutationGeneration,
        validationScope,
        continuityCheckpoint: validationCheckpoint,
        nextSteps: ["Run build_unreal_project if C++ or Build.cs changed."],
        phase: "validating",
        userMessage: validationSummary,
        cancellable: false
      });
    }

    if (name === "search_files") {
      const resolution = await resolveReadToolPath(args.path || ".");
      const base = resolution.absolutePath;
      const maxResults = Math.max(1, Math.min(Number(args.maxResults || 100), 1000));
      const useRegex = !!args.regex;
      const query = String(args.query || "");
      if (!query) return fail("query must not be empty");
      const explicitFileNameMode = typeof args.matchFileNames === "boolean";
      const looksLikeFileNameQuery = useRegex
        ? /\\\.(?:h|hpp|cpp|c|cc|cxx|inl|cs|uproject|uplugin)(?:\\?\$|\$|\)|\]|$)/i.test(query)
        : /(?:^|[\\/.])[^\\/]+\.(?:h|hpp|cpp|c|cc|cxx|inl|cs|uproject|uplugin)$/i.test(query.trim());
      const matchFileNames = args.matchFileNames === true
        || (!explicitFileNameMode && looksLikeFileNameQuery);
      const effectiveSearchArgs = matchFileNames && !explicitFileNameMode
        ? { ...args, matchFileNames: true }
        : args;

      const baseStat = await statSafe(base);
      if (!baseStat) {
        return fail(`not found: ${args.path || "."}`, {
          errorCode: "SEARCH_PATH_NOT_FOUND",
          retryable: false,
          doNotRetry: ["search_files"],
          nextSteps: ["Call get_active_project / list_directory, then search under project://Source."],
        });
      }
      if (!baseStat.isDirectory() && !baseStat.isFile()) {
        return fail(`not a searchable path: ${args.path || "."}`, {
          errorCode: "SEARCH_PATH_INVALID",
          retryable: false,
        });
      }
      const mutationGeneration = await resolveMutationGenerationForRead(resolution, base);
      const readContext = buildReadEvidenceContext(base, baseStat, resolution, {
        mutationGeneration,
        scopeSignature: fileStatSignature(baseStat),
        taskSessionId: requiredFields(args).taskSessionId,
        evidenceSessionId: args.sessionId,
        taskAuthorization: args.taskAuthorization,
        detachedReadOnlyObservation,
      });
      const guard = prepareReadGuard("search_files", effectiveSearchArgs, readContext);
      const blocked = applyReadGuard("search_files", guard, readContext);
      if (blocked) return blocked;
      const recoveryBlocked = applyBuildRecoveryEvidenceGuard("search_files", readContext, args);
      if (recoveryBlocked) return recoveryBlocked;
      const normalizedArgs = guard.normalizedArgs;

      const ignoreDirs = new Set([
        ".git", ".vs", ".idea", "Binaries", "DerivedDataCache", "Intermediate",
        "Saved", "node_modules", ".gradle", ".cache"
      ]);

      const matcher = useRegex
        ? new RegExp(query, "i")
        : null;

      const results = [];
      const fileNameResults = [];
      let filesSeen = 0;
      let filesSkippedBySize = 0;
      let lastReservationHeartbeat = Date.now();
      let reservationHeartbeatFailure = null;

      function resultLimitReached() {
        return results.length >= maxResults
          && (!matchFileNames || fileNameResults.length >= maxResults);
      }

      async function walk(p) {
        if (reservationHeartbeatFailure) return;
        if (Date.now() - lastReservationHeartbeat > 30_000) {
          const hb = runBudgetOp(
            heartbeatRouteReservation,
            String(pendingBudgetReservation?.id || "")
          );
          if (hb && hb.ok === false) {
            reservationHeartbeatFailure = fail(
              hb.error || "Reservation heartbeat failed during search_files.",
              {
                errorCode: hb.errorCode || "TASK_RESERVATION_HEARTBEAT_FAILED",
                retryable: false,
                stopCurrentWorkflow: true,
              }
            );
            try {
              runBudgetOp(
                rollbackRouteReservation,
                String(pendingBudgetReservation?.id || "")
              );
            } catch {
              // Best-effort rollback after heartbeat failure.
            }
            return;
          }
          lastReservationHeartbeat = Date.now();
        }
        if (resultLimitReached() || filesSeen >= SEARCH_MAX_FILES) return;
        await assertReadChildContained(p, resolution);
        const st = await statSafe(p);
        if (!st) return;

        if (st.isDirectory()) {
          const dirName = path.basename(p);
          if (ignoreDirs.has(dirName)) return;
          const entries = await fsp.readdir(p, { withFileTypes: true });
          for (const e of entries) {
            if (reservationHeartbeatFailure) break;
            await walk(path.join(p, e.name));
            if (resultLimitReached() || filesSeen >= SEARCH_MAX_FILES) break;
          }
          return;
        }

        if (!st.isFile()) return;
        filesSeen++;

        if (matchFileNames && fileNameResults.length < maxResults) {
          const relativeFile = path.relative(base, p).replace(/\\/g, "/");
          const basename = path.basename(p);
          const fileNameHit = useRegex
            ? matcher.test(basename)
            : basename.toLowerCase().includes(query.toLowerCase());
          if (fileNameHit) {
            fileNameResults.push({
              file: `${displayPath(resolution).replace(/\/$/, "")}/${relativeFile}`,
              basename,
            });
          }
        }

        if (st.size > MAX_READ_BYTES) {
          filesSkippedBySize++;
          return;
        }
        const buf = await fsp.readFile(p);
        if (!isTextLikely(buf)) return;

        const content = buf.toString("utf8");
        const lines = content.split(/\r?\n/);
        for (let i = 0; i < lines.length; i++) {
          const line = lines[i];
          const hit = useRegex ? matcher.test(line) : line.toLowerCase().includes(query.toLowerCase());
          if (hit && results.length < maxResults) {
            results.push({
              file: `${displayPath(resolution).replace(/\/$/, "")}/${path.relative(base, p).replace(/\\/g, "/")}`,
              line: i + 1,
              text: line.slice(0, 500)
            });
            if (results.length >= maxResults) break;
          }
        }
      }

      await walk(base);
      if (reservationHeartbeatFailure) return reservationHeartbeatFailure;
      const payload = {
        path: pathMetadata(resolution),
        results,
        filesSeen,
        filesSkippedBySize,
        searchComplete: filesSeen < SEARCH_MAX_FILES && filesSkippedBySize === 0,
        incompleteReasons: filesSkippedBySize > 0 ? ["large_files_skipped"] : [],
      };
      if (matchFileNames) payload.fileNameResults = fileNameResults;
      if (!explicitFileNameMode && matchFileNames) {
        payload.fileNameMatchMode = "inferred_from_filename_shaped_query";
      }
      if (results.length === 0 && !matchFileNames) {
        payload.discoveryHint = "No content match was found. If the query names a file or extension, retry once with matchFileNames=true.";
        payload.suggestedToolCalls = [{
          tool: "search_files",
          args: { ...args, matchFileNames: true },
        }];
      }
      const output = JSON.stringify(payload, null, 2);
      const completeFileNameMiss = Boolean(
        matchFileNames
        && !useRegex
        && fileNameResults.length === 0
        && payload.searchComplete
      );
      const absentProjectPath = scopedAbsentEvidencePath(
        resolution.projectRelativePath,
        query
      );
      const discoveryPaths = [...results.map((entry) => entry.file), ...fileNameResults.map((entry) => entry.file)];
      const budgetFail = commitDeferredBudgetOrFail(
        completeFileNameMiss
          ? {
            absentEvidence: {
              projectRelativePath: absentProjectPath,
              query,
              scopePath: displayPath(resolution),
              searchComplete: true,
            },
          }
          : {
            inspectionDiscoveryCandidates: { paths: discoveryPaths },
          }
      );
      if (budgetFail) return budgetFail;
      recordReadSuccess("search_files", normalizedArgs, {
        ...readContext,
        evidenceHash: sha256Text(output),
      }, output);
      commitBuildRecoveryEvidence("search_files", readContext, args);
      return attachCommittedToolOutcomeControl(
        text(output),
        committedDeferredBudget,
        "search_files"
      );
    }

    if (name === "run_command") {
      if (!ALLOW_COMMANDS) return fail("run_command blocked. Set ALLOW_COMMANDS=1 to enable.");
      const command = String(args.command || "");
      if (!allowedCommandBase(command)) {
        return fail(`command not allowlisted or blocked: ${command}`);
      }
      const cwd = normalizeRelPath(args.cwd || ".");
      const s = await statSafe(cwd);
      if (!s || !s.isDirectory()) return fail(`cwd not found or not directory: ${args.cwd || "."}`);
      const result = await execCommand(command, cwd, Number(args.timeoutMs || COMMAND_TIMEOUT_MS));
      return text(JSON.stringify(result, null, 2));
    }

    if (name === "run_unreal_automation_tests") {
      if (!ALLOW_UNREAL_BUILD) {
        const gate = boundedAutomationRetryGate(args);
        const blocker = recordRecoveryObligationViaPython(WORKSPACE_ROOT, args, {
          source: "automation",
          status: gate.status,
          scopeDisposition: "infrastructure",
          errorCode: "AUTOMATION_DISABLED",
          mutationGeneration: 0,
          requiredTool: gate.requiredTool,
          targetFiles: [],
          message: "Automation execution is disabled by server configuration.",
        });
        const payload = {
          errorCode: "AUTOMATION_DISABLED",
          retryable: true,
          requiredNextTool: gate.requiredTool.name,
          requiredNextToolArgs: gate.requiredTool.args,
        };
        bindAuthoritativeLifecycleControl(payload, blocker);
        return fail("run_unreal_automation_tests blocked. Set ALLOW_UNREAL_BUILD=1 to enable.", payload);
      }
      const planResult = await resolveBuildPlan(WORKSPACE_ROOT, CONFIG_PATH, args);
      if (!planResult.ok || !planResult.build) {
        const gate = boundedAutomationRetryGate(args);
        const blocker = recordRecoveryObligationViaPython(WORKSPACE_ROOT, args, {
          source: "automation",
          status: gate.status,
          scopeDisposition: "infrastructure",
          errorCode: "AUTOMATION_PLAN_RESOLUTION_FAILED",
          mutationGeneration: 0,
          requiredTool: gate.requiredTool,
          targetFiles: [],
          message: String(planResult.error || "Could not resolve Unreal Automation plan."),
        });
        const payload = {
          errorCode: "AUTOMATION_PLAN_RESOLUTION_FAILED",
          retryable: true,
          requiredNextTool: gate.requiredTool.name,
          requiredNextToolArgs: gate.requiredTool.args,
        };
        bindAuthoritativeLifecycleControl(payload, blocker);
        return fail(planResult.error || "Could not resolve Unreal Automation plan.", payload);
      }
      const projectPath = path.resolve(planResult.build.projectPath);
      const projectRoot = path.dirname(projectPath);
      const taskProjectBinding = validateTaskProofProject(args, projectPath);
      if (!taskProjectBinding.ok) {
        return taskProofFailure(
          taskProjectBinding,
          "Automation resolved a different project than the authoritative task route."
        );
      }
      const taskState = taskProjectBinding.state;
      const verification = taskState?.buildVerification
        && typeof taskState.buildVerification === "object"
        ? taskState.buildVerification
        : {};
      if (taskProjectBinding.active) {
        const persistedProject = String(verification.projectFile || "").trim();
        if (
          persistedProject
          && canonicalProjectIdentity(persistedProject, WORKSPACE_ROOT)
            !== canonicalProjectIdentity(projectPath, WORKSPACE_ROOT)
        ) {
          return taskProofFailure({
            ...taskProjectBinding,
            ok: false,
            errorCode: "AUTOMATION_PROOF_PROJECT_MISMATCH",
            error: "Automation project does not match the persisted successful-build proof.",
            expectedProject: persistedProject,
            observedProject: projectPath,
          });
        }
        const requiredArgs = taskRequiredToolArgs(taskState);
        const expectedEngineRoot = String(
          verification.engineRoot || requiredArgs.engineRoot || ""
        ).trim();
        if (!expectedEngineRoot) {
          return taskEngineProofMismatch(
            taskProjectBinding,
            planResult.build.engineRoot,
            "",
            "AUTOMATION_ENGINE_PROOF_UNBOUND"
          );
        }
        if (
          canonicalProjectIdentity(expectedEngineRoot, WORKSPACE_ROOT)
          !== canonicalProjectIdentity(planResult.build.engineRoot, WORKSPACE_ROOT)
        ) {
          return taskEngineProofMismatch(
            taskProjectBinding,
            planResult.build.engineRoot,
            expectedEngineRoot,
            "AUTOMATION_PROOF_ENGINE_MISMATCH"
          );
        }
      }
      const mutation = await readMutationState(projectRoot);
      const mutationGeneration = Number(mutation.mutationGeneration || 0);
      const automationScope = automationScopeForTask(args, mutationGeneration);
      if (automationScope.kind === "task_scope_unavailable") {
        const requiredTool = exactCheckpointRebaseTool();
        const blocker = recordRecoveryObligationViaPython(WORKSPACE_ROOT, args, {
          source: "automation",
          status: "checkpoint_rebase_required",
          scopeDisposition: "out_of_slice",
          errorCode: "AUTOMATION_SCOPE_UNAVAILABLE",
          mutationGeneration,
          requiredTool,
          targetFiles: [],
          message: "Automation could not bind declarations to the active task slice.",
        });
        const payload = {
          errorCode: "AUTOMATION_SCOPE_UNAVAILABLE",
          automationScope,
          retryable: true,
          requiredNextTool: requiredTool.name,
          requiredNextToolArgs: requiredTool.args,
        };
        bindAuthoritativeLifecycleControl(payload, blocker);
        return fail("Automation could not bind the current task slice.", payload);
      }
      const scopeTargets = Array.isArray(automationScope.targets)
        ? automationScope.targets
        : [];
      const discovery = discoverAutomationTests(projectRoot, {
        scopeTargets,
        maxFiles: 5000,
      });
      const unmappedScopeTargets = Array.isArray(discovery.unmappedScopeTargets)
        ? discovery.unmappedScopeTargets.map(String).filter(Boolean)
        : [];
      if (discovery.scopeBound === true && unmappedScopeTargets.length) {
        const requiredTool = scopeTargets.length
          ? {
            name: "unreal_code_sketch_claim_validate",
            args: { targetFiles: scopeTargets.slice(0, 4) },
          }
          : exactCheckpointRebaseTool();
        const blocker = recordRecoveryObligationViaPython(WORKSPACE_ROOT, args, {
          source: "automation",
          status: scopeTargets.length
            ? "repair_planning_required"
            : "checkpoint_rebase_required",
          scopeDisposition: "out_of_slice",
          errorCode: "AUTOMATION_SCOPE_UNMAPPED",
          mutationGeneration,
          requiredTool,
          targetFiles: scopeTargets,
          message: "One or more active-slice targets could not be mapped to an Automation coverage module.",
        });
        const payload = {
          errorCode: "AUTOMATION_SCOPE_UNMAPPED",
          automationCoverage: discovery,
          unmappedScopeTargets,
          retryable: true,
          requiredNextTool: requiredTool.name,
          requiredNextToolArgs: requiredTool.args,
        };
        bindAuthoritativeLifecycleControl(payload, blocker);
        return fail("Automation coverage could not map every active-slice target.", payload);
      }
      if (discovery.truncated) {
        const gate = boundedAutomationRetryGate(args, taskState);
        const blocker = recordRecoveryObligationViaPython(WORKSPACE_ROOT, args, {
          source: "automation",
          status: gate.status,
          scopeDisposition: "infrastructure",
          errorCode: "AUTOMATION_DISCOVERY_TRUNCATED",
          mutationGeneration,
          requiredTool: gate.requiredTool,
          targetFiles: scopeTargets,
          message: "Automation declaration discovery was truncated before coverage could be proven.",
        });
        const payload = {
          errorCode: "AUTOMATION_DISCOVERY_TRUNCATED",
          automationCoverage: discovery,
          retryable: true,
          requiredNextTool: gate.requiredTool.name,
          requiredNextToolArgs: gate.requiredTool.args,
        };
        bindAuthoritativeLifecycleControl(payload, blocker);
        return fail("Automation declaration discovery was truncated.", payload);
      }
      if (!discovery.count) {
        const requiredTool = scopeTargets.length
          ? {
            name: "unreal_code_sketch_claim_validate",
            args: { targetFiles: scopeTargets.slice(0, 4) },
          }
          : exactCheckpointRebaseTool();
        const blocker = recordRecoveryObligationViaPython(WORKSPACE_ROOT, args, {
          source: "automation",
          status: scopeTargets.length
            ? "repair_planning_required"
            : "checkpoint_rebase_required",
          scopeDisposition: "infrastructure",
          errorCode: "NO_AUTOMATION_TESTS_DECLARED",
          mutationGeneration,
          requiredTool,
          targetFiles: scopeTargets,
          message: "The pending Automation gate no longer has discoverable declarations.",
        });
        const payload = {
          errorCode: "NO_AUTOMATION_TESTS_DECLARED",
          automationCoverage: discovery,
          retryable: true,
          requiredNextTool: requiredTool.name,
          requiredNextToolArgs: requiredTool.args,
        };
        bindAuthoritativeLifecycleControl(payload, blocker);
        return fail("No project Automation test declarations were found.", payload);
      }
      const boundFilters = Array.isArray(verification.testFilters)
        ? verification.testFilters.map(String).map((item) => item.trim()).filter(Boolean)
        : [String(verification.testFilter || "").trim()].filter(Boolean);
      const requestedFilters = Array.isArray(args.testFilters)
        ? args.testFilters.map(String).map((item) => item.trim()).filter(Boolean)
        : [String(args.testFilter || "").trim()].filter(Boolean);
      const testFilters = boundFilters.length
        ? (requestedFilters.length ? requestedFilters : boundFilters)
        : (requestedFilters.length ? requestedFilters : discovery.suggestedFilters);
      if (!testFilters.length) {
        const requiredTool = scopeTargets.length
          ? {
            name: "unreal_code_sketch_claim_validate",
            args: { targetFiles: scopeTargets.slice(0, 4) },
          }
          : exactCheckpointRebaseTool();
        const blocker = recordRecoveryObligationViaPython(WORKSPACE_ROOT, args, {
          source: "automation",
          status: scopeTargets.length
            ? "repair_planning_required"
            : "checkpoint_rebase_required",
          scopeDisposition: "infrastructure",
          errorCode: "AUTOMATION_FILTER_REQUIRED",
          mutationGeneration,
          requiredTool,
          targetFiles: scopeTargets,
          message: "Automation discovery did not produce a concrete bounded filter set.",
        });
        const payload = {
          errorCode: "AUTOMATION_FILTER_REQUIRED",
          declaredTests: discovery.names.slice(0, 100),
          retryable: true,
          requiredNextTool: requiredTool.name,
          requiredNextToolArgs: requiredTool.args,
        };
        bindAuthoritativeLifecycleControl(payload, blocker);
        return fail("Automation tests require one or more concrete server-derived filters.", payload);
      }
      if (testFilters.length > MAX_AUTOMATION_FILTERS) {
        const requiredTool = exactCheckpointRebaseTool();
        const blocker = recordRecoveryObligationViaPython(WORKSPACE_ROOT, args, {
          source: "automation",
          status: "checkpoint_rebase_required",
          scopeDisposition: "infrastructure",
          errorCode: "AUTOMATION_FILTER_SET_TOO_LARGE",
          mutationGeneration,
          requiredTool,
          targetFiles: scopeTargets,
          message: `Automation requires ${testFilters.length} exact filters; the bounded maximum is ${MAX_AUTOMATION_FILTERS}.`,
        });
        const payload = {
          errorCode: "AUTOMATION_FILTER_SET_TOO_LARGE",
          filterCount: testFilters.length,
          maxFilters: MAX_AUTOMATION_FILTERS,
          retryable: true,
          requiredNextTool: requiredTool.name,
          requiredNextToolArgs: requiredTool.args,
        };
        bindAuthoritativeLifecycleControl(payload, blocker);
        return fail("Automation coverage exceeds the bounded exact-filter contract.", payload);
      }
      if (boundFilters.length && JSON.stringify(testFilters) !== JSON.stringify(boundFilters)) {
        return fail("Automation filters do not match the pending server-owned coverage plan.", {
          errorCode: "AUTOMATION_FILTER_BINDING_MISMATCH",
          requiredNextTool: "run_unreal_automation_tests",
          requiredNextToolArgs: { testFilters: boundFilters },
          retryable: true,
        });
      }
      if (
        boundFilters.length
        && Number(verification.mutationGeneration || 0) !== mutationGeneration
      ) {
        const revalidation = recordRecoveryObligationViaPython(WORKSPACE_ROOT, args, {
          source: "automation",
          status: "revalidate_required",
          scopeDisposition: "in_slice",
          errorCode: "AUTOMATION_GENERATION_STALE",
          mutationGeneration,
          requiredTool: {
            name: "static_validate_project",
            args: { projectRoot, fullAudit: false },
          },
          targetFiles: scopeTargets,
          message: "Automation proof is stale for the current mutation generation.",
        });
        const payload = {
          errorCode: "AUTOMATION_GENERATION_STALE",
          retryable: false,
        };
        bindAuthoritativeLifecycleControl(payload, revalidation);
        return fail("Automation proof is stale for the current mutation generation.", payload);
      }
      const logPath = path.join(projectRoot, ".agent", "logs", "latest-automation.log");
      const executions = [];
      for (const testFilter of testFilters) {
        const execution = await runAutomationTests({
          engineRoot: planResult.build.engineRoot,
          projectPath,
          testFilter,
          timeoutMs: Number(args.timeoutMs || 30 * 60 * 1000),
          logPath,
          scopeTargets,
        });
        executions.push(execution);
        if (execution.ok !== true) break;
      }
      const failedExecution = executions.find((item) => item.ok !== true) || null;
      const succeededCount = executions.reduce(
        (total, item) => total + Number(item.succeededCount || 0),
        0
      );
      const failedCount = executions.reduce(
        (total, item) => total + Number(item.failedCount || 0),
        0
      );
      const payload = {
        ok: !failedExecution && executions.length === testFilters.length,
        proofLevel: !failedExecution ? "AutomationPassed" : "AutomationFailed",
        errorCode: failedExecution?.errorCode || "",
        error: failedExecution?.error || "",
        testFilters,
        declaredTestCount: discovery.count,
        declaredTests: discovery.names.slice(0, 100),
        succeededCount,
        failedCount,
        queueEmpty: executions.length === testFilters.length
          && executions.every((item) => item.queueEmpty === true),
        exitCode: Number(failedExecution?.exitCode ?? 0),
        timedOut: executions.some((item) => item.timedOut === true),
        fullLogPath: failedExecution?.fullLogPath || executions.at(-1)?.fullLogPath || logPath,
        mutationGeneration,
        automationScope,
      };
      if (args.verboseOutput === true) {
        payload.stdout = executions.map((item) => String(item.stdout || "")).join("\n").slice(-16000);
        payload.stderr = executions.map((item) => String(item.stderr || "")).join("\n").slice(-8000);
      }
      if (!payload.ok) {
        const mutationFailure = payload.failedCount > 0
          || payload.errorCode === "AUTOMATION_TEST_FAILED";
        if (mutationFailure) {
          payload.pendingMutationTransactions = await markPendingMutationJournals(
            pendingMutationQuery(projectRoot, args, payload.mutationGeneration),
            "build_failed",
            {
              proofKind: "automation",
              errorCode: payload.errorCode || "AUTOMATION_FAILED",
              failedAt: new Date().toISOString(),
            }
          );
        }
        const recovery = recordRecoveryObligationViaPython(
          WORKSPACE_ROOT,
          args,
          mutationFailure
            ? {
              source: "automation",
              status: "evidence_required",
              scopeDisposition: "in_slice",
              errorCode: payload.errorCode || "AUTOMATION_TEST_FAILED",
              mutationGeneration,
              requiredTool: {
                name: "read_unreal_logs",
                args: {
                  mode: "first_error",
                  fileName: "latest-automation.log",
                  maxFiles: 1,
                  maxLines: 200,
                  summaryOnly: true,
                },
              },
              targetFiles: scopeTargets,
              message: payload.error || "A bound Automation test failed.",
            }
            : {
              source: "automation",
              status: "environment_recovery",
              scopeDisposition: "infrastructure",
              errorCode: payload.errorCode || "AUTOMATION_PROCESS_FAILED",
              mutationGeneration,
              requiredTool: {
                name: "run_unreal_automation_tests",
                args: { testFilters },
              },
              targetFiles: scopeTargets,
              message: payload.error || "Automation infrastructure did not produce complete proof.",
            }
        );
        bindAuthoritativeLifecycleControl(payload, recovery);
        return fail("Unreal Automation exit gate failed.", {
          ...payload,
          retryable: recovery?.control?.disposition === "require_tool",
          nextSteps: ["Inspect latest-automation.log, fix the first failing project test, then rebuild the new mutation before rerunning Automation."],
        });
      }
      const completion = completeTaskAfterBuildViaPython(
        WORKSPACE_ROOT,
        args,
        {
          proofLevel: payload.proofLevel,
          proofKind: "automation",
          mutationGeneration: payload.mutationGeneration,
          buildLogPath: payload.fullLogPath,
          automationFilters: testFilters,
          automationSucceededCount: payload.succeededCount,
          automationFailedCount: payload.failedCount,
          automationQueueEmpty: payload.queueEmpty,
          projectFile: projectPath,
          engineRoot: planResult.build.engineRoot,
          resolvedEngineVersion: String(verification.resolvedEngineVersion || ""),
        }
      );
      payload.taskLifecycle = completion?.ok === true && completion?.active === true
        ? {
          status: completion.automationBatchAdvanced === true
            ? "automation_batch_advanced"
            : "slice_advanced",
          routeOwnershipReleased: false,
          completedSliceId: String(completion.completedSliceId || ""),
          activeSliceId: String(completion.activeSliceId || ""),
          pendingSlices: Array.isArray(completion.pendingSlices)
            ? completion.pendingSlices.map(String)
            : [],
          ...(completion.automationBatchAdvanced === true ? {
            automationBatchAdvanced: true,
            filterBatchIndex: Number(completion.filterBatchIndex || 0),
            filterBatchCount: Number(completion.filterBatchCount || 1),
            testFilters: Array.isArray(completion.testFilters)
              ? completion.testFilters.map(String)
              : [],
          } : {}),
          taskAuthorization: completion.taskAuthorization || undefined,
          toolRoute: completion.toolRoute || undefined,
        }
        : completion?.ok === true
          ? { status: "completed", routeOwnershipReleased: true }
          : {
            status: "completion_failed",
            routeOwnershipReleased: false,
            errorCode: String(completion?.errorCode || "TASK_AUTOMATION_COMPLETION_FAILED"),
          };
      bindAuthoritativeLifecycleControl(payload, completion);
      if (completion?.ok !== true) {
        payload.ok = false;
        payload.errorCode = String(completion?.errorCode || "TASK_AUTOMATION_COMPLETION_FAILED");
        payload.error = String(completion?.error || "Automation proof could not be committed to the task lifecycle.");
        payload.retryable = false;
        return fail("Automation proof passed, but task lifecycle completion failed.", payload);
      }
      if (completion?.ok === true && completion?.automationBatchAdvanced !== true) {
        payload.finalizedMutationTransactions = await finalizePendingBuildJournals(
          pendingMutationQuery(projectRoot, args, payload.mutationGeneration),
          "completed"
        );
      }
      try { await server.sendToolListChanged(); } catch { /* advisory */ }
      return text(JSON.stringify(payload, null, 2));
    }

    if (name === "build_unreal_project") {
      if (!ALLOW_UNREAL_BUILD) {
        const gate = boundedBuildRetryGate(args);
        const blocker = recordRecoveryObligationViaPython(WORKSPACE_ROOT, args, {
          source: "build",
          status: gate.status,
          scopeDisposition: "infrastructure",
          errorCode: "BUILD_DISABLED",
          mutationGeneration: 0,
          requiredTool: gate.requiredTool,
          targetFiles: [],
          message: "Unreal build execution is disabled by server configuration.",
        });
        const payload = {
          errorCode: "BUILD_DISABLED",
          retryable: true,
          requiredNextTool: gate.requiredTool.name,
          requiredNextToolArgs: gate.requiredTool.args,
          nextSteps: ["Rerun the root integrated installer, choose AGENT authority for a trusted project, restart LM Studio, then retry."]
        };
        bindAuthoritativeLifecycleControl(payload, blocker);
        return fail("build_unreal_project blocked. Set ALLOW_UNREAL_BUILD=1 to enable.", payload);
      }

      const planResult = await resolveBuildPlan(WORKSPACE_ROOT, CONFIG_PATH, args);
      if (!planResult.ok || !planResult.build) {
        const gate = boundedBuildRetryGate(args);
        const blocker = recordRecoveryObligationViaPython(WORKSPACE_ROOT, args, {
          source: "build",
          status: gate.status,
          scopeDisposition: "infrastructure",
          errorCode: "BUILD_PLAN_RESOLUTION_FAILED",
          mutationGeneration: 0,
          requiredTool: gate.requiredTool,
          targetFiles: [],
          message: String(planResult.error || "Could not resolve Unreal build plan."),
        });
        const payload = {
          errorCode: "BUILD_PLAN_RESOLUTION_FAILED",
          retryable: true,
          userMessage: "Build plan could not be resolved for the active project.",
          agentInstruction: `Call ${gate.requiredTool.name} with requiredNextToolArgs exactly once; do not invent or hard-code a project path.`,
          requiredNextTool: gate.requiredTool.name,
          requiredNextToolArgs: gate.requiredTool.args,
          nextSteps: [
            `Use the server-owned ${gate.requiredTool.name} recovery contract.`,
          ],
        };
        bindAuthoritativeLifecycleControl(payload, blocker);
        return fail(planResult.error || "Could not resolve Unreal build plan.", payload);
      }

      const build = planResult.build;
      if (!build.buildTool || !(await exists(build.buildTool))) {
        const gate = boundedBuildRetryGate(args);
        const blocker = recordRecoveryObligationViaPython(WORKSPACE_ROOT, args, {
          source: "build",
          status: gate.status,
          scopeDisposition: "infrastructure",
          errorCode: "BUILD_TOOL_UNAVAILABLE",
          mutationGeneration: 0,
          requiredTool: gate.requiredTool,
          targetFiles: [],
          message: `Unreal build tool not found: ${build.buildTool || "not resolved"}`,
        });
        const payload = {
          errorCode: "BUILD_TOOL_UNAVAILABLE",
          retryable: true,
          requiredNextTool: gate.requiredTool.name,
          requiredNextToolArgs: gate.requiredTool.args,
        };
        bindAuthoritativeLifecycleControl(payload, blocker);
        return fail(`Unreal build tool not found: ${build.buildTool || "not resolved"}`, payload);
      }

      let projectPath = build.projectPath;
      const rawProject = String(args.project || "");
      if (path.isAbsolute(rawProject)) {
        if (!args.allowAbsoluteProject) {
          const rel = path.relative(WORKSPACE_ROOT, path.resolve(rawProject));
          if (rel.startsWith("..") || path.isAbsolute(rel)) {
            return fail("absolute project path outside WORKSPACE_ROOT blocked. Move project under WORKSPACE_ROOT or pass allowAbsoluteProject=true intentionally.");
          }
        }
        projectPath = path.resolve(rawProject);
      }

      if (!(await exists(projectPath))) return fail(`uproject not found: ${projectPath}`);
      if (!projectPath.toLowerCase().endsWith(".uproject")) return fail("project must be a .uproject file");

      const taskProjectBinding = validateTaskProofProject(args, projectPath);
      if (!taskProjectBinding.ok) {
        return taskProofFailure(
          taskProjectBinding,
          "Build resolved a different project than the authoritative task route."
        );
      }
      if (taskProjectBinding.active) {
        const requiredArgs = taskRequiredToolArgs(taskProjectBinding.state);
        if (args.allowEngineFallback === true && requiredArgs.allowEngineFallback !== true) {
          return taskEngineProofMismatch(
            taskProjectBinding,
            build.engineRoot,
            requiredArgs.engineRoot || "",
            "TASK_ENGINE_FALLBACK_NOT_AUTHORIZED"
          );
        }
        const requiredEngineRoot = String(requiredArgs.engineRoot || "").trim();
        if (
          requiredEngineRoot
          && canonicalProjectIdentity(requiredEngineRoot, WORKSPACE_ROOT)
            !== canonicalProjectIdentity(build.engineRoot, WORKSPACE_ROOT)
        ) {
          return taskEngineProofMismatch(
            taskProjectBinding,
            build.engineRoot,
            requiredEngineRoot,
            "TASK_ENGINE_PROOF_MISMATCH"
          );
        }
        if (String(args.engineRoot || "").trim() && !requiredEngineRoot) {
          const automaticArgs = {
            ...args,
            project: taskProjectBinding.expectedProject,
            allowAbsoluteProject: true,
            allowEngineFallback: false,
          };
          delete automaticArgs.engineRoot;
          const automaticPlan = await resolveBuildPlan(
            WORKSPACE_ROOT,
            CONFIG_PATH,
            automaticArgs
          );
          const automaticEngineRoot = String(
            automaticPlan?.ok === true ? automaticPlan.build?.engineRoot || "" : ""
          ).trim();
          if (
            !automaticEngineRoot
            || canonicalProjectIdentity(automaticEngineRoot, WORKSPACE_ROOT)
              !== canonicalProjectIdentity(build.engineRoot, WORKSPACE_ROOT)
          ) {
            return taskEngineProofMismatch(
              taskProjectBinding,
              build.engineRoot,
              automaticEngineRoot,
              automaticEngineRoot
                ? "TASK_ENGINE_PROOF_MISMATCH"
                : "TASK_ENGINE_PROOF_UNBOUND"
            );
          }
        }
      }

      const projectRoot = path.dirname(projectPath);
      let mutation;
      try {
        mutation = await readMutationState(projectRoot);
      } catch (err) {
        if (err && err.errorCode === "MUTATION_STATE_CORRUPT") {
          return fail("build blocked: mutation state corrupt.", {
            errorCode: "MUTATION_STATE_CORRUPT",
            nextSteps: ["Repair .agent/state/mutation.json, then run static_validate_project."],
          });
        }
        throw err;
      }
      const dirtyState = getDirtyState(projectRoot);
      if (dirtyState.corrupt) {
        return fail("build blocked: validation state corrupt.", {
          errorCode: "VALIDATION_STATE_CORRUPT",
          nextSteps: ["Repair .agent/state/validation.json, then run static_validate_project."],
        });
      }
      const failBuildGate = async (errorCode, error, details = {}) => {
        const lifecycleResult = details.lifecycleResult
          && typeof details.lifecycleResult === "object"
          ? details.lifecycleResult
          : null;
        const publicDetails = { ...details };
        delete publicDetails.lifecycleResult;
        const gateLoop = recordBuildGateFailure(
          projectRoot,
          mutation.mutationGeneration,
          errorCode,
          durableGuardScopeForArgs(args, {
            projectRoot,
            mutationGeneration: mutation.mutationGeneration,
          })
        );
        if (gateLoop.blocked) {
          const mutationRollback = await rollbackPendingForWorkflowStop(args, "BUILD_GATE_LOOP_BLOCKED");
          const payload = {
            errorCode: "WORKFLOW_LOOP_BLOCKED",
            retryable: false,
            stopCurrentWorkflow: true,
            doNotRetry: ["build_unreal_project"],
            requiredNextTool: publicDetails.requiredNextTool,
            mutationGeneration: gateLoop.mutationGeneration,
            nextSteps: ["Do not call build_unreal_project again with unchanged project state. Follow the required next tool or stop and report the blocker."],
            ...(mutationRollback ? { mutationRollback } : {}),
          };
          bindAuthoritativeLifecycleControl(payload, lifecycleResult);
          return fail("Build gate loop blocked: the same pre-build failure repeated without a file mutation.", payload);
        }
        const payload = { errorCode, retryable: false, ...publicDetails };
        bindAuthoritativeLifecycleControl(payload, lifecycleResult);
        return fail(error, payload);
      };
      const validationOverride = args.validationOverride === true;
      const validationOverrideNote = String(args.validationOverrideNote || "").trim();
      if (validationOverride && validationOverrideNote.length < 12) {
        return await failBuildGate(
          "VALIDATION_OVERRIDE_NOTE_REQUIRED",
          "validationOverride requires a concrete validationOverrideNote of at least 12 characters.",
          {
            stopCurrentWorkflow: false,
            requiredNextTool: "build_unreal_project",
            nextSteps: ["Retry at most once with a concrete validationOverrideNote, or remove validationOverride and run static_validate_project."],
          }
        );
      }
      const dirtyGate = requireCleanOrFail(projectRoot, {
        override: validationOverride,
        auditNote: validationOverrideNote,
      });
      if (!dirtyGate.ok) {
        const recovery = recordRecoveryObligationViaPython(WORKSPACE_ROOT, args, {
          source: "build",
          status: "revalidate_required",
          scopeDisposition: "in_slice",
          errorCode: "VALIDATION_REQUIRED",
          mutationGeneration: mutation.mutationGeneration,
          requiredTool: {
            name: "static_validate_project",
            args: { projectRoot, fullAudit: false },
          },
          targetFiles: validationScopeForTask(args, mutation.mutationGeneration).targets || [],
          message: String(dirtyGate.error || "Static validation is required before build."),
        });
        return await failBuildGate("VALIDATION_REQUIRED", dirtyGate.error, {
          validationDirty: dirtyGate.state,
          stopCurrentWorkflow: false,
          doNotRetry: ["build_unreal_project"],
          requiredNextTool: "static_validate_project",
          requiredNextToolArgs: { projectRoot, fullAudit: false },
          lifecycleResult: recovery,
          nextSteps: dirtyGate.nextSteps,
        });
      }
      const validationProofGate = requireValidationProofOrOverride(mutation, {
        override: validationOverride,
        auditNote: validationOverrideNote,
      });
      if (!validationProofGate.ok) {
        const recovery = recordRecoveryObligationViaPython(WORKSPACE_ROOT, args, {
          source: "build",
          status: "revalidate_required",
          scopeDisposition: "in_slice",
          errorCode: String(validationProofGate.errorCode || "VALIDATION_PROOF_REQUIRED"),
          mutationGeneration: mutation.mutationGeneration,
          requiredTool: {
            name: "static_validate_project",
            args: { projectRoot, fullAudit: false },
          },
          targetFiles: validationScopeForTask(args, mutation.mutationGeneration).targets || [],
          message: String(validationProofGate.error || "A current-generation static proof is required before build."),
        });
        return await failBuildGate(validationProofGate.errorCode, validationProofGate.error, {
          validatedGeneration: validationProofGate.validatedGeneration,
          mutationGeneration: validationProofGate.mutationGeneration,
          stopCurrentWorkflow: false,
          doNotRetry: ["build_unreal_project"],
          requiredNextTool: "static_validate_project",
          requiredNextToolArgs: { projectRoot, fullAudit: false },
          lifecycleResult: recovery,
          nextSteps: validationProofGate.nextSteps,
        });
      }

      const target = String(build.target || "").trim();
      if (!/^[A-Za-z0-9_]+$/.test(target)) return fail("target must be a simple target name, e.g. MyGameEditor");

      const platform = String(build.platform || defaultPlatform()).trim();
      const configuration = String(build.configuration || "Development").trim();

      if (!/^[A-Za-z0-9_]+$/.test(platform)) return fail("invalid platform");
      if (!/^[A-Za-z0-9_]+$/.test(configuration)) return fail("invalid configuration");
      const authoritativeBuildArgs = {
        project: projectPath,
        ...(String(build.engineRoot || "").trim() ? { engineRoot: String(build.engineRoot) } : {}),
        target,
        platform,
        configuration,
        allowAbsoluteProject: true,
        allowEngineFallback: args.allowEngineFallback === true,
      };

      const buildGuardScope = durableGuardScopeForArgs(args, {
        projectRoot,
        mutationGeneration: mutation.mutationGeneration,
      });
      const buildAttempt = beginBuildAttempt(
        projectRoot,
        mutation.mutationGeneration,
        buildGuardScope
      );
      if (!buildAttempt.ok) {
        if (buildAttempt.reason === "build_bookkeeping_pending") {
          const transaction = buildAttempt.buildBookkeeping || {};
          const evidence = {
            proofLevel: String(transaction.proofLevel || ""),
            buildProofDigest: String(transaction.buildProofDigest || ""),
            proofKind: String(transaction.proofKind || "build"),
            mutationGeneration: Number(transaction.mutationGeneration || mutation.mutationGeneration),
            buildLogPath: String(transaction.buildLogPath || ""),
            projectFile: String(transaction.projectFile || projectPath),
            engineRoot: String(transaction.engineRoot || build.engineRoot || ""),
            resolvedEngineVersion: String(transaction.resolvedEngineVersion || ""),
            target: String(transaction.target || target),
            platform: String(transaction.platform || platform),
            configuration: String(transaction.configuration || configuration),
            bookkeepingTransactionId: String(transaction.transactionId || ""),
            testFilter: Array.isArray(transaction.testFilters) && transaction.testFilters.length === 1
              ? String(transaction.testFilters[0])
              : "",
            testFilters: Array.isArray(transaction.testFilters) ? transaction.testFilters.map(String) : [],
            declaredTests: Array.isArray(transaction.declaredTests) ? transaction.declaredTests.map(String) : [],
          };
          const lifecycleResult = transaction.operation === "require_automation"
            ? requireAutomationAfterBuildViaPython(WORKSPACE_ROOT, args, evidence)
            : completeTaskAfterBuildViaPython(WORKSPACE_ROOT, args, evidence);
          const payload = {
            ok: lifecycleResult?.ok === true,
            buildOutcome: "succeeded",
            proofLevel: evidence.proofLevel,
            mutationGeneration: evidence.mutationGeneration,
            fullLogPath: evidence.buildLogPath,
            commandExecuted: false,
            bookkeepingReplayed: true,
            bookkeepingTransactionId: String(transaction.transactionId || ""),
            taskLifecycle: lifecycleResult?.ok === true
              ? transaction.operation === "require_automation"
                ? {
                  status: "awaiting_automation",
                  routeOwnershipReleased: false,
                  taskAuthorization: lifecycleResult.taskAuthorization || undefined,
                  toolRoute: lifecycleResult.toolRoute || undefined,
                }
                : lifecycleResult?.active === true
                  ? {
                    status: "slice_advanced",
                    routeOwnershipReleased: false,
                    completedSliceId: String(lifecycleResult.completedSliceId || ""),
                    activeSliceId: String(lifecycleResult.activeSliceId || ""),
                    pendingSlices: Array.isArray(lifecycleResult.pendingSlices)
                      ? lifecycleResult.pendingSlices.map(String)
                      : [],
                    taskAuthorization: lifecycleResult.taskAuthorization || undefined,
                    toolRoute: lifecycleResult.toolRoute || undefined,
                  }
                  : { status: "completed", routeOwnershipReleased: true }
              : {
                status: "completion_failed",
                errorCode: String(lifecycleResult?.errorCode || "TASK_BUILD_COMPLETION_FAILED"),
              },
          };
          bindAuthoritativeLifecycleControl(payload, lifecycleResult);
          if (lifecycleResult?.ok !== true) {
            payload.errorCode = String(lifecycleResult?.errorCode || "TASK_BUILD_COMPLETION_FAILED");
            payload.error = String(lifecycleResult?.error || "Successful build proof bookkeeping is still pending.");
            payload.retryable = true;
            return fail("Build already passed, but its authoritative bookkeeping replay failed.", payload);
          }
          if (transaction.operation === "require_automation") {
            payload.requiredNextTool = "run_unreal_automation_tests";
            payload.requiredNextToolArgs = { testFilters: evidence.testFilters };
            payload.pendingMutationTransactions = await markPendingMutationJournals(
              pendingMutationQuery(projectRoot, args, evidence.mutationGeneration),
              "built_awaiting_automation",
              {
                proofKind: evidence.proofKind,
                buildLogPath: evidence.buildLogPath,
                builtAt: new Date().toISOString(),
              }
            );
          } else {
            payload.finalizedMutationTransactions = await finalizePendingBuildJournals(
              pendingMutationQuery(projectRoot, args, evidence.mutationGeneration),
              "completed"
            );
          }
          completeBuildBookkeeping(
            projectRoot,
            evidence.mutationGeneration,
            transaction.transactionId,
            buildGuardScope
          );
          const budgetFail = commitDeferredBudgetOrFail();
          if (budgetFail) return budgetFail;
          try { await server.sendToolListChanged(); } catch { /* advisory */ }
          return text(JSON.stringify(payload, null, 2));
        }
        const mutationRollback = await rollbackPendingMutationJournals(
          pendingMutationQuery(projectRoot, args, mutation.mutationGeneration),
          "build_recovery_exhausted",
          args
        );
        const logArgs = {
          mode: "first_error",
          fileName: "latest-build.log",
          summaryOnly: true,
          maxFiles: 1,
          maxLines: 200,
        };
        const rollbackGeneration = Number(
          mutationRollback?.reconciliation?.mutationGeneration
          ?? mutation.mutationGeneration
        );
        const scope = validationScopeForTask(args, rollbackGeneration);
        const recovery = recordRecoveryObligationViaPython(WORKSPACE_ROOT, args, {
          source: "build",
          status: "evidence_required",
          scopeDisposition: scope.kind === "task_scope_unavailable" ? "out_of_slice" : "in_slice",
          errorCode: "WORKFLOW_LOOP_BLOCKED",
          mutationGeneration: rollbackGeneration,
          requiredTool: { name: "read_unreal_logs", args: logArgs },
          targetFiles: scope.targets || [],
          message: "The same mutation generation already had a build attempt; inspect its first actionable diagnostic before any repair.",
        });
        const payload = {
          errorCode: "WORKFLOW_LOOP_BLOCKED",
          retryable: false,
          stopCurrentWorkflow: false,
          doNotRetry: ["build_unreal_project", "static_validate_project"],
          requiredNextTool: "read_unreal_logs",
          requiredNextToolArgs: logArgs,
          mutationGeneration: rollbackGeneration,
          mutationRollback,
          suggestedToolCalls: [{ tool: "read_unreal_logs", args: logArgs }],
          nextSteps: ["Read the newest build log once. Fix its first actionable error; if it contains no actionable source error, stop and report that evidence instead of making a synthetic edit."],
        };
        bindAuthoritativeLifecycleControl(payload, recovery);
        return fail("Build loop blocked: this mutation generation already had a build attempt.", payload);
      }
      const buildTimeout = Number(args.timeoutMs || COMMAND_TIMEOUT_MS);
      const logRel = path.join(".agent", "logs", "latest-build.log");
      const logAbs = path.join(projectRoot, logRel);
      const buildGen = await beginBuild(path.dirname(projectPath));
      await agentNotify(`Building ${target} ${platform} ${configuration}…`);
      let execResult;
      try {
        execResult = await runUnrealBuildFromPlan({
          workspaceRoot: path.dirname(projectPath),
          build: { ...build, target, platform, configuration, projectPath },
          allowEngineFallback: args.allowEngineFallback === true,
          expectedEngineVersion: process.env.UNREAL_EXPECTED_ENGINE_VERSION || "",
          timeoutMs: buildTimeout,
          logPath: logAbs,
        });
      } catch (error) {
        execResult = {
          ok: false,
          commandSucceeded: false,
          timedOut: false,
          exitCode: 1,
          errorCode: "BUILD_EXECUTOR_ERROR",
          error: String(error?.message || error),
          stdout: "",
          stderr: "",
          fullLogPath: logAbs,
          executable: "",
          args: [],
        };
      }
      finishBuildAttempt(
        projectRoot,
        mutation.mutationGeneration,
        execResult,
        buildGuardScope
      );
      const endGen = await finishBuild(path.dirname(projectPath), buildGen.buildStartGeneration);
      if (execResult.errorCode === "ENGINE_VERSION_MISMATCH") {
        // Engine selection is an environmental precondition, not proof that the
        // mutation is bad. Preserve the exact build tuple so the task can resume
        // after the configured engine becomes available.
        cancelBuildAttempt(projectRoot, mutation.mutationGeneration, buildGuardScope);
        const blocker = recordRecoveryObligationViaPython(WORKSPACE_ROOT, args, {
          source: "build",
          status: "environment_recovery",
          scopeDisposition: "infrastructure",
          errorCode: "ENGINE_VERSION_MISMATCH",
          mutationGeneration: endGen.mutationGeneration,
          requiredTool: { name: "build_unreal_project", args: authoritativeBuildArgs },
          targetFiles: validationScopeForTask(args, endGen.mutationGeneration).targets || [],
          message: String(execResult.error || "The selected Unreal Engine version does not match the project contract."),
        });
        const payload = {
          errorCode: execResult.errorCode,
          resolvedEngineVersion: execResult.resolvedEngineVersion,
          expectedEngineVersion: execResult.expectedEngineVersion,
          requestedEngineAssociation: execResult.requestedEngineAssociation,
          resolvedUbtPath: execResult.resolvedUbtPath,
          engineMismatch: true,
          retryable: true,
          requiredNextTool: "build_unreal_project",
          requiredNextToolArgs: authoritativeBuildArgs,
          nextSteps: [
            taskProjectBinding.active
              ? `Install/select Unreal Engine ${execResult.expectedEngineVersion}, or update the trusted project/engine configuration and start a newly authorized task.`
              : `Install or select Unreal Engine ${execResult.expectedEngineVersion}, or pass allowEngineFallback=true with an audit note for a compatible manual build.`,
          ],
        };
        bindAuthoritativeLifecycleControl(payload, blocker);
        return fail(execResult.error, payload);
      }
      const result = {
        ok: Boolean(execResult.commandSucceeded),
        exitCode: execResult.exitCode ?? 1,
        stdout: execResult.stdout || "",
        stderr: execResult.stderr || "",
        error: execResult.error || "",
        timedOut: Boolean(execResult.timedOut),
        errorCode: execResult.errorCode || "",
      };
      const command = [
        String(execResult.executable || "").trim(),
        (execResult.args || []).join(" "),
      ].filter(Boolean).join(" ");
      const logPath = execResult.fullLogPath || logAbs;
      const verbose = args.verboseOutput === true || BUILD_VERBOSE_OUTPUT;
      const payload = buildResponsePayload({
        result,
        build: { ...build, target, platform, configuration },
        planResult,
        projectPath,
        command,
        logPath,
        verbose,
      });
      const hasTaskAuthorization = Boolean(
        args.taskAuthorization && typeof args.taskAuthorization === "object"
        && args.taskAuthorization.taskSessionId
      );
      let durableBuildRecoveryBound = false;
      if (payload.recovery) {
        Object.assign(payload.recovery, {
          commandFingerprint: payload.commandFingerprint,
          diagnosticFingerprint: payload.diagnosticFingerprint,
          outputHash: payload.outputHash,
          outputTail: payload.outputTail,
          exitCode: payload.exitCode,
          fullLogPath: payload.fullLogPath,
          target,
          platform,
          configuration,
        });
        const sourceScopedRecovery = Boolean(
          String(payload.recovery.targetFile || "").trim()
          && ["read_file", "read_file_range", "search_files", "unreal_symbol_lookup"].includes(
            String(payload.recovery.requiredNextTool || "")
          )
        );
        const semanticScopedRecovery = Boolean(
          payload.recovery.category === "linker_missing_definition"
          && String(payload.recovery.ownerSymbol || "").trim()
          && String(payload.recovery.missingSymbol || "").trim()
        );
        const taskBindableRecovery = sourceScopedRecovery || semanticScopedRecovery;
        if (sourceScopedRecovery) {
          recordBuildRecoveryContract(
            projectRoot,
            endGen.mutationGeneration,
            payload.recovery,
            durableGuardScopeForArgs(args, {
              projectRoot,
              mutationGeneration: endGen.mutationGeneration,
            })
          );
        }
        if (hasTaskAuthorization && taskBindableRecovery) {
          const recoveryBinding = recordBuildRecoveryViaPython(
            WORKSPACE_ROOT,
            args,
            {
              ...payload.recovery,
              mutationGeneration: endGen.mutationGeneration,
            }
          );
          payload.recovery.taskScopeBound = recoveryBinding?.ok === true;
          durableBuildRecoveryBound = recoveryBinding?.ok === true;
          applyBuildRecoveryScopeBinding(payload, recoveryBinding);
          bindAuthoritativeLifecycleControl(payload, recoveryBinding);
          if (recoveryBinding?.ok !== true) {
            payload.recovery.taskScopeBindingErrorCode = String(
              recoveryBinding?.errorCode || "BUILD_RECOVERY_TASK_BINDING_FAILED"
            );
          }
        }
        if (!taskBindableRecovery) {
          payload.recovery.taskScopeBound = false;
          payload.recovery.scopeStrategy = "unbound_recovery";
        } else if (
          semanticScopedRecovery
          && payload.recovery.scopeStrategy !== "out_of_slice_blocker"
        ) {
          payload.recovery.scopeStrategy = "symbol_lookup_then_semantic_evidence";
        }
        if (!hasTaskAuthorization && sourceScopedRecovery) {
          payload.recovery.requiredSequence = [
            payload.recovery.requiredNextTool,
            "unreal_agent_plan",
            "unreal_code_sketch_claim_validate",
            "replace_in_file",
            "static_validate_project",
            "build_unreal_project",
          ].filter(Boolean);
          payload.recovery.planRequiredAfterEvidence = true;
          payload.recovery.forbiddenUntilMutation = [];
          payload.nextSteps = [
            `Call ${payload.recovery.requiredNextTool} exactly once with requiredNextToolArgs; do not substitute another evidence tool.`,
            "Then start a compile-fix plan for recovery.targetFile, validate a bounded code sketch for that file, and apply the smallest mutation.",
            "Rebuild only after a mutation; a new compiler error starts a new recovery state.",
          ];
        }
      }
      payload.resolvedEngineVersion = execResult.resolvedEngineVersion;
      payload.expectedEngineVersion = execResult.expectedEngineVersion;
      payload.requestedEngineAssociation = execResult.requestedEngineAssociation;
      payload.resolvedUbtPath = execResult.resolvedUbtPath;
      payload.commandSucceeded = execResult.commandSucceeded;
      payload.timedOut = Boolean(execResult.timedOut);
      payload.mutationGeneration = endGen.mutationGeneration;
      payload.validatedGenerationAtBuild = validationProofGate.validatedGeneration;
      payload.validationOverrideApplied = Boolean(
        validationProofGate.overridden || (validationOverride && dirtyGate.state.validationRequired)
      );
      if (payload.validationOverrideApplied) {
        payload.validationOverrideNote = validationProofGate.auditNote || validationOverrideNote || "Explicit validationOverride=true";
      }
      payload.buildStartGeneration = buildGen.buildStartGeneration;
      payload.buildEndGeneration = endGen.buildEndGeneration;
      payload.buildAttemptId = String(buildAttempt.buildAttemptId || "");
      if (endGen.buildStale) {
        payload.proofLevel = "BuiltStale";
        payload.proofSemantic = "StaleDuringBuild";
        payload.phase = "stale";
        payload.ok = false;
      }
      if (
        execResult.commandSucceeded === true
        && String(payload.proofLevel || "").trim() !== "Built"
      ) {
        payload.phase = String(payload.proofLevel || "") === "BuiltStale"
          ? "stale"
          : "unverified";
        payload.ok = false;
        payload.errorCode = payload.phase === "stale"
          ? "BUILD_PROOF_STALE"
          : "BUILD_PROOF_UNVERIFIED";
        payload.error = (
          "The build process exited successfully, but did not produce authoritative "
          + "current-generation compile/UHT/link proof."
        );
      }
      if (execResult.errorCode === "BUILD_TIMEOUT") {
        payload.errorCode = "BUILD_TIMEOUT";
        payload.ok = false;
      }
      const disposition = buildToolDisposition(payload);
      payload.buildOutcome = disposition.buildOutcome;
      payload.failureClass = disposition.failureClass;
      payload.toolExecutionSucceeded = disposition.toolExecutionSucceeded;
      payload.recoverable = disposition.recoverable;
      if (disposition.buildOutcome === "compile_failed") {
        if (hasTaskAuthorization && !durableBuildRecoveryBound) {
          const scope = validationScopeForTask(args, endGen.mutationGeneration);
          const scopeTargets = Array.isArray(scope.targets) ? scope.targets : [];
          const diagnosticArgs = {
            mode: "first_error",
            fileName: "latest-build.log",
            summaryOnly: true,
            maxFiles: 1,
            maxLines: 200,
          };
          const recovery = recordRecoveryObligationViaPython(
            WORKSPACE_ROOT,
            args,
            {
              source: "build",
              status: "evidence_required",
              scopeDisposition: scopeTargets.length ? "in_slice" : "out_of_slice",
              errorCode: scopeTargets.length
                ? String(payload.errorCode || "BUILD_FAILED")
                : "BUILD_DIAGNOSTIC_SCOPE_UNAVAILABLE",
              mutationGeneration: endGen.mutationGeneration,
              requiredTool: { name: "read_unreal_logs", args: diagnosticArgs },
              targetFiles: scopeTargets,
              commandFingerprint: payload.commandFingerprint,
              diagnosticFingerprint: payload.diagnosticFingerprint,
              outputHash: payload.outputHash,
              outputTail: payload.outputTail,
              exitCode: payload.exitCode,
              fullLogPath: payload.fullLogPath,
              target,
              platform,
              configuration,
              message: String(
                payload.error
                || payload.summary
                || "The build failed without source coordinates; inspect the exact bounded build log."
              ),
            }
          );
          bindAuthoritativeLifecycleControl(payload, recovery);
          payload.requiredNextTool = "read_unreal_logs";
          payload.requiredNextToolArgs = diagnosticArgs;
        }
        payload.pendingMutationTransactions = await markPendingMutationJournals(
          pendingMutationQuery(projectRoot, args, endGen.mutationGeneration),
          "build_failed",
          {
            proofKind: "build",
            errorCode: payload.errorCode || "BUILD_FAILED",
            failedAt: new Date().toISOString(),
            recoverable: disposition.recoverable,
          }
        );
      } else if (disposition.buildOutcome === "tool_failed") {
        // Missing executables, spawn failures, timeouts, and stale builds do
        // not prove the mutation is invalid. Permit correction/retry without
        // manufacturing a source edit or rolling valid code back.
        cancelBuildAttempt(projectRoot, mutation.mutationGeneration, buildGuardScope);
        const recovery = recordRecoveryObligationViaPython(WORKSPACE_ROOT, args, {
          source: "build",
          status: "environment_recovery",
          scopeDisposition: "infrastructure",
          errorCode: String(payload.errorCode || "BUILD_TOOL_FAILED"),
          mutationGeneration: endGen.mutationGeneration,
          requiredTool: {
            name: endGen.buildStale ? "static_validate_project" : "build_unreal_project",
            args: endGen.buildStale
              ? { projectRoot, fullAudit: false }
              : authoritativeBuildArgs,
          },
          targetFiles: validationScopeForTask(args, endGen.mutationGeneration).targets || [],
          message: String(payload.error || payload.summary || "Build infrastructure failed."),
        });
        bindAuthoritativeLifecycleControl(payload, recovery);
      }
      payload.buildProofDigest = String(payload.proofLevel || "") === "Built"
        && endGen.buildStale !== true
        ? crypto.createHash("sha256").update(JSON.stringify({
          version: 1,
          buildAttemptId: payload.buildAttemptId,
          taskSessionId: String(args.taskAuthorization?.taskSessionId || ""),
          mutationGeneration: endGen.mutationGeneration,
          projectFile: projectPath,
          engineRoot: build.engineRoot,
          target,
          platform,
          configuration,
          proofLevel: payload.proofLevel,
          outputHash: payload.outputHash,
          buildLogPath: logPath,
        })).digest("hex")
        : "";
      if (disposition.buildOutcome !== "succeeded") {
        const budgetFail = commitDeferredBudgetOrFail();
        if (budgetFail) return budgetFail;
      }
      if (disposition.buildOutcome === "succeeded") {
        const automationScope = automationScopeForTask(args, endGen.mutationGeneration);
        const automationCoverage = automationScope.kind === "task_scope_unavailable"
          ? {
            count: 0,
            names: [],
            suggestedFilters: [],
            scopeUnavailable: true,
          }
          : discoverAutomationTests(projectRoot, {
            scopeTargets: automationScope.targets || [],
            maxFiles: 5000,
          });
        automationCoverage.validationScope = automationScope;
        payload.automationCoverage = automationCoverage;
        let lifecycleResult;
        const automationFilterCount = Array.isArray(automationCoverage.suggestedFilters)
          ? automationCoverage.suggestedFilters.length
          : 0;
        const automationFilterLimitExceeded = automationFilterCount > MAX_AUTOMATION_FILTERS_TOTAL;
        const automationUnmappedTargets = Array.isArray(automationCoverage.unmappedScopeTargets)
          ? automationCoverage.unmappedScopeTargets.map(String).filter(Boolean)
          : [];
        if (
          automationCoverage.scopeUnavailable
          || automationCoverage.truncated
          || automationFilterLimitExceeded
          || (automationCoverage.scopeBound === true && automationUnmappedTargets.length > 0)
        ) {
          const scopeUnmapped = automationCoverage.scopeBound === true
            && automationUnmappedTargets.length > 0;
          const repairTargets = Array.isArray(automationScope.targets)
            ? automationScope.targets.map(String).filter(Boolean).slice(0, 4)
            : [];
          const requiredTool = (scopeUnmapped || automationFilterLimitExceeded) && repairTargets.length
            ? {
              name: "unreal_code_sketch_claim_validate",
              args: { targetFiles: repairTargets },
            }
            : exactCheckpointRebaseTool();
          lifecycleResult = recordRecoveryObligationViaPython(WORKSPACE_ROOT, args, {
            source: "automation",
            status: requiredTool.name === "unreal_code_sketch_claim_validate"
              ? "repair_planning_required"
              : "checkpoint_rebase_required",
            scopeDisposition: automationCoverage.scopeUnavailable || scopeUnmapped
              ? "out_of_slice"
              : "infrastructure",
            errorCode: automationCoverage.scopeUnavailable
              ? "AUTOMATION_SCOPE_UNAVAILABLE"
              : scopeUnmapped
                ? "AUTOMATION_SCOPE_UNMAPPED"
              : automationFilterLimitExceeded
                ? "AUTOMATION_FILTER_SET_TOO_LARGE"
                : "AUTOMATION_DISCOVERY_TRUNCATED",
            mutationGeneration: endGen.mutationGeneration,
            requiredTool,
            targetFiles: automationScope.targets || [],
            message: "Build passed, but Automation coverage could not be proven for the active slice.",
          });
          payload.ok = false;
          payload.errorCode = automationCoverage.scopeUnavailable
            ? "AUTOMATION_SCOPE_UNAVAILABLE"
            : scopeUnmapped
              ? "AUTOMATION_SCOPE_UNMAPPED"
            : automationFilterLimitExceeded
              ? "AUTOMATION_FILTER_SET_TOO_LARGE"
              : "AUTOMATION_DISCOVERY_TRUNCATED";
          if (scopeUnmapped) payload.unmappedScopeTargets = automationUnmappedTargets;
          if (automationFilterLimitExceeded) {
            payload.automationFilterCount = automationFilterCount;
            payload.maxAutomationFilters = MAX_AUTOMATION_FILTERS_TOTAL;
          }
          payload.requiredNextTool = requiredTool.name;
          payload.requiredNextToolArgs = requiredTool.args;
          payload.taskLifecycle = {
            status: "automation_coverage_blocked",
            routeOwnershipReleased: false,
            errorCode: payload.errorCode,
          };
        } else if (automationCoverage.count > 0) {
          const testFilters = Array.isArray(automationCoverage.suggestedFilters)
            ? automationCoverage.suggestedFilters.map(String).filter(Boolean)
            : [];
          const bookkeepingTransactionId = crypto.createHash("sha256").update(JSON.stringify({
            taskSessionId: String(args.taskAuthorization?.taskSessionId || ""),
            mutationGeneration: endGen.mutationGeneration,
            proofLevel: payload.proofLevel,
            buildProofDigest: payload.buildProofDigest,
            buildAttemptId: payload.buildAttemptId,
            buildLogPath: logPath,
            operation: "require_automation",
            testFilters,
          })).digest("hex");
          const pendingBookkeeping = hasTaskAuthorization ? recordBuildBookkeepingPending(
            projectRoot,
            endGen.mutationGeneration,
            {
              transactionId: bookkeepingTransactionId,
              operation: "require_automation",
              proofLevel: payload.proofLevel,
              buildProofDigest: payload.buildProofDigest,
              buildAttemptId: payload.buildAttemptId,
              proofKind: "build",
              buildLogPath: logPath,
              projectFile: projectPath,
              engineRoot: build.engineRoot,
              resolvedEngineVersion: String(execResult.resolvedEngineVersion || ""),
              target,
              platform,
              configuration,
              testFilters,
              declaredTests: automationCoverage.names,
            },
            buildGuardScope
          ) : { durable: true };
          if (pendingBookkeeping.durable !== true) {
            cancelBuildAttempt(projectRoot, endGen.mutationGeneration, buildGuardScope);
            return fail("Build passed, but the bookkeeping replay transaction could not be persisted.", {
              errorCode: "BUILD_BOOKKEEPING_JOURNAL_PERSIST_FAILED",
              retryable: true,
              buildOutcome: "succeeded",
              proofLevel: payload.proofLevel,
              buildProofDigest: payload.buildProofDigest,
              mutationGeneration: endGen.mutationGeneration,
            });
          }
          lifecycleResult = requireAutomationAfterBuildViaPython(
            WORKSPACE_ROOT,
            args,
            {
              mutationGeneration: endGen.mutationGeneration,
              proofLevel: payload.proofLevel,
              buildProofDigest: payload.buildProofDigest,
              buildLogPath: logPath,
              testFilter: testFilters.length === 1 ? testFilters[0] : "",
              testFilters,
              declaredTests: automationCoverage.names,
              projectFile: projectPath,
              engineRoot: build.engineRoot,
              resolvedEngineVersion: String(execResult.resolvedEngineVersion || ""),
              target,
              platform,
              configuration,
              bookkeepingTransactionId,
            }
          );
          payload.requiredNextTool = "run_unreal_automation_tests";
          payload.requiredNextToolArgs = { testFilters };
          payload.taskLifecycle = lifecycleResult?.ok === true
            ? {
              status: "awaiting_automation",
              routeOwnershipReleased: false,
              activeSliceId: String(lifecycleResult.activeSliceId || ""),
              taskAuthorization: lifecycleResult.taskAuthorization || undefined,
              toolRoute: lifecycleResult.toolRoute || undefined,
            }
            : {
              status: "automation_gate_binding_failed",
              routeOwnershipReleased: false,
              errorCode: String(lifecycleResult?.errorCode || "TASK_AUTOMATION_GATE_BINDING_FAILED"),
            };
          payload.nextSteps = [
            "Call run_unreal_automation_tests with the exact returned testFilters; build success is not terminal while slice tests are pending.",
          ];
          if (lifecycleResult?.ok === true) {
            payload.pendingMutationTransactions = await markPendingMutationJournals(
              pendingMutationQuery(projectRoot, args, endGen.mutationGeneration),
              "built_awaiting_automation",
              {
                proofKind: "build",
                buildLogPath: logPath,
                builtAt: new Date().toISOString(),
              }
            );
            if (hasTaskAuthorization) {
              completeBuildBookkeeping(
                projectRoot,
                endGen.mutationGeneration,
                bookkeepingTransactionId,
                buildGuardScope
              );
            }
          } else {
            payload.ok = false;
            payload.errorCode = String(
              lifecycleResult?.errorCode || "TASK_AUTOMATION_GATE_BINDING_FAILED"
            );
            payload.error = String(
              lifecycleResult?.error || "Build proof could not be bound to the task Automation gate."
            );
          }
        } else {
          const bookkeepingTransactionId = crypto.createHash("sha256").update(JSON.stringify({
            taskSessionId: String(args.taskAuthorization?.taskSessionId || ""),
            mutationGeneration: endGen.mutationGeneration,
            proofLevel: payload.proofLevel,
            buildProofDigest: payload.buildProofDigest,
            buildAttemptId: payload.buildAttemptId,
            buildLogPath: logPath,
            operation: "complete_task",
          })).digest("hex");
          const pendingBookkeeping = hasTaskAuthorization ? recordBuildBookkeepingPending(
            projectRoot,
            endGen.mutationGeneration,
            {
              transactionId: bookkeepingTransactionId,
              operation: "complete_task",
              proofLevel: payload.proofLevel,
              buildProofDigest: payload.buildProofDigest,
              buildAttemptId: payload.buildAttemptId,
              proofKind: "build",
              buildLogPath: logPath,
              projectFile: projectPath,
              engineRoot: build.engineRoot,
              resolvedEngineVersion: String(execResult.resolvedEngineVersion || ""),
              target,
              platform,
              configuration,
            },
            buildGuardScope
          ) : { durable: true };
          if (pendingBookkeeping.durable !== true) {
            cancelBuildAttempt(projectRoot, endGen.mutationGeneration, buildGuardScope);
            return fail("Build passed, but the bookkeeping replay transaction could not be persisted.", {
              errorCode: "BUILD_BOOKKEEPING_JOURNAL_PERSIST_FAILED",
              retryable: true,
              buildOutcome: "succeeded",
              proofLevel: payload.proofLevel,
              buildProofDigest: payload.buildProofDigest,
              mutationGeneration: endGen.mutationGeneration,
            });
          }
          lifecycleResult = completeTaskAfterBuildViaPython(
            WORKSPACE_ROOT,
            args,
            {
              proofLevel: payload.proofLevel,
              buildProofDigest: payload.buildProofDigest,
              proofKind: "build",
              mutationGeneration: endGen.mutationGeneration,
              buildLogPath: logPath,
              projectFile: projectPath,
              engineRoot: build.engineRoot,
              resolvedEngineVersion: String(execResult.resolvedEngineVersion || ""),
              target,
              platform,
              configuration,
              bookkeepingTransactionId,
            }
          );
          payload.taskLifecycle = lifecycleResult?.ok === true && lifecycleResult?.active === true
            ? {
              status: "slice_advanced",
              routeOwnershipReleased: false,
              completedSliceId: String(lifecycleResult.completedSliceId || ""),
              activeSliceId: String(lifecycleResult.activeSliceId || ""),
              pendingSlices: Array.isArray(lifecycleResult.pendingSlices)
                ? lifecycleResult.pendingSlices.map(String)
                : [],
              taskAuthorization: lifecycleResult.taskAuthorization || undefined,
              toolRoute: lifecycleResult.toolRoute || undefined,
            }
            : lifecycleResult?.ok === true
              ? { status: "completed", routeOwnershipReleased: true }
              : {
                status: "completion_failed",
                routeOwnershipReleased: false,
                errorCode: String(lifecycleResult?.errorCode || "TASK_BUILD_COMPLETION_FAILED"),
              };
          if (lifecycleResult?.ok === true) {
            payload.finalizedMutationTransactions = await finalizePendingBuildJournals(
              pendingMutationQuery(projectRoot, args, endGen.mutationGeneration),
              "completed"
            );
            if (hasTaskAuthorization) {
              completeBuildBookkeeping(
                projectRoot,
                endGen.mutationGeneration,
                bookkeepingTransactionId,
                buildGuardScope
              );
            }
          } else {
            payload.ok = false;
            payload.errorCode = String(
              lifecycleResult?.errorCode || "TASK_BUILD_COMPLETION_FAILED"
            );
            payload.error = String(
              lifecycleResult?.error || "Build proof could not be committed to the task lifecycle."
            );
          }
        }
        if (lifecycleResult?.ok === true && lifecycleResult?.taskSessionId) {
          try {
            await server.sendToolListChanged();
          } catch {
            // Older clients may not accept list-changed notifications.
          }
        }
        bindAuthoritativeLifecycleControl(payload, lifecycleResult);
        if (lifecycleResult?.ok !== true) {
          payload.retryable = false;
          return fail("Build execution passed, but task lifecycle persistence failed.", payload);
        }
        const budgetFail = commitDeferredBudgetOrFail();
        if (budgetFail) return budgetFail;
      }
      await agentNotify(
        payload.userMessage || payload.summary,
        payload.ok || disposition.recoverable ? "info" : "error"
      );
      const response = text(JSON.stringify(payload, null, 2));
      if (disposition.mcpIsError) {
        response.isError = true;
      }
      return response;
    }

    return fail(`unknown tool: ${name}`, { errorCode: "UNKNOWN_TOOL" });
    } finally {
      // Drop unused reservations on fail/early-return paths after reserve.
      rollbackDeferredBudget();
    }
  } catch (err) {
    const message = err && err.message ? String(err.message) : String(err);
    console.error(err && err.stack ? err.stack : err);
    const validationLike = /must be a concrete|must contain at least|may contain at most|write blocked|path escapes|path must be|not found or not file|Duplicate deletion path/i.test(message);
    if (!validationLike) {
      recordToolFailure(
        name,
        args,
        "INTERNAL_ERROR",
        toolCallContext.getStore()?.durableGuardScope || durableGuardScopeForArgs(args)
      );
    }
    return fail(message, {
      errorCode: validationLike ? "VALIDATION_ERROR" : "INTERNAL_ERROR",
      retryable: false,
      doNotRetry: validationLike ? [] : [name],
      userMessage: message.split(/\r?\n/, 1)[0],
      agentInstruction: validationLike
        ? `Fix the invalid ${name} arguments and retry once with corrected input.`
        : `Do not retry ${name} with the same arguments. Stop the current workflow and report the MCP internal error.`,
    });
    }
  });
});

async function main() {
  runtimeComponentStatus = verifyRuntimeComponent("agent", {
    componentRoot: path.resolve(__dirname, ".."),
  });
  try {
    const startupStateRoot = resolveAgentStateRoot();
    const recovery = await recoverIncompleteJournals(startupStateRoot, {
      checkpointRollback: recoverRollbackContinuityCheckpoint,
      promoteRecoveryRequired: (item) => promoteJournalRecoveryRequired(item, startupStateRoot),
    });
    if (recovery.recoveryRequired?.length) {
      console.error(`[unreal-agent] transaction recovery required: ${JSON.stringify(recovery.recoveryRequired)}`);
    }
    if (recovery.skippedCorrupt?.length) {
      console.error(`[unreal-agent] skipped corrupt journals: ${recovery.skippedCorrupt.length}`);
    }
    if (recovery.promotionFailures?.length) {
      console.error(`[unreal-agent] transaction recovery promotion failed: ${JSON.stringify(recovery.promotionFailures)}`);
    }
  } catch (err) {
    console.error(`[unreal-agent] transaction recovery scan failed: ${err.message || err}`);
  }
  const semanticGuardHealth = probeMutationSemanticGuard();
  if (!semanticGuardHealth.ok) {
    console.error(
      `[unreal-agent] mutation semantic guard unhealthy: ${semanticGuardHealth.reason}`
      + " (writes that need the guard will fail closed until mutation_semantic_guard.py and unreal_api_denylist.py are present and importable)"
    );
  }
  const transport = new StdioServerTransport();
  await server.connect(transport);
  // Catalog is profile-stable; list_changed remains advisory for clients that
  // refresh tool metadata when route fingerprints change. Do not use it to
  // shrink the advertised Essential surface.
  startActiveRouteWatcher({
    readContext: () => listToolsRouteContext(
      WORKSPACE_ROOT,
      getActiveProject(CONFIG_PATH) || ""
    ),
    notify: async (context, fingerprint) => {
      lastObservedRouteFingerprint = fingerprint;
      await server.sendToolListChanged();
    },
    onNotifyError: (diagnostic) => {
      console.warn(`[unreal-agent] ${diagnostic.code}: ${diagnostic.message}`);
    },
  });
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
