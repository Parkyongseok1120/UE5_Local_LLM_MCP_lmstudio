"use strict";

/**
 * Model-facing response contract for Direct Model Mode.
 *
 * Direct mode has no server-owned workflow, so transport responses must not
 * expose or reconstruct task/route/synthesis control.  Errors have one retry
 * directive and, at most, one advisory tool suggestion.
 */

const INTERNAL_FIELDS = new Set([
  "activeSliceId",
  "allowedTools",
  "authorizationBound",
  "blockerFingerprint",
  "claimLedger",
  "commitEligible",
  "control",
  "controlEpoch",
  "controlFingerprint",
  "evidenceBundle",
  "evidenceStateHash",
  "expiryTransition",
  "fingerprint",
  "foreignHealthy",
  "ownerCapability",
  "pendingGates",
  "planRevision",
  "promptContract",
  "requiredTool",
  "routeHash",
  "routeOwnership",
  "serverControl",
  "sourceEvidence",
  "synthesisEvidence",
  "synthesisLatch",
  "synthesisReadiness",
  "taskAuthorization",
  "taskLifecycle",
  "taskRouteOwnership",
  "toolRoute",
]);

const LEGACY_DIRECTIVE_FIELDS = new Set([
  "agentInstruction",
  "doNotRetry",
  "doNotRetryTools",
  "nextAction",
  "nextActionArgs",
  "nextActionIsTool",
  "recoveryActionRequired",
  "requiredNextAction",
  "requiredNextTool",
  "requiredNextToolArgs",
  "retryPolicy",
  "retryable",
  "stopCurrentPhase",
  "stopCurrentWorkflow",
]);

function isRecord(value) {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function clipString(value, maxChars) {
  const text = String(value ?? "");
  if (text.length <= maxChars) return text;
  return `${text.slice(0, Math.max(0, maxChars - 20))}\n...[truncated]`;
}

function stripInternal(value, depth = 0) {
  if (depth > 12) return "[depth limited]";
  if (Array.isArray(value)) {
    return value.slice(0, 1000).map((item) => stripInternal(item, depth + 1));
  }
  if (!isRecord(value)) {
    return typeof value === "string" ? clipString(value, 256_000) : value;
  }
  const result = {};
  for (const [key, item] of Object.entries(value)) {
    if (INTERNAL_FIELDS.has(key) || LEGACY_DIRECTIVE_FIELDS.has(key)) continue;
    if (/^(?:owner|route|control).*capability$/i.test(key)) continue;
    result[key] = stripInternal(item, depth + 1);
  }
  return result;
}

function suggestionFrom(payload) {
  if (!isRecord(payload)) return null;
  if (isRecord(payload.suggestion) && String(payload.suggestion.tool || "").trim()) {
    return {
      tool: String(payload.suggestion.tool).trim(),
      args: isRecord(payload.suggestion.args) ? stripInternal(payload.suggestion.args) : {},
    };
  }
  const suggested = Array.isArray(payload.suggestedToolCalls)
    ? payload.suggestedToolCalls.find((entry) => isRecord(entry) && String(entry.tool || "").trim())
    : null;
  if (suggested) {
    return {
      tool: String(suggested.tool).trim(),
      args: isRecord(suggested.args) ? stripInternal(suggested.args) : {},
    };
  }
  return null;
}

function normalizeRetry(payload, ok) {
  if (isRecord(payload?.retry)) {
    return {
      allowed: payload.retry.allowed === true,
      mode: String(payload.retry.mode || (payload.retry.allowed === true ? "same_arguments" : "none")),
    };
  }
  if (ok) return undefined;
  const allowed = payload?.retryable === true;
  return {
    allowed,
    mode: allowed ? "different_arguments" : "none",
  };
}

function normalizeDirectPayload(payload, options = {}) {
  const source = isRecord(payload) ? payload : { value: payload };
  const ok = options.ok !== undefined ? options.ok === true : source.ok !== false;
  let suggestion = suggestionFrom(source);
  const stripped = stripInternal(source);
  delete stripped.suggestedToolCalls;
  const result = { ...stripped, ok };
  delete result.suggestion;
  if (!ok) {
    result.errorCode = String(source.errorCode || options.errorCode || "TOOL_FAILED").slice(0, 120);
    result.message = clipString(
      source.message || source.error || source.userMessage || options.message || "The tool call failed.",
      1800,
    );
    delete result.error;
    delete result.userMessage;
    result.retry = normalizeRetry(source, false);
    if (
      suggestion
      && result.retry.allowed !== true
      && String(options.currentTool || "").trim()
      && suggestion.tool === String(options.currentTool).trim()
    ) {
      // A non-retryable response must never smuggle a same-tool retry through
      // the advisory slot.  Different-tool recovery remains optional advice.
      suggestion = null;
    }
  } else {
    delete result.retry;
  }
  if (suggestion) result.suggestion = suggestion;
  return result;
}

function success(payload = {}) {
  return normalizeDirectPayload({ ...payload, ok: true }, { ok: true });
}

function failure(errorCode, message, options = {}) {
  const payload = {
    ...(isRecord(options.details) ? options.details : {}),
    ok: false,
    errorCode: String(errorCode || "TOOL_FAILED"),
    message: String(message || "The tool call failed."),
    retry: {
      allowed: options.retryAllowed === true,
      mode: String(options.retryMode || (options.retryAllowed === true ? "different_arguments" : "none")),
    },
  };
  if (options.suggestion) payload.suggestion = options.suggestion;
  return normalizeDirectPayload(payload, { ok: false });
}

function boundedJson(payload, maxChars) {
  const limit = Math.max(512, Number(maxChars || 32_000));
  let rendered = JSON.stringify(payload, null, 2);
  if (rendered.length <= limit) return { payload, rendered, truncated: false };

  // Never silently clip a successful payload after a capability has computed
  // byte/line cursors. Doing so would advance the cursor past data the model
  // never received. Capabilities with cursors fit their own payloads; any
  // unexpected oversize result becomes a retryable, data-preserving error.
  const minimal = payload.ok === false
    ? failure(payload.errorCode, payload.message || payload.error || "The tool call failed.", {
      retryAllowed: payload.retry?.allowed === true,
      retryMode: payload.retry?.mode,
      suggestion: payload.suggestion,
    })
    : failure(
      "OUTPUT_LIMIT_EXCEEDED",
      "The result exceeded the transport limit. Request a smaller byte range, line range, or result count.",
      { retryAllowed: true, retryMode: "different_arguments" },
    );
  rendered = JSON.stringify(minimal, null, 2);
  return {
    payload: minimal,
    rendered: rendered.length <= limit ? rendered : clipString(rendered, limit),
    truncated: true,
  };
}

function toMcpResult(payload, options = {}) {
  const normalized = normalizeDirectPayload(payload, {
    ok: payload?.ok !== false,
    currentTool: options.currentTool,
  });
  const limit = normalized.ok === false
    ? Math.min(4096, Number(options.maxChars || 4096))
    : Number(options.maxChars || 256_000);
  const bounded = boundedJson(normalized, limit);
  return {
    content: [{ type: "text", text: bounded.rendered }],
    structuredContent: bounded.payload,
    isError: bounded.payload.ok === false,
  };
}

module.exports = {
  INTERNAL_FIELDS,
  LEGACY_DIRECTIVE_FIELDS,
  boundedJson,
  failure,
  normalizeDirectPayload,
  stripInternal,
  success,
  suggestionFrom,
  toMcpResult,
};
