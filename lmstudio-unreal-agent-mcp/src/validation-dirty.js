"use strict";

const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

const dirtyByProject = new Map();

function projectKey(projectRoot) {
  return path.resolve(String(projectRoot || "")).toLowerCase();
}

function stateFilePath(projectRoot) {
  return path.join(path.resolve(String(projectRoot || "")), ".agent", "state", "validation.json");
}

function hashFile(absPath) {
  try {
    const data = fs.readFileSync(absPath);
    return crypto.createHash("sha256").update(data).digest("hex");
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
    return null;
  }
}

function savePersisted(projectRoot, entry) {
  const filePath = stateFilePath(projectRoot);
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  const temp = `${filePath}.tmp`;
  fs.writeFileSync(temp, JSON.stringify(entry, null, 2), "utf8");
  fs.renameSync(temp, filePath);
}

function hydrateFromDisk(projectRoot) {
  const key = projectKey(projectRoot);
  if (dirtyByProject.has(key)) {
    return;
  }
  const persisted = loadPersisted(projectRoot);
  if (persisted && persisted.validationRequired) {
    dirtyByProject.set(key, {
      validationRequired: true,
      unvalidatedPaths: [...(persisted.unvalidatedPaths || [])],
      fileHashes: { ...(persisted.fileHashes || {}) },
      reason: persisted.reason || "validation skipped",
    });
  }
}

function getDirtyState(projectRoot) {
  hydrateFromDisk(projectRoot);
  const key = projectKey(projectRoot);
  const entry = dirtyByProject.get(key) || { validationRequired: false, unvalidatedPaths: [] };
  return {
    validationRequired: Boolean(entry.validationRequired),
    unvalidatedPaths: [...(entry.unvalidatedPaths || [])],
  };
}

function markUnvalidated(projectRoot, relPath, reason = "validation skipped") {
  hydrateFromDisk(projectRoot);
  const key = projectKey(projectRoot);
  const entry = dirtyByProject.get(key) || {
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
      entry.fileHashes = entry.fileHashes || {};
      entry.fileHashes[normalized] = digest;
    }
  }
  entry.reason = reason;
  dirtyByProject.set(key, entry);
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
  const key = projectKey(projectRoot);
  dirtyByProject.delete(key);
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
