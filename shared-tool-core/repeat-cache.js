"use strict";

const crypto = require("crypto");

function stableValue(value) {
  if (Array.isArray(value)) return value.map(stableValue);
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(
    Object.keys(value)
      .filter((key) => key !== "repeatReceipt")
      .sort()
      .map((key) => [key, stableValue(value[key])]),
  );
}

function digest(value) {
  return crypto.createHash("sha256")
    .update(JSON.stringify(stableValue(value)))
    .digest("hex");
}

class DirectRepeatCache {
  constructor(options = {}) {
    this.maxEntries = Math.max(8, Number(options.maxEntries || 256));
    this.ttlMs = Math.max(1_000, Number(options.ttlMs || 10 * 60 * 1000));
    this.now = typeof options.now === "function" ? options.now : Date.now;
    this.entries = new Map();
  }

  key(tool, args, stateSignature = "") {
    return digest({ tool: String(tool || ""), args: stableValue(args || {}), stateSignature });
  }

  lookup(tool, args, stateSignature = "") {
    const key = this.key(tool, args, stateSignature);
    const entry = this.entries.get(key);
    if (!entry) return null;
    if (this.now() - entry.at > this.ttlMs) {
      this.entries.delete(key);
      return null;
    }
    if (entry.ok && String(args?.repeatReceipt || "") !== entry.repeatReceipt) return null;
    this.entries.delete(key);
    this.entries.set(key, entry);
    return {
      ...entry.observation,
      ok: entry.ok,
      duplicate: true,
      status: "no_new_information",
      message: entry.ok
        ? "Unchanged observation; body omitted for the echoed receipt."
        : `This identical call already failed against the same observable state; no new information was produced. ${entry.message}`.trim(),
      originalOutcome: entry.ok ? "success" : "failure",
      ...(!entry.ok && entry.errorCode ? { originalErrorCode: entry.errorCode } : {}),
      ...(!entry.ok ? {
        errorCode: entry.errorCode || "REPEATED_TOOL_FAILURE",
        retry: { allowed: false, mode: "none" },
      } : {}),
      resultDigest: entry.resultDigest,
      repeatOfDigest: entry.resultDigest,
      ...(entry.ok ? { bodyOmitted: true, repeatReceipt: entry.repeatReceipt } : {}),
    };
  }

  remember(tool, args, stateSignature, payload) {
    const key = this.key(tool, args, stateSignature);
    const repeatReceipt = crypto.randomBytes(18).toString("base64url");
    const entry = {
      at: this.now(),
      ok: payload?.ok !== false,
      errorCode: String(payload?.errorCode || ""),
      message: String(payload?.message || "").slice(0, 600),
      repeatReceipt,
      resultDigest: digest(payload || {}).slice(0, 24),
      // Observation identity is portable; mutation capabilities never are.
      observation: payload?.ok !== false && /^[a-f0-9]{64}$/iu.test(String(payload?.sha256 || ""))
        ? { ...Object.fromEntries(["path", "sha256", "canonicalProject", "resolvedRootType",
          "engineIdentity", "workspaceIdentity", "startLine", "endLine", "totalLines", "offsetBytes", "nextOffsetBytes", "size",
          "rangeSha256", "requestedEndLine", "requestedRangeComplete", "rangeComplete", "coverage", "range"]
          .filter(key => payload[key] !== undefined).map(key => [key, payload[key]])),
          canonicalProject: payload.canonicalProject || payload.activeProject } : {},
    };
    this.entries.set(key, entry);
    while (this.entries.size > this.maxEntries) {
      this.entries.delete(this.entries.keys().next().value);
    }
    return { repeatReceipt, resultDigest: entry.resultDigest };
  }
}

module.exports = {
  DirectRepeatCache,
  digest,
  stableValue,
};
