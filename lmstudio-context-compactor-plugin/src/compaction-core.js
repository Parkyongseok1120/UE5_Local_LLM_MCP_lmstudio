"use strict";

const crypto = require("node:crypto");

const COMPACTION_SCHEMA_VERSION = 2;
const REQUEST_INTENT_VERSION = 1;
const REQUEST_INTENT_MUTABILITIES = new Set([
  "none",
  "control_state",
  "source_files",
  "external_process",
]);
const REQUEST_INTENT_SPEECH_ACTS = new Set(["query", "command", "proposal"]);
const REQUEST_INTENT_AMBIGUITY_STATES = new Set(["resolved", "unresolved"]);
const REQUEST_INTENT_SERVER_TOOL_NAMES = new Set([
  "unreal_agent_plan",
  "unreal_task_start",
  "unreal_task_status",
  "unreal_task_recover_active",
  "unreal_task_checkpoint",
]);
const MAX_EDIT_EVIDENCE_FILES = 2;
const MAX_EDIT_EVIDENCE_CHARS = 16000;
const MAX_REPEAT_EVIDENCE_FILES = 1;
const MAX_REPEAT_EVIDENCE_CHARS = 12000;
const DEFAULT_COMPACTION_CONFIG = Object.freeze({
  enabled: true,
  observeOnly: false,
  softRemainingTokens: 14000,
  hardRemainingTokens: 8000,
  maxOutputReserve: 4096,
  safetyMarginTokens: 1024,
  normalToolResultReserve: 3000,
  buildToolResultReserve: 8000,
  recentCompleteTurns: 1,
  maxCurrentTurnMessages: 12,
  minimumTurnsBetweenCompactions: 0,
  targetRemainingTokensAfterCompaction: 24000,
  maxCheckpointFacts: 32,
});

function stableStringify(value) {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(",")}]`;
  return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableStringify(value[key])}`).join(",")}}`;
}

function sha256(value) {
  return crypto.createHash("sha256").update(String(value || ""), "utf8").digest("hex");
}

function objectiveHashOf(value) {
  return sha256(String(value || "").trim());
}

function boundedRequestIntentValue(value, depth = 0) {
  if (value == null || typeof value === "boolean") return value;
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string") return value.slice(0, 500);
  if (depth >= 4) return null;
  if (Array.isArray(value)) {
    return value.slice(0, 12).map((item) => boundedRequestIntentValue(item, depth + 1));
  }
  if (typeof value !== "object") return String(value).slice(0, 500);
  return Object.fromEntries(
    Object.entries(value)
      .slice(0, 16)
      .map(([key, child]) => [String(key).slice(0, 120), boundedRequestIntentValue(child, depth + 1)]),
  );
}

function compactRequestIntent(value, objective = "", expectedObjectiveHash = "") {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  if (Number(value.version) !== REQUEST_INTENT_VERSION) return null;
  const objectiveHash = String(value.objectiveHash || "").trim().toLowerCase();
  const expectedHash = String(expectedObjectiveHash || objectiveHashOf(objective)).trim().toLowerCase();
  const domain = String(value.domain || "").trim();
  const operation = String(value.operation || "").trim();
  const mutability = String(value.mutability || "").trim();
  const speechAct = String(value.speechAct || "").trim();
  const ambiguity = value.ambiguity;
  if (
    !/^[a-f0-9]{64}$/.test(objectiveHash)
    || !/^[a-f0-9]{64}$/.test(expectedHash)
    || objectiveHash !== expectedHash
    || !domain
    || !operation
    || !REQUEST_INTENT_MUTABILITIES.has(mutability)
    || !REQUEST_INTENT_SPEECH_ACTS.has(speechAct)
    || typeof value.negated !== "boolean"
    || !value.targets
    || typeof value.targets !== "object"
    || Array.isArray(value.targets)
    || !ambiguity
    || typeof ambiguity !== "object"
    || Array.isArray(ambiguity)
    || !REQUEST_INTENT_AMBIGUITY_STATES.has(String(ambiguity.status || "").trim())
    || typeof ambiguity.material !== "boolean"
  ) return null;
  return {
    version: REQUEST_INTENT_VERSION,
    objectiveHash,
    domain: domain.slice(0, 80),
    operation: operation.slice(0, 80),
    mutability,
    speechAct,
    negated: value.negated,
    targets: boundedRequestIntentValue(value.targets),
    ambiguity: {
      status: String(ambiguity.status).trim(),
      material: ambiguity.material,
    },
  };
}

function matchingRequestIntent(text, context = {}) {
  const candidate = context && typeof context === "object"
    ? (context.requestIntent || (Number(context.version) === REQUEST_INTENT_VERSION ? context : null))
    : null;
  const authoritativeObjectiveHash = context?.authoritativeObjectiveProjection === true
    ? String(context.objectiveHash || "").trim().toLowerCase()
    : "";
  return compactRequestIntent(
    candidate,
    String(text || "").trim(),
    authoritativeObjectiveHash,
  );
}

function isWindowsHostPlatform(hostPlatform = process.platform) {
  return ["win32", "windows", "nt"].includes(
    String(hostPlatform || "").trim().toLowerCase(),
  );
}

function asciiWindowsFold(value) {
  return String(value || "").replace(/[A-Z]/g, (character) => character.toLowerCase());
}

function normalizeProjectEvidencePath(value, hostPlatform = process.platform) {
  let normalized = String(value || "").trim().replace(/\\/g, "/");
  while (normalized.startsWith("./")) normalized = normalized.slice(2);
  normalized = normalized.replace(/^project:\/\//i, "").replace(/\/{2,}/g, "/");
  normalized = normalized.replace(/^\/+|\/+$/g, "");
  return isWindowsHostPlatform(hostPlatform)
    ? asciiWindowsFold(normalized)
    : normalized;
}

function textOf(message) {
  if (!message) return "";
  if (typeof message === "string") return message;
  if (typeof message.text === "string") return message.text;
  if (typeof message.content === "string") return message.content;
  if (typeof message.getText === "function") {
    try { return String(message.getText() || ""); } catch { return ""; }
  }
  return "";
}

function roleOf(message) {
  if (!message) return "unknown";
  if (typeof message.role === "string") return message.role;
  if (typeof message.getRole === "function") {
    try { return String(message.getRole() || "unknown"); } catch { return "unknown"; }
  }
  return "unknown";
}

function toolRequestsOf(message) {
  if (Array.isArray(message?.toolCalls)) return message.toolCalls;
  if (typeof message?.getToolCallRequests === "function") {
    try { return message.getToolCallRequests() || []; } catch { return []; }
  }
  return [];
}

function toolResultsOf(message) {
  if (Array.isArray(message?.toolResults)) return message.toolResults;
  if (typeof message?.getToolCallResults === "function") {
    try { return message.getToolCallResults() || []; } catch { return []; }
  }
  return [];
}

function toolResultContent(result) {
  // The MCP control envelope lives in structuredContent. Prefer it over the
  // intentionally concise text projection so compaction never has to infer
  // protocol state from prose.
  const raw = (
    result?.structuredContent && typeof result.structuredContent === "object"
      ? result.structuredContent
      : result?.content ?? result?.result ?? ""
  );
  if (typeof raw === "string") {
    const source = raw.trim();
    if ((source.startsWith("[") || source.startsWith("{")) && source.length > 1) {
      try {
        const parsed = JSON.parse(source);
        const isTransportBlock = Array.isArray(parsed)
          ? parsed.some((block) => block && typeof block === "object" && ["text", "resource"].includes(block.type))
          : parsed && typeof parsed === "object" && ["text", "resource"].includes(parsed.type);
        if (isTransportBlock) return toolResultContent({ content: parsed });
      } catch { /* ordinary source/text result */ }
    }
    return raw;
  }
  if (Array.isArray(raw)) {
    return raw.map((block) => {
      if (typeof block === "string") return block;
      if (typeof block?.text === "string") return block.text;
      if (typeof block?.content === "string") return block.content;
      try { return JSON.stringify(block); } catch { return ""; }
    }).filter(Boolean).join("\n");
  }
  if (raw && typeof raw === "object") {
    if (typeof raw.text === "string") return raw.text;
    try { return JSON.stringify(raw); } catch { return ""; }
  }
  return String(raw || "");
}

function messageSnapshot(message) {
  return {
    role: roleOf(message),
    text: textOf(message),
    toolCalls: toolRequestsOf(message).map((call) => ({
      id: call.id || null,
      name: call.name || "",
      arguments: call.arguments || {},
    })),
    toolResults: toolResultsOf(message).map((result) => ({
      toolCallId: result.toolCallId || null,
      name: result.name || "",
      content: toolResultContent(result),
      isError: result.isError === true,
    })),
  };
}

function snapshotMessages(messages) {
  return (messages || []).map(messageSnapshot);
}

function parseJsonObjects(text) {
  const values = [];
  const parseNested = (candidate, depth = 0) => {
    if (depth > 4 || candidate == null) return;
    if (Array.isArray(candidate)) {
      for (const item of candidate) parseNested(item, depth + 1);
      return;
    }
    if (candidate && typeof candidate === "object") {
      // LM Studio persists MCP text blocks as a JSON-encoded array inside the
      // tool result's content string. The block is transport, not evidence;
      // recursively parse its text to recover the actual MCP payload.
      if (candidate.type === "text" && typeof candidate.text === "string") {
        parseNested(candidate.text, depth + 1);
        return;
      }
      values.push(candidate);
      return;
    }
    const source = String(candidate || "").trim();
    if (!source) return;
    try {
      parseNested(JSON.parse(source), depth + 1);
    } catch {
      const matches = source.match(/\{[\s\S]*\}/g) || [];
      for (const match of matches.slice(-4)) {
        try {
          parseNested(JSON.parse(match), depth + 1);
        } catch { /* text is not JSON; keep the raw message */ }
      }
    }
  };
  parseNested(text);
  return values;
}

function toolResultSucceeded(result) {
  if (result?.isError === true) return false;
  const payloads = parseJsonObjects(result?.content);
  for (const payload of payloads) {
    if (
      payload.isError === true
      || payload.ok === false
      || payload.toolExecutionSucceeded === false
      || payload.phase === "failed"
      || payload.validationProofPassed === false
      || payload.validationPassed === false
      || payload.buildAllowedForValidatedGeneration === false
    ) {
      return false;
    }
    const validationSummary = payload.validationSummary;
    if (validationSummary && (validationSummary.ok === false || validationSummary.skipped === true)) {
      return false;
    }
    if (typeof payload.buildOutcome === "string" && /fail|error/i.test(payload.buildOutcome)) {
      return false;
    }
  }
  // Plain-text tool results are successful unless the transport or structured
  // payload explicitly marks them as failed. This preserves compatibility with
  // MCP tools that return human-readable output instead of JSON.
  return true;
}

function isNonToolNextAction(_value) {
  // Kept as a compatibility export for the generator. New checkpoints only
  // persist actions whose server-owned control.nextActionIsTool is true, so a
  // duplicated sentinel allowlist is neither needed nor authoritative.
  return false;
}

const ARCHITECTURE_CONTROL_STATES = new Set([
  "Discovery",
  "InitialProposal",
  "FullReplan",
  "EvidenceRefill",
  "ExactRepair",
  "Revalidation",
  "Validated",
  "FailedClosed",
]);

function compactProtocolControl(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  if (Number(value.version || 0) !== 1) return null;
  return {
    version: Number(value.version),
    taskId: String(value.taskId || "").slice(0, 160),
    phase: String(value.phase || "").slice(0, 160),
    status: String(value.status || "").slice(0, 160),
    nextAction: String(value.nextAction || "").slice(0, 160),
    nextActionIsTool: value.nextActionIsTool === true,
    retryPolicy: String(value.retryPolicy || "none").slice(0, 40),
    blockerFingerprint: String(value.blockerFingerprint || "").slice(0, 160),
    continuationToken: String(value.continuationToken || "").slice(0, 160),
  };
}

const SERVER_CONTROL_DISPOSITIONS = new Set([
  "continue",
  "require_tool",
  "rediscover",
  "checkpoint",
  "await_user",
  "workflow_stop",
  "complete",
]);

function compactServerControl(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  if (Number(value.version || 0) < 2) return null;
  const epoch = Number(value.epoch);
  const disposition = String(value.disposition || "").trim().toLowerCase();
  const taskSessionId = String(value.taskSessionId || "").trim().slice(0, 160);
  if (!Number.isInteger(epoch) || epoch < 0 || !taskSessionId) return null;
  if (!SERVER_CONTROL_DISPOSITIONS.has(disposition)) return null;
  const requiredValue = value.requiredTool;
  let requiredTool = null;
  if (requiredValue != null) {
    if (!requiredValue || typeof requiredValue !== "object" || Array.isArray(requiredValue)) return null;
    const name = String(requiredValue.name || "").trim();
    if (!/^[a-z][a-z0-9_-]{1,160}$/i.test(name)) return null;
    if (
      requiredValue.args != null
      && (typeof requiredValue.args !== "object" || Array.isArray(requiredValue.args))
    ) return null;
    requiredTool = {
      name,
      args: requiredValue.args && typeof requiredValue.args === "object"
        ? requiredValue.args
        : {},
    };
  }
  const allowedTools = normalizeToolNames(value.allowedTools).slice(0, 32);
  if (["require_tool", "checkpoint"].includes(disposition)) {
    if (!requiredTool) return null;
    if (allowedTools.length !== 1 || !toolNamesMatch(allowedTools[0], requiredTool.name)) return null;
  } else if (["await_user", "workflow_stop", "complete"].includes(disposition)) {
    if (requiredTool || allowedTools.length > 0) return null;
  } else if (requiredTool) {
    return null;
  }
  const retryValue = String(value.retryPolicy?.sameSemanticInput || "allowed").trim();
  if (!["allowed", "once", "forbidden"].includes(retryValue)) return null;
  const blocker = value.blocker && typeof value.blocker === "object" && !Array.isArray(value.blocker)
    ? {
      code: String(value.blocker.code || "").slice(0, 120),
      fingerprint: String(value.blocker.fingerprint || "").slice(0, 160),
    }
    : null;
  return {
    version: 2,
    epoch,
    taskSessionId,
    routeHash: String(value.routeHash || "").slice(0, 160),
    phase: String(value.phase || "unknown").slice(0, 160),
    disposition,
    requiredTool,
    allowedTools,
    retryPolicy: { sameSemanticInput: retryValue },
    blocker,
  };
}

function acceptServerControl(state, incoming) {
  if (!incoming) return false;
  const current = compactServerControl(state.serverControl);
  if (current && current.taskSessionId === incoming.taskSessionId) {
    if (incoming.epoch < current.epoch) {
      state.lastDiagnostics.push(
        `controlEpochRegression=${incoming.epoch}<${current.epoch}`,
      );
      return false;
    }
    if (incoming.epoch === current.epoch && stableStringify(incoming) !== stableStringify(current)) {
      state.lastDiagnostics.push(`controlEpochConflict=${incoming.epoch}`);
      return false;
    }
  }
  state.serverControl = incoming;
  return true;
}

function projectServerControl(state) {
  const control = compactServerControl(state.serverControl);
  if (!control) return false;
  state.serverControl = control;
  state.protocolControl = null;
  state.taskRouteTerminal = ["workflow_stop", "complete"].includes(control.disposition);
  if (state.taskRouteTerminal) {
    state.toolRoute = null;
  } else {
    state.toolRoute = {
      routeHash: control.routeHash,
      phase: control.phase,
      activeTools: [...control.allowedTools],
      selectedSlice: state.toolRoute?.selectedSlice || null,
    };
  }
  state.requiredNextTool = control.requiredTool?.name || null;
  state.requiredNextToolRef = control.requiredTool
    ? { sourceField: "control.requiredTool", epoch: control.epoch }
    : null;
  state.requiredNextToolArgs = control.requiredTool?.args || null;
  return true;
}

function compactEvidenceLedger(value, absent = false) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const files = value.files && typeof value.files === "object" && !Array.isArray(value.files)
    ? value.files
    : {};
  const compactFiles = {};
  for (const [rawKey, rawEntry] of Object.entries(files).slice(0, 32)) {
    if (!rawEntry || typeof rawEntry !== "object" || Array.isArray(rawEntry)) continue;
    const pathValue = String(rawEntry.path || rawKey || "").replace(/\\/g, "/").slice(0, 500);
    if (!pathValue) continue;
    // The entry path retains authoritative spelling in checkpoints written by
    // older releases whose lowercased object key may already be lossy.
    const pathKey = normalizeProjectEvidencePath(pathValue);
    if (!pathKey) continue;
    if (absent) {
      compactFiles[pathKey] = {
        evidenceId: String(rawEntry.evidenceId || "").slice(0, 80),
        path: pathValue,
        searchComplete: rawEntry.searchComplete === true,
        scopes: Array.isArray(rawEntry.scopes) ? rawEntry.scopes.map(String).slice(0, 8) : [],
        queries: Array.isArray(rawEntry.queries) ? rawEntry.queries.map(String).slice(0, 8) : [],
      };
    } else {
      compactFiles[pathKey] = {
        evidenceId: String(rawEntry.evidenceId || "").slice(0, 80),
        path: pathValue,
        contentHash: String(rawEntry.contentHash || "").slice(0, 80),
        sourceKind: String(rawEntry.sourceKind || "").slice(0, 40),
        coveredRanges: Array.isArray(rawEntry.coveredRanges)
          ? rawEntry.coveredRanges.slice(0, 16).map((range) => [Number(range?.[0]), Number(range?.[1])])
            .filter((range) => Number.isInteger(range[0]) && Number.isInteger(range[1]) && range[0] > 0 && range[1] >= range[0])
          : [],
        declarations: Array.isArray(rawEntry.declarations) ? rawEntry.declarations.map(String).slice(0, 32) : [],
        implementations: Array.isArray(rawEntry.implementations) ? rawEntry.implementations.map(String).slice(0, 32) : [],
      };
    }
  }
  return {
    version: Number(value.version || (absent ? 1 : 2)),
    planRevision: String(value.planRevision || "").slice(0, 160),
    files: compactFiles,
  };
}

function compactTaskRouteOwnership(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const taskSessionId = String(value.taskSessionId || "").trim();
  const ownerCapability = String(value.ownerCapability || "").trim();
  if (!taskSessionId || !ownerCapability) return null;
  return { taskSessionId, ownerCapability };
}

function rememberInvalidatedTaskSession(state, taskSessionId) {
  const value = String(taskSessionId || "").trim();
  if (!value) return;
  if (!(state.invalidatedTaskSessionIds instanceof Set)) {
    state.invalidatedTaskSessionIds = new Set();
  }
  // The id is deliberately retained across compactions so a delayed result
  // from the previous user objective cannot silently reactivate its route.
  state.invalidatedTaskSessionIds.add(value);
  while (state.invalidatedTaskSessionIds.size > 16) {
    state.invalidatedTaskSessionIds.delete(state.invalidatedTaskSessionIds.values().next().value);
  }
}

function resetTaskScopedControl(state, reason = "new_user_objective") {
  rememberInvalidatedTaskSession(state, compactServerControl(state.serverControl)?.taskSessionId);
  rememberInvalidatedTaskSession(state, compactTaskRouteOwnership(state.taskRouteOwnership)?.taskSessionId);
  state.serverControl = null;
  state.protocolControl = null;
  state.architectureControl = null;
  state.architectureProposal = null;
  state.taskRouteTerminal = false;
  state.toolRoute = null;
  state.taskRouteOwnership = null;
  state.requiredNextTool = null;
  state.requiredNextToolRef = null;
  state.requiredNextToolArgs = null;
  state.semanticBlocker = null;
  state.selectedSlice = null;
  state.sliceProgress = null;
  state.buildState = {};
  state.buildVerification = null;
  state.sourceEvidence = null;
  state.absentEvidence = null;
  state.evidenceFacts = [];
  state.editEvidence = [];
  state.repeatEvidence = [];
  state.requestIntent = null;
  state.exactSignatureContracts = [];
  state.coverageEvidence = [];
  state.touchedPaths = [];
  state.failedToolResults = [];
  state.mutationGeneration = 0;
  state.lastDiagnostics.push(`taskScopedControlReset=${reason}`);
}

function payloadTargetsInvalidatedTask(value, state) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const invalidated = state.invalidatedTaskSessionIds;
  if (!(invalidated instanceof Set) || invalidated.size === 0) return false;
  const candidates = [
    String(value.taskSessionId || "").trim(),
    String(value.state?.taskSessionId || "").trim(),
    compactServerControl(value.control)?.taskSessionId,
    compactTaskRouteOwnership(value.taskAuthorization)?.taskSessionId,
    compactTaskRouteOwnership(value.routeAuthorization)?.taskSessionId,
    compactTaskRouteOwnership(value.state?.taskAuthorization)?.taskSessionId,
  ];
  return candidates.some((candidate) => candidate && invalidated.has(candidate));
}

function normalizeToolNames(value, sourceTool = "") {
  const candidates = Array.isArray(value)
    ? value
    : (value === true ? [sourceTool] : (typeof value === "string" ? [value] : []));
  return [...new Set(candidates
    .map((item) => String(item || "").trim())
    .filter((item) => /^[a-z][a-z0-9_-]{1,160}$/i.test(item)))];
}

function collectSemanticBlockerFields(value, state, sourceTool = "") {
  if (!value || typeof value !== "object" || Array.isArray(value)) return;
  const control = compactProtocolControl(value.control);
  const retryTargets = normalizeToolNames(value.doNotRetry, sourceTool);
  const explicitForbiddenTools = normalizeToolNames(value.doNotRetryTools, sourceTool);
  const errorCode = String(value.errorCode || "");
  const stopCurrentWorkflow = value.stopCurrentWorkflow === true;
  const evidencePhaseBoundary = (
    (value.stopCurrentPhase === true && String(value.phaseBoundary || "").toLowerCase() === "evidence")
    || /^EVIDENCE_STAGNATION(?:_REPEAT)?$/i.test(errorCode)
  );
  const forbiddenTools = [...new Set([
    ...explicitForbiddenTools,
    ...((evidencePhaseBoundary || stopCurrentWorkflow) ? retryTargets : []),
  ])];
  const requiredNextTool = String(value.requiredNextTool || (
    value.nextActionIsTool === true ? value.nextAction : ""
  ) || "").trim();
  const handoffBoundary = Boolean(requiredNextTool && explicitForbiddenTools.length > 0);

  // retryPolicy=forbidden is often derived from retryable=false and does not
  // mean an entire tool family is forbidden. READ_REPEAT_DETECTED and corrected
  // write retries must remain possible with different arguments.
  // A fail-closed workflow stop is authoritative even when the server does not
  // enumerate a tool deny-list.  Semantic recovery gates intentionally return
  // nextActionIsTool=false when only a user/project contract can unblock the
  // work.  Dropping that signal here lets the prediction loop immediately start
  // another read/search cycle after the tool result.
  if (!evidencePhaseBoundary && !handoffBoundary && !stopCurrentWorkflow) return;

  const scope = evidencePhaseBoundary
    ? "evidence_phase"
    : (handoffBoundary ? "until_required_tool_success" : "workflow");

  const prior = state.semanticBlocker && typeof state.semanticBlocker === "object"
    ? state.semanticBlocker
    : {};
  const preserveEvidencePhase = (
    prior.active === true
    && prior.scope === "evidence_phase"
    && evidencePhaseBoundary
  );
  state.semanticBlocker = {
    active: true,
    scope: preserveEvidencePhase ? prior.scope : scope,
    errorCode: String(preserveEvidencePhase ? prior.errorCode : (errorCode || prior.errorCode || "")).slice(0, 120),
    blockerFingerprint: String(
      control?.blockerFingerprint || value.blockerFingerprint || prior.blockerFingerprint || "",
    ).slice(0, 160),
    stopCurrentWorkflow: preserveEvidencePhase
      ? prior.stopCurrentWorkflow === true
      : stopCurrentWorkflow,
    stopCurrentPhase: preserveEvidencePhase
      ? prior.stopCurrentPhase === true
      : evidencePhaseBoundary,
    phaseBoundary: preserveEvidencePhase ? prior.phaseBoundary : (evidencePhaseBoundary ? "evidence" : ""),
    forbiddenTools: [...new Set([
      ...(preserveEvidencePhase && Array.isArray(prior.forbiddenTools) ? prior.forbiddenTools : []),
      ...forbiddenTools,
    ])].slice(-32),
    clearOnTool: String(preserveEvidencePhase ? prior.clearOnTool : (requiredNextTool || "")).slice(0, 160),
    clearOnToolArgs: preserveEvidencePhase
      ? (prior.clearOnToolArgs || null)
      : (requiredNextTool && value.requiredNextToolArgs && typeof value.requiredNextToolArgs === "object"
        ? value.requiredNextToolArgs
        : null),
    agentInstruction: String(
      preserveEvidencePhase
        ? prior.agentInstruction
        : (value.agentInstruction || value.userMessage || prior.agentInstruction || ""),
    ).slice(0, 800),
  };
}

function isContinuationUserMessage(text) {
  const source = String(text || "").trim();
  const directContinuation = /^(?:continue|resume|retry|keep\s+going|go\s+on|계속(?:해|해서|\s*진행(?:해|하세요)?|\s*작업(?:해|하세요)?)?|이어(?:서)?(?:\s*진행(?:해|하세요)?)?|재개(?:해|하세요)?|중단한\s*곳부터\s*(?:계속|진행)(?:해|하세요)?|다시\s*시도(?:해|하세요)?)[\s.!?]*$/i;
  const contextualContinuation = /^(?:(?:아까|이전|전에|기존|중단한)\s*(?:하던\s*)?(?:작업|일|것|내용)|그\s*(?:작업|일|것|내용))(?:을|를)?\s*(?:계속(?:해|하세요)?|재개(?:해|하세요)?|이어(?:서)?\s*진행(?:해|하세요)?)[\s.!?]*$/i;
  const englishContextualContinuation = /^(?:continue|resume)\s+(?:the\s+)?(?:previous|prior|active|same)\s+(?:task|work)[\s.!?]*$/i;
  const englishShortContinuation = /^(?:continue|resume)\s+(?:this|that|the)\s+(?:validation|analysis|plan|replan|work|task)[\s.!?]*$/i;
  return directContinuation.test(source)
    || contextualContinuation.test(source)
    || englishContextualContinuation.test(source)
    || englishShortContinuation.test(source);
}

function mutationToolName(name) {
  const normalized = String(name || "").trim().toLowerCase();
  return ["replace_in_file", "write_file", "apply_edit_bundle"].some(
    (candidate) => normalized === candidate || normalized.endsWith(`_${candidate}`),
  );
}

function toolArgumentsSatisfy(requiredArgs, actualArgs) {
  if (!requiredArgs || typeof requiredArgs !== "object" || Array.isArray(requiredArgs)) return true;
  const actual = actualArgs && typeof actualArgs === "object" && !Array.isArray(actualArgs)
    ? actualArgs
    : {};
  const matches = (expected, received, key = "") => {
    if (key === "sessionId" || key === "session_id") return true;
    if (typeof expected === "string" && /^<[^>]+>$/.test(expected.trim())) return true;
    if (Array.isArray(expected)) {
      return Array.isArray(received)
        && expected.length === received.length
        && expected.every((item, index) => matches(item, received[index]));
    }
    if (expected && typeof expected === "object") {
      if (!received || typeof received !== "object" || Array.isArray(received)) return false;
      return Object.entries(expected).every(([childKey, child]) => (
        matches(child, received[childKey], childKey)
      ));
    }
    return stableStringify(received) === stableStringify(expected);
  };
  return Object.entries(requiredArgs).every(([key, expected]) => matches(expected, actual[key], key));
}

function boundedArchitecturePatchPreview(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const allowed = [
    "networking",
    "stateInventory",
    "lifecycleTransitions",
    "impactedSurfaces",
    "implementationFiles",
    "migrationPlan",
  ];
  const bound = (child, depth = 0) => {
    if (typeof child === "string") return child.slice(0, 300);
    if (typeof child === "number" || typeof child === "boolean" || child == null) return child;
    if (depth >= 4) return "[depth-truncated]";
    if (Array.isArray(child)) return child.slice(0, 10).map((item) => bound(item, depth + 1));
    if (typeof child === "object") {
      return Object.fromEntries(
        Object.entries(child).slice(0, 16).map(([key, item]) => [key, bound(item, depth + 1)]),
      );
    }
    return String(child).slice(0, 300);
  };
  const selected = Object.fromEntries(
    allowed.filter((key) => Object.hasOwn(value, key)).map((key) => [key, bound(value[key])]),
  );
  return Object.keys(selected).length ? selected : null;
}

function isTrustedRequestIntentToolName(toolName) {
  const rawToolName = String(toolName || "").trim().toLowerCase().replace(/\\/g, "/");
  const parsedToolName = parseProviderQualifiedToolName(rawToolName);
  const trustedName = parsedToolName.functionName;
  if (parsedToolName.qualified) {
    const trustedProvider = (
      /^mcp[/:]unreal-rag[/:]/u.test(rawToolName)
      || rawToolName.startsWith("mcp__unreal-rag__")
      || rawToolName.startsWith("mcp_unreal_rag_")
    );
    if (!trustedProvider) return false;
  } else if (rawToolName !== trustedName) {
    return false;
  }
  return REQUEST_INTENT_SERVER_TOOL_NAMES.has(trustedName);
}

function isTrustedRequestIntentResult(callName, resultName, payload, matchedCallObserved) {
  if (matchedCallObserved !== true || !payload || typeof payload !== "object" || Array.isArray(payload)) {
    return false;
  }
  if (!isTrustedRequestIntentToolName(callName)) return false;
  const parsedCallName = parseProviderQualifiedToolName(callName);
  const normalizedResultName = String(resultName || "").trim();
  if (normalizedResultName) {
    if (
      !isTrustedRequestIntentToolName(normalizedResultName)
      || !toolNamesMatch(callName, normalizedResultName)
    ) {
      return false;
    }
  }
  const trustedName = parsedCallName.functionName;
  if (trustedName === "unreal_agent_plan") {
    return (
      typeof payload.taskKind === "string"
      || payload.taskSessionStarted === false
      || (payload.projectControl && typeof payload.projectControl === "object")
      || payload.taskAuthorizationRequiredForWrites === true
    );
  }
  return Boolean(
    String(payload.taskSessionId || "").trim()
    && (
      (payload.state && typeof payload.state === "object")
      || (payload.control && typeof payload.control === "object")
      || (payload.continuity && typeof payload.continuity === "object")
      || (payload.taskAuthorization && typeof payload.taskAuthorization === "object")
    )
  );
}

function collectControlFields(value, state, context = {}) {
  if (!value || typeof value !== "object") return;
  if (payloadTargetsInvalidatedTask(value, state)) {
    state.lastDiagnostics.push("ignoredControlForInvalidatedTaskSession");
    return;
  }
  if (Object.hasOwn(value, "requestIntent")) {
    if (context.requestIntentTrusted === true) {
      const requestIntent = compactRequestIntent(
        value.requestIntent,
        state.objective,
        state.objectiveHash,
      );
      if (requestIntent) {
        state.requestIntent = requestIntent;
      } else if (value.requestIntent != null) {
        state.lastDiagnostics.push("ignoredInvalidOrMismatchedRequestIntent");
      }
    } else if (value.requestIntent != null) {
      state.lastDiagnostics.push("ignoredUntrustedRequestIntentSource");
    }
  }
  const declaredServerControl = value.control
    && typeof value.control === "object"
    && !Array.isArray(value.control)
    && Number(value.control.version || 0) >= 2;
  const incomingServerControl = compactServerControl(value.control);
  if (declaredServerControl && !incomingServerControl) {
    state.serverControl = null;
    state.protocolControl = null;
    state.taskRouteTerminal = true;
    state.toolRoute = null;
    state.requiredNextTool = null;
    state.requiredNextToolRef = null;
    state.requiredNextToolArgs = null;
    state.lastDiagnostics.push("invalidServerControlV2=fail_closed");
    return;
  }
  if (incomingServerControl) acceptServerControl(state, incomingServerControl);
  const authoritativeServerControl = compactServerControl(state.serverControl);
  const protocolControl = authoritativeServerControl
    ? null
    : compactProtocolControl(value.control);
  if (protocolControl) {
    state.protocolControl = protocolControl;
    if (
      ARCHITECTURE_CONTROL_STATES.has(protocolControl.status)
      || /architecture/i.test(protocolControl.phase)
    ) {
      state.architectureControl = protocolControl;
    }
  }
  if (typeof value.proposalRevision === "string" && value.proposalRevision.trim()) {
    const previous = state.architectureProposal || {};
    const nextErrorCode = String(value.errorCode || previous.lastErrorCode || "").slice(0, 120);
    const validation = value.proposalValidation && typeof value.proposalValidation === "object"
      ? value.proposalValidation
      : null;
    const repairs = validation && Array.isArray(validation.repairRequirements)
      ? validation.repairRequirements.slice(0, 24).map((row) => ({
        jsonPath: String(row?.jsonPath || "proposal").slice(0, 160),
        constraint: String(row?.constraint || "").slice(0, 500),
      }))
      : (previous.repairRequirements || []);
    state.architectureProposal = {
      ...previous,
      revision: value.proposalRevision.trim(),
      validationOk: validation ? validation.ok === true : previous.validationOk,
      proposalPatchApplied: value.proposalPatchApplied === true,
      repairRequirements: repairs,
      lastErrorCode: nextErrorCode,
      unchangedCorePaths: (
        nextErrorCode === "ARCHITECTURE_PROPOSAL_REPLAN_CORE_UNCHANGED"
        && Array.isArray(value.requiredChangedPaths)
      )
        ? value.requiredChangedPaths.slice(0, 24).map((path) => String(path).slice(0, 160))
        : [],
      requiredNextAction: String(
        value.requiredNextAction || previous.requiredNextAction || ""
      ).slice(0, 160),
      repairStrategy: String(
        validation?.repairStrategy || value.repairStrategy || previous.repairStrategy || ""
      ).slice(0, 80),
      stagedContractRequired: typeof validation?.designContract?.stagedImplementation === "boolean"
        ? validation.designContract.stagedImplementation
        : previous.stagedContractRequired === true,
      networkedContractRequired: typeof validation?.designContract?.networkedProposal === "boolean"
        ? validation.designContract.networkedProposal
        : previous.networkedContractRequired === true,
      requiresFullReplan: validation?.designContract?.requiresFullReplan === true
        || value.repairSubmission?.mode === "fullProposal",
      repairMode: String(value.repairSubmission?.mode || previous.repairMode || "").slice(0, 80),
      requiredRepairPaths: Array.isArray(value.repairSubmission?.requiredJsonPaths)
        ? value.repairSubmission.requiredJsonPaths.slice(0, 24).map((path) => String(path).slice(0, 160))
        : (previous.requiredRepairPaths || []),
      sourceSnapshotFingerprint: String(
        value.graphEvidence?.sourceSnapshotFingerprint
        || previous.sourceSnapshotFingerprint
        || ""
      ).slice(0, 96),
    };
  }
  const directActionIsTool = authoritativeServerControl
    ? Boolean(authoritativeServerControl.requiredTool)
    : protocolControl
    ? protocolControl.nextActionIsTool === true
    : value.nextActionIsTool === true || Boolean(value.requiredNextTool);
  let directRequiredNextToolSeen = Boolean(authoritativeServerControl?.requiredTool);
  let directRequiredNextTool = authoritativeServerControl?.requiredTool || null;
  let directAction = authoritativeServerControl?.requiredTool?.name || protocolControl?.nextAction || "";
  let directActionField = authoritativeServerControl?.requiredTool
    ? "control.requiredTool"
    : (protocolControl?.nextAction ? "control.nextAction" : "");
  // Only requiredNextToolArgs are server-owned equality constraints. Ordinary
  // nextActionArgs are model-facing templates/defaults and may deliberately
  // contain placeholders or omit values the model must derive.
  let directArgs = authoritativeServerControl?.requiredTool?.args
    || (value.requiredNextToolArgs && typeof value.requiredNextToolArgs === "object"
    ? value.requiredNextToolArgs
    : null);
  for (const [key, child] of Object.entries(value)) {
    if (key === "requestIntent") {
      continue;
    } else if (key === "control") {
      continue;
    } else if (!protocolControl && !authoritativeServerControl && key === "requiredNextTool") {
      directRequiredNextToolSeen = true;
      directRequiredNextTool = child;
    } else if (!protocolControl && !authoritativeServerControl && ["requiredNextAction", "nextAction"].includes(key) && typeof child === "string") {
      const candidate = child.trim();
      if (/^[a-z][a-z0-9_]{2,}(?::[a-z0-9_-]+)?$/.test(candidate)) {
        if (!directAction || key === "nextAction") {
          directAction = candidate;
          directActionField = key;
        }
      }
    } else if (!protocolControl && !authoritativeServerControl && key === "requiredNextToolArgs" && child && typeof child === "object") {
      directArgs = child;
    } else if (key === "taskRouteTerminal" && child === true) {
      state.taskRouteTerminal = true;
      state.toolRoute = null;
      state.taskRouteOwnership = null;
      state.requiredNextTool = null;
      state.requiredNextToolRef = null;
      state.requiredNextToolArgs = null;
    } else if (["taskAuthorization", "routeAuthorization"].includes(key)) {
      const ownership = compactTaskRouteOwnership(child);
      if (ownership && state.taskRouteTerminal !== true) state.taskRouteOwnership = ownership;
    } else if (key === "constraints" && Array.isArray(child)) {
      state.constraints.push(...child.filter((item) => typeof item === "string"));
    } else if (["diagnosticCode", "errorCode", "errorKey", "errorSubkind", "firstError"].includes(key) && child != null) {
      state.lastDiagnostics.push(`${key}=${String(child)}`.slice(0, 400));
    } else if (key === "signatureContract" && child && typeof child === "object") {
      state.exactSignatureContracts.push(child);
    } else if (["path", "file", "projectRelative", "projectPath"].includes(key) && typeof child === "string") {
      state.touchedPaths.push(child.replaceAll("\\", "/"));
    } else if (["activeProject", "uprojectPath", "projectFile"].includes(key) && typeof child === "string" && /\.uproject$/i.test(child)) {
      state.activeProject = child;
    } else if (key === "projectName" && typeof child === "string") {
      state.activeProjectName = child;
    } else if (key === "mutationGeneration" && Number.isFinite(Number(child))) {
      state.mutationGeneration = Math.max(state.mutationGeneration, Number(child));
    } else if (key === "buildOutcome" || key === "proofLevel" || key === "phase") {
      state.buildState[key] = child;
    } else if (key === "selectedSlice" && child && typeof child === "object") {
      state.selectedSlice = child;
    } else if (key === "sliceProgress" && child && typeof child === "object") {
      state.sliceProgress = child;
    } else if (key === "buildVerification" && child && typeof child === "object") {
      state.buildVerification = child;
    } else if (key === "sourceEvidence" && child && typeof child === "object") {
      state.sourceEvidence = compactEvidenceLedger(child, false);
    } else if (key === "absentEvidence" && child && typeof child === "object") {
      state.absentEvidence = compactEvidenceLedger(child, true);
    } else if (key === "toolRoute" && child && typeof child === "object") {
      if (state.taskRouteTerminal !== true) {
        state.toolRoute = {
          routeHash: child.routeHash || "",
          phase: child.phase || "",
          activeTools: Array.isArray(child.activeTools) ? child.activeTools.slice(0, 16) : [],
          selectedSlice: child.selectedSlice || null,
        };
      }
    } else if (["invariants", "acceptanceCriteria", "postconditions"].includes(key) && Array.isArray(child)) {
      state.invariants.push(...child.filter((item) => typeof item === "string"));
    } else if (["automationCoverage", "engineHeaderLookup", "coverageStatus", "coverage"].includes(key)) {
      state.coverageEvidence.push({ [key]: child });
    }
    collectControlFields(child, state, context);
  }
  if (projectServerControl(state)) return;
  // Parent control fields describe the action that must happen now. Reapply
  // them after recursion so nextActionArgs.requiredNextAction (the action
  // after a recovery checkpoint) cannot overwrite nextAction itself.
  if (directRequiredNextToolSeen) {
    if (directRequiredNextTool === null || directRequiredNextTool === false || directRequiredNextTool === "") {
      state.requiredNextTool = null;
      state.requiredNextToolRef = null;
      state.requiredNextToolArgs = null;
    } else if (typeof directRequiredNextTool === "string") {
      state.requiredNextTool = directRequiredNextTool;
      state.requiredNextToolRef = null;
    } else if (directRequiredNextTool && typeof directRequiredNextTool === "object") {
      const name = typeof directRequiredNextTool.name === "string"
        ? directRequiredNextTool.name
        : (typeof directRequiredNextTool.tool === "string" ? directRequiredNextTool.tool : "");
      if (name) {
        state.requiredNextTool = name;
        state.requiredNextToolRef = directRequiredNextTool;
      }
    }
  } else if (directAction) {
    if (!directActionIsTool) {
      // This is a server routing sentinel, not an MCP tool name. It means the
      // prior exact-tool gate is no longer applicable and any currently active
      // route tool may be selected.
      state.requiredNextTool = null;
      state.requiredNextToolRef = null;
      state.requiredNextToolArgs = null;
    } else {
      state.requiredNextTool = directAction.split(":", 1)[0];
      state.requiredNextToolRef = { sourceField: directActionField, value: directAction };
    }
  } else if (protocolControl) {
    // An enveloped completed response with no next action is authoritative and
    // clears any older nested/legacy action discovered during recursion.
    state.requiredNextTool = null;
    state.requiredNextToolRef = null;
    state.requiredNextToolArgs = null;
  }
  if (directArgs && state.requiredNextTool) state.requiredNextToolArgs = directArgs;
}

function semanticBlockerClearToolSucceeded(blocker, matchedCallName, matchedCall, payload) {
  if (!blocker || blocker.scope !== "until_required_tool_success") return false;
  if (!blocker.clearOnTool || !toolNamesMatch(blocker.clearOnTool, matchedCallName)) return false;
  const requiredArgs = blocker.clearOnToolArgs && typeof blocker.clearOnToolArgs === "object"
    ? blocker.clearOnToolArgs
    : null;
  if (requiredArgs) {
    const actualArgs = matchedCall?.arguments && typeof matchedCall.arguments === "object"
      ? matchedCall.arguments
      : {};
    if (!toolArgumentsSatisfy(requiredArgs, actualArgs)) return false;
  }
  const normalized = String(matchedCallName || "").toLowerCase();
  if (normalized.endsWith("search_files")) {
    return payload?.searchComplete === true || Array.isArray(payload?.results) || Array.isArray(payload?.fileNameResults);
  }
  return true;
}

function semanticAnchors(content) {
  const lines = String(content || "").replace(/^\[path-metadata:[^\n]*\]\r?\n?/, "")
    .replace(/^\[line-endings:[^\n]*\]\r?\n?/, "")
    .split(/\r?\n/);
  const ranked = [];
  const add = (index, score, line) => {
    const normalized = String(line || "").trim().replace(/\s+/g, " ");
    if (!normalized || normalized.startsWith("//")) return;
    ranked.push({ index, score, text: normalized.slice(0, 220) });
  };
  lines.forEach((line, index) => {
    const value = line.trim().replace(/^\d+\|/, "").trim();
    if (/IMPLEMENT_(?:SIMPLE|COMPLEX)_AUTOMATION_TEST|BEGIN_DEFINE_SPEC|END_DEFINE_SPEC|Describe\s*\(|It\s*\(/.test(value)) add(index, 110, value);
    else if (/^U(?:CLASS|STRUCT|ENUM|INTERFACE|FUNCTION)\b/.test(value)) add(index, 100, value);
    else if (/^(?:class|struct|enum(?:\s+class)?)\s+[A-Za-z_]/.test(value)) add(index, 95, value);
    else if (/^[A-Za-z_][\w:<>,*&\s]*::[~A-Za-z_]\w*\s*\(/.test(value)) add(index, 90, value);
    else if (/DOREPLIFETIME|HasAuthority\s*\(|_Implementation\s*\(|OnRep_|Server[A-Za-z_]*\s*\(/.test(value)) add(index, 85, value);
    else if (/^[A-Za-z_][\w:<>,*&\s]*\([^;{}]*\)\s*(?:const\s*)?;\s*$/.test(value)) add(index, 80, value);
    else if (/^UPROPERTY\b/.test(value)) add(index, 70, value);
    else if (/^(?:case\s+E\w+|return\s+E\w+|switch\s*\()/.test(value)) add(index, 65, value);
    else if (/^[A-Za-z_][\w:<>,*&\s]+\s+[A-Za-z_]\w*\s*(?:=\s*[^;]+)?;\s*$/.test(value)) add(index, 45, value);
  });
  const selected = ranked.sort((a, b) => b.score - a.score || a.index - b.index).slice(0, 12);
  selected.sort((a, b) => a.index - b.index);
  return selected.map((row) => `L${row.index + 1}: ${row.text}`);
}

function exactReadBody(toolName, source) {
  let body = String(source || "").replace(/\r\n/g, "\n");
  if (String(toolName || "").toLowerCase().endsWith("read_file_range")) {
    const split = body.indexOf("\n\n");
    if (split >= 0 && /^(?:File:|Path-Metadata:|Lines:)/m.test(body.slice(0, split))) {
      body = body.slice(split + 2);
    }
    body = body.split("\n").map((line) => line.replace(/^\d+\|/u, "")).join("\n");
  } else {
    body = body
      .split("\n")
      .filter((line) => !/^\[(?:path-metadata|read-truncation|line-endings):/iu.test(line))
      .join("\n");
  }
  const maxChars = 12_000;
  return {
    content: body.slice(0, maxChars),
    truncated: body.length > maxChars,
  };
}

function coveredRangeForRead(args, payload, source) {
  const start = Math.max(1, Number(args.startLine || args.start || 1));
  let end = Number(args.endLine || args.end || 0);
  if (!Number.isFinite(end) || end < start) {
    const headerMatch = String(source || "").match(/^Lines:\s*(\d+)-(\d+)/mu);
    const observedLineCount = Number(
      payload?.cachedLineCount || (source ? String(source).split(/\r?\n/u).length : 0)
    );
    end = headerMatch
      ? Number(headerMatch[2])
      : start + Math.max(0, observedLineCount - 1);
  }
  return Number.isInteger(start) && Number.isInteger(end) && end >= start
    ? [[start, end]]
    : [];
}

function compactToolEvidence(call, payload, resultContent = "") {
  const name = String(call?.name || "");
  const args = call?.arguments && typeof call.arguments === "object" ? call.arguments : {};
  const normalized = name.toLowerCase();
  if (normalized.endsWith("get_active_project") || normalized === "get_workspace_info") {
    const activeProject = String(
      payload?.activeProject || payload?.uprojectPath || payload?.projectFile
      || payload?.details?.projectFile || ""
    );
    return activeProject ? {
      tool: name,
      activeProject,
      projectName: String(payload?.projectName || payload?.details?.projectName || ""),
      projectDir: String(payload?.projectDir || payload?.details?.projectDir || ""),
    } : null;
  }
  if (normalized.endsWith("search_files")) {
    const matches = [
      ...(Array.isArray(payload?.results) ? payload.results : []),
      ...(Array.isArray(payload?.fileNameResults) ? payload.fileNameResults : []),
    ];
    return {
      tool: name,
      query: String(args.query || "").slice(0, 160),
      path: String(args.path || payload?.path?.displayPath || payload?.path || "").slice(0, 260),
      resultCount: Array.isArray(payload?.results) ? payload.results.length : 0,
      fileNameResultCount: Array.isArray(payload?.fileNameResults) ? payload.fileNameResults.length : 0,
      searchComplete: payload?.searchComplete === true,
      matchedFiles: [...new Set(matches.map((row) => String(row?.file || "")).filter(Boolean))].slice(0, 12),
      cached: payload?.cached === true,
      repeatDetected: payload?.repeatDetected === true,
    };
  }
  if (normalized.endsWith("list_directory")) {
    const entries = Array.isArray(payload?.entries) ? payload.entries : [];
    return {
      tool: name,
      path: String(args.path || payload?.path?.displayPath || payload?.path || "").slice(0, 260),
      entryCount: entries.length,
      entries: entries.map((row) => String(row?.name || row?.path || row || "")).filter(Boolean).slice(0, 32),
    };
  }
  if (normalized.endsWith("read_file") || normalized.endsWith("read_file_range")) {
    const source = String(payload?.content || resultContent || "");
    const exact = exactReadBody(name, source);
    const suppliedAnchors = Array.isArray(payload?.semanticAnchors)
      ? payload.semanticAnchors.filter((line) => typeof line === "string").slice(0, 16)
      : [];
    return {
      tool: name,
      path: String(args.path || payload?.path?.displayPath || payload?.path || "").slice(0, 260),
      startLine: Number(args.startLine || args.start || 0),
      endLine: Number(args.endLine || args.end || 0),
      lineCount: Number(payload?.cachedLineCount || (source ? source.split(/\r?\n/).length : 0)),
      contentHash: String(payload?.contentHash || "").slice(0, 80),
      evidenceHash: String(payload?.evidenceHash || payload?.contentHash || (source ? sha256(source) : "")).slice(0, 80),
      coveredRanges: coveredRangeForRead(args, payload, exact.content),
      exactContent: exact.content,
      exactContentTruncated: exact.truncated,
      semanticAnchors: suppliedAnchors.length ? suppliedAnchors : semanticAnchors(source),
      repeatDetected: payload?.repeatDetected === true,
      readAttempts: Number(payload?.readAttempts || 1),
    };
  }
  return null;
}

function normalizedProjectPath(value) {
  const source = String(value || "").replaceAll("\\", "/").replace(/^\/+/, "");
  const withoutScheme = source.replace(/^(?:project|workspace):\/\//i, "");
  const folded = asciiWindowsFold(withoutScheme);
  const sourceIndex = folded.indexOf("source/");
  const pluginsIndex = folded.indexOf("plugins/");
  const configIndex = folded.indexOf("config/");
  const indexes = [sourceIndex, pluginsIndex, configIndex].filter((index) => index >= 0);
  return indexes.length ? withoutScheme.slice(Math.min(...indexes)) : withoutScheme;
}

function selectedSliceFiles(selectedSlice) {
  return Array.isArray(selectedSlice?.files)
    ? selectedSlice.files.map(normalizedProjectPath).filter(Boolean).slice(0, MAX_EDIT_EVIDENCE_FILES)
    : [];
}

function compactEditEvidence(call, payload, resultContent, selectedSlice) {
  const name = String(call?.name || "");
  if (!/read_file(?:_range)?$/i.test(name)) return null;
  const args = call?.arguments && typeof call.arguments === "object" ? call.arguments : {};
  const path = normalizedProjectPath(args.path || payload?.path?.displayPath || payload?.path || "");
  const selectedFiles = selectedSliceFiles(selectedSlice);
  const pathIdentity = normalizeProjectEvidencePath(path);
  if (!pathIdentity || !selectedFiles.some((file) => {
    const fileIdentity = normalizeProjectEvidencePath(file);
    return pathIdentity === fileIdentity || pathIdentity.endsWith(`/${fileIdentity}`);
  })) return null;
  const source = String(payload?.content || resultContent || "");
  if (!source.trim()) return null;
  return {
    path,
    tool: name,
    startLine: Number(args.startLine || args.start || 0),
    endLine: Number(args.endLine || args.end || 0),
    content: source.slice(0, MAX_EDIT_EVIDENCE_CHARS),
    truncated: source.length > MAX_EDIT_EVIDENCE_CHARS,
    evidenceHash: String(payload?.evidenceHash || payload?.contentHash || sha256(source)).slice(0, 80),
  };
}

function compactRepeatEvidence(call, payload) {
  const name = String(call?.name || "");
  if (!/^(?:read_file|read_file_range|read_symbol|search_files)$/i.test(name)) return null;
  if (payload?.repeatDetected !== true || payload?.doNotRepeatRead !== true) return null;
  const args = call?.arguments && typeof call.arguments === "object" ? call.arguments : {};
  const source = String(payload?.content || "");
  if (!source.trim()) return null;
  return {
    tool: name,
    path: normalizedProjectPath(args.path || payload?.path?.displayPath || payload?.path || ""),
    query: String(args.query || "").slice(0, 500),
    startLine: Number(args.startLine || args.start || 0),
    endLine: Number(args.endLine || args.end || 0),
    content: source.slice(0, MAX_REPEAT_EVIDENCE_CHARS),
    truncated: source.length > MAX_REPEAT_EVIDENCE_CHARS,
    evidenceHash: String(payload?.evidenceHash || payload?.contentHash || sha256(source)).slice(0, 80),
    errorCode: String(payload?.errorCode || "READ_REPEAT_DETECTED").slice(0, 120),
  };
}

function classifyMutationIntent(text, context = {}) {
  const requestIntent = matchingRequestIntent(text, context);
  if (requestIntent) {
    const isMutation = requestIntent.mutability !== "none";
    return {
      isMutation,
      kind: isMutation ? "mutation" : "non_mutation",
    };
  }
  const source = String(text || "");
  const withoutExplicitNegation = source
    .replace(/\b(?:do\s+not|don't|dont)\s+(?:fix|edit|patch|change|modify|write|delete|rename|build|implement|create|add)\b/gi, " ")
    .replace(/(?:구현|완성|개발|만들|추가|고쳐|고치|수정|패치|리팩터|작성|삭제|적용|반영|편집|변경|이름\s*바꿔|빌드)(?:은|는|을|를|이|가|도)?\s*하(?:지\s*)?(?:마(?:라|세요)?|말(?:아|고|라|자|기)?)/g, " ");
  const isMutation = Boolean(
    /\b(?:implement|create|add|fix|patch|edit|modify|refactor|write|delete|rename|build|complete|finish)\b/i.test(withoutExplicitNegation)
    || /(?:구현|완성|개발|만들|추가|고쳐|고치|수정|패치|리팩터|작성|삭제|적용|반영|편집|변경|이름\s*바꿔|빌드)(?:(?:을|를|이|가|은|는|부터|까지|도|에|으로|로)\s*)?(?:해|하|할|해서|하고|해줘|하세요|할까|해야|시작|진행|우선|실행|줘|주세요)/.test(withoutExplicitNegation)
    || /(?:실제로|직접)\s*(?:구현|완성|개발|수정|적용|반영)/.test(withoutExplicitNegation)
  );
  return {
    isMutation,
    kind: isMutation ? "mutation" : "non_mutation",
  };
}

function classifyUserIntent(text, context = {}) {
  const source = String(text || "");
  const requestIntent = matchingRequestIntent(source, context);
  if (requestIntent) {
    return requestIntent.mutability === "none" ? "READ_ONLY" : "MUTATION";
  }
  const lower = source.toLowerCase();
  const explicitNoWrite = (
    /수정은\s*하(?:지\s*)?마/.test(source)
    || /수정하지\s*말/.test(source)
    || /찾기만하고/.test(source)
    || /분석만/.test(source)
    || /보고만/.test(source)
    ||
    /\b(?:do\s+not|don't|dont)\s+(?:fix|edit|patch|change|modify|write)\b/.test(lower)
    || /\b(?:no|without)\s+(?:fixes|edits|patches)\b/.test(lower)
    || /\bfind\s+bugs?\s+only\b/.test(lower)
    || /\banalysis only\b/.test(lower)
    || /\breport only\b/.test(lower)
  );
  // A question can contain words such as "structure" or "analysis" while
  // still explicitly asking for an implementation. Mutation intent wins;
  // otherwise ordinary show/describe/status questions are read-only even
  // when the user does not spell out "do not edit".
  if (classifyMutationIntent(source, context).isMutation) return "MUTATION";
  if (explicitNoWrite) return "READ_ONLY";
  const readOnlyIntent = Boolean(
    /\b(?:what|which|where|show|list|describe|explain|summari[sz]e|inspect|analy[sz]e|review|report|look\s+up)\b/i.test(source)
    || /(?:구조|상태|현황|진행\s*상황|어디까지|의미|내용|목록|경로|문제점|원인)(?:을|를|이|가|은|는|만|부터|에)?\s*(?:알려|보여|설명|요약|분석|확인|말해|찾아|점검)/.test(source)
    || /(?:구조|상태|현황|진행\s*상황|내용|목록|경로)[\s\S]{0,100}(?:봐|살펴)/.test(source)
    || /(?:알려|보여|설명|요약|분석|확인|말해)\s*(?:줘|주세요|봐|보자)?[.!?\s]*$/.test(source)
    || /(?:뭐|무엇|어떤|어디)(?:야|인가|인지|예요|입니까|냐)?[.!?\s]*$/.test(source)
  );
  return readOnlyIntent ? "READ_ONLY" : "AMBIGUOUS";
}

function isReadOnlyUserGoal(text, context = {}) {
  return classifyUserIntent(text, context) === "READ_ONLY";
}

function classifyUserTurnIntent(text, context = {}) {
  const value = String(text || "").trim();
  if (isContinuationUserMessage(value)) return "CONTINUE_ACTIVE_TASK";
  const activeObjective = String(context.activeObjective || "").trim();
  if (
    context.hasActiveTask === true
    && activeObjective
    && value !== activeObjective
    && isReadOnlyUserGoal(value, context)
  ) {
    return "SIDE_QUERY";
  }
  return "NEW_TASK";
}

function isMetaUserMessage(text) {
  const source = String(text || "");
  const lower = source.toLowerCase();
  // An upstream console/automation encoding failure can collapse a real prompt
  // into question-mark placeholders. Such text carries no recoverable intent
  // and must not replace an existing task objective or clear its blocker.
  if (/^(?:\?+\s*)+[.!?]*$/.test(source.trim())) return true;
  // LM Studio auto-names chats by injecting a synthetic user prompt mid-turn.
  // Treating it as a real goal wipe causes zero-tail compaction and tool-loop amnesia.
  if (/come up with a .{0,80}title for this conversation/i.test(source)) return true;
  if (/come up with a .{0,80}title\b/i.test(source) && /<title>/i.test(source)) return true;
  if (/put your answer in\s*<title>/i.test(source)) return true;
  if (/just return the title in the specified format/i.test(lower)) return true;
  if (/conversation naming technique/i.test(lower)) return true;
  if (/^\s*<title>[\s\S]*<\/title>\s*$/i.test(source)) return true;
  if (/\b2-5 word title\b/i.test(source) && /<\/title>/i.test(source)) return true;
  return false;
}

function requestIntentFromCheckpointSummary(text) {
  const source = String(text || "");
  const marker = "Conversation checkpoint (control state is authoritative; do not reinterpret it).";
  const markerIndex = source.lastIndexOf(marker);
  if (markerIndex < 0) return null;
  const checkpointSection = source.slice(markerIndex + marker.length);
  // Only the generator-owned final envelope is authoritative. Dynamic summary
  // fields (constraints, paths, diagnostics, and tool evidence) precede this
  // line and may contain arbitrary newlines. Scanning an earlier
  // `requestIntent=` line lets an untrusted tool smuggle a forged intent into a
  // later cold rebuild even though its live result was correctly rejected.
  const line = checkpointSection.split(/\r?\n/u)
    .filter((item) => item.startsWith("checkpointRequestIntent="))
    .at(-1);
  if (!line) return null;
  try {
    const value = JSON.parse(line.slice("checkpointRequestIntent=".length));
    return value && typeof value === "object" && !Array.isArray(value) ? value : null;
  } catch {
    return null;
  }
}

function findLatestRealUserIndex(snapshots) {
  for (let i = (snapshots || []).length - 1; i >= 0; i -= 1) {
    const message = snapshots[i];
    if (message.role !== "user") continue;
    const text = String(message.text || "").trim();
    if (!text || isMetaUserMessage(text)) continue;
    return i;
  }
  return -1;
}

function extractControlState(messages, prior = {}, options = {}) {
  const snapshots = snapshotMessages(messages || []);
  const priorCount = Number(prior.sourceMessageCount || 0);
  const priorHasActiveTaskRoute = Boolean(prior.toolRoute?.routeHash);
  const priorHasRouteOwnership = Boolean(compactTaskRouteOwnership(prior.taskRouteOwnership));
  const canResume = priorCount > 0
    && priorCount <= snapshots.length
    && prior.sourceHistoryHash === sha256(stableStringify(snapshots.slice(0, priorCount)))
    // Recover legacy checkpoints that persisted an encoding placeholder as the
    // objective by rescanning the bounded conversation and ignoring that row.
    && !isMetaUserMessage(prior.objective)
    // Revision 22 migration: old checkpoints discarded ownerCapability. When
    // an active task route is present, rescan the bounded conversation once so
    // compact route ownership can be recovered from an earlier tool result.
    && (!priorHasActiveTaskRoute || priorHasRouteOwnership)
    && Number(prior.schemaVersion || 0) === COMPACTION_SCHEMA_VERSION;
  const source = canResume ? snapshots.slice(priorCount) : snapshots;
  const resumedObjective = canResume ? String(prior.objective || "") : "";
  const persistedObjectiveHash = String(prior.objectiveHash || "").trim().toLowerCase();
  const resumedObjectiveHash = canResume
    ? (/^[a-f0-9]{64}$/.test(persistedObjectiveHash)
      ? persistedObjectiveHash
      : objectiveHashOf(resumedObjective))
    : "";
  const state = {
    schemaVersion: COMPACTION_SCHEMA_VERSION,
    objective: resumedObjective,
    objectiveHash: resumedObjectiveHash,
    requestIntent: canResume
      ? compactRequestIntent(prior.requestIntent, resumedObjective, resumedObjectiveHash)
      : null,
    constraints: canResume && Array.isArray(prior.constraints) ? [...prior.constraints] : [],
    activeProject: canResume ? (prior.activeProject || null) : null,
    activeProjectName: canResume ? (prior.activeProjectName || "") : "",
    touchedPaths: canResume && Array.isArray(prior.modifiedFiles) ? [...prior.modifiedFiles] : [],
    lastDiagnostics: canResume && Array.isArray(prior.diagnostics) ? [...prior.diagnostics] : [],
    exactSignatureContracts: canResume && Array.isArray(prior.exactSignatureContracts) ? [...prior.exactSignatureContracts] : [],
    requiredNextTool: canResume ? (prior.requiredNextTool?.name || null) : null,
    requiredNextToolRef: canResume ? (prior.requiredNextTool?.reference || null) : null,
    requiredNextToolArgs: canResume ? (prior.requiredNextTool?.args || null) : null,
    mutationGeneration: canResume ? Number(prior.mutationGeneration || 0) : 0,
    buildState: canResume ? { ...(prior.buildState || {}) } : {},
    selectedSlice: canResume ? (prior.selectedSlice || null) : null,
    sliceProgress: canResume ? (prior.sliceProgress || null) : null,
    buildVerification: canResume ? (prior.buildVerification || null) : null,
    toolRoute: canResume ? (prior.toolRoute || null) : null,
    taskRouteOwnership: canResume ? compactTaskRouteOwnership(prior.taskRouteOwnership) : null,
    invariants: canResume && Array.isArray(prior.invariants) ? [...prior.invariants] : [],
    coverageEvidence: canResume && Array.isArray(prior.coverageEvidence) ? [...prior.coverageEvidence] : [],
    architectureProposal: canResume && prior.architectureProposal
      ? { ...prior.architectureProposal }
      : null,
    protocolControl: canResume && prior.protocolControl
      ? { ...prior.protocolControl }
      : null,
    architectureControl: canResume && prior.architectureControl
      ? { ...prior.architectureControl }
      : null,
    serverControl: canResume && compactServerControl(prior.serverControl)
      ? compactServerControl(prior.serverControl)
      : null,
    sourceEvidence: canResume && compactEvidenceLedger(prior.sourceEvidence, false)
      ? compactEvidenceLedger(prior.sourceEvidence, false)
      : null,
    absentEvidence: canResume && compactEvidenceLedger(prior.absentEvidence, true)
      ? compactEvidenceLedger(prior.absentEvidence, true)
      : null,
    semanticBlocker: canResume && prior.semanticBlocker
      ? { ...prior.semanticBlocker }
      : null,
    sideQuery: canResume && prior.sideQuery?.active === true
      ? { ...prior.sideQuery }
      : null,
    failedToolResults: canResume && Array.isArray(prior.failedToolResults) ? [...prior.failedToolResults] : [],
    facts: canResume && Array.isArray(prior.facts) ? [...prior.facts] : [],
    evidenceFacts: canResume && Array.isArray(prior.evidenceFacts) ? [...prior.evidenceFacts] : [],
    editEvidence: canResume && Array.isArray(prior.editEvidence) ? [...prior.editEvidence] : [],
    repeatEvidence: canResume && Array.isArray(prior.repeatEvidence) ? [...prior.repeatEvidence] : [],
    invalidatedTaskSessionIds: new Set(
      canResume && Array.isArray(prior.invalidatedTaskSessionIds)
        ? prior.invalidatedTaskSessionIds.map((value) => String(value || "").trim()).filter(Boolean).slice(-16)
        : [],
    ),
  };
  const toolCallsById = new Map();
  const anonymousToolCalls = [];
  const trustedCheckpointRequestIntents = [];

  for (const snapshot of source) {
    if (snapshot.role === "system") {
      const checkpointRequestIntent = requestIntentFromCheckpointSummary(snapshot.text);
      if (checkpointRequestIntent) trustedCheckpointRequestIntents.push(checkpointRequestIntent);
    }
    if (snapshot.role === "user" && snapshot.text.trim()) {
      if (isMetaUserMessage(snapshot.text)) {
        continue;
      }
      // Latest real user message always wins — pinning the first turn causes goal drift.
      // Synthetic LM Studio title prompts must not replace the active goal.
      const userText = snapshot.text.trim();
      const userObjectiveHash = objectiveHashOf(userText);
      const turnIntent = classifyUserTurnIntent(userText, {
        hasActiveTask: Boolean(state.taskRouteOwnership && state.toolRoute?.routeHash),
        activeObjective: state.objective,
        requestIntent: state.requestIntent,
      });
      const continuation = Boolean(state.objective) && turnIntent === "CONTINUE_ACTIVE_TASK";
      if (turnIntent === "SIDE_QUERY") {
        state.sideQuery = {
          active: true,
          request: userText.slice(0, 1200),
          taskSessionId: String(state.taskRouteOwnership?.taskSessionId || ""),
          activeObjective: String(state.objective || "").slice(0, 1200),
        };
        continue;
      }
      const objectiveChanged = Boolean(
        state.objective
        && (
          /^[a-f0-9]{64}$/.test(String(state.objectiveHash || ""))
            ? userObjectiveHash !== state.objectiveHash
            : userText !== state.objective
        )
        && !continuation,
      );
      if (objectiveChanged) {
        resetTaskScopedControl(state);
      }
      if (
        state.semanticBlocker?.active
        && objectiveChanged
      ) {
        state.semanticBlocker = null;
      }
      // A continuation utterance advances the existing task; it is not a new
      // objective. Replacing the objective here loses intent after compaction
      // and can silently turn an implementation task into a generic "continue".
      if (continuation) {
        state.sideQuery = null;
        continue;
      }
      state.sideQuery = null;
      state.objective = userText.slice(0, 1200);
      state.objectiveHash = userObjectiveHash;
      state.constraints = state.constraints.filter((item) =>
        typeof item === "string" && !item.startsWith("active_goal:") && !item.startsWith("read_only_"));
      state.constraints.push(`active_goal:${userText.slice(0, 400)}`);
      if (isReadOnlyUserGoal(userText)) {
        state.constraints.push(
          "read_only_findings_only: do not edit files; do not invent refactor/implementation plans; "
          + "do not re-emit a prior project-structure overview unless the latest user asked for it",
        );
      }
    }
    for (const payload of parseJsonObjects(snapshot.text)) {
      collectControlFields(payload, state);
    }
    for (const call of snapshot.toolCalls) {
      state.facts.push(`tool:${call.name}`);
      if (call.id) toolCallsById.set(call.id, call);
      else anonymousToolCalls.push(call);
    }
    for (const result of snapshot.toolResults) {
      const observedCall = result.toolCallId
        ? (toolCallsById.get(result.toolCallId) || null)
        : (anonymousToolCalls.shift() || null);
      const matchedCall = observedCall || { name: result.name, arguments: {} };
      const matchedCallName = matchedCall.name || result.name;
      const normalizedCallName = String(matchedCallName || "").toLowerCase();
      if (
        normalizedCallName.endsWith("unreal_architecture_reasoning")
        && (matchedCall.arguments?.proposalPatch || matchedCall.arguments?.proposalRepairs)
      ) {
        const patch = matchedCall.arguments.proposalPatch || matchedCall.arguments.proposalRepairs;
        const patchDigest = sha256(stableStringify(patch));
        const previousDigest = state.architectureProposal?.lastPatchDigest || "";
        const repairPaths = Array.isArray(matchedCall.arguments?.proposalRepairs)
          ? matchedCall.arguments.proposalRepairs.map((row) => String(row?.jsonPath || "")).filter(Boolean)
          : [];
        state.architectureProposal = {
          ...(state.architectureProposal || {}),
          lastPatchDigest: patchDigest,
          lastPatchFields: repairPaths.length ? repairPaths.slice(0, 24) : Object.keys(patch).slice(0, 20),
          lastPatchPreview: repairPaths.length
            ? matchedCall.arguments.proposalRepairs.slice(0, 24).map((row) => ({
              jsonPath: String(row?.jsonPath || "").slice(0, 160),
              value: String(stableStringify(row?.value)).slice(0, 500),
            }))
            : boundedArchitecturePatchPreview(patch),
          unchangedPatchAttempts: previousDigest === patchDigest
            ? Number(state.architectureProposal?.unchangedPatchAttempts || 0) + 1
            : 0,
        };
      }
      const resultPayloads = parseJsonObjects(result.content);
      for (const payload of resultPayloads) {
        const resultNameMatchesCall = Boolean(
          observedCall
          && (!result.name || toolNamesMatch(observedCall.name, result.name)),
        );
        collectControlFields(payload, state, {
          requestIntentTrusted: isTrustedRequestIntentResult(
            matchedCallName,
            result.name,
            payload,
            resultNameMatchesCall,
          ),
        });
        collectSemanticBlockerFields(payload, state, matchedCallName);
      }
      if (toolResultSucceeded(result)) {
        const evidence = compactToolEvidence(matchedCall, resultPayloads.slice(-1)[0] || {}, result.content);
        if (evidence) state.evidenceFacts.push(evidence);
        const editEvidence = compactEditEvidence(
          matchedCall,
          resultPayloads.slice(-1)[0] || {},
          result.content,
          state.selectedSlice,
        );
        if (editEvidence && resultPayloads.slice(-1)[0]?.repeatDetected !== true) {
          state.editEvidence = [
            ...state.editEvidence.filter((item) => (
              normalizeProjectEvidencePath(normalizedProjectPath(item?.path))
              !== normalizeProjectEvidencePath(editEvidence.path)
            )),
            editEvidence,
          ].slice(-MAX_EDIT_EVIDENCE_FILES);
        }
        const repeatEvidence = compactRepeatEvidence(
          matchedCall,
          resultPayloads.slice(-1)[0] || {},
        );
        if (repeatEvidence) {
          state.repeatEvidence = [repeatEvidence].slice(-MAX_REPEAT_EVIDENCE_FILES);
        }
      }
      if (!toolResultSucceeded(result)) {
        const failurePayload = parseJsonObjects(result.content).slice(-1)[0] || {};
        state.failedToolResults.push({
          tool: String(matchedCallName || result.name || ""),
          errorCode: String(failurePayload.errorCode || ""),
          detail: String(
            failurePayload.error
            || failurePayload.userMessage
            || "tool result marked failed"
          ).slice(0, 400),
        });
      }
      if (toolResultSucceeded(result) && mutationToolName(matchedCallName)) {
        const reportedMutationGeneration = resultPayloads
          .map((payload) => Number(payload?.mutationGeneration))
          .filter((value) => Number.isFinite(value) && value >= 0)
          .at(-1);
        if (reportedMutationGeneration === undefined) {
          state.mutationGeneration += 1;
        } else {
          state.mutationGeneration = Math.max(state.mutationGeneration, reportedMutationGeneration);
        }
        const mutationArgs = matchedCall.arguments && typeof matchedCall.arguments === "object"
          ? matchedCall.arguments
          : {};
        const changedPaths = [
          mutationArgs.path,
          ...(Array.isArray(mutationArgs.files) ? mutationArgs.files.map((item) => item?.path) : []),
          ...(Array.isArray(mutationArgs.patches) ? mutationArgs.patches.map((item) => item?.path) : []),
        ].map((item) => normalizeProjectEvidencePath(item))
          .filter(Boolean);
        if (changedPaths.length) {
          const changed = new Set(changedPaths);
          state.evidenceFacts = state.evidenceFacts.filter((fact) => {
            const factPath = normalizeProjectEvidencePath(fact?.path);
            return !changed.has(factPath);
          });
          if (state.sourceEvidence?.files) {
            state.sourceEvidence.files = Object.fromEntries(
              Object.entries(state.sourceEvidence.files).filter(([key, entry]) => {
                const entryPath = normalizeProjectEvidencePath(entry?.path || key);
                return !changed.has(entryPath);
              }),
            );
          }
        }
        // Evidence-level stop/do-not-retry controls remain authoritative until
        // the user changes the goal or a successful mutation changes the source
        // snapshot that made the evidence stale.
        state.semanticBlocker = null;
        // Exact pre-mutation text is stale as soon as a write succeeds. A
        // post-mutation validation read may repopulate this bounded cache.
        state.editEvidence = [];
        state.repeatEvidence = [];
      }
      if (
        toolResultSucceeded(result)
        && semanticBlockerClearToolSucceeded(
          state.semanticBlocker,
          matchedCallName,
          matchedCall,
          resultPayloads.slice(-1)[0] || {},
        )
      ) {
        state.semanticBlocker = null;
      }
      // A generated call is only intent. Keep the required tool gate until the
      // paired result is observed and is explicitly non-failing.
      if (
        !state.serverControl
        &&
        state.requiredNextTool
        && toolNamesMatch(state.requiredNextTool, matchedCallName)
        && toolResultSucceeded(result)
        && toolArgumentsSatisfy(state.requiredNextToolArgs, matchedCall.arguments)
      ) {
        state.requiredNextTool = null;
        state.requiredNextToolRef = null;
        state.requiredNextToolArgs = null;
      }
    }
  }

  if (!state.requestIntent) {
    for (let index = trustedCheckpointRequestIntents.length - 1; index >= 0; index -= 1) {
      const requestIntent = compactRequestIntent(
        trustedCheckpointRequestIntents[index],
        state.objective,
        state.objectiveHash,
      );
      if (requestIntent) {
        state.requestIntent = requestIntent;
        break;
      }
    }
  }
  state.requestIntent = compactRequestIntent(
    state.requestIntent,
    state.objective,
    state.objectiveHash,
  );
  if (state.requestIntent) {
    state.constraints = state.constraints.filter((item) => (
      typeof item === "string" && !item.startsWith("read_only_")
    ));
    if (state.requestIntent.mutability === "none") {
      state.constraints.push(
        "read_only_findings_only: do not edit files; do not invent refactor/implementation plans; "
        + "do not re-emit a prior project-structure overview unless the latest user asked for it",
      );
    }
  }

  const cap = Number(options.maxCheckpointFacts || DEFAULT_COMPACTION_CONFIG.maxCheckpointFacts);
  state.touchedPaths = [...new Set(state.touchedPaths)].slice(-cap);
  state.lastDiagnostics = [...new Set(state.lastDiagnostics)].slice(-cap);
  state.constraints = [...new Set(state.constraints)].slice(-cap);
  state.exactSignatureContracts = [...new Map(
    state.exactSignatureContracts.map((contract) => [stableStringify(contract), contract]),
  ).values()].slice(-cap);
  state.facts = [...new Set(state.facts)].slice(-cap);
  state.invariants = [...new Set(state.invariants)].slice(-cap);
  state.coverageEvidence = state.coverageEvidence.slice(-cap);
  state.failedToolResults = state.failedToolResults.slice(-cap);
  const evidenceByKey = new Map();
  for (const fact of state.evidenceFacts) {
      const tool = String(fact?.tool || "").toLowerCase();
      let key = stableStringify(fact);
      if (tool.endsWith("read_file") || tool.endsWith("read_file_range")) {
        key = `read:${normalizeProjectEvidencePath(fact.path)}:${fact.contentHash || fact.evidenceHash || "unknown"}`;
      }
      else if (tool.endsWith("list_directory")) key = `list:${normalizeProjectEvidencePath(fact.path)}`;
      else if (tool.endsWith("search_files")) key = `search:${normalizeProjectEvidencePath(fact.path)}:${fact.query}`;
      else if (tool.endsWith("get_active_project") || tool === "get_workspace_info") key = `project:${tool}`;
      const priorFact = evidenceByKey.get(key);
      if (priorFact && (tool.endsWith("read_file") || tool.endsWith("read_file_range"))) {
        const merged = { ...priorFact, ...fact };
        if (!fact.evidenceHash) merged.evidenceHash = priorFact.evidenceHash;
        if (!fact.lineCount) merged.lineCount = priorFact.lineCount;
        if (!Array.isArray(fact.semanticAnchors) || fact.semanticAnchors.length === 0) {
          merged.semanticAnchors = priorFact.semanticAnchors || [];
        }
        const ranges = [
          ...(Array.isArray(priorFact.coveredRanges) ? priorFact.coveredRanges : []),
          ...(Array.isArray(fact.coveredRanges) ? fact.coveredRanges : []),
        ].filter((range) => Array.isArray(range) && range.length >= 2)
          .map((range) => [Number(range[0]), Number(range[1])])
          .filter((range) => Number.isInteger(range[0]) && Number.isInteger(range[1]) && range[0] > 0 && range[1] >= range[0])
          .sort((left, right) => left[0] - right[0] || left[1] - right[1]);
        merged.coveredRanges = [];
        for (const range of ranges) {
          const priorRange = merged.coveredRanges.at(-1);
          if (!priorRange || range[0] > priorRange[1] + 1) {
            merged.coveredRanges.push([...range]);
          } else {
            priorRange[1] = Math.max(priorRange[1], range[1]);
          }
        }
        if (!fact.exactContent) merged.exactContent = priorFact.exactContent || "";
        merged.exactContentTruncated = Boolean(
          fact.exactContent ? fact.exactContentTruncated : priorFact.exactContentTruncated
        );
        evidenceByKey.set(key, merged);
      } else if (priorFact && tool.endsWith("search_files")) {
        const merged = { ...priorFact, ...fact };
        const repeatedWithoutFreshRows = fact.cached === true || fact.repeatDetected === true;
        if (repeatedWithoutFreshRows) {
          merged.resultCount = Math.max(Number(priorFact.resultCount || 0), Number(fact.resultCount || 0));
          merged.fileNameResultCount = Math.max(
            Number(priorFact.fileNameResultCount || 0),
            Number(fact.fileNameResultCount || 0),
          );
          merged.searchComplete = priorFact.searchComplete === true || fact.searchComplete === true;
          merged.matchedFiles = [...new Set([
            ...(Array.isArray(priorFact.matchedFiles) ? priorFact.matchedFiles : []),
            ...(Array.isArray(fact.matchedFiles) ? fact.matchedFiles : []),
          ])].slice(0, 12);
        }
        evidenceByKey.set(key, merged);
      } else {
        evidenceByKey.set(key, fact);
      }
  }
  state.evidenceFacts = [...evidenceByKey.values()].slice(-cap);
  const selectedFiles = new Set(selectedSliceFiles(state.selectedSlice));
  state.editEvidence = state.editEvidence
    .filter((item) => {
      const itemIdentity = normalizeProjectEvidencePath(normalizedProjectPath(item?.path));
      return [...selectedFiles].some(
        (selected) => normalizeProjectEvidencePath(selected) === itemIdentity,
      );
    })
    .slice(-MAX_EDIT_EVIDENCE_FILES);
  return state;
}

function buildWorkingSet(control) {
  const selectedSlice = control.selectedSlice && typeof control.selectedSlice === "object"
    ? control.selectedSlice
    : (control.toolRoute?.selectedSlice && typeof control.toolRoute.selectedSlice === "object"
      ? control.toolRoute.selectedSlice
      : null);
  const rawSelected = selectedSlice
    ? [
      ...(Array.isArray(selectedSlice.files) ? selectedSlice.files : []),
      ...(Array.isArray(selectedSlice.targetFiles) ? selectedSlice.targetFiles : []),
      ...(Array.isArray(selectedSlice.paths) ? selectedSlice.paths : []),
    ]
    : [];
  const selected = new Set(rawSelected.map((item) => (
    normalizeProjectEvidencePath(item?.path || item)
  )).filter(Boolean));
  const reads = (Array.isArray(control.evidenceFacts) ? control.evidenceFacts : [])
    .filter((fact) => {
      const tool = String(fact?.tool || "").toLowerCase();
      const factPath = normalizeProjectEvidencePath(fact?.path);
      return (tool.endsWith("read_file") || tool.endsWith("read_file_range"))
        && factPath
        && typeof fact.exactContent === "string"
        && fact.exactContent.length > 0
        && (selected.size === 0 || selected.has(factPath));
    });
  const byPath = new Map();
  for (const fact of reads) {
    const pathKey = normalizeProjectEvidencePath(fact.path);
    const ledgerEntry = control.sourceEvidence?.files?.[pathKey];
    if (
      ledgerEntry?.contentHash
      && fact.contentHash
      && String(ledgerEntry.contentHash) !== String(fact.contentHash)
    ) continue;
    byPath.set(pathKey, {
      path: String(fact.path || "").replace(/\\/g, "/"),
      contentHash: String(fact.contentHash || fact.evidenceHash || ""),
      coveredRanges: Array.isArray(fact.coveredRanges) ? fact.coveredRanges.slice(0, 16) : [],
      content: String(fact.exactContent || "").slice(0, 12_000),
      truncated: fact.exactContentTruncated === true,
    });
  }
  return [...byPath.values()].slice(-2);
}

function buildCheckpoint(messages, prior = {}, options = {}) {
  const control = extractControlState(messages, prior, options);
  const snapshots = snapshotMessages(messages || []);
  const generation = Number(prior.checkpointGeneration || 0) + 1;
  const activeServerControl = compactServerControl(control.serverControl);
  const semanticForbiddenTools = Array.isArray(control.semanticBlocker?.forbiddenTools)
    ? control.semanticBlocker.forbiddenTools
    : [];
  const controlBlockerConflict = Boolean(
    activeServerControl
    && control.semanticBlocker?.active
    && (
      (
        control.semanticBlocker.stopCurrentWorkflow === true
        && !String(control.semanticBlocker.clearOnTool || "").trim()
      )
      || activeServerControl.allowedTools.some((name) => (
        semanticForbiddenTools.some((forbidden) => toolNamesMatch(forbidden, name))
      ))
      || (
        activeServerControl.requiredTool
        && semanticForbiddenTools.some((forbidden) => (
          toolNamesMatch(forbidden, activeServerControl.requiredTool.name)
        ))
      )
    )
  );
  if (controlBlockerConflict) {
    // Do not guess whether a stale v2 route or an evidence/recovery blocker is
    // newer.  Their intersection can otherwise expose a tool that the same
    // checkpoint expressly prohibits.  A new user objective or a fresh server
    // control envelope is required to resume the task.
    rememberInvalidatedTaskSession(control, activeServerControl.taskSessionId);
    control.serverControl = null;
    control.protocolControl = null;
    control.architectureControl = null;
    control.taskRouteTerminal = true;
    control.toolRoute = null;
    control.taskRouteOwnership = null;
    control.requiredNextTool = null;
    control.requiredNextToolRef = null;
    control.requiredNextToolArgs = null;
    control.semanticBlocker = {
      active: true,
      scope: "workflow",
      errorCode: "CONTROL_BLOCKER_CONFLICT",
      blockerFingerprint: String(
        control.semanticBlocker?.blockerFingerprint || activeServerControl.blocker?.fingerprint || "",
      ).slice(0, 160),
      stopCurrentWorkflow: true,
      stopCurrentPhase: true,
      phaseBoundary: "control",
      forbiddenTools: [...new Set(semanticForbiddenTools)].slice(-32),
      clearOnTool: "",
      clearOnToolArgs: null,
      agentInstruction: "Conflicting stale task controls were discarded. Do not call a tool for this task; wait for a new user objective.",
    };
    control.lastDiagnostics.push("controlBlockerConflict=fail_closed");
  }
  if (
    control.semanticBlocker?.active
    && control.requiredNextTool
    && (
      (
        control.semanticBlocker.stopCurrentWorkflow === true
        && !String(control.semanticBlocker.clearOnTool || "").trim()
      )
      || control.semanticBlocker.forbiddenTools.some((name) => toolNamesMatch(name, control.requiredNextTool))
    )
  ) {
    control.requiredNextTool = null;
    control.requiredNextToolRef = null;
    control.requiredNextToolArgs = null;
  }
  return {
    schemaVersion: COMPACTION_SCHEMA_VERSION,
    checkpointGeneration: generation,
    createdAt: new Date().toISOString(),
    objective: control.objective,
    objectiveHash: control.objectiveHash,
    requestIntent: control.requestIntent,
    constraints: control.constraints,
    activeProject: control.activeProject,
    activeProjectName: control.activeProjectName,
    modifiedFiles: control.touchedPaths,
    mutationGeneration: control.mutationGeneration,
    buildState: control.buildState,
    selectedSlice: control.selectedSlice,
    sliceProgress: control.sliceProgress,
    buildVerification: control.buildVerification,
    toolRoute: control.toolRoute,
    taskRouteOwnership: control.taskRouteOwnership,
    invariants: control.invariants,
    coverageEvidence: control.coverageEvidence,
    architectureProposal: control.architectureProposal,
    protocolControl: control.protocolControl,
    architectureControl: control.architectureControl,
    serverControl: control.serverControl,
    sourceEvidence: control.sourceEvidence,
    absentEvidence: control.absentEvidence,
    workingSet: buildWorkingSet(control),
    semanticBlocker: control.semanticBlocker,
    sideQuery: control.sideQuery,
    failedToolResults: control.failedToolResults,
    requiredNextTool: control.requiredNextTool ? {
      name: control.requiredNextTool,
      reference: control.requiredNextToolRef,
      args: control.requiredNextToolArgs,
    } : null,
    exactSignatureContracts: control.exactSignatureContracts,
    diagnostics: control.lastDiagnostics,
    facts: control.facts,
    evidenceFacts: control.evidenceFacts,
    editEvidence: control.editEvidence,
    repeatEvidence: control.repeatEvidence,
    invalidatedTaskSessionIds: [...control.invalidatedTaskSessionIds].slice(-16),
    pendingToolCall: prior.pendingToolCall || null,
    pendingToolCalls: Array.isArray(prior.pendingToolCalls) ? [...prior.pendingToolCalls] : [],
    completedToolCallIds: Array.isArray(prior.completedToolCallIds) ? [...prior.completedToolCallIds].slice(-256) : [],
    // RAG and Agent publish tool catalogs independently. Preserve the one-shot
    // catalog refresh across its tool-result turn so a stale client catalog
    // cannot send the model into an unbounded health/read recovery loop.
    catalogRefresh: prior.catalogRefresh && typeof prior.catalogRefresh === "object"
      && !Array.isArray(prior.catalogRefresh)
      ? { ...prior.catalogRefresh }
      : null,
    compactionGeneration: Number(prior.compactionGeneration || 0),
    sourceMessageCount: snapshots.length,
    sourceHistoryHash: sha256(stableStringify(snapshots)),
    lastCompactionSourceMessageCount: Number(prior.lastCompactionSourceMessageCount || 0),
  };
}

const SESSION_MARKER_RE = /<!--\s*ucc-session:([a-f0-9]{16,64})\s*-->/i;

function extractSessionMarker(messages) {
  for (const snapshot of snapshotMessages(messages || [])) {
    const match = String(snapshot.text || "").match(SESSION_MARKER_RE);
    if (match) return String(match[1] || "").toLowerCase();
  }
  return null;
}

function formatSessionMarker(sessionId) {
  const id = String(sessionId || "").replace(/[^a-f0-9]/gi, "").toLowerCase().slice(0, 32);
  if (id.length < 16) return "";
  return `<!-- ucc-session:${id} -->`;
}

function messageLineageFingerprints(messages) {
  return snapshotMessages(messages || []).map((message) => {
    const toolIds = (message.toolCalls || [])
      .map((call) => String(call.id || ""))
      .filter(Boolean)
      .join(",");
    return sha256(`${message.role}:${String(message.text || "").slice(0, 500)}:${toolIds}`).slice(0, 16);
  });
}

function lineageContinues(previous, current) {
  if (!Array.isArray(previous) || !Array.isArray(current)) return false;
  if (previous.length === 0) return current.length >= 0;
  if (previous.length > current.length) return false;
  return previous.every((hash, index) => hash === current[index]);
}

function baseSessionKey(messages, salt = "") {
  const snapshots = snapshotMessages(messages || []);
  const firstSystem = snapshots.find((message) => message.role === "system");
  const firstUser = snapshots.find(
    (message) => message.role === "user" && String(message.text || "").trim() && !isMetaUserMessage(message.text),
  );
  const seed = [firstSystem, firstUser]
    .filter(Boolean)
    .map((message) => `${message.role}:${message.text}`)
    .join("\n");
  return sha256(`${salt}\n${seed || "empty-session"}`).slice(0, 32);
}

function sessionFingerprint(messages, salt = "", options = {}) {
  const marker = String(
    options.sessionMarker
    || options.explicitSessionId
    || extractSessionMarker(messages)
    || "",
  ).trim().toLowerCase();
  if (marker) {
    // A UCC marker is already a minted session identity. Re-hashing it turns
    // marker A into session B on the next generation and breaks continuity.
    return marker.replace(/[^a-f0-9]/g, "").slice(0, 32);
  }
  return baseSessionKey(messages, salt);
}

function lmStudioConversationSessionFingerprint(workingDirectory, modelIdentifier = "") {
  const raw = String(workingDirectory || "").trim();
  if (!raw) return "";
  const normalized = raw.replace(/\\/g, "/").replace(/\/+$/, "");
  const match = normalized.match(/(?:^|\/)working-directories\/([^/]+)$/i);
  if (!match || !String(match[1] || "").trim()) return "";
  // LM Studio assigns this directory per conversation. It remains stable while
  // assistant/tool messages grow or are cancelled, unlike message lineage.
  // Include the model so switching generator targets cannot inherit an
  // incompatible checkpoint. Normalize Windows drive/path casing only.
  const pathIdentity = /^[A-Za-z]:\//.test(normalized)
    ? asciiWindowsFold(normalized)
    : normalized;
  return sha256(`lmstudio-conversation\n${pathIdentity}\n${String(modelIdentifier || "")}`).slice(0, 32);
}

function isMajorGoalChange(priorObjective, latestObjective) {
  const prior = String(priorObjective || "").trim();
  const latest = String(latestObjective || "").trim();
  if (!prior || !latest || prior === latest) return false;
  if (isMetaUserMessage(latest)) return false;
  const priorReadOnly = isReadOnlyUserGoal(prior);
  const latestReadOnly = isReadOnlyUserGoal(latest);
  if (priorReadOnly !== latestReadOnly) return true;
  const goalBucket = (text) => {
    if (isReadOnlyUserGoal(text)) {
      if (/\b(?:bug|error|failure|diagnos|root cause)\b|버그|오류|에러|문제점|원인/i.test(text)) {
        return "readonly_diagnostic";
      }
      if (/\b(?:structure|architecture|layout|tree)\b|구조|아키텍처|폴더|목록/i.test(text)) {
        return "readonly_structure";
      }
      return "readonly";
    }
    if (classifyMutationIntent(text).isMutation) {
      return "write";
    }
    if (/\b(analyze|review|find|structure|구조|분석|버그|조사|찾아)\b/i.test(text)) {
      return "inspect";
    }
    return "other";
  };
  const priorBucket = goalBucket(prior);
  const latestBucket = goalBucket(latest);
  return Boolean(
    priorBucket !== "other"
    && latestBucket !== "other"
    && priorBucket !== latestBucket,
  );
}

function parseProviderQualifiedToolName(value) {
  const raw = String(value || "").trim().toLowerCase();
  if (!raw) return { qualified: false, functionName: "" };
  const normalized = raw.replace(/\\/g, "/");
  const slashParts = normalized.split(/[/:]/u).filter(Boolean);
  if (slashParts.length >= 3 && slashParts[0] === "mcp") {
    return {
      qualified: true,
      functionName: slashParts.at(-1).replace(/[^a-z0-9_-]/g, ""),
    };
  }
  const doubleParts = normalized.split("__").filter(Boolean);
  if (doubleParts.length >= 3 && doubleParts[0] === "mcp") {
    return {
      qualified: true,
      functionName: doubleParts.at(-1).replace(/[^a-z0-9_-]/g, ""),
    };
  }
  if (normalized.startsWith("mcp_")) {
    const knownProviderPrefix = ["mcp_unreal_agent_", "mcp_unreal_rag_"]
      .find((prefix) => normalized.startsWith(prefix));
    return {
      qualified: true,
      functionName: normalized.slice((knownProviderPrefix || "mcp_").length),
    };
  }
  return {
    qualified: false,
    functionName: normalized.replace(/[^a-z0-9_-]/g, "_"),
  };
}

function toolNamesMatch(expected, actual) {
  const left = parseProviderQualifiedToolName(expected);
  const right = parseProviderQualifiedToolName(actual);
  if (!left.functionName || !right.functionName) return false;
  return left.functionName === right.functionName;
}

function expectedToolReserve(toolName, config = {}) {
  const normalized = String(toolName || "").toLowerCase();
  if (normalized.includes("build") || normalized.includes("compile")) {
    return Number(config.buildToolResultReserve || DEFAULT_COMPACTION_CONFIG.buildToolResultReserve);
  }
  return Number(config.normalToolResultReserve || DEFAULT_COMPACTION_CONFIG.normalToolResultReserve);
}

function budgetDecision({ contextLength, inputTokens, nextToolName, config = {}, toolSchemaTokens = 0 }) {
  const merged = { ...DEFAULT_COMPACTION_CONFIG, ...config };
  const reserve = Number(merged.maxOutputReserve)
    + Number(merged.safetyMarginTokens || 0)
    + Number(toolSchemaTokens || 0)
    + expectedToolReserve(nextToolName, merged);
  const remaining = Number(contextLength) - Number(inputTokens) - reserve;
  let action = "normal";
  if (remaining < merged.hardRemainingTokens) action = "hard_compact";
  else if (remaining < merged.softRemainingTokens) action = "soft_compact";
  return {
    action,
    contextLength: Number(contextLength),
    inputTokens: Number(inputTokens),
    reservedTokens: reserve,
    remainingTokens: remaining,
    thresholds: {
      soft: merged.softRemainingTokens,
      hard: merged.hardRemainingTokens,
    },
  };
}

function isCompleteToolPair(messages) {
  const pending = new Set();
  const known = new Set();
  const completed = new Set();
  let anonymousPending = 0;
  for (const message of messages || []) {
    for (const call of messageSnapshot(message).toolCalls) {
      if (call.id) {
        known.add(call.id);
        pending.add(call.id);
      } else anonymousPending += 1;
    }
    for (const result of messageSnapshot(message).toolResults) {
      if (result.toolCallId && !known.has(result.toolCallId)) return false;
      if (result.toolCallId) {
        if (completed.has(result.toolCallId)) return false;
        completed.add(result.toolCallId);
        pending.delete(result.toolCallId);
      }
      else {
        anonymousPending -= 1;
        if (anonymousPending < 0) return false;
      }
    }
    // Tool results are validated in the loop above.
  }
  return pending.size === 0 && anonymousPending === 0;
}

function completeTailStart(snapshots, startIndex) {
  let start = Math.max(0, Number(startIndex || 0));
  while (start > 0) {
    const tail = snapshots.slice(start);
    const callIds = new Set();
    let anonymousBalance = 0;
    let orphanResult = false;
    for (const message of tail) {
      for (const call of message.toolCalls || []) {
        if (call.id) callIds.add(call.id);
        else anonymousBalance += 1;
      }
      for (const result of message.toolResults || []) {
        if (result.toolCallId && !callIds.has(result.toolCallId)) orphanResult = true;
        if (!result.toolCallId) {
          anonymousBalance -= 1;
          if (anonymousBalance < 0) orphanResult = true;
        }
      }
    }
    if (!orphanResult) return start;
    start -= 1;
  }
  return 0;
}
function summarizeOldMessages(messages, checkpoint) {
  const lines = [
    "Conversation checkpoint (control state is authoritative; do not reinterpret it).",
    `checkpointGeneration=${checkpoint.checkpointGeneration}`,
    `objective=${checkpoint.objective || "(not captured)"}`,
  ];
  if (checkpoint.objectiveHash) lines.push(`objectiveHash=${checkpoint.objectiveHash}`);
  if (checkpoint.requestIntent) lines.push(`requestIntent=${JSON.stringify(checkpoint.requestIntent)}`);
  if (checkpoint.modifiedFiles?.length) lines.push(`modifiedFiles=${checkpoint.modifiedFiles.join(", ")}`);
  if (checkpoint.constraints?.length) lines.push(`constraints=${checkpoint.constraints.join(" | ")}`);
  if (checkpoint.activeProject) lines.push(`activeProject=${checkpoint.activeProject}`);
  if (checkpoint.activeProjectName) lines.push(`activeProjectName=${checkpoint.activeProjectName}`);
  lines.push(`mutationGeneration=${Number(checkpoint.mutationGeneration || 0)}`);
  if (checkpoint.buildState && Object.keys(checkpoint.buildState).length) {
    lines.push(`buildState=${JSON.stringify(checkpoint.buildState)}`);
  }
  if (checkpoint.selectedSlice) lines.push(`selectedSlice=${JSON.stringify(checkpoint.selectedSlice)}`);
  if (checkpoint.sliceProgress) lines.push(`sliceProgress=${JSON.stringify(checkpoint.sliceProgress)}`);
  if (checkpoint.buildVerification) lines.push(`buildVerification=${JSON.stringify(checkpoint.buildVerification)}`);
  if (checkpoint.toolRoute) lines.push(`toolRoute=${JSON.stringify(checkpoint.toolRoute)}`);
  if (checkpoint.taskRouteOwnership) {
    lines.push(`taskAuthorization=${JSON.stringify(checkpoint.taskRouteOwnership)}`);
    lines.push(
      "routeOwnershipInstruction=Use the compact taskAuthorization above for active routed tools. "
      + "Do not recover, cancel, or replace the healthy task merely because authToken is omitted.",
    );
  }
  if (checkpoint.invariants?.length) lines.push(`invariants=${checkpoint.invariants.join(" | ")}`);
  if (checkpoint.coverageEvidence?.length) {
    lines.push(`coverageEvidence=${JSON.stringify(checkpoint.coverageEvidence)}`);
  }
  if (checkpoint.architectureProposal) {
    lines.push(`architectureProposalContinuation=${JSON.stringify(checkpoint.architectureProposal)}`);
    if (
      checkpoint.architectureProposal.requiresFullReplan
      || checkpoint.architectureProposal.repairStrategy === "full_replan"
      || checkpoint.architectureProposal.repairMode === "fullProposal"
    ) {
      lines.push(
        "architectureProposalInstruction=The retained proposal has a core ownership/state/lifecycle contradiction. "
        + "Reuse retained direct-source evidence while sourceSnapshotFingerprint is unchanged. Re-read only when "
        + "source changed, required evidence is missing, or needed lines were not covered. Submit one complete "
        + "independently derived proposal. Do not use proposalPatch/proposalRepairs, do not reuse lastPatchPreview, "
        + "and do not preserve the rejected central owner.",
      );
    } else {
      lines.push(
        "architectureProposalInstruction=Use the exact proposal revision above. Resolve each retained repair "
        + "requirement by changing the corresponding values. Compare against lastPatchPreview and never resubmit "
        + "the same patch digest; when repairMode is proposalRepairs, call unreal_architecture_reasoning with "
        + "baseProposalRevision plus one {jsonPath,value} entry per requiredRepairPaths item. Keep each path exact, "
        + "fill values from your own design, and do not regenerate or resend the prior proposalPatch. For an array "
        + "path, send one complete replacement array rather than repeating that jsonPath per item.",
      );
    }
  }
  if (checkpoint.protocolControl) {
    lines.push(`protocolControl=${JSON.stringify(checkpoint.protocolControl)}`);
  }
  if (checkpoint.serverControl) {
    lines.push(`serverControl=${JSON.stringify(checkpoint.serverControl)}`);
  }
  if (checkpoint.architectureControl) {
    lines.push(`architectureControl=${JSON.stringify(checkpoint.architectureControl)}`);
  }
  if (checkpoint.semanticBlocker?.active) {
    lines.push(`semanticBlocker=${JSON.stringify(checkpoint.semanticBlocker)}`);
    lines.push(
      "semanticBlockerInstruction=This server-owned blocker survives compaction. Do not call any forbiddenTools. "
      + "If scope=evidence_phase, only discovery is closed: continue from retained evidence with an allowed "
      + "write/validation/final action. If scope=until_required_tool_success, call clearOnTool once. "
      + "Never retry a forbidden tool merely because older tool results were compacted.",
    );
  }
  if (checkpoint.sideQuery?.active) {
    lines.push(`detachedSideQuery=${JSON.stringify(checkpoint.sideQuery)}`);
    lines.push(
      "detachedSideQueryInstruction=Answer only this read-only observation request. Do not advance, "
      + "replan, checkpoint, cancel, validate, or mutate the suspended task. A later continuation "
      + "returns to the retained task objective and server route.",
    );
  }
  if (checkpoint.failedToolResults?.length) {
    lines.push(`failedToolResults=${JSON.stringify(checkpoint.failedToolResults)}`);
  }
  if (checkpoint.diagnostics?.length) lines.push(`diagnostics=${checkpoint.diagnostics.join(" | ")}`);
  if (checkpoint.requiredNextTool?.name) {
    lines.push(`requiredNextTool=${checkpoint.requiredNextTool.name}`);
    lines.push(`requiredNextToolArgs=${JSON.stringify(checkpoint.requiredNextTool.args || {})}`);
  }
  if (checkpoint.exactSignatureContracts?.length) {
    lines.push(`exactSignatureContracts=${JSON.stringify(checkpoint.exactSignatureContracts)}`);
  }
  if (checkpoint.facts?.length) lines.push(`facts=${checkpoint.facts.join(" | ")}`);
  if (checkpoint.evidenceFacts?.length) {
    const readPaths = checkpoint.evidenceFacts
      .filter((fact) => /read_file(?:_range)?$/i.test(String(fact?.tool || "")) && fact?.path)
      .map((fact) => fact.path);
    if (readPaths.length) {
      lines.push(
        `discoveryLedger=already-read unchanged files (${readPaths.length}): ${readPaths.join(", ")}. `
        + "Do not re-read these paths merely to remember them; use their semanticAnchors below. "
        + "Read again only after a mutation, when a required edit needs an exact range absent from the anchors, "
        + "or when the tool reports changed evidence.",
      );
    }
    lines.push(`evidenceFacts=${JSON.stringify(checkpoint.evidenceFacts)}`);
  }
  if (checkpoint.editEvidence?.length) {
    lines.push(
      "editEvidenceInstruction=The exact unchanged target text below is retained specifically for the active "
      + "selected slice. Reuse it for the bounded mutation; do not re-read the same target merely to recover "
      + "discarded context. If truncated=true and the needed edit text is outside the retained body, request "
      + "one narrower exact range.",
    );
    lines.push(`editEvidence=${JSON.stringify(checkpoint.editEvidence)}`);
  }
  if (checkpoint.repeatEvidence?.length) {
    lines.push(
      "repeatEvidenceInstruction=The server returned this exact unchanged body from a cached repeat. "
      + "Use it now; do not issue the same tool/path/query/range again. Other genuinely unread evidence "
      + "remains available. This bounded cache clears on mutation or a new user goal.",
    );
    lines.push(`repeatEvidence=${JSON.stringify(checkpoint.repeatEvidence)}`);
  }
  if (checkpoint.sourceEvidence) {
    lines.push(`sourceEvidence=${JSON.stringify(checkpoint.sourceEvidence)}`);
  }
  if (checkpoint.absentEvidence) {
    lines.push(`absentEvidence=${JSON.stringify(checkpoint.absentEvidence)}`);
  }
  if (Array.isArray(checkpoint.workingSet) && checkpoint.workingSet.length) {
    lines.push(
      "workingSetExactCode="
      + JSON.stringify(checkpoint.workingSet),
    );
  }
  lines.push(`compactedMessageCount=${(messages || []).length}`);
  lines.push(
    "Only use this summary for continuity. The checkpoint objective is the latest user goal; "
    + "do not continue an older structure/overview or refactor plan unless that latest goal asks for it. "
    + "Do not invent missing classes, modules, or GameFramework paths from memory or prior assistant prose. "
    + "Trust verified evidenceFacts and semanticAnchors for unchanged files; use tools for unread, changed, or exact-range evidence.",
  );
  // Keep this as the final generated line. requestIntentFromCheckpointSummary
  // deliberately ignores similarly named text embedded in every earlier,
  // potentially tool-derived summary field.
  lines.push(`checkpointRequestIntent=${JSON.stringify(checkpoint.requestIntent || null)}`);
  return lines.join("\n");
}

function compactSnapshots(messages, checkpoint, options = {}) {
  const snapshots = snapshotMessages(messages || []);
  const configuredTurns = options.recentCompleteTurns === undefined
    ? DEFAULT_COMPACTION_CONFIG.recentCompleteTurns
    : Number(options.recentCompleteTurns);
  // 0 retained turns => systems + latest real user + current-turn tools only (no older tail).
  const tailCount = configuredTurns <= 0
    ? 0
    : Math.max(1, configuredTurns * 2);
  const latestUserIndex = findLatestRealUserIndex(snapshots);
  const systems = [];
  const older = [];
  const currentTurn = [];
  let latestUser = null;
  for (let i = 0; i < snapshots.length; i += 1) {
    const message = snapshots[i];
    if (message.role === "system") {
      systems.push(message);
      continue;
    }
    if (message.role === "user" && isMetaUserMessage(message.text)) {
      continue;
    }
    if (i === latestUserIndex) {
      latestUser = message;
      continue;
    }
    if (latestUserIndex >= 0 && i > latestUserIndex) {
      currentTurn.push(message);
      continue;
    }
    older.push(message);
  }
  const olderTailStart = tailCount === 0
    ? older.length
    : completeTailStart(older, Math.max(0, older.length - tailCount));
  const olderTail = older.slice(olderTailStart);
  let keptCurrentTurn = currentTurn;
  const maxCurrent = options.maxCurrentTurnMessages;
  if (Number.isFinite(maxCurrent) && Number(maxCurrent) >= 0 && currentTurn.length > Number(maxCurrent)) {
    const keepStart = completeTailStart(
      currentTurn,
      Math.max(0, currentTurn.length - Number(maxCurrent)),
    );
    keptCurrentTurn = currentTurn.slice(keepStart);
  }
  // Many chat templates (Qwen/ChatML/Llama) allow only ONE leading system message.
  // Emitting a second system for the checkpoint makes applyPromptTemplate fail or
  // collapse to an empty user prompt (~10 tokens) and the model loses the goal.
  const checkpointText = summarizeOldMessages(older.slice(0, olderTailStart), checkpoint);
  const systemParts = [];
  for (const message of systems) {
    const text = String(message.text || "").trim();
    if (text) systemParts.push(text);
  }
  systemParts.push(checkpointText);
  const result = [{
    role: "system",
    text: systemParts.join("\n\n"),
    toolCalls: [],
    toolResults: [],
  }];
  result.push(...olderTail);
  if (latestUser) result.push(latestUser);
  // Prefer keeping the full in-flight turn; only trim oldest pairs when the
  // caller hits the hard token margin after older history is already gone.
  result.push(...keptCurrentTurn);
  if (options.trailingMetaUser && typeof options.trailingMetaUser === "object") {
    result.push(options.trailingMetaUser);
  }
  return result;
}

function validateCheckpoint(checkpoint) {
  if (!checkpoint || checkpoint.schemaVersion !== COMPACTION_SCHEMA_VERSION) return false;
  if (!Number.isFinite(Number(checkpoint.checkpointGeneration))) return false;
  if (
    checkpoint.objectiveHash !== undefined
    && checkpoint.objectiveHash !== ""
    && !/^[a-f0-9]{64}$/.test(String(checkpoint.objectiveHash || ""))
  ) return false;
  if (checkpoint.requestIntent !== undefined && checkpoint.requestIntent !== null) {
    const requestIntent = compactRequestIntent(
      checkpoint.requestIntent,
      checkpoint.objective,
      checkpoint.objectiveHash,
    );
    if (!requestIntent || stableStringify(requestIntent) !== stableStringify(checkpoint.requestIntent)) {
      return false;
    }
  }
  if (
    checkpoint.requiredNextTool
    && (
      typeof checkpoint.requiredNextTool !== "object"
      || Array.isArray(checkpoint.requiredNextTool)
      || typeof checkpoint.requiredNextTool.name !== "string"
      || !checkpoint.requiredNextTool.name.trim()
    )
  ) return false;
  if (!Array.isArray(checkpoint.completedToolCallIds)) return false;
  if (!checkpoint.completedToolCallIds.every((id) => typeof id === "string" && id.length > 0)) return false;
  if (checkpoint.pendingToolCall !== undefined && checkpoint.pendingToolCall !== null) {
    if (typeof checkpoint.pendingToolCall !== "object" || Array.isArray(checkpoint.pendingToolCall)) return false;
    if (typeof checkpoint.pendingToolCall.name !== "string" || !checkpoint.pendingToolCall.name.trim()) return false;
  }
  if (checkpoint.pendingToolCalls !== undefined) {
    if (!Array.isArray(checkpoint.pendingToolCalls)) return false;
    if (checkpoint.pendingToolCalls.some((call) => (
      !call
      || typeof call !== "object"
      || Array.isArray(call)
      || typeof call.name !== "string"
      || !call.name.trim()
    ))) return false;
  }
  if (checkpoint.sourceMessageCount !== undefined) {
    const count = Number(checkpoint.sourceMessageCount);
    if (!Number.isFinite(count) || count < 0) return false;
  }
  if (checkpoint.sourceHistoryHash !== undefined && typeof checkpoint.sourceHistoryHash !== "string") return false;
  if (checkpoint.catalogRefresh !== undefined && checkpoint.catalogRefresh !== null) {
    if (typeof checkpoint.catalogRefresh !== "object" || Array.isArray(checkpoint.catalogRefresh)) return false;
    if (typeof checkpoint.catalogRefresh.routeHash !== "string") return false;
    const attempts = Number(checkpoint.catalogRefresh.attempts);
    if (!Number.isInteger(attempts) || attempts < 0 || attempts > 1) return false;
    if (!["requested", "synchronized", "failed"].includes(checkpoint.catalogRefresh.status)) return false;
  }
  if (checkpoint.semanticBlocker !== undefined && checkpoint.semanticBlocker !== null) {
    if (typeof checkpoint.semanticBlocker !== "object" || Array.isArray(checkpoint.semanticBlocker)) return false;
    if (!Array.isArray(checkpoint.semanticBlocker.forbiddenTools)) return false;
    if (checkpoint.semanticBlocker.forbiddenTools.some((name) => typeof name !== "string" || !name.trim())) return false;
  }
  if (checkpoint.sideQuery !== undefined && checkpoint.sideQuery !== null) {
    if (typeof checkpoint.sideQuery !== "object" || Array.isArray(checkpoint.sideQuery)) return false;
    if (checkpoint.sideQuery.active !== true || typeof checkpoint.sideQuery.request !== "string") return false;
  }
  if (checkpoint.evidenceFacts !== undefined && !Array.isArray(checkpoint.evidenceFacts)) return false;
  if (checkpoint.repeatEvidence !== undefined) {
    if (!Array.isArray(checkpoint.repeatEvidence)) return false;
    if (checkpoint.repeatEvidence.length > MAX_REPEAT_EVIDENCE_FILES) return false;
    if (checkpoint.repeatEvidence.some((item) => (
      !item
      || typeof item !== "object"
      || typeof item.tool !== "string"
      || !item.tool.trim()
      || typeof item.content !== "string"
      || item.content.length > MAX_REPEAT_EVIDENCE_CHARS
    ))) return false;
  }
  if (checkpoint.invalidatedTaskSessionIds !== undefined) {
    if (!Array.isArray(checkpoint.invalidatedTaskSessionIds) || checkpoint.invalidatedTaskSessionIds.length > 16) return false;
    if (checkpoint.invalidatedTaskSessionIds.some((value) => typeof value !== "string" || !value.trim())) return false;
  }
  if (checkpoint.editEvidence !== undefined) {
    if (!Array.isArray(checkpoint.editEvidence) || checkpoint.editEvidence.length > MAX_EDIT_EVIDENCE_FILES) return false;
    if (checkpoint.editEvidence.some((item) => (
      !item
      || typeof item !== "object"
      || Array.isArray(item)
      || typeof item.path !== "string"
      || typeof item.content !== "string"
      || item.content.length > MAX_EDIT_EVIDENCE_CHARS
    ))) return false;
  }
  if (
    checkpoint.sourceEvidence !== undefined
    && checkpoint.sourceEvidence !== null
    && !compactEvidenceLedger(checkpoint.sourceEvidence, false)
  ) return false;
  if (
    checkpoint.absentEvidence !== undefined
    && checkpoint.absentEvidence !== null
    && !compactEvidenceLedger(checkpoint.absentEvidence, true)
  ) return false;
  if (checkpoint.workingSet !== undefined) {
    if (!Array.isArray(checkpoint.workingSet) || checkpoint.workingSet.length > 2) return false;
    if (checkpoint.workingSet.some((entry) => (
      !entry
      || typeof entry !== "object"
      || Array.isArray(entry)
      || typeof entry.path !== "string"
      || !entry.path.trim()
      || typeof entry.content !== "string"
      || entry.content.length > 12_000
    ))) return false;
  }
  if (
    checkpoint.serverControl !== undefined
    && checkpoint.serverControl !== null
    && !compactServerControl(checkpoint.serverControl)
  ) return false;
  if (
    checkpoint.taskRouteOwnership !== undefined
    && checkpoint.taskRouteOwnership !== null
    && !compactTaskRouteOwnership(checkpoint.taskRouteOwnership)
  ) return false;
  return true;
}

module.exports = {
  COMPACTION_SCHEMA_VERSION,
  REQUEST_INTENT_VERSION,
  DEFAULT_COMPACTION_CONFIG,
  stableStringify,
  sha256,
  objectiveHashOf,
  compactRequestIntent,
  matchingRequestIntent,
  isWindowsHostPlatform,
  asciiWindowsFold,
  normalizeProjectEvidencePath,
  textOf,
  roleOf,
  toolRequestsOf,
  toolResultsOf,
  messageSnapshot,
  snapshotMessages,
  parseJsonObjects,
  toolResultSucceeded,
  isNonToolNextAction,
  compactTaskRouteOwnership,
  compactServerControl,
  collectSemanticBlockerFields,
  isContinuationUserMessage,
  mutationToolName,
  toolArgumentsSatisfy,
  collectControlFields,
  classifyMutationIntent,
  isReadOnlyUserGoal,
  classifyUserIntent,
  classifyUserTurnIntent,
  isMetaUserMessage,
  requestIntentFromCheckpointSummary,
  findLatestRealUserIndex,
  extractControlState,
  buildCheckpoint,
  SESSION_MARKER_RE,
  extractSessionMarker,
  formatSessionMarker,
  messageLineageFingerprints,
  lineageContinues,
  baseSessionKey,
  sessionFingerprint,
  lmStudioConversationSessionFingerprint,
  isMajorGoalChange,
  parseProviderQualifiedToolName,
  toolNamesMatch,
  expectedToolReserve,
  budgetDecision,
  isCompleteToolPair,
  completeTailStart,
  summarizeOldMessages,
  compactSnapshots,
  validateCheckpoint,
};
