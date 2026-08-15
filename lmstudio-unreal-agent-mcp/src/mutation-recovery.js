"use strict";

const crypto = require("crypto");

const TRANSPORT_ARGUMENT_KEYS = new Set([
  "taskAuthorization",
  "task_authorization",
  "taskSessionId",
  "task_session_id",
  "authToken",
  "auth_token",
  "planId",
  "plan_id",
  "planRevision",
  "plan_revision",
  "activeSliceId",
  "active_slice_id",
  "routeHash",
  "route_hash",
  "routePhase",
  "route_phase",
  "ownerCapability",
  "owner_capability",
  "conversationId",
  "conversation_id",
]);

function stableStringify(value) {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) {
    return `[${value.map((item) => stableStringify(item)).join(",")}]`;
  }
  const keys = Object.keys(value).sort();
  return `{${keys.map((key) => `${JSON.stringify(key)}:${stableStringify(value[key])}`).join(",")}}`;
}

function normalizeSemanticValue(value, key = "") {
  if (Array.isArray(value)) {
    return value.map((item) => normalizeSemanticValue(item));
  }
  if (value && typeof value === "object") {
    const normalized = {};
    for (const childKey of Object.keys(value).sort()) {
      if (TRANSPORT_ARGUMENT_KEYS.has(childKey)) continue;
      const childValue = value[childKey];
      if (childValue === undefined) continue;
      normalized[childKey] = normalizeSemanticValue(childValue, childKey);
    }
    return normalized;
  }
  const normalizedKey = String(key || "")
    .replace(/([a-z0-9])([A-Z])/g, "$1_$2")
    .toLowerCase();
  if (typeof value === "string" && /(?:^|_)(?:path|root)$/u.test(normalizedKey)) {
    return value.replace(/\\/g, "/");
  }
  return value;
}

function normalizeSemanticArguments(args = {}) {
  const source = args && typeof args === "object" && !Array.isArray(args) ? args : {};
  return normalizeSemanticValue(source);
}

function stableMutationCallFingerprint(toolName, args = {}) {
  return crypto
    .createHash("sha256")
    .update(String(toolName || ""))
    .update("\u0000")
    .update(stableStringify(normalizeSemanticArguments(args)))
    .digest("hex");
}

function lineAtOffset(content, offset) {
  if (!Number.isInteger(offset) || offset < 0) return 1;
  return content.slice(0, offset).split("\n").length;
}

function identifierTokens(value) {
  return new Set(
    String(value || "")
      .toLocaleLowerCase("en-US")
      .match(/[\p{L}\p{N}_]{3,}/gu) || []
  );
}

function bestApproximateLine(lines, oldText) {
  const candidateLines = String(oldText || "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  const targetTokens = identifierTokens(candidateLines.slice(0, 4).join(" "));
  if (!targetTokens.size) return 1;
  let bestLine = 1;
  let bestScore = 0;
  for (let index = 0; index < lines.length; index += 1) {
    const observed = identifierTokens(lines[index]);
    let overlap = 0;
    for (const token of targetTokens) {
      if (observed.has(token)) overlap += 1;
    }
    const score = overlap / targetTokens.size;
    if (score > bestScore) {
      bestScore = score;
      bestLine = index + 1;
    }
  }
  return bestLine;
}

/**
 * Return one concrete, bounded range around the closest useful match.  The
 * range is intentionally independent of a repository/project layout.
 */
function boundedRecoveryRead(pathValue, contentValue, oldTextValue, options = {}) {
  const content = String(contentValue || "").replace(/\r\n/g, "\n");
  const oldText = String(oldTextValue || "").replace(/\r\n/g, "\n");
  const lines = content.split("\n");
  const totalLines = Math.max(1, lines.length);
  let anchorLine = 1;

  const exactOffset = oldText ? content.indexOf(oldText) : -1;
  if (exactOffset >= 0) {
    anchorLine = lineAtOffset(content, exactOffset);
  } else {
    const firstUsefulLine = oldText
      .split("\n")
      .map((line) => line.trim())
      .find(Boolean) || "";
    const partialOffset = firstUsefulLine ? content.indexOf(firstUsefulLine) : -1;
    anchorLine = partialOffset >= 0
      ? lineAtOffset(content, partialOffset)
      : bestApproximateLine(lines, oldText);
  }

  const maxLines = Math.max(20, Math.min(120, Number(options.maxLines || 80)));
  const before = Math.max(0, Math.min(maxLines - 1, Number(options.contextBefore ?? 20)));
  let startLine = Math.max(1, anchorLine - before);
  let endLine = Math.min(totalLines, startLine + maxLines - 1);
  if (endLine - startLine + 1 < maxLines) {
    startLine = Math.max(1, endLine - maxLines + 1);
  }
  return {
    path: String(pathValue || ""),
    startLine,
    endLine,
    detailLevel: "compact",
  };
}

function exactMutationCallGuard(toolName, args = {}) {
  const fingerprint = stableMutationCallFingerprint(toolName, args);
  return {
    failedCallFingerprint: fingerprint,
    forbiddenCallFingerprints: [fingerprint],
    forbiddenCalls: [{ tool: String(toolName || ""), fingerprint }],
  };
}

function bundleTargetFiles(bundle = {}) {
  return [...(Array.isArray(bundle.files) ? bundle.files : []), ...(Array.isArray(bundle.patches) ? bundle.patches : [])]
    .map((entry) => String(entry?.path || "").replace(/\\/g, "/").replace(/^\/+/, ""))
    .filter(Boolean)
    .filter((value, index, values) => values.indexOf(value) === index)
    .slice(0, 4);
}

function bundleFailureRecovery(tx = {}, bundle = {}, options = {}) {
  const rollback = tx.rollback && typeof tx.rollback === "object" ? tx.rollback : {};
  const externalChangeDetected = rollback.externalChangeDetected || tx.externalChangeDetected || [];
  const unrestoredPaths = rollback.unrestoredPaths || tx.unrestoredPaths || [];
  const rollbackErrors = rollback.rollbackErrors || tx.rollbackErrors || [];
  const rollbackIncomplete = Boolean(
    rollback.rollbackIncomplete
    || tx.rollbackIncomplete
    || externalChangeDetected.length
    || unrestoredPaths.length
    || rollbackErrors.length
  );
  const rolledBack = Boolean(rollback.rolledBack ?? tx.rolledBack) && !rollbackIncomplete;
  const semanticFailure = Boolean(tx.validation?.semanticGuard);
  const staticFailure = !semanticFailure && /static validation/i.test(String(tx.error || ""));
  const targetFiles = bundleTargetFiles(bundle);

  if (rollbackIncomplete) {
    return {
      errorCode: externalChangeDetected.length
        ? "BUNDLE_EXTERNAL_CHANGE_DETECTED"
        : "BUNDLE_ROLLBACK_INCOMPLETE",
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
      targetFiles,
      message: "The bundle rollback could not restore every pre-image. Reconcile the current files with an exact task checkpoint rebase.",
      rolledBack,
      rollbackIncomplete,
    };
  }

  if (tx.lockFailure) {
    return {
      errorCode: "BUNDLE_PATH_LOCKED",
      status: "repair_planning_required",
      scopeDisposition: "in_slice",
      requiredTool: {
        name: "unreal_code_sketch_claim_validate",
        args: targetFiles.length ? { targetFiles } : {},
      },
      targetFiles,
      message: "A concurrent mutation owns one bundle path. No bundle write was published; validate a bounded repair after refreshing the affected source evidence.",
      rolledBack: true,
      rollbackIncomplete: false,
    };
  }

  return {
    errorCode: semanticFailure
      ? "BUNDLE_SEMANTIC_VALIDATION_FAILED"
      : staticFailure
        ? "BUNDLE_STATIC_VALIDATION_FAILED"
        : "BUNDLE_TRANSACTION_FAILED",
    status: "repair_planning_required",
    scopeDisposition: "in_slice",
    requiredTool: {
      name: "unreal_code_sketch_claim_validate",
      args: targetFiles.length ? { targetFiles } : {},
    },
    targetFiles,
    message: semanticFailure
      ? "The prospective bundle violated a semantic mutation guard and was rolled back. Validate a corrected bounded repair claim."
      : staticFailure
        ? "The prospective bundle failed static validation and was rolled back. Validate a corrected bounded repair claim."
        : "The bundle transaction failed without publishing a mutation. Revalidate the bounded repair before constructing a new call.",
    rolledBack,
    rollbackIncomplete: false,
  };
}

module.exports = {
  stableMutationCallFingerprint,
  boundedRecoveryRead,
  exactMutationCallGuard,
  bundleFailureRecovery,
  bundleTargetFiles,
  normalizeSemanticArguments,
};
