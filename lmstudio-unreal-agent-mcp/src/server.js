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
  buildProjectBrowsePaths
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
  runStaticValidation,
  resolveValidateOnWrite,
  VALIDATE_ON_WRITE_TIMEOUT_MS,
  clearValidated
} = require("./validate-write.js");
const { validateMutationSemanticText, probeMutationSemanticGuard } = require("./mutation-semantic-guard.js");
const {
  requireCleanOrFail,
  requireValidationProofOrOverride,
  getDirtyState,
} = require("./validation-dirty");
const {
  authorizeActiveRouteTool,
  authorizeTaskRouteTool,
  discoverActiveTaskContext,
  listToolsRouteContext,
  SAFE_ROUTE_RECOVERY_TOOLS,
  validateMutationAuth,
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
  recordBuildRecoveryViaPython,
  markBuildRecoveryEvidenceViaPython,
} = require("./task-auth");
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
const { recoverIncompleteJournals } = require("./transaction-journal");
const { resolveAgentStateRoot, ensureStateRootLayout } = require("./state-root");
const {
  validateWriteTarget,
  shouldRollback,
  isDeleteAllowedPath,
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
  buildResponsePayload,
  buildToolDisposition,
  compactLogPayload,
  compactMcpContent,
  compactValidationPayload,
  errorPayload,
  firstErrorCluster,
  formatSessionHandoff,
  isInterestingLogLine,
  resolveAgentResultMaxChars,
  slimWriteSuccessPayload,
  writeDisciplineOptions,
  writeTextArtifact
} = require("./context-ux.js");
const {
  callableAgentToolNames,
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
  beginToolCall,
  checkToolRepeatBlocked,
  recordToolFailure,
  toolRepeatBlockedMessage,
  clearToolFailureHistory
} = require("./tool-failure-history");
const {
  checkReadRepeat,
  recordReadSuccess,
  recordReadStagnation,
  normalizeReadToolArgs,
  cachedReadInstruction,
  clearReadSuccessHistory
} = require("./tool-read-history");
const { runUnrealBuildFromPlan } = require("./build-executor");
const { readUtf8Range, readUtf8Tail } = require("./bounded-read");
const { beginBuild, finishBuild, beginValidation, finishValidationAndClear, recordMutation, recordDeletion, readMutationState } = require("./mutation-generation");
const {
  recordValidationFailure,
  recordValidationSuccess,
  recordBuildGateFailure,
  beginBuildAttempt,
  finishBuildAttempt,
  recordBuildRecoveryContract,
  recordRecoveryEvidenceCall,
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
const ALLOW_WRITE = process.env.ALLOW_WRITE === "1" || process.env.ALLOW_WRITE === "true";
const ALLOW_COMMANDS = process.env.ALLOW_COMMANDS === "1" || process.env.ALLOW_COMMANDS === "true";
const ALLOW_UNREAL_BUILD = process.env.ALLOW_UNREAL_BUILD === "1" || process.env.ALLOW_UNREAL_BUILD === "true";
const ALLOW_EXISTING_SOURCE_WRITE = ["1", "true", "yes", "on"].includes(
  String(process.env.ALLOW_EXISTING_SOURCE_WRITE || "").trim().toLowerCase()
);
if (ALLOW_EXISTING_SOURCE_WRITE) {
  // stderr only: stdout carries the MCP stdio protocol.
  console.error(
    "[unreal-agent] WARNING: ALLOW_EXISTING_SOURCE_WRITE=1 — write_file may OVERWRITE existing files. "
    + "This is a manual override; unset it in mcp.json after the one-off operation."
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
let lastObservedRouteFingerprint = "";

function launchProjectPicker(explorer = false) {
  if (process.platform !== "win32") {
    return {
      ok: false,
      error: "project_picker_windows_only",
      message: "The project picker requires Windows (PowerShell). Use rag.ps1 pick-project manually or set activeProject in the shared config."
    };
  }
  const ragRoot = process.env.UNREAL58_ROOT
    ? path.resolve(process.env.UNREAL58_ROOT)
    : path.join(os.homedir(), ".lmstudio", "Unreal58-RAG");
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
  return {
    content: [{
      type: "text",
      text: compactMcpContent(content, MCP_AGENT_RESULT_MAX_CHARS)
    }]
  };
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

function mutationBookkeepingFailure(message, operation, relPath) {
  const retryToolByOperation = {
    create: "write_file",
    replace: "replace_in_file",
    apply_edit_bundle: "apply_edit_bundle",
  };
  const doNotRetry = [retryToolByOperation[operation] || operation].filter(Boolean);
  return fail(String(message || "Mutation bookkeeping failed after write."), {
    errorCode: "MUTATION_LOCK_BUSY",
    path: relPath,
    operation,
    writeApplied: true,
    bookkeepingFailed: true,
    mutationGenerationNotRecorded: true,
    retryable: false,
    doNotRetry,
    nextSteps: [
      `Do NOT retry ${doNotRetry.join(" or ")} — the file change is already on disk.`,
      "Call read_file on the same path to confirm current content.",
      "Call static_validate_project (or build_unreal_project when appropriate) to recover validation state.",
    ],
    agentInstruction: "Bookkeeping failed after a successful write; verify disk state before any further edits.",
  });
}

async function bumpProjectMutationGeneration(targetPath, content) {
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
  return await recordMutation(projectDir, projectRelativePath, content);
}

function recordAutomaticContinuityCheckpoint(args, modifiedFiles, validation = null) {
  const checkpoint = checkpointMutationViaPython(WORKSPACE_ROOT, args, modifiedFiles, {
    requiredNextAction: "continue_active_slice_then_validate",
    validation: validation || {},
  });
  if (!checkpoint || checkpoint.ok !== true) {
    return {
      ok: false,
      errorCode: String(checkpoint?.errorCode || "CONTINUITY_CHECKPOINT_FAILED"),
      error: String(checkpoint?.error || "Automatic continuity checkpoint failed."),
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
  };
}

function continuityCheckpointFailure(checkpoint, operation, paths, mutation = null) {
  return fail(checkpoint.error || "Automatic continuity checkpoint failed after write.", {
    errorCode: "CONTINUITY_CHECKPOINT_FAILED",
    underlyingErrorCode: checkpoint.errorCode || "",
    operation,
    paths,
    writeApplied: true,
    checkpointFailed: true,
    retryable: false,
    doNotRetry: [operation],
    ...(mutation ? { mutationGeneration: mutation.mutationGeneration } : {}),
    nextSteps: [
      "Do NOT retry the mutation — the file change and mutation generation are already recorded.",
      "Read the affected file(s), then call unreal_task_checkpoint to recover continuity.",
      "Run static_validate_project before build_unreal_project.",
    ],
  });
}

async function agentNotify(message, level = "info") {
  try {
    await server.notification({
      method: "notifications/message",
      params: { level, logger: "unreal-agent", data: String(message) }
    });
  } catch {
    // Client may not subscribe to logging notifications.
  }
}

function enforceTaskAuth(args, options = {}) {
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
    const routeStale = auth.errorCode === "TASK_ROUTE_STALE";
    const authMismatch = auth.errorCode === "TASK_AUTH_MISMATCH";
    const incomplete = auth.errorCode === "TASK_AUTH_INCOMPLETE";
    const invalidFormat = auth.errorCode === "TASK_AUTH_INVALID_FORMAT";
    const missingState = auth.errorCode === "TASK_STATE_MISSING";
    const toolInactive = auth.errorCode === "TASK_TOOL_NOT_ACTIVE";
    const budgetExhausted = auth.errorCode === "TASK_PHASE_TOOL_BUDGET_EXHAUSTED";
    const routeRedirect = new Set([
      "TASK_SLICE_SCOPE_REQUIRED",
      "TASK_SLICE_TARGET_MISMATCH",
      "TASK_ROUTE_SCOPE_EXCEEDED",
    ]).has(auth.errorCode);
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
      ...(auth.nextAction ? { nextAction: auth.nextAction } : {}),
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
        agentInstruction: "Plan identity changed. Copy taskAuthorization from this error, then replan or re-run required gates before writing. Do not retry the write tool alone and do not stop the whole user workflow.",
      } : {}),
      ...(invalidFormat || missingState ? {
        doNotRetry: [String(options.toolName || "write_tool")],
        nextAction: "unreal_agent_plan",
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

const ROUTE_SAME_CALL_RETRY_CODES = new Set([
  "TASK_AUTH_INCOMPLETE",
  "TASK_ROUTE_OWNERSHIP_REQUIRED",
  "TASK_ROUTE_STALE",
  "TASK_STATE_LOCKED",
]);
const ROUTE_REDIRECT_CODES = new Set([
  "TASK_PHASE_TOOL_BUDGET_EXHAUSTED",
  "TASK_SLICE_SCOPE_REQUIRED",
  "TASK_SLICE_TARGET_MISMATCH",
  "TASK_ROUTE_SCOPE_EXCEEDED",
  "TASK_TOOL_NOT_ACTIVE",
]);

function routeAuthorizationFailureOptions(result = {}, toolName = "") {
  const errorCode = String(result.errorCode || "TASK_ROUTE_AUTH_FAILED");
  const sameCallRetry = ROUTE_SAME_CALL_RETRY_CODES.has(errorCode);
  const routeRedirect = ROUTE_REDIRECT_CODES.has(errorCode);
  let nextAction = String(result.nextAction || "").trim();
  if (!nextAction && errorCode === "TASK_PHASE_TOOL_BUDGET_EXHAUSTED") {
    nextAction = "unreal_task_checkpoint";
  }
  if (!nextAction && errorCode === "TASK_TOOL_NOT_ACTIVE") {
    const route = result.toolRoute && typeof result.toolRoute === "object"
      ? result.toolRoute
      : {};
    const pending = Array.isArray(route.pendingGates)
      ? route.pendingGates.map(String).filter(Boolean)
      : [];
    const active = Array.isArray(route.activeTools)
      ? route.activeTools.map(String).filter(Boolean)
      : [];
    nextAction = pending[0] || active[0] || "unreal_task_checkpoint";
  }
  if (
    !nextAction
    && (errorCode === "TASK_AUTH_INVALID_FORMAT"
      || errorCode === "TASK_STATE_MISSING")
  ) {
    nextAction = "unreal_agent_plan";
  }
  if (errorCode === "TASK_AUTH_MISMATCH") {
    nextAction = "unreal_agent_plan";
  }
  const advertisedActions = Array.isArray(result.nextActions)
    ? result.nextActions.map(String).filter(Boolean)
    : [];
  const recoveryActionRequired = Boolean(nextAction || advertisedActions.length);
  const canContinueWorkflow = sameCallRetry || recoveryActionRequired;
  const instruction = errorCode === "TASK_PHASE_TOOL_BUDGET_EXHAUSTED"
    ? "Do not retry the budgeted work tool. Call unreal_task_checkpoint with nextActionArgs exactly as returned (action=record); action=status does not renew the budget. Continue requiredNextAction with the returned taskAuthorization."
    : errorCode === "TASK_AUTH_INVALID_FORMAT" || errorCode === "TASK_STATE_MISSING"
      ? "The supplied taskAuthorization was not server-issued or no longer exists. Never fabricate authorization. Call unreal_agent_plan once with the original request, then continue the returned route."
      : errorCode === "TASK_ROUTE_OWNERSHIP_REQUIRED"
        ? "Retry the same tool once with the complete taskAuthorization previously returned by unreal_agent_plan, a successful gate, or a continuity checkpoint. Do not recover, cancel, or create another task."
      : sameCallRetry
        ? "Retry the same tool once using the complete server-issued taskAuthorization returned by the latest response."
        : recoveryActionRequired
          ? `Do not retry ${String(toolName || "the blocked tool")}. Call ${nextAction || advertisedActions[0]} and continue the same user workflow.`
          : "Stop the current workflow and report the exact routing integrity failure.";
  return {
    errorCode,
    retryable: sameCallRetry || routeRedirect,
    stopCurrentWorkflow: !canContinueWorkflow,
    recoveryActionRequired,
    taskAuthorizationSource: "server_only",
    doNotFabricateTaskAuthorization: true,
    ...(toolName && !sameCallRetry ? { doNotRetry: [String(toolName)] } : {}),
    ...(nextAction ? { nextAction } : {}),
    ...(result.nextActionArgs && typeof result.nextActionArgs === "object"
      ? { nextActionArgs: result.nextActionArgs }
      : {}),
    ...(advertisedActions.length ? { nextActions: advertisedActions } : {}),
    ...(result.taskAuthorization ? { taskAuthorization: result.taskAuthorization } : {}),
    ...(result.toolRoute ? { toolRoute: result.toolRoute } : {}),
    ...(result.toolRouteUsage ? { toolRouteUsage: result.toolRouteUsage } : {}),
    agentInstruction: instruction,
  };
}

function commitMutationRouteBudget(args, toolName) {
  const fields = requiredFields(args || {});
  if (!fields.taskSessionId) {
    return null;
  }
  const consumed = consumeRouteCall(
    WORKSPACE_ROOT,
    fields.taskSessionId,
    fields,
    args || {},
    toolName
  );
  if (!consumed.ok) {
    return fail(
      consumed.error || "Task phase tool budget exhausted.",
      {
        taskSessionId: fields.taskSessionId,
        ...routeAuthorizationFailureOptions(consumed, toolName),
      }
    );
  }
  return null;
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
    "commandSucceeded", "proofSatisfied", "recoveryRequired", "errorCode",
    "retryable", "doNotRetry", "stopCurrentWorkflow", "suggestedToolCalls",
    "validationOverrideAvailable", "buildAllowedForValidatedGeneration", "requiredNextTool",
    "continuityCheckpoint", "taskAuthorization", "toolRoute",
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
  }
  const result = text(JSON.stringify(base, null, 2));
  if (options.isError) result.isError = true;
  return result;
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
    const relKey = relPath.toLowerCase();
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

function exposureProfileName() {
  return MCP_EXTENDED_TOOLS ? "extended" : "essential";
}

function filterAgentTools(tools, _context = null) {
  // Advertised catalog is profile ∩ control-plane visibility only.
  // Route/phase/lease/ownership remain CallTool authorization boundaries —
  // never shrink tools/list so LM Studio does not look like a partial install.
  const allowed = callableAgentToolNames(tools.map((tool) => tool.name));
  return tools.filter((tool) => allowed.has(tool.name));
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
  };
}

function emitCatalogInitializedDiagnostic(context = null) {
  const tools = allAgentTools();
  const catalog = buildToolCatalogDiagnostics(tools, context);
  console.error(JSON.stringify({
    event: "mcp_catalog_initialized",
    server: "unreal-agent",
    profile: catalog.profile,
    registeredToolCount: catalog.registeredCount,
    advertisedToolCount: catalog.advertisedCount,
    routeContextStatus: catalog.routeContextStatus,
    routeErrorCode: catalog.routeErrorCode,
    stateRoot: catalog.stateRoot,
    activeProject: getActiveProject(CONFIG_PATH) || "",
  }));
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

function allowedCommandBase(commandLine) {
  const trimmed = String(commandLine || "").trim();
  if (!trimmed) return false;
  if (/[&|<>]/.test(trimmed)) return false;

  const lower = trimmed.toLowerCase();

  const denyPatterns = [
    /\bdel\b/i,
    /\berase\b/i,
    /\brmdir\b/i,
    /\brd\b/i,
    /\bformat\b/i,
    /\breg\s+delete\b/i,
    /\bshutdown\b/i,
    /\btaskkill\b/i,
    /\bsetx\b/i,
    /\bmklink\b/i,
    /\btakeown\b/i,
    /\bicacls\b/i,
    /\bpowershell\b.*\b(iwr|irm|invoke-webrequest|invoke-restmethod)\b/i,
    /\bcurl\b.*\|\s*(powershell|cmd|sh|bash)/i
  ];

  if (denyPatterns.some((re) => re.test(lower))) return false;

  const allowPatterns = [
    /^dir(\s|$)/i,
    /^type(\s|$)/i,
    /^where(\s|$)/i,
    /^git\s+(status|diff|log|show|rev-parse|branch)(\s|$)/i,
    /^findstr(\s|$)/i,
    /^cl(\s|$)/i,
    /^msbuild(\s|$)/i,
    /^dotnet\s+build(\s|$)/i,
    /^node\s+--version$/i,
    /^npm\s+--version$/i,
    /^python\s+--version$/i,
    /^py\s+--version$/i
  ];

  return allowPatterns.some((re) => re.test(trimmed));
}

function parseAllowedCommand(commandLine) {
  const trimmed = String(commandLine || "").trim();
  if (!allowedCommandBase(trimmed)) return null;
  if (process.platform === "win32" && /^(dir|type|where|findstr)(\s|$)/i.test(trimmed)) {
    return { file: process.env.ComSpec || "cmd.exe", args: ["/d", "/s", "/c", trimmed], shell: false };
  }
  const parts = trimmed.match(/(?:[^\s"]+|"[^"]*")+/g) || [];
  if (!parts.length) return null;
  const file = parts[0].replace(/^"|"$/g, "");
  const args = parts.slice(1).map((part) => part.replace(/^"|"$/g, ""));
  return { file, args, shell: false };
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
      description: "Server-issued auth object from unreal_task_start / checkpoint / stale-auth refresh. Include ownerCapability for multi-chat route ownership; never reuse another chat's capability.",
      properties: {
        taskSessionId: { type: "string" },
        authToken: { type: "string" },
        ownerCapability: {
          type: "string",
          description: "Secret ownership token from task_start. Not a conversationId.",
        },
        conversationId: {
          type: "string",
          description: "Public chat scope label. Not sufficient for ownership by itself.",
        },
        planId: { type: "string" },
        planRevision: { type: "string" },
        activeSliceId: { type: "string" },
        routeHash: { type: "string" },
        routePhase: { type: "string" },
      },
      required: [
        "taskSessionId",
        "authToken",
        "ownerCapability",
        "planId",
        "planRevision",
        "activeSliceId",
        "routeHash",
        "routePhase",
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
  return {
    fileAbsPath: target ? path.resolve(target) : null,
    fileSignature: stat ? fileStatSignature(stat) : null,
    mutationGeneration: options.mutationGeneration ?? 0,
    scopeSignature: options.scopeSignature || null,
    evidenceHash: options.evidenceHash || null,
    taskSessionId: String(options.taskSessionId || ""),
    taskAuthorization: options.taskAuthorization && typeof options.taskAuthorization === "object"
      ? options.taskAuthorization
      : null,
    activeProject: resolution?.activeProject || getActiveProject(CONFIG_PATH) || null,
  };
}

function cachedReadSuccess(content, options = {}) {
  const errorCode = options.errorCode || "READ_REPEAT_DETECTED";
  const payload = {
    ok: true,
    cached: true,
    evidenceStatus: "cached",
    repeatDetected: true,
    doNotRepeatRead: true,
    stopCurrentWorkflow: options.stopCurrentWorkflow !== false,
    errorCode,
    retryable: false,
    phase: "evidence_cached",
    userMessage: options.userMessage || cachedReadInstruction(errorCode),
    agentInstruction: options.agentInstruction || cachedReadInstruction(errorCode),
    content: content == null ? "" : content,
    readAttempts: options.readAttempts || 2,
  };
  if (options.readCount != null) payload.readCount = options.readCount;
  if (options.fullyCovered) payload.fullyCovered = true;
  if (options.coveredBy) payload.coveredBy = options.coveredBy;
  return text(JSON.stringify(payload, null, 2));
}

function evidenceStagnationFail(tool, guard, options = {}) {
  const errorCode = guard.reason || "EVIDENCE_STAGNATION";
  recordReadStagnation(tool, guard.normalizedArgs, options.context || {});
  return fail(
    errorCode === "EVIDENCE_STAGNATION_REPEAT"
      ? `identical ${tool} evidence call blocked after stagnation.`
      : "Evidence read stagnating — no new line coverage or soft budget exhausted.",
    {
      errorCode,
      retryable: false,
      doNotRetry: ["read_file", "read_file_range", "read_symbol", "search_files"],
      stopCurrentWorkflow: true,
      agentInstruction: cachedReadInstruction(errorCode),
      userMessage: cachedReadInstruction(errorCode),
      nextSteps: [
        "Do not call another evidence tool.",
        "Produce the final analysis from evidence already in the conversation.",
      ],
      readAttempts: guard.attempts,
      pingPong: Boolean(guard.pingPong),
    }
  );
}

function prepareReadGuard(tool, args, context) {
  const normalizedArgs = normalizeReadToolArgs(tool, args);
  const decision = checkReadRepeat(tool, normalizedArgs, context);
  return { normalizedArgs, decision, ...decision };
}

function applyBuildRecoveryEvidenceGuard(tool, context = {}) {
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
    }
  );
  if (
    !recovery.blocked
    && recovery.active
    && recovery.recoveryContract?.evidenceSatisfied === true
    && context.taskAuthorization
  ) {
    const persisted = markBuildRecoveryEvidenceViaPython(
      WORKSPACE_ROOT,
      { taskAuthorization: context.taskAuthorization },
      recovery.recoveryContract.targetFile
    );
    if (!persisted || persisted.ok !== true) {
      return fail("The exact build-recovery read could not be bound to the active task.", {
        errorCode: String(persisted?.errorCode || "BUILD_RECOVERY_EVIDENCE_BINDING_FAILED"),
        retryable: true,
        stopCurrentWorkflow: false,
        nextAction: "unreal_task_checkpoint",
        agentInstruction: "Checkpoint the active task once, then retry the exact required source range.",
      });
    }
  }
  if (!recovery.blocked) return null;
  const contract = recovery.recoveryContract || {};
  if (recovery.reason === "build_recovery_required_tool_mismatch"
      || recovery.reason === "build_recovery_target_mismatch") {
    return fail("Build recovery requires the first compiler diagnostic's exact source range.", {
      errorCode: "BUILD_RECOVERY_REQUIRED_EVIDENCE",
      retryable: true,
      stopCurrentWorkflow: false,
      doNotRetry: [tool],
      requiredNextTool: contract.requiredNextTool || "read_file_range",
      requiredNextToolArgs: contract.requiredNextToolArgs || {},
      nextAction: contract.requiredNextTool || "read_file_range",
      nextActionArgs: contract.requiredNextToolArgs || {},
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
      ? { targetFiles: [contract.targetFile].filter(Boolean) }
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

function applyReadGuard(tool, guard, context) {
  if (!guard || guard.action === "allow" || !guard.repeat) return null;
  if (
    guard.action === "stagnation"
    || guard.reason === "EVIDENCE_STAGNATION"
    || guard.reason === "EVIDENCE_STAGNATION_REPEAT"
  ) {
    return evidenceStagnationFail(tool, guard, { context });
  }
  // Identical / fully-covered range: return cached success (no wrong-range body injection for uncovered misses).
  if (guard.action === "cache" || guard.reason === "READ_REPEAT_DETECTED") {
    if (guard.cachedContent == null && guard.fullyCovered) {
      return fail("Requested line range is already covered by prior reads.", {
        errorCode: "READ_REPEAT_DETECTED",
        retryable: false,
        doNotRetry: [tool],
        fullyCovered: true,
        coveredBy: guard.coveredBy || [],
        agentInstruction:
          "Those lines were already returned. Do not re-scan. Finish analysis or call read_symbol for a named function.",
        nextSteps: [
          "Use existing evidence, or call read_symbol with an exact C++ symbol name.",
        ],
      });
    }
    return cachedReadSuccess(guard.cachedContent, {
      errorCode: "READ_REPEAT_DETECTED",
      readAttempts: guard.attempts,
      fullyCovered: guard.fullyCovered,
      coveredBy: guard.coveredBy,
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
    if (!projectDir || !absolutePath.toLowerCase().startsWith(projectDir.toLowerCase() + path.sep)) continue;
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
    allowExistingSourceWrite: ALLOW_EXISTING_SOURCE_WRITE,
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
          taskAuthorization: {
            type: "object",
            description: "Optional. Pass ownerCapability to mark which tasks you own.",
            properties: {
              ownerCapability: { type: "string" },
              conversationId: { type: "string" },
              taskSessionId: { type: "string" },
            },
            additionalProperties: true,
          },
          ownerCapability: { type: "string" },
          conversationId: { type: "string" },
        })
      },
      {
        name: "cancel_active_task",
        description: "Cancel the single active running task, or a named taskSessionId when multiple are present. Pass taskAuthorization.ownerCapability to cancel your own task without force. Foreign healthy tasks require force=true.",
        inputSchema: makeJsonSchema({
          taskSessionId: { type: "string", description: "Optional explicit taskSessionId when multiple running tasks exist." },
          force: { type: "boolean", description: "Force-cancel a healthy task owned by another MCP connection after user confirmation." },
          taskAuthorization: {
            type: "object",
            description: "Ownership proof from unreal_task_start. Include ownerCapability.",
            properties: {
              ownerCapability: { type: "string" },
              conversationId: { type: "string" },
              taskSessionId: { type: "string" },
            },
            additionalProperties: true,
          },
          ownerCapability: { type: "string" },
          conversationId: { type: "string" },
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
        description: "Choose the active Unreal project by .uproject path or hint. Pass clear=true to unset.",
        inputSchema: makeJsonSchema({
          projectPath: { type: "string", description: "Absolute or workspace-relative .uproject path." },
          hint: { type: "string", description: "Project name fragment, e.g. JRPG or CiciToon." },
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
        description: "List workspace:// or project:// directories. Source/, Plugins/, Config/, and Content/ resolve against activeProject even when it is outside WORKSPACE_ROOT.",
        inputSchema: makeJsonSchema({
          path: { type: "string", description: "Relative path inside workspace, e.g. '.', 'Source'." },
          maxEntries: { type: "number", description: "Max entries to show. Default 200." }
        }, ["path"])
      },
      {
        name: "read_file",
        description: "Read a UTF-8 file under workspace:// or project://. Active-project source may be outside WORKSPACE_ROOT. Required before writes; large source should use read_file_range.",
        inputSchema: makeJsonSchema({
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
          path: { type: "string", description: "Source file containing the function." },
          symbol: { type: "string", description: "Function or qualified function, e.g. UFoo::Tick or Tick." },
          contextLines: { type: "number", description: "Extra lines around the function. Default 3, max 30." }
        }, ["path", "symbol"])
      },
      {
        name: "write_file",
        description: "Create one brand-new UTF-8 file under the active project's Source/Config/Plugins source tree (or .agent/ under WORKSPACE_ROOT). Keep the first file body bounded (prefer <=8,000 characters); extend it later with replace_in_file if needed. Requires ALLOW_WRITE=1 and server-issued gateCompletion.taskAuthorization with routePhase=executor; never fabricate authorization, and call unreal_agent_plan once if none exists. Pass concrete targetFiles and changeKind=new_file to unreal_code_sketch_claim_validate before write. Create-only: any file that already exists is blocked. Use replace_in_file to modify existing files. Do not retry write_file after a 'file already exists' error.",
        inputSchema: makeJsonSchema({
          ...taskAuthSchemaProperties(),
          path: { type: "string", description: "workspace:// or project:// path (active-project Source allowed even outside WORKSPACE_ROOT)." },
          content: { type: "string", description: "Full file content to write." },
          createDirs: { type: "boolean", description: "Create parent directories if needed. Default false." }
        }, ["taskAuthorization", "path", "content"])
      },
      {
        name: "replace_in_file",
        description: "Safely replace one exact bounded region in an existing file under the active project's Source/Config/Plugins source tree (or .agent/ under WORKSPACE_ROOT). Use at most 60 changed lines and prefer <=8,000 combined oldText/newText characters; never duplicate a complete file as old/new text. Split larger work into multiple read_file_range + replace_in_file calls. Requires ALLOW_WRITE=1 and server-issued taskAuthorization; never fabricate authorization, and call unreal_agent_plan once if none exists. Read the target range first and set expectedOccurrences=1. Line endings (CRLF/LF) are normalized automatically. If oldText is not found, re-read a narrower range and correct it; never retry unchanged. Byte-identical repeat calls are rejected.",
        inputSchema: makeJsonSchema({
          ...taskAuthSchemaProperties(),
          path: { type: "string", description: "workspace:// or project:// path (active-project Source allowed even outside WORKSPACE_ROOT)." },
          oldText: { type: "string", description: "Exact text to replace." },
          newText: { type: "string", description: "Replacement text." },
          expectedOccurrences: { type: "number", description: "If set, replacement only proceeds when occurrence count matches." }
        }, ["taskAuthorization", "path", "oldText", "newText"])
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
        description: "Delete one file under the active project's Source/ tree only after propose_file_deletions returned a per-file approvalToken and the user approved that plan. Requires server-issued taskAuthorization from unreal_agent_plan, ALLOW_WRITE=1, and ALLOW_SOURCE_DELETE=1; never fabricate authorization. Extended mode only.",
        inputSchema: makeJsonSchema({
          ...taskAuthSchemaProperties(),
          path: { type: "string", description: "workspace:// or project:// path inside the active project's Source tree." },
          completedEditsSummary: { type: "string", description: "Same completedEditsSummary used in propose_file_deletions." },
          reason: { type: "string", description: "Specific reason this file must be deleted." },
          ifNotDeleted: { type: "string", description: "What concretely happens if this file is not deleted." },
          ifDeleted: { type: "string", description: "What concretely happens if this file is deleted." },
          approvalToken: { type: "string", description: "Per-file approvalToken returned by propose_file_deletions after user approval." },
          expectedContent: { type: "string", description: "Optional exact file content guard before delete." }
        }, ["taskAuthorization", "path", "completedEditsSummary", "reason", "ifNotDeleted", "ifDeleted", "approvalToken"])
      },
      {
        name: "apply_edit_bundle",
        description: "Apply a small edit bundle atomically with pre-hash capture, scoped validation, and rollback on failure. For existing files use patches only, each covering at most 60 changed lines; multiple patches for the same file are allowed and applied in listed order. never put a complete existing file in files/content. The files form is only for bounded brand-new files. Requires ALLOW_WRITE=1 and server-issued taskAuthorization; never fabricate authorization, and call unreal_agent_plan once if none exists.",
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
        }, ["taskAuthorization"])
      },
      {
        name: "static_validate_project",
        description: "Run static Unreal compile-readiness validation on active project and enabled plugin source. A completed scan stamps the current mutation generation even when findings remain, so one authoritative UBT build can follow without an override.",
        inputSchema: makeJsonSchema({
          projectRoot: { type: "string", description: "Optional project root or .uproject path. Defaults to active project." }
        })
      },
      {
        name: "search_files",
        description: "Search text under workspace:// or project://. For current Unreal code, scope to project://Source or project://Plugins and use direct source evidence.",
        inputSchema: makeJsonSchema({
          query: { type: "string", description: "Regex or plain text to search." },
          path: { type: "string", description: "Relative directory/file to search. Default '.'." },
          regex: { type: "boolean", description: "Use query as regex. Default false." },
          matchFileNames: {
            type: "boolean",
            description: "Also return file paths whose basename matches query. Default false; use for exact component/class discovery."
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
  return {
    tools: filterAgentTools(tools, context)
  };
});

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const name = request.params.name;
  const args = request.params.arguments || {};
  const priorSeq = beginToolCall();
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
      } catch {
        // Older clients may not accept list-changed notifications.
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
    const nestedTaskAuthorization = args.taskAuthorization
      && typeof args.taskAuthorization === "object"
      ? args.taskAuthorization
      : args.task_authorization
      && typeof args.task_authorization === "object"
      ? args.task_authorization
      : null;
    const hasExplicitTaskAuthorization = Boolean(
      nestedTaskAuthorization
      || String(args.taskSessionId || args.task_session_id || "").trim()
    );
    let routePreflight = { ok: true };
    if (SAFE_ROUTE_RECOVERY_TOOLS.has(name)) {
      routePreflight = { ok: true, controlSurface: true };
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
      return fail(
        routePreflight.error || "Task route authorization failed.",
        routeAuthorizationFailureOptions(routePreflight, name)
      );
    }
    const earlyRepeatBlock = checkToolRepeatBlocked(name, args, priorSeq);
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
      recordToolFailure(name, args, "INVALID_TOOL_ARGUMENTS");
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
      recordToolFailure(name, args, "INVALID_TOOL_ARGUMENTS");
      return fail("Missing required argument(s): " + missingNonAuthorizationArgs.join(", "), {
        errorCode: "INVALID_TOOL_ARGUMENTS",
        requiredArguments: argumentCheck.required,
        providedArguments: argumentCheck.provided,
        retryable: true,
        stopCurrentWorkflow: false,
        agentInstruction: "Retry this same tool once with the missing required arguments. Do not create a new plan.",
      });
    }

    // Validation-heavy read tools: reserve budget before I/O, commit on success,
    // rollback on semantic/validation failure so concurrent calls still contend.
    const DEFER_BUDGET_UNTIL_SUCCESS = new Set([
      "read_symbol",
      "read_file",
      "read_file_range",
      "search_files",
      "list_directory",
    ]);
    let pendingBudgetReservation = null;
    const budgetFields = requiredFields(args || {});
    const runBudgetOp = (op, reservationId = "") => {
      if (hasExplicitTaskAuthorization) {
        return op(
          WORKSPACE_ROOT,
          budgetFields.taskSessionId,
          budgetFields,
          args,
          name,
          reservationId
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
        reservationId
      );
    };
    if (
      !ROUTE_MUTATION_TOOLS.has(name)
      && !SAFE_ROUTE_RECOVERY_TOOLS.has(name)
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
    const rollbackDeferredBudget = () => {
      if (!pendingBudgetReservation) return;
      const reservationId = String(pendingBudgetReservation.id || "");
      pendingBudgetReservation = null;
      if (reservationId) {
        runBudgetOp(rollbackRouteReservation, reservationId);
      }
    };
    const heartbeatDeferredBudget = () => {
      if (!pendingBudgetReservation || !pendingBudgetReservation.id) return;
      runBudgetOp(heartbeatRouteReservation, String(pendingBudgetReservation.id));
    };
    const commitDeferredBudgetOrFail = () => {
      if (!pendingBudgetReservation) return null;
      const reservationId = String(pendingBudgetReservation.id || "");
      pendingBudgetReservation = null;
      const committed = runBudgetOp(commitRouteReservation, reservationId);
      if (!committed.ok) {
        if (reservationId) {
          runBudgetOp(rollbackRouteReservation, reservationId);
        }
        return fail(
          committed.error || "Task route authorization failed.",
          routeAuthorizationFailureOptions(committed, name)
        );
      }
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
      const maxEntries = Math.max(1, Math.min(Number(args.maxEntries || 200), 1000));
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
      const budgetFail = commitDeferredBudgetOrFail();
      if (budgetFail) return budgetFail;
      return text(JSON.stringify({ path: pathMetadata(resolution), entries: rows }, null, 2));
    }

    if (name === "read_unreal_logs") {
      const activeProject = getActiveProject(CONFIG_PATH);
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
      if (logsDirs.length === 0) {
        return fail(`logs directories not found under: ${projectDir}`, {
          nextSteps: ["Run build_unreal_project or launch the project once to create a log."]
        });
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
        nextSteps: chunks.length
          ? ["Use only the first actionable error or assertion for the next fix."]
          : ["Run the project or build once, then read logs again."]
      });
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
        return fail(`not found: ${args.path}`, {
          nextSteps: ["Search for the basename inside the active project before guessing a new path."],
          suggestedToolCalls: [{
            tool: "search_files",
            args: { query: path.basename(String(args.path || "")), path: resolution.resolvedRootType === "active_project" ? "project://Source" : "workspace://" }
          }]
        });
      }
      if (!s.isFile()) return fail(`not a file: ${args.path}`, {
        path: pathMetadata(resolution),
        suggestedToolCalls: [{ tool: "list_directory", args: { path: displayPath(resolution) } }]
      });

      const mutationGeneration = await resolveMutationGenerationForRead(resolution, target);
      const readContext = buildReadEvidenceContext(target, s, resolution, {
        mutationGeneration,
        taskSessionId: requiredFields(args).taskSessionId,
        taskAuthorization: args.taskAuthorization,
      });
      const guard = prepareReadGuard("read_file", args, readContext);
      const blocked = applyReadGuard("read_file", guard, readContext);
      if (blocked) return blocked;
      const recoveryBlocked = applyBuildRecoveryEvidenceGuard("read_file", readContext);
      if (recoveryBlocked) return recoveryBlocked;

      const detail = resolveCodeDetail(args.detailLevel);
      const tierCap = CODE_DETAIL_READ_BYTES[detail];
      const maxBytes = Math.max(
        1,
        Math.min(Number(args.maxBytes || tierCap), tierCap, MAX_READ_BYTES)
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
      const budgetFail = commitDeferredBudgetOrFail();
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
        evidenceHash: sha256Text(out),
      }, output, { lineRange: { start: 1, end: truncated.endLine } });
      return text(output);
    }

    if (name === "read_file_range") {
      const resolution = await resolveReadToolPath(args.path);
      const target = resolution.absolutePath;
      const s = await statSafe(target);
      if (!s) return fail(`not found: ${args.path}`);
      if (!s.isFile()) return fail(`not a file: ${args.path}`);

      const mutationGeneration = await resolveMutationGenerationForRead(resolution, target);
      const readContext = buildReadEvidenceContext(target, s, resolution, {
        mutationGeneration,
        taskSessionId: requiredFields(args).taskSessionId,
        taskAuthorization: args.taskAuthorization,
      });
      const guard = prepareReadGuard("read_file_range", args, readContext);
      const blocked = applyReadGuard("read_file_range", guard, readContext);
      if (blocked) return blocked;
      const recoveryBlocked = applyBuildRecoveryEvidenceGuard("read_file_range", readContext);
      if (recoveryBlocked) return recoveryBlocked;
      const normalizedArgs = guard.normalizedArgs;

      const detail = resolveCodeDetail(args.detailLevel);
      const lineCap = CODE_DETAIL_LINE_CAP[detail];
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
      const numbered = slice.map((line, idx) => `${startLine + idx}|${line}`).join("\n");
      const output = `File: ${displayPath(resolution)}\nPath-Metadata: ${JSON.stringify(pathMetadata(resolution))}\nLines: ${startLine}-${Math.min(endLine, lines.length)} of ${lines.length}\n\n${numbered}`;
      const budgetFail = commitDeferredBudgetOrFail();
      if (budgetFail) return budgetFail;
      rememberReadEvidence(
        target,
        s,
        resolution,
        `${startLine}-${Math.min(endLine, lines.length)}`,
        sha256Text(content)
      );
      recordReadSuccess("read_file_range", normalizedArgs, {
        ...readContext,
        evidenceHash: sha256Text(content),
      }, output);
      return text(output);
    }

    if (name === "read_symbol") {
      const resolution = await resolveReadToolPath(args.path);
      const target = resolution.absolutePath;
      const stat = await statSafe(target);
      if (!stat || !stat.isFile()) return fail(`not found or not a file: ${args.path}`);

      const mutationGeneration = await resolveMutationGenerationForRead(resolution, target);
      const readContext = buildReadEvidenceContext(target, stat, resolution, {
        mutationGeneration,
        taskSessionId: requiredFields(args).taskSessionId,
        taskAuthorization: args.taskAuthorization,
      });
      const guard = prepareReadGuard("read_symbol", args, readContext);
      const blocked = applyReadGuard("read_symbol", guard, readContext);
      if (blocked) return blocked;
      const recoveryBlocked = applyBuildRecoveryEvidenceGuard("read_symbol", readContext);
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
      const budgetFail = commitDeferredBudgetOrFail();
      if (budgetFail) return budgetFail;
      rememberReadEvidence(
        target,
        stat,
        resolution,
        `${startLine}-${endLine}`,
        sha256Text(content)
      );
      recordReadSuccess("read_symbol", normalizedArgs, {
        ...readContext,
        evidenceHash: sha256Text(content),
      }, output);
      return text(output);
    }

    if (name === "write_file") {
      if (!ALLOW_WRITE) return fail("write_file blocked. Set ALLOW_WRITE=1 to enable.");
      const authFail = enforceTaskAuth(args, {
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
        allowExistingWrite: ALLOW_EXISTING_SOURCE_WRITE
      });
      if (!guard.ok) {
        const rel = displayPath(writeResolution);
        const fileExists = await exists(target);
        const discipline = writeDisciplineOptions(fileExists);
        return fail(guard.message, {
          ...discipline,
          suggestedToolCalls: fileExists ? [
            { tool: "read_file", args: { path: rel, detailLevel: "compact" } },
            { tool: "replace_in_file", args: { path: rel, oldText: "<exact text from read_file>", newText: "<replacement>", expectedOccurrences: 1 } }
          ] : discipline.suggestedToolCalls
        });
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
        const repeat = checkMutationDuplicate("write_file", target, mutationPayload);
        if (repeat.duplicate) {
          return fail(duplicateMutationMessage("write_file", rel, repeat), {
            errorCode: "MUTATION_REPEAT_BLOCKED",
            retryable: false,
            doNotRetry: ["write_file"],
            stopCurrentWorkflow: true,
          });
        }
        const contentToWrite = mutationPayload;
        if (isSemanticGuardSourcePath(target)) {
          const semanticGuard = validateMutationSemanticText(contentToWrite);
          if (!semanticGuard.ok) {
            return mutationSemanticGuardFailure(semanticGuard, rel);
          }
        }
        const budgetFail = commitMutationRouteBudget(args, "write_file");
        if (budgetFail) return budgetFail;
        const targetExists = await exists(target);
        const priorContent = targetExists && ALLOW_EXISTING_SOURCE_WRITE
          ? await fsp.readFile(target, "utf8")
          : null;
        try {
          if (ALLOW_EXISTING_SOURCE_WRITE) {
            atomicWriteText(target, contentToWrite);
          } else {
            await createExclusive(target, contentToWrite);
          }
        } catch (err) {
          if (err && err.code === "EEXIST") {
            const discipline = writeDisciplineOptions(true);
            return fail(`write_file blocked because file already exists: ${rel}. Use replace_in_file. Do not retry write_file.`, {
              ...discipline,
              suggestedToolCalls: [
                { tool: "read_file", args: { path: rel, detailLevel: "compact" } },
                { tool: "replace_in_file", args: { path: rel, oldText: "<exact text from read_file>", newText: "<replacement>", expectedOccurrences: 1 } }
              ]
            });
          }
          throw err;
        }
        recordMutationAttempt("write_file", target, mutationPayload);
        invalidateFileCache(target);
        const validation = await validateAfterWrite(target, () => getActiveProject(CONFIG_PATH));
        if (validationFailed(validation)) {
          // Stale-safe rollback: only revert if the file still holds exactly what this
          // request wrote. A newer operation's content must never be clobbered.
          let current = null;
          try { current = await fsp.readFile(target, "utf8"); } catch { current = null; }
          if (shouldRollback(current, contentToWrite)) {
            if (priorContent === null) {
              await fsp.unlink(target);
            } else {
              atomicWriteText(target, priorContent);
            }
            invalidateFileCache(target);
            return validationToolResult(
              `WRITE ROLLED BACK — ${rel} failed static validation.`,
              validation,
              {
                ok: false,
                path: rel,
                operation: "create",
                rolledBack: true,
                isError: true,
                error: "Static validation failed after create; the write was reverted.",
                nextSteps: ["Fix the first blocking finding, then submit a corrected write_file call."]
              }
            );
          }
          invalidateFileCache(target);
          return validationToolResult(
            `WRITE CONFLICT — ${rel} failed validation and rollback was skipped.`,
            validation,
            {
              ok: false,
              path: rel,
              operation: "create",
              rolledBack: false,
              conflict: true,
              isError: true,
              error: "Another operation changed the file after this write.",
              nextSteps: ["Read the current file before any further edit and reconcile the conflict."]
            }
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
        let mutation;
        try {
          mutation = await bumpProjectMutationGeneration(target, contentToWrite);
        } catch (err) {
          return mutationBookkeepingFailure(err.message || err, "create", rel);
        }
        const checkpoint = recordAutomaticContinuityCheckpoint(args, [target], validation);
        if (!checkpoint.ok) {
          return continuityCheckpointFailure(checkpoint, "write_file", [rel], mutation);
        }
        return validationToolResult(summary, validation, {
          path: rel,
          operation: "create",
          bytesWritten: Buffer.byteLength(contentToWrite, "utf8"),
          nextSteps,
          ...(mutation ? { mutationGeneration: mutation.mutationGeneration } : {}),
          continuityCheckpoint: checkpoint,
        });
      } finally {
        releasePathLock(target);
      }
    }

    if (name === "replace_in_file") {
      if (!ALLOW_WRITE) return fail("replace_in_file blocked. Set ALLOW_WRITE=1 to enable.");
      const authFail = enforceTaskAuth(args, {
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
        return fail("replace_in_file patch is too large for a reliable LM Studio tool call.", {
          errorCode: "BOUNDED_PATCH_REQUIRED",
          retryable: true,
          stopCurrentWorkflow: false,
          nextAction: "read_file_range",
          nextActionArgs: {
            path: displayPath(writeResolution),
            startLine: 1,
            endLine: 120,
            detailLevel: "compact",
          },
          limits: {
            maxCombinedPatchChars: MAX_PATCH_ARGUMENT_CHARS,
            maxChangedLines: MAX_PATCH_CHANGED_LINES,
          },
          agentInstruction: "Read one narrower target range, then replace only that exact region. Split the change across additional bounded patches; never duplicate the complete file as oldText/newText and do not stop or cancel the task.",
        });
      }

      const lock = tryAcquirePathLock(target, "replace_in_file");
      if (!lock.ok) {
        return fail("previous write still in progress on this path; verify file state with read_file before retrying.");
      }
      try {
        const mutationPayload = `${oldText}\u0000${newText}\u0000${args.expectedOccurrences ?? ""}`;
        const repeat = checkMutationDuplicate("replace_in_file", target, mutationPayload);
        if (repeat.duplicate) {
          return fail(duplicateMutationMessage("replace_in_file", displayPath(writeResolution), repeat), {
            errorCode: "MUTATION_REPEAT_BLOCKED",
            retryable: false,
            doNotRetry: ["replace_in_file"],
            stopCurrentWorkflow: true,
          });
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
          return fail(`oldText not found in ${args.path} (file uses ${hasCRLF ? "CRLF" : "LF"} line endings).${hint}`, {
            errorCode: "OLD_TEXT_NOT_FOUND",
            retryable: false,
            doNotRetry: ["replace_in_file"],
            nextSteps: ["Call read_file_range for the target lines, then retry replace_in_file with corrected oldText."],
          });
        }
        const isSourcePath = [".h", ".hpp", ".cpp", ".c", ".cc", ".cs"].includes(path.extname(target).toLowerCase());
        const expectedOccurrences = args.expectedOccurrences !== undefined
          ? Number(args.expectedOccurrences)
          : (isSourcePath ? 1 : undefined);
        if (isSourcePath && args.expectedOccurrences === undefined && occurrences > 1) {
          const snippets = contentNorm.split("\n")
            .map((line, index) => ({ line, index }))
            .filter(({ line }) => line.includes(oldTextNorm.split("\n")[0]))
            .slice(0, 3)
            .map(({ line, index }) => `L${index + 1}: ${line.slice(0, 120)}`)
            .join("\n");
          return fail(
            `ambiguous replace in ${args.path}: found ${occurrences} matches; specify expectedOccurrences or narrow oldText.${snippets ? `\n\nMatches:\n${snippets}` : ""}`,
            {
              errorCode: "AMBIGUOUS_REPLACE",
              retryable: false,
              doNotRetry: ["replace_in_file"],
            }
          );
        }
        if (expectedOccurrences !== undefined && occurrences !== expectedOccurrences) {
          return fail(`occurrence mismatch: expected ${expectedOccurrences}, found ${occurrences}`, {
            errorCode: "OCCURRENCE_MISMATCH",
            retryable: false,
            doNotRetry: ["replace_in_file"],
          });
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
        const budgetFail = commitMutationRouteBudget(args, "replace_in_file");
        if (budgetFail) return budgetFail;
        const evidenceEntry = readEvidence.get(path.resolve(target));
        const casResult = await replaceWithCAS({
          targetPath: target,
          priorContent: content,
          oldText,
          newText,
          expectedOccurrences,
          readHash: evidenceEntry?.contentHash || null,
        });
        if (!casResult.ok) {
          return fail(casResult.error || "replace_in_file blocked by read-hash CAS.", {
            errorCode: casResult.errorCode || "READ_HASH_CAS_MISMATCH",
            retryable: false,
            doNotRetry: ["replace_in_file"],
            nextSteps: ["Re-read the file, then retry replace_in_file with exact oldText."],
          });
        }
        recordMutationAttempt("replace_in_file", target, mutationPayload);
        const updated = casResult.updated;
        invalidateFileCache(target);
        const validation = await validateAfterWrite(target, () => getActiveProject(CONFIG_PATH));
        const rel = displayPath(writeResolution);
        if (validationFailed(validation)) {
          // Stale-safe rollback: only restore if the file still holds exactly what this
          // request wrote; otherwise a newer operation owns the file — skip and warn.
          let current = null;
          try { current = await fsp.readFile(target, "utf8"); } catch { current = null; }
          if (shouldRollback(current, updated)) {
            atomicWriteText(target, priorContent);
            invalidateFileCache(target);
            return validationToolResult(
              `PATCH ROLLED BACK — ${rel} failed static validation.`,
              validation,
              {
                ok: false,
                path: rel,
                operation: "replace",
                replacements: occurrences,
                rolledBack: true,
                isError: true,
                error: "Static validation failed after replace; the file was restored.",
                nextSteps: ["Fix the first blocking finding, re-read the target, then submit a corrected patch."]
              }
            );
          }
          invalidateFileCache(target);
          return validationToolResult(
            `PATCH CONFLICT — ${rel} failed validation and rollback was skipped.`,
            validation,
            {
              ok: false,
              path: rel,
              operation: "replace",
              replacements: occurrences,
              rolledBack: false,
              conflict: true,
              isError: true,
              error: "Another operation changed the file after this patch.",
              nextSteps: ["Read the current file before any further edit and reconcile the conflict."]
            }
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
        let mutation;
        try {
          mutation = await bumpProjectMutationGeneration(target, updated);
        } catch (err) {
          return mutationBookkeepingFailure(err.message || err, "replace", rel);
        }
        const checkpoint = recordAutomaticContinuityCheckpoint(args, [target], validation);
        if (!checkpoint.ok) {
          return continuityCheckpointFailure(checkpoint, "replace_in_file", [rel], mutation);
        }
        return validationToolResult(summary, validation, {
          path: rel,
          operation: "replace",
          replacements: occurrences,
          nextSteps,
          ...(mutation ? { mutationGeneration: mutation.mutationGeneration } : {}),
          continuityCheckpoint: checkpoint,
        });
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
      const authFail = enforceTaskAuth(args, {
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
        if (args.expectedContent !== undefined) {
          const content = await fsp.readFile(target, "utf8");
          if (content !== String(args.expectedContent)) {
            return fail("expectedContent mismatch; delete aborted.");
          }
        }
        const budgetFail = commitMutationRouteBudget(args, "delete_file");
        if (budgetFail) return budgetFail;
        await fsp.unlink(target);
        invalidateFileCache(target);
        const activeProjectForMutation = activeProject;
        let mutation = null;
        if (activeProjectForMutation) {
          const projectDir = path.dirname(path.resolve(activeProjectForMutation));
          const projectRelativePath = path.relative(projectDir, target).replace(/\\/g, "/");
          if (!projectRelativePath || projectRelativePath.startsWith("../") || path.isAbsolute(projectRelativePath)) {
            return fail(`mutation path outside active project: ${target}`, {
              deleted: rel,
              writeApplied: true,
              bookkeepingFailed: true,
              retryable: false,
            });
          }
          try {
            mutation = await recordDeletion(projectDir, projectRelativePath);
          } catch (error) {
            return fail(String(error.message || error), {
              errorCode: "MUTATION_LOCK_BUSY",
              deleted: rel,
              writeApplied: true,
              bookkeepingFailed: true,
              mutationGenerationNotRecorded: true,
              retryable: false,
              nextSteps: [
                "Do NOT retry delete_file — the file is already removed from disk.",
                "Call static_validate_project before build_unreal_project.",
              ],
            });
          }
        }
        const checkpoint = recordAutomaticContinuityCheckpoint(args, [target], null);
        if (!checkpoint.ok) {
          return continuityCheckpointFailure(checkpoint, "delete_file", [rel], mutation);
        }
        return text(JSON.stringify({
          ok: true,
          deleted: rel,
          fileName: path.basename(target),
          completedEditsSummary,
          reason,
          ifNotDeleted,
          ifDeleted,
          ...(mutation ? { mutationGeneration: mutation.mutationGeneration } : {}),
          continuityCheckpoint: checkpoint,
          ...(checkpoint.taskAuthorization ? { taskAuthorization: checkpoint.taskAuthorization } : {}),
          ...(checkpoint.toolRoute ? { toolRoute: checkpoint.toolRoute } : {}),
        }, null, 2));
      } finally {
        releasePathLock(target);
      }
    }

    if (name === "apply_edit_bundle") {
      if (!ALLOW_WRITE) return fail("apply_edit_bundle blocked. Set ALLOW_WRITE=1 to enable.");
      const authFail = enforceTaskAuth(args, {
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
          return fail(`apply_edit_bundle.files cannot overwrite existing file: ${relPath}`, {
            errorCode: "BUNDLE_EXISTING_FILE_CONTENT_FORBIDDEN",
            retryable: true,
            stopCurrentWorkflow: false,
            nextAction: "replace_in_file",
            agentInstruction: "Use a bounded exact patch for the existing file. Never resend its complete content in apply_edit_bundle.files; split larger work into read_file_range + replace_in_file calls.",
          });
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
            limits: {
              maxCombinedPatchChars: MAX_PATCH_ARGUMENT_CHARS,
              maxChangedLines: MAX_PATCH_CHANGED_LINES,
            },
            agentInstruction: "Split the bundle entry into bounded exact patches and continue. Do not send complete existing files or stop the task.",
          });
        }
      }

      const budgetFail = commitMutationRouteBudget(args, "apply_edit_bundle");
      if (budgetFail) return budgetFail;

      const tx = await applyBundleTransaction(bundle, resolveBundlePath, {
        maxFilesPerEdit: auth.maxFilesPerEdit || DEFAULT_MAX_FILES_PER_EDIT,
        onCommitted: async (commit) => {
          const validationResults = [];
          for (const absPath of commit.writtenAbs) {
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
          }
          const failed = validationResults.find((item) => validationFailed(item));
          if (failed) {
            return { ok: false, error: "static validation failed", validation: failed, validationResults };
          }
          return { ok: true, validationResults };
        },
      });
      if (!tx.ok) {
        await agentNotify(`apply_edit_bundle failed: ${tx.error}`, "error");
        return fail(`apply_edit_bundle failed: ${tx.error}`, {
          ...(tx.validation?.semanticGuard ? {
            errorCode: "MUTATION_SEMANTIC_GUARD_FAILED",
            semanticGuard: tx.validation.semanticGuard,
            nextAction: "unreal_code_sketch_claim_validate",
            retryable: true,
            stopCurrentWorkflow: false,
            agentInstruction: "Correct the first known-bad pattern, revalidate the exact target sketch, then retry the bounded bundle.",
          } : {}),
          rolledBack: tx.rollback?.rolledBack ?? tx.rolledBack ?? false,
          rollbackIncomplete: tx.rollback?.rollbackIncomplete ?? tx.rollbackIncomplete ?? true,
          restoredPaths: tx.rollback?.restoredPaths || tx.restoredPaths || [],
          unrestoredPaths: tx.rollback?.unrestoredPaths || tx.unrestoredPaths || [],
          externalChangeDetected: tx.rollback?.externalChangeDetected || tx.externalChangeDetected || [],
          rollbackErrors: tx.rollback?.rollbackErrors || [],
          recoveryRequired: Boolean(tx.lockFailure),
        });
      }

      const validationResults = Array.isArray(tx.validation?.validationResults)
        ? tx.validation.validationResults
        : [];
      const primaryValidation = validationResults.find((item) => item?.skipped)
        || validationResults[0]
        || null;
      let lastMutation = null;
      for (const absPath of tx.writtenAbs) {
        const relPath = path.relative(projectRoot, absPath).replace(/\\/g, "/");
        const finalContent = await fsp.readFile(absPath, "utf8");
        try {
          lastMutation = await recordMutation(projectRoot, relPath, finalContent);
        } catch (error) {
          return mutationBookkeepingFailure(error.message, "apply_edit_bundle", relPath);
        }
      }
      const checkpoint = recordAutomaticContinuityCheckpoint(
        args,
        tx.writtenAbs,
        primaryValidation
      );
      if (!checkpoint.ok) {
        return continuityCheckpointFailure(
          checkpoint,
          "apply_edit_bundle",
          tx.writtenAbs.map((item) => path.relative(projectRoot, item).replace(/\\/g, "/")),
          lastMutation
        );
      }
      const bundleNextSteps = ["Run build_unreal_project after C++ edits."];
      if (primaryValidation?.skipped) {
        bundleNextSteps.unshift("Run static_validate_project before build.");
      }
      return validationToolResult(`OK — applied ${tx.writtenAbs.length} file(s) from bundle.`, primaryValidation, {
        operation: "apply_edit_bundle",
        writtenCount: tx.writtenAbs.length,
        preChangeHashes: tx.preChangeHashes,
        transactionId: tx.transactionId,
        ...(lastMutation ? { mutationGeneration: lastMutation.mutationGeneration } : {}),
        continuityCheckpoint: checkpoint,
        nextSteps: bundleNextSteps,
        phase: "editing",
        userMessage: `Applied ${tx.writtenAbs.length} file(s) from bundle`,
        cancellable: false,
      });
    }

    if (name === "static_validate_project") {
      await agentNotify("Running static validation…");
      const activeProject = getActiveProject(CONFIG_PATH);
      let projectRoot = String(args.projectRoot || "").trim();
      if (!projectRoot && activeProject) {
        projectRoot = path.dirname(path.resolve(activeProject));
      }
      if (!projectRoot) {
        const switchGuidance = projectSwitchGuidance(agentRegisteredToolNames());
        return fail("No active project and no projectRoot provided.", {
          nextSteps: ["Select an active .uproject, then run static validation again."],
          ...switchGuidance
        });
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
      const validation = await runStaticValidation(projectRoot);
      const severityCounts = (validation.findings || []).reduce((counts, finding) => {
        const key = String(finding.severity || "unknown").toLowerCase();
        counts[key] = (counts[key] || 0) + 1;
        return counts;
      }, {});
      const validationSummary = validationFailed(validation)
        ? `STATIC VALIDATION FAILED — ${severityCounts.error || 0} error(s), ${severityCounts.warning || 0} warning(s)`
        : `STATIC VALIDATION PASSED — ${severityCounts.warning || 0} warning(s)`;
      if (validationFailed(validation)) {
        const loopState = recordValidationFailure(projectRoot, validationStart.startGeneration, validation);
        if (loopState.blocked) {
          return validationToolResult("WORKFLOW BLOCKED: same validation/build failure repeated without a file mutation.", validation, {
            ok: false,
            operation: "static_validate",
            isError: true,
            error: "Validation/build loop blocked until the source is changed.",
            errorCode: "WORKFLOW_LOOP_BLOCKED",
            retryable: false,
            doNotRetry: ["static_validate_project", "build_unreal_project"],
            stopCurrentWorkflow: true,
            validationOverrideAvailable: false,
            mutationGeneration: loopState.mutationGeneration,
            nextSteps: ["Read the first blocking finding, edit the responsible source file, then validate the new mutation generation."],
          });
        }
        const finish = await finishValidationAndClear(projectRoot, validationStart.startGeneration);
        if (finish.validationStale) {
          return fail("Validation stale: project mutated during validation.", {
            validationStale: true,
            mutationGeneration: finish.mutationGeneration,
            nextSteps: ["Re-run static_validate_project after edits settle."],
          });
        }
        await agentNotify(validationSummary);
        return validationToolResult(validationSummary, validation, {
          ok: false,
          operation: "static_validate",
          error: "Static validation found blocking errors; the completed scan is fresh for this mutation generation.",
          errorCode: "STATIC_VALIDATION_FAILED",
          retryable: false,
          doNotRetry: ["static_validate_project"],
          stopCurrentWorkflow: false,
          validationOverrideAvailable: false,
          buildAllowedForValidatedGeneration: true,
          requiredNextTool: "build_unreal_project",
          validatedGeneration: finish.validatedGeneration,
          mutationGeneration: finish.mutationGeneration,
          nextSteps: ["Fix the first blocking finding, or run build_unreal_project exactly once without validationOverride to obtain authoritative UBT errors."],
        });
      }
      recordValidationSuccess(projectRoot, validationStart.startGeneration);
      const finish = await finishValidationAndClear(projectRoot, validationStart.startGeneration);
      if (finish.validationStale) {
        return fail("Validation stale: project mutated during validation.", {
          validationStale: true,
          mutationGeneration: finish.mutationGeneration,
          nextSteps: ["Re-run static_validate_project after edits settle."],
        });
      }
      await agentNotify(validationSummary);
      return validationToolResult(validationSummary, validation, {
        operation: "static_validate",
        validatedGeneration: finish.validatedGeneration,
        mutationGeneration: finish.mutationGeneration,
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
      const matchFileNames = args.matchFileNames === true;
      const query = String(args.query || "");
      if (!query) return fail("query must not be empty");

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
        taskAuthorization: args.taskAuthorization,
      });
      const guard = prepareReadGuard("search_files", args, readContext);
      const blocked = applyReadGuard("search_files", guard, readContext);
      if (blocked) return blocked;
      const recoveryBlocked = applyBuildRecoveryEvidenceGuard("search_files", readContext);
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
      const output = JSON.stringify(payload, null, 2);
      const budgetFail = commitDeferredBudgetOrFail();
      if (budgetFail) return budgetFail;
      recordReadSuccess("search_files", normalizedArgs, {
        ...readContext,
        evidenceHash: sha256Text(output),
      }, output);
      return text(output);
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

    if (name === "build_unreal_project") {
      if (!ALLOW_UNREAL_BUILD) {
        return fail("build_unreal_project blocked. Set ALLOW_UNREAL_BUILD=1 to enable.", {
          nextSteps: ["Rerun the root integrated installer, choose AGENT authority for a trusted project, restart LM Studio, then retry."]
        });
      }

      const planResult = await resolveBuildPlan(WORKSPACE_ROOT, CONFIG_PATH, args);
      if (!planResult.ok || !planResult.build) {
        return fail(planResult.error || "Could not resolve Unreal build plan.", {
          errorCode: "BUILD_PLAN_RESOLUTION_FAILED",
          retryable: false,
          userMessage: "Build plan could not be resolved for the active project.",
          agentInstruction: "Call unreal_set_active_project on unreal-rag, confirm the .uproject path, then retry build_unreal_project.",
          requiredNextTool: { server: "unreal-rag", name: "unreal_set_active_project" },
          nextSteps: [
            "Call unreal_set_active_project on unreal-rag with a valid .uproject path.",
            "Confirm build target and configuration, then retry build_unreal_project.",
          ],
        });
      }

      const build = planResult.build;
      if (!build.buildTool || !(await exists(build.buildTool))) {
        return fail(`Unreal build tool not found: ${build.buildTool || "not resolved"}`);
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
      const failBuildGate = (errorCode, error, details = {}) => {
        const gateLoop = recordBuildGateFailure(projectRoot, mutation.mutationGeneration, errorCode);
        if (gateLoop.blocked) {
          return fail("Build gate loop blocked: the same pre-build failure repeated without a file mutation.", {
            errorCode: "WORKFLOW_LOOP_BLOCKED",
            retryable: false,
            stopCurrentWorkflow: true,
            doNotRetry: ["build_unreal_project"],
            requiredNextTool: details.requiredNextTool,
            mutationGeneration: gateLoop.mutationGeneration,
            nextSteps: ["Do not call build_unreal_project again with unchanged project state. Follow the required next tool or stop and report the blocker."],
          });
        }
        return fail(error, { errorCode, retryable: false, ...details });
      };
      const validationOverride = args.validationOverride === true;
      const validationOverrideNote = String(args.validationOverrideNote || "").trim();
      if (validationOverride && validationOverrideNote.length < 12) {
        return failBuildGate(
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
        return failBuildGate("VALIDATION_REQUIRED", dirtyGate.error, {
          validationDirty: dirtyGate.state,
          stopCurrentWorkflow: false,
          doNotRetry: ["build_unreal_project"],
          requiredNextTool: "static_validate_project",
          nextSteps: dirtyGate.nextSteps,
        });
      }
      const validationProofGate = requireValidationProofOrOverride(mutation, {
        override: validationOverride,
        auditNote: validationOverrideNote,
      });
      if (!validationProofGate.ok) {
        return failBuildGate(validationProofGate.errorCode, validationProofGate.error, {
          validatedGeneration: validationProofGate.validatedGeneration,
          mutationGeneration: validationProofGate.mutationGeneration,
          stopCurrentWorkflow: false,
          doNotRetry: ["build_unreal_project"],
          requiredNextTool: "static_validate_project",
          nextSteps: validationProofGate.nextSteps,
        });
      }

      const target = String(build.target || "").trim();
      if (!/^[A-Za-z0-9_]+$/.test(target)) return fail("target must be a simple target name, e.g. MyGameEditor");

      const platform = String(build.platform || "Win64").trim();
      const configuration = String(build.configuration || "Development").trim();

      if (!/^[A-Za-z0-9_]+$/.test(platform)) return fail("invalid platform");
      if (!/^[A-Za-z0-9_]+$/.test(configuration)) return fail("invalid configuration");

      const buildAttempt = beginBuildAttempt(projectRoot, mutation.mutationGeneration);
      if (!buildAttempt.ok) {
        return fail("Build loop blocked: this mutation generation already had a build attempt.", {
          errorCode: "WORKFLOW_LOOP_BLOCKED",
          retryable: false,
          stopCurrentWorkflow: true,
          doNotRetry: ["build_unreal_project", "static_validate_project"],
          requiredNextTool: "read_unreal_logs",
          mutationGeneration: buildAttempt.mutationGeneration,
          suggestedToolCalls: [{ tool: "read_unreal_logs", args: { summaryOnly: true, maxFiles: 1, maxLines: 200 } }],
          nextSteps: ["Read the newest build log once. Fix its first actionable error; if it contains no actionable source error, stop and report that evidence instead of making a synthetic edit."],
        });
      }
      const buildTimeout = Number(args.timeoutMs || COMMAND_TIMEOUT_MS);
      const logRel = path.join(".agent", "logs", "latest-build.log");
      const logAbs = path.join(projectRoot, logRel);
      const buildGen = await beginBuild(path.dirname(projectPath));
      await agentNotify(`Building ${target} ${platform} ${configuration}…`);
      const execResult = await runUnrealBuildFromPlan({
        workspaceRoot: path.dirname(projectPath),
        build: { ...build, target, platform, configuration, projectPath },
        allowEngineFallback: args.allowEngineFallback === true,
        expectedEngineVersion: process.env.UNREAL_EXPECTED_ENGINE_VERSION || "",
        timeoutMs: buildTimeout,
        logPath: logAbs,
      });
      finishBuildAttempt(projectRoot, mutation.mutationGeneration, execResult);
      if (execResult.errorCode === "ENGINE_VERSION_MISMATCH") {
        return fail(execResult.error, {
          errorCode: execResult.errorCode,
          resolvedEngineVersion: execResult.resolvedEngineVersion,
          expectedEngineVersion: execResult.expectedEngineVersion,
          requestedEngineAssociation: execResult.requestedEngineAssociation,
          resolvedUbtPath: execResult.resolvedUbtPath,
          engineMismatch: true,
          retryable: false,
          nextSteps: [
            `Install or select Unreal Engine ${execResult.expectedEngineVersion}, or pass allowEngineFallback=true with an audit note if the project is compatible.`,
          ],
        });
      }
      const endGen = await finishBuild(path.dirname(projectPath), buildGen.buildStartGeneration);
      const result = {
        ok: Boolean(execResult.commandSucceeded),
        exitCode: execResult.exitCode ?? 1,
        stdout: execResult.stdout || "",
        stderr: execResult.stderr || "",
        error: execResult.error || "",
        timedOut: Boolean(execResult.timedOut),
        errorCode: execResult.errorCode || "",
      };
      const command = `${execResult.executable} ${(execResult.args || []).join(" ")}`;
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
      if (payload.recovery) {
        const sourceScopedRecovery = Boolean(
          String(payload.recovery.targetFile || "").trim()
          && ["read_file", "read_file_range", "search_files"].includes(
            String(payload.recovery.requiredNextTool || "")
          )
        );
        if (sourceScopedRecovery) {
          recordBuildRecoveryContract(
            projectRoot,
            endGen.mutationGeneration,
            payload.recovery
          );
        }
        const hasTaskAuthorization = Boolean(
          args.taskAuthorization && typeof args.taskAuthorization === "object"
          && args.taskAuthorization.taskSessionId
        );
        if (hasTaskAuthorization && sourceScopedRecovery) {
          const recoveryBinding = recordBuildRecoveryViaPython(
            WORKSPACE_ROOT,
            args,
            {
              ...payload.recovery,
              mutationGeneration: endGen.mutationGeneration,
            }
          );
          payload.recovery.taskScopeBound = recoveryBinding?.ok === true;
          if (recoveryBinding?.ok !== true) {
            payload.recovery.taskScopeBindingErrorCode = String(
              recoveryBinding?.errorCode || "BUILD_RECOVERY_TASK_BINDING_FAILED"
            );
          }
        }
        if (!sourceScopedRecovery) {
          payload.recovery.taskScopeBound = false;
          payload.recovery.scopeStrategy = "symbol_lookup_then_replan";
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
      if (endGen.buildStale) {
        payload.proofLevel = "BuiltStale";
        payload.phase = "stale";
        payload.ok = false;
      }
      if (execResult.errorCode === "BUILD_TIMEOUT") {
        payload.errorCode = "BUILD_TIMEOUT";
        payload.ok = false;
      }
      const disposition = buildToolDisposition(payload);
      payload.buildOutcome = disposition.buildOutcome;
      payload.toolExecutionSucceeded = disposition.toolExecutionSucceeded;
      payload.recoverable = disposition.recoverable;
      const budgetFail = commitDeferredBudgetOrFail();
      if (budgetFail) return budgetFail;
      if (disposition.buildOutcome === "succeeded") {
        const completion = completeTaskAfterBuildViaPython(
          WORKSPACE_ROOT,
          args,
          {
            proofLevel: payload.proofLevel,
            mutationGeneration: endGen.mutationGeneration,
            buildLogPath: logPath,
          }
        );
        payload.taskLifecycle = completion?.ok === true
          ? { status: "completed", routeOwnershipReleased: true }
          : {
            status: "completion_failed",
            routeOwnershipReleased: false,
            errorCode: String(completion?.errorCode || "TASK_BUILD_COMPLETION_FAILED"),
          };
        if (completion?.ok === true && completion?.taskSessionId) {
          try {
            await server.sendToolListChanged();
          } catch {
            // Older clients may not accept list-changed notifications.
          }
        }
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
      recordToolFailure(name, args, "INTERNAL_ERROR");
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

async function main() {
  try {
    const recovery = await recoverIncompleteJournals(resolveAgentStateRoot());
    if (recovery.recoveryRequired?.length) {
      console.error(`[unreal-agent] transaction recovery required: ${JSON.stringify(recovery.recoveryRequired)}`);
    }
    if (recovery.skippedCorrupt?.length) {
      console.error(`[unreal-agent] skipped corrupt journals: ${recovery.skippedCorrupt.length}`);
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
  const startupRouteContext = listToolsRouteContext(
    WORKSPACE_ROOT,
    getActiveProject(CONFIG_PATH) || ""
  );
  emitCatalogInitializedDiagnostic(startupRouteContext);
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
  });
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
