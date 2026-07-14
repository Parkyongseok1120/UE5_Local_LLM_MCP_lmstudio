"use strict";

const fs = require("fs");
const path = require("path");
const { atomicWriteJson } = require("./atomic-io");

function projectKey(projectRoot) {
  return path.resolve(String(projectRoot || "")).toLowerCase();
}

function stateFilePath(projectRoot) {
  return path.join(path.resolve(String(projectRoot || "")), ".agent", "state", "validation.json");
}

function hashFile(absPath) {
  try {
    const data = fs.readFileSync(absPath);
    return require("crypto").createHash("sha256").update(data).digest("hex");
  } catch {
    return "";
  }
}

function loadPersisted(projectRoot) {
  const filePath = stateFilePath(projectRoot);
  if (!fs.existsSync(filePath)) {
    return null;
  }
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch {
    return { validationRequired: true, unvalidatedPaths: [], reason: "state_corrupt", corrupt: true };
  }
}

function savePersisted(projectRoot, entry) {
  atomicWriteJson(stateFilePath(projectRoot), entry);
}

function getDirtyState(projectRoot) {
  const persisted = loadPersisted(projectRoot);
  if (!persisted) {
    return {
      validationRequired: false,
      unvalidatedPaths: [],
      reason: null,
      corrupt: false,
    };
  }
  if (persisted.corrupt || persisted.reason === "state_corrupt") {
    return {
      validationRequired: true,
      unvalidatedPaths: [...(persisted.unvalidatedPaths || [])],
      reason: persisted.reason || "state_corrupt",
      corrupt: true,
    };
  }
  return {
    validationRequired: Boolean(persisted.validationRequired),
    unvalidatedPaths: [...(persisted.unvalidatedPaths || [])],
    reason: persisted.reason || null,
    corrupt: false,
  };
}

function markUnvalidated(projectRoot, relPath, reason = "validation skipped") {
  const existing = loadPersisted(projectRoot);
  const entry = existing && !existing.corrupt
    ? {
        validationRequired: Boolean(existing.validationRequired),
        unvalidatedPaths: [...(existing.unvalidatedPaths || [])],
        fileHashes: { ...(existing.fileHashes || {}) },
        reason: existing.reason || reason,
      }
    : {
        validationRequired: false,
        unvalidatedPaths: [],
        fileHashes: {},
      };
  entry.validationRequired = true;
  const normalized = String(relPath || "").replace(/\\/g, "/");
  if (normalized && !entry.unvalidatedPaths.includes(normalized)) {
    entry.unvalidatedPaths.push(normalized);
    const absPath = path.join(path.resolve(projectRoot), normalized);
    const digest = hashFile(absPath);
    if (digest) {
      entry.fileHashes[normalized] = digest;
    }
  }
  entry.reason = reason;
  savePersisted(projectRoot, {
    validationRequired: entry.validationRequired,
    unvalidatedPaths: entry.unvalidatedPaths,
    fileHashes: entry.fileHashes || {},
    reason: entry.reason,
    updatedAt: new Date().toISOString(),
  });
  return getDirtyState(projectRoot);
}

function clearValidated(projectRoot) {
  const filePath = stateFilePath(projectRoot);
  try {
    if (fs.existsSync(filePath)) {
      fs.unlinkSync(filePath);
    }
  } catch {
    // Best-effort cleanup.
  }
  return getDirtyState(projectRoot);
}

function requireValidationProofOrOverride(mutation, { override = false, auditNote = "" } = {}) {
  const validatedGeneration = Number(mutation && mutation.validatedGeneration || 0);
  const mutationGeneration = Number(mutation && mutation.mutationGeneration || 0);
  if (validatedGeneration === mutationGeneration) {
    return { ok: true, overridden: false, validatedGeneration, mutationGeneration, auditNote: "" };
  }
  if (override) {
    return {
      ok: true,
      overridden: true,
      validatedGeneration,
      mutationGeneration,
      auditNote: String(auditNote || "Explicit validationOverride=true"),
    };
  }
  return {
    ok: false,
    overridden: false,
    validatedGeneration,
    mutationGeneration,
    error: "build blocked: validation proof stale.",
    errorCode: "VALIDATION_PROOF_STALE",
    retryable: false,
    stopCurrentWorkflow: true,
    nextSteps: ["Run static_validate_project after the latest edits before building."],
  };
}

function requireCleanOrFail(projectRoot, { override = false, auditNote = "" } = {}) {
  const state = getDirtyState(projectRoot);
  if (state.corrupt) {
    return {
      ok: false,
      state,
      error: "build blocked: validation state corrupt",
      nextSteps: [
        "Repair or delete .agent/state/validation.json, then run static_validate_project.",
      ],
    };
  }
  if (!state.validationRequired || override) {
    return { ok: true, state, auditNote };
  }
  return {
    ok: false,
    state,
    error: "build blocked: validation required after unvalidated writes",
    nextSteps: [
      "Run static_validate_project on the active project before building.",
      ...(auditNote ? [`Override note: ${auditNote}`] : []),
    ],
  };
}

module.exports = {
  getDirtyState,
  markUnvalidated,
  clearValidated,
  requireCleanOrFail,
  requireValidationProofOrOverride,
  stateFilePath,
};
