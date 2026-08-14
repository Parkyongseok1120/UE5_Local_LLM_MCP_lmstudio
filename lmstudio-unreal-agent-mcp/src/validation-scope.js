"use strict";

const { filesystemPathIdentity } = require("./filesystem-path-identity");

function normalizeValidationScopePath(value, hostPlatform = process.platform) {
  const normalized = String(value || "")
    .trim()
    .replace(/\\/g, "/")
    .replace(/^project:\/\//i, "")
    .replace(/^\.\//, "")
    .replace(/^\/+|\/+$/g, "");
  if (!normalized || normalized.split("/").includes("..")) return "";
  const parts = normalized.split("/");
  const sourceRootNames = new Set([
    filesystemPathIdentity("Source", hostPlatform),
    filesystemPathIdentity("Plugins", hostPlatform),
  ]);
  const sourceIndex = parts.findIndex((part) => (
    sourceRootNames.has(filesystemPathIdentity(part, hostPlatform))
  ));
  if (sourceIndex < 0) return "";
  return parts.slice(sourceIndex).join("/");
}

function deriveValidationScope(taskState, mutationGeneration, options = {}) {
  const hostPlatform = String(options.hostPlatform || process.platform);
  if (options.fullAudit === true || options.taskBound !== true) {
    return { kind: "full_audit", targets: [] };
  }
  if (!taskState || String(taskState.status || "") !== "running") {
    return {
      kind: "task_scope_unavailable",
      targets: [],
      reason: "active task state is unavailable",
    };
  }
  const checkpoint = taskState.continuity?.checkpoint
    && typeof taskState.continuity.checkpoint === "object"
    ? taskState.continuity.checkpoint
    : {};
  if (Number(checkpoint.mutationGeneration || 0) !== Number(mutationGeneration || 0)) {
    return {
      kind: "task_scope_unavailable",
      targets: [],
      reason: "task checkpoint does not match the current mutation generation",
    };
  }
  const selectedSlice = taskState.toolRoute?.selectedSlice
    && typeof taskState.toolRoute.selectedSlice === "object"
    ? taskState.toolRoute.selectedSlice
    : {};
  const selected = Array.isArray(selectedSlice.files) ? selectedSlice.files : [];
  const checkpointFiles = Array.isArray(checkpoint.modifiedFiles)
    ? checkpoint.modifiedFiles
    : [];
  const normalizedSelected = selected
    .map((item) => normalizeValidationScopePath(item, hostPlatform))
    .filter(Boolean);
  const normalizedModified = checkpointFiles
    .map((item) => normalizeValidationScopePath(item, hostPlatform))
    .filter(Boolean);
  const modifiedKeys = new Set(normalizedModified.map(
    (item) => filesystemPathIdentity(item, hostPlatform)
  ));
  const selectedModified = normalizedSelected.filter(
    (item) => modifiedKeys.has(filesystemPathIdentity(item, hostPlatform))
  );
  const targetByIdentity = new Map();
  for (const target of (normalizedSelected.length ? selectedModified : normalizedModified)) {
    const identity = filesystemPathIdentity(target, hostPlatform);
    if (!targetByIdentity.has(identity)) targetByIdentity.set(identity, target);
  }
  const targets = [...targetByIdentity.values()].slice(0, 4);
  return targets.length
    ? { kind: "task_slice", targets }
    : {
      kind: "task_scope_unavailable",
      targets: [],
      reason: "current task slice has no source targets",
    };
}

module.exports = {
  deriveValidationScope,
  normalizeValidationScopePath,
};
