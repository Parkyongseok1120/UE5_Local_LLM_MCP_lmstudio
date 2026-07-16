"use strict";

const crypto = require("node:crypto");

const COMPACTION_SCHEMA_VERSION = 1;
const DEFAULT_COMPACTION_CONFIG = Object.freeze({
  enabled: true,
  observeOnly: false,
  softRemainingTokens: 10000,
  hardRemainingTokens: 5000,
  maxOutputReserve: 4096,
  normalToolResultReserve: 3000,
  buildToolResultReserve: 8000,
  recentCompleteTurns: 6,
  minimumTurnsBetweenCompactions: 3,
  targetRemainingTokensAfterCompaction: 20000,
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
      content: String(result.content || ""),
    })),
  };
}

function snapshotMessages(messages) {
  return (messages || []).map(messageSnapshot);
}

function parseJsonObjects(text) {
  const values = [];
  const source = String(text || "").trim();
  if (!source) return values;
  try {
    const parsed = JSON.parse(source);
    if (parsed && typeof parsed === "object") values.push(parsed);
  } catch {
    const matches = source.match(/\{[\s\S]*\}/g) || [];
    for (const match of matches.slice(-4)) {
      try {
        const parsed = JSON.parse(match);
        if (parsed && typeof parsed === "object") values.push(parsed);
      } catch { /* text is not JSON; keep the raw message */ }
    }
  }
  return values;
}

function collectControlFields(value, state) {
  if (!value || typeof value !== "object") return;
  for (const [key, child] of Object.entries(value)) {
    if (key === "requiredNextTool") {
      if (child === null || child === false || child === "") {
        state.requiredNextTool = null;
        state.requiredNextToolRef = null;
        state.requiredNextToolArgs = null;
      } else if (typeof child === "string") {
        state.requiredNextTool = child;
        state.requiredNextToolRef = null;
      } else if (child && typeof child === "object") {
        const name = typeof child.name === "string"
          ? child.name
          : (typeof child.tool === "string" ? child.tool : "");
        if (name) {
          state.requiredNextTool = name;
          state.requiredNextToolRef = child;
        }
      }
    } else if (key === "requiredNextToolArgs" && child && typeof child === "object") {
      state.requiredNextToolArgs = child;
    } else if (key === "constraints" && Array.isArray(child)) {
      state.constraints.push(...child.filter((item) => typeof item === "string"));
    } else if (["diagnosticCode", "errorCode", "errorKey", "errorSubkind", "firstError"].includes(key) && child != null) {
      state.lastDiagnostics.push(`${key}=${String(child)}`.slice(0, 400));
    } else if (key === "signatureContract" && child && typeof child === "object") {
      state.exactSignatureContracts.push(child);
    } else if (["path", "file", "projectRelative", "projectPath"].includes(key) && typeof child === "string") {
      state.touchedPaths.push(child.replaceAll("\\", "/"));
    } else if (["activeProject", "projectName"].includes(key) && typeof child === "string") {
      state.activeProject = child;
    } else if (key === "mutationGeneration" && Number.isFinite(Number(child))) {
      state.mutationGeneration = Math.max(state.mutationGeneration, Number(child));
    } else if (key === "buildOutcome" || key === "proofLevel" || key === "phase") {
      state.buildState[key] = child;
    }
    collectControlFields(child, state);
  }
}

function extractControlState(messages, prior = {}, options = {}) {
  const snapshots = snapshotMessages(messages || []);
  const priorCount = Number(prior.sourceMessageCount || 0);
  const canResume = priorCount > 0
    && priorCount <= snapshots.length
    && prior.sourceHistoryHash === sha256(stableStringify(snapshots.slice(0, priorCount)));
  const source = canResume ? snapshots.slice(priorCount) : snapshots;
  const state = {
    schemaVersion: COMPACTION_SCHEMA_VERSION,
    objective: canResume ? (prior.objective || "") : "",
    constraints: canResume && Array.isArray(prior.constraints) ? [...prior.constraints] : [],
    activeProject: canResume ? (prior.activeProject || null) : null,
    touchedPaths: canResume && Array.isArray(prior.modifiedFiles) ? [...prior.modifiedFiles] : [],
    lastDiagnostics: canResume && Array.isArray(prior.diagnostics) ? [...prior.diagnostics] : [],
    exactSignatureContracts: canResume && Array.isArray(prior.exactSignatureContracts) ? [...prior.exactSignatureContracts] : [],
    requiredNextTool: canResume ? (prior.requiredNextTool?.name || null) : null,
    requiredNextToolRef: canResume ? (prior.requiredNextTool?.reference || null) : null,
    requiredNextToolArgs: canResume ? (prior.requiredNextTool?.args || null) : null,
    mutationGeneration: canResume ? Number(prior.mutationGeneration || 0) : 0,
    buildState: canResume ? { ...(prior.buildState || {}) } : {},
    facts: canResume && Array.isArray(prior.facts) ? [...prior.facts] : [],
  };

  for (const snapshot of source) {
    if (!state.objective && snapshot.role === "user" && snapshot.text.trim()) {
      state.objective = snapshot.text.trim().slice(0, 1200);
    }
    for (const payload of parseJsonObjects(snapshot.text)) {
      collectControlFields(payload, state);
      if (payload.ok === true && (payload.phase === "complete" || payload.buildOutcome === "succeeded")) {
        state.requiredNextTool = null;
        state.requiredNextToolRef = null;
        state.requiredNextToolArgs = null;
      }
    }
    for (const result of snapshot.toolResults) {
      for (const payload of parseJsonObjects(result.content)) {
        collectControlFields(payload, state);
        if (payload.ok === true && (payload.phase === "complete" || payload.buildOutcome === "succeeded")) {
          state.requiredNextTool = null;
          state.requiredNextToolRef = null;
          state.requiredNextToolArgs = null;
        }
      }
    }
    for (const call of snapshot.toolCalls) {
      state.facts.push(`tool:${call.name}`);
      const normalizedName = String(call.name || "").toLowerCase();
      if (state.requiredNextTool && toolNamesMatch(state.requiredNextTool, call.name)) {
        state.requiredNextTool = null;
        state.requiredNextToolRef = null;
        state.requiredNextToolArgs = null;
      }
      if (["replace_in_file", "write_file"].some((name) => normalizedName === name || normalizedName.endsWith(`_${name}`))) {
        state.mutationGeneration += 1;
        state.requiredNextTool = null;
        state.requiredNextToolRef = null;
        state.requiredNextToolArgs = null;
      }
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
  return state;
}

function buildCheckpoint(messages, prior = {}, options = {}) {
  const control = extractControlState(messages, prior, options);
  const snapshots = snapshotMessages(messages || []);
  const generation = Number(prior.checkpointGeneration || 0) + 1;
  return {
    schemaVersion: COMPACTION_SCHEMA_VERSION,
    checkpointGeneration: generation,
    createdAt: new Date().toISOString(),
    objective: control.objective,
    constraints: control.constraints,
    activeProject: control.activeProject,
    modifiedFiles: control.touchedPaths,
    mutationGeneration: control.mutationGeneration,
    buildState: control.buildState,
    requiredNextTool: control.requiredNextTool ? {
      name: control.requiredNextTool,
      reference: control.requiredNextToolRef,
      args: control.requiredNextToolArgs,
    } : null,
    exactSignatureContracts: control.exactSignatureContracts,
    diagnostics: control.lastDiagnostics,
    facts: control.facts,
    pendingToolCall: prior.pendingToolCall || null,
    pendingToolCalls: Array.isArray(prior.pendingToolCalls) ? [...prior.pendingToolCalls] : [],
    completedToolCallIds: Array.isArray(prior.completedToolCallIds) ? [...prior.completedToolCallIds].slice(-256) : [],
    compactionGeneration: Number(prior.compactionGeneration || 0),
    sourceMessageCount: snapshots.length,
    sourceHistoryHash: sha256(stableStringify(snapshots)),
    lastCompactionSourceMessageCount: Number(prior.lastCompactionSourceMessageCount || 0),
  };
}

function sessionFingerprint(messages, salt = "") {
  const snapshots = snapshotMessages(messages || []);
  const firstSystem = snapshots.find((message) => message.role === "system");
  const firstUser = snapshots.find((message) => message.role === "user");
  const seed = [firstSystem, firstUser]
    .filter(Boolean)
    .map((message) => `${message.role}:${message.text}`)
    .join("\n");
  return sha256(`${salt}\n${seed || "empty-session"}`).slice(0, 32);
}

function toolNamesMatch(expected, actual) {
  const left = String(expected || "").trim().toLowerCase();
  const right = String(actual || "").trim().toLowerCase();
  if (!left || !right) return false;
  return left === right || left.endsWith(`_${right}`) || right.endsWith(`_${left}`);
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
  const reserve = Number(merged.maxOutputReserve) + Number(toolSchemaTokens || 0) + expectedToolReserve(nextToolName, merged);
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
  for (const message of messages || []) {
    for (const call of messageSnapshot(message).toolCalls) {
      if (call.id) {
        known.add(call.id);
        pending.add(call.id);
      }
    }
    for (const result of messageSnapshot(message).toolResults) {
      if (result.toolCallId && !known.has(result.toolCallId)) return false;
      if (result.toolCallId) pending.delete(result.toolCallId);
    }
    // Tool results are validated in the loop above.
  }
  return pending.size === 0;
}

function completeTailStart(snapshots, startIndex) {
  let start = Math.max(0, Number(startIndex || 0));
  while (start > 0) {
    const tail = snapshots.slice(start);
    const callIds = new Set();
    let orphanResult = false;
    for (const message of tail) {
      for (const call of message.toolCalls || []) if (call.id) callIds.add(call.id);
      for (const result of message.toolResults || []) {
        if (result.toolCallId && !callIds.has(result.toolCallId)) orphanResult = true;
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
  if (checkpoint.modifiedFiles?.length) lines.push(`modifiedFiles=${checkpoint.modifiedFiles.join(", ")}`);
  if (checkpoint.constraints?.length) lines.push(`constraints=${checkpoint.constraints.join(" | ")}`);
  if (checkpoint.activeProject) lines.push(`activeProject=${checkpoint.activeProject}`);
  lines.push(`mutationGeneration=${Number(checkpoint.mutationGeneration || 0)}`);
  if (checkpoint.buildState && Object.keys(checkpoint.buildState).length) {
    lines.push(`buildState=${JSON.stringify(checkpoint.buildState)}`);
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
  lines.push(`compactedMessageCount=${(messages || []).length}`);
  lines.push("Only use this summary for continuity. Re-read current files and trust latest tool results.");
  return lines.join("\n");
}

function compactSnapshots(messages, checkpoint, options = {}) {
  const snapshots = snapshotMessages(messages || []);
  const configuredTurns = options.recentCompleteTurns === undefined
    ? DEFAULT_COMPACTION_CONFIG.recentCompleteTurns
    : Number(options.recentCompleteTurns);
  const tailCount = Math.max(1, configuredTurns * 2);
  const pinned = [];
  const rest = [];
  let firstUserKept = false;
  for (const message of snapshots) {
    if (message.role === "system" || (message.role === "user" && !firstUserKept)) {
      pinned.push(message);
      if (message.role === "user") firstUserKept = true;
    } else rest.push(message);
  }
  const restTailStart = completeTailStart(rest, Math.max(0, rest.length - tailCount));
  const tail = rest.slice(restTailStart);
  return [
    ...pinned,
    { role: "system", text: summarizeOldMessages(rest.slice(0, restTailStart), checkpoint), toolCalls: [], toolResults: [] },
    ...tail,
  ];
}

function validateCheckpoint(checkpoint) {
  if (!checkpoint || checkpoint.schemaVersion !== COMPACTION_SCHEMA_VERSION) return false;
  if (!Number.isFinite(Number(checkpoint.checkpointGeneration))) return false;
  if (checkpoint.requiredNextTool && typeof checkpoint.requiredNextTool.name !== "string") return false;
  if (!Array.isArray(checkpoint.completedToolCallIds)) return false;
  return true;
}

module.exports = {
  COMPACTION_SCHEMA_VERSION,
  DEFAULT_COMPACTION_CONFIG,
  stableStringify,
  sha256,
  textOf,
  roleOf,
  messageSnapshot,
  snapshotMessages,
  extractControlState,
  buildCheckpoint,
  sessionFingerprint,
  budgetDecision,
  expectedToolReserve,
  toolNamesMatch,
  isCompleteToolPair,
  compactSnapshots,
  completeTailStart,
  validateCheckpoint,
};
