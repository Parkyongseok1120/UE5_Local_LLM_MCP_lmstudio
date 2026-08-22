"use strict";

const { clip } = require("./continuity-text.js");

const INTERNAL_KEYS = new Set([
  "activeSliceId", "allowedTools", "authorizationBound", "claimLedger",
  "commitEligible", "control", "controlEpoch", "controlFingerprint",
  "evidenceBundle", "evidenceStateHash", "foreignHealthy", "ownerCapability",
  "pendingGates", "phase", "phaseState", "planRevision", "promptContract", "requiredTool",
  "routeHash", "routeOwnership", "serverControl", "sourceEvidence",
  "state", "stateHash", "synthesisEvidence", "synthesisLatch", "synthesisReadiness",
  "taskAuthorization", "taskLifecycle", "taskRouteOwnership", "toolRoute",
]);

const CONTROL_DIRECTIVES = new Set([
  "agentInstruction", "doNotRetry", "doNotRetryTools", "nextAction",
  "nextActionArgs", "nextActionIsTool", "recoveryActionRequired",
  "requiredNextAction", "requiredNextTool", "requiredNextToolArgs",
  "requiredSequence", "retryPolicy", "stopCurrentPhase", "stopCurrentWorkflow",
]);

const CONTROL_KEY_ROOTS = new Set(["control", "phase", "route", "state", "synthesis", "task"]);

function normalizeKeyToken(value) {
  return String(value || "").replace(/[^A-Za-z0-9]/g, "").toLowerCase();
}

const CONTROL_KEY_TOKENS = new Set(
  [...INTERNAL_KEYS, ...CONTROL_DIRECTIVES].map((key) => normalizeKeyToken(key)),
);

function isRecord(value) {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function keyRoot(value) {
  const separated = String(value || "").replace(/([a-z0-9])([A-Z])/g, "$1_$2");
  return String(separated.split(/[^A-Za-z0-9]+/, 1)[0] || "").toLowerCase();
}

function isControlKey(value) {
  const normalized = normalizeKeyToken(value);
  return CONTROL_KEY_TOKENS.has(normalized) || CONTROL_KEY_ROOTS.has(keyRoot(value));
}

function sanitizeRetainedString(value, maxChars = 4000) {
  const sanitized = String(value ?? "").replace(
    /[A-Za-z][A-Za-z0-9_]*/g,
    (token) => isControlKey(token) ? "[control-token-omitted]" : token,
  );
  return clip(sanitized, maxChars);
}

function stripControl(value, depth = 0) {
  if (depth > 8) return "[depth limited]";
  if (Array.isArray(value)) return value.slice(0, 40).map((item) => stripControl(item, depth + 1));
  if (!isRecord(value)) return typeof value === "string" ? sanitizeRetainedString(value) : value;
  const out = {};
  for (const [key, item] of Object.entries(value)) {
    if (isControlKey(key)) continue;
    out[key] = stripControl(item, depth + 1);
  }
  return out;
}

function parseToolResult(content) {
  const text = String(content || "").trim();
  if (!text) return { summary: "empty tool result" };
  try {
    const source = stripControl(JSON.parse(text));
    const out = {};
    for (const key of [
      "ok", "status", "summary", "message", "errorCode", "path", "operation",
      "sha256", "previousSha256", "size", "truncated", "hasMore",
      "startLine", "endLine", "totalLines", "filesScanned", "findingCount",
      "validationOk", "blocksBuild", "exitCode", "likelyErrors", "fullLogPath",
      "upToDate", "actionsExecuted", "proofLevel", "failedCount", "succeededCount", "files",
      "activeProject", "projectPath", "project", "engineAssociation", "resolvedEngineVersion",
      "requestedEngineAssociation", "fileVersionReceipt", "snapshotCapturedAt", "snapshotVersion",
      "hashSource", "canonicalProject", "canonicalPath",
    ]) {
      if (source[key] !== undefined) out[key] = source[key];
    }
    if (!Object.keys(out).length) out.summary = "tool result contained no retained factual fields";
    return out;
  } catch {
    return { summary: "malformed tool result omitted" };
  }
}

function toolOutcomeMemory(messages, beforeIndex, options = {}) {
  const maxItems = Math.max(1, Number(options.maxItems || 12));
  const maxChars = Math.max(200, Number(options.maxToolResultChars || 1200));
  const outcomes = [];
  for (const message of messages.slice(0, beforeIndex)) {
    if (message.role !== "tool") continue;
    for (const result of message.toolResults) {
      outcomes.push(clip(JSON.stringify(parseToolResult(result?.content ?? result)), maxChars));
    }
    if (!message.toolResults.length && message.text) {
      outcomes.push(clip(JSON.stringify(parseToolResult(message.text)), maxChars));
    }
  }
  return outcomes.slice(-maxItems);
}

function parsedOutcomes(outcomes) {
  const parsed = [];
  for (const serialized of outcomes || []) {
    try {
      const value = JSON.parse(serialized);
      if (isRecord(value)) parsed.push(value);
    } catch {
      // A response-size clip is not valid evidence and is ignored.
    }
  }
  return parsed;
}

function stateMemory(outcomes) {
  const files = [];
  const builds = [];
  let activeProject = null;
  for (const item of parsedOutcomes(outcomes)) {
    const projectCandidate = item.activeProject || item.projectPath
      || (String(item.project || "").toLowerCase().endsWith(".uproject") ? item.project : "");
    if (projectCandidate) {
      activeProject = {
        descriptor: String(projectCandidate),
        source: "tool_result_fact",
        ...(item.engineAssociation ? { engineAssociation: item.engineAssociation } : {}),
        ...(item.resolvedEngineVersion ? { engineVersion: item.resolvedEngineVersion } : {}),
      };
    }
    if (item.path && (item.operation || item.sha256 || item.previousSha256 || item.fileVersionReceipt)) {
      files.push({
        path: item.path,
        operation: item.operation || "observed",
        sha256: item.sha256 || undefined,
        previousSha256: item.previousSha256 || undefined,
        fileVersionReceipt: item.fileVersionReceipt || undefined,
        snapshotVersion: item.snapshotVersion || undefined,
      });
    }
    if (Array.isArray(item.files)) {
      for (const file of item.files) {
        if (!isRecord(file) || !file.path) continue;
        files.push({
          path: file.path,
          operation: file.operation || item.operation || "observed",
          sha256: file.sha256 || undefined,
          previousSha256: file.previousSha256 || undefined,
          fileVersionReceipt: file.fileVersionReceipt || undefined,
          snapshotVersion: file.snapshotVersion || undefined,
        });
      }
    }
    if (item.exitCode !== undefined || item.proofLevel || item.fullLogPath || item.likelyErrors) {
      builds.push({
        ok: item.ok,
        exitCode: item.exitCode,
        proofLevel: item.proofLevel,
        upToDate: item.upToDate,
        actionsExecuted: item.actionsExecuted,
        likelyErrors: item.likelyErrors,
        fullLogPath: item.fullLogPath,
      });
    }
  }
  return {
    files: files.slice(-16),
    builds: builds.slice(-4),
    activeProject,
  };
}

module.exports = {
  CONTROL_DIRECTIVES,
  INTERNAL_KEYS,
  parseToolResult,
  stateMemory,
  stripControl,
  toolOutcomeMemory,
};
