"use strict";

const crypto = require("node:crypto");

const COMPACTION_SCHEMA_VERSION = 1;
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

function isReadOnlyUserGoal(text) {
  const source = String(text || "");
  const lower = source.toLowerCase();
  if (
    /수정은\s*하(?:지\s*)?마/.test(source)
    || /수정하지\s*말/.test(source)
    || /찾기만하고/.test(source)
    || /분석만/.test(source)
    || /보고만/.test(source)
  ) {
    return true;
  }
  return (
    /\b(?:do\s+not|don't|dont)\s+(?:fix|edit|patch|change|modify|write)\b/.test(lower)
    || /\b(?:no|without)\s+(?:fixes|edits|patches)\b/.test(lower)
    || /\bfind\s+bugs?\s+only\b/.test(lower)
    || /\banalysis only\b/.test(lower)
    || /\breport only\b/.test(lower)
  );
}

function isMetaUserMessage(text) {
  const source = String(text || "");
  const lower = source.toLowerCase();
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
    if (snapshot.role === "user" && snapshot.text.trim()) {
      if (isMetaUserMessage(snapshot.text)) {
        continue;
      }
      // Latest real user message always wins — pinning the first turn causes goal drift.
      // Synthetic LM Studio title prompts must not replace the active goal.
      const userText = snapshot.text.trim();
      state.objective = userText.slice(0, 1200);
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
    return sha256(`${salt}\nmarker:${marker}`).slice(0, 32);
  }
  return baseSessionKey(messages, salt);
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
    if (isReadOnlyUserGoal(text)) return "readonly";
    if (/\b(implement|refactor|fix|patch|edit|write|compile|build|구현|수정|리팩터)\b/i.test(text)) {
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
  lines.push(
    "Only use this summary for continuity. The checkpoint objective is the latest user goal; "
    + "do not continue an older structure/overview or refactor plan unless that latest goal asks for it. "
    + "Do not invent missing classes, modules, or GameFramework paths from memory or prior assistant prose. "
    + "Re-read current files with tools and trust only latest tool results / verified facts.",
  );
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
  toolRequestsOf,
  toolResultsOf,
  messageSnapshot,
  snapshotMessages,
  parseJsonObjects,
  collectControlFields,
  isReadOnlyUserGoal,
  isMetaUserMessage,
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
  isMajorGoalChange,
  toolNamesMatch,
  expectedToolReserve,
  budgetDecision,
  isCompleteToolPair,
  completeTailStart,
  summarizeOldMessages,
  compactSnapshots,
  validateCheckpoint,
};
