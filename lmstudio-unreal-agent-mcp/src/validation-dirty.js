"use strict";

const path = require("path");

const dirtyByProject = new Map();

function projectKey(projectRoot) {
  return path.resolve(String(projectRoot || "")).toLowerCase();
}

function getDirtyState(projectRoot) {
  const key = projectKey(projectRoot);
  const entry = dirtyByProject.get(key) || { validationRequired: false, unvalidatedPaths: [] };
  return {
    validationRequired: Boolean(entry.validationRequired),
    unvalidatedPaths: [...(entry.unvalidatedPaths || [])],
  };
}

function markUnvalidated(projectRoot, relPath, reason = "validation skipped") {
  const key = projectKey(projectRoot);
  const entry = dirtyByProject.get(key) || { validationRequired: false, unvalidatedPaths: [] };
  entry.validationRequired = true;
  const normalized = String(relPath || "").replace(/\\/g, "/");
  if (normalized && !entry.unvalidatedPaths.includes(normalized)) {
    entry.unvalidatedPaths.push(normalized);
  }
  entry.reason = reason;
  dirtyByProject.set(key, entry);
  return getDirtyState(projectRoot);
}

function clearValidated(projectRoot) {
  const key = projectKey(projectRoot);
  dirtyByProject.delete(key);
  return getDirtyState(projectRoot);
}

function requireCleanOrFail(projectRoot, { override = false, auditNote = "" } = {}) {
  const state = getDirtyState(projectRoot);
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
};
