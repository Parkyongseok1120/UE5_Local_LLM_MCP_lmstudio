"use strict";

function normalizeValidationScopePath(value) {
  const normalized = String(value || "")
    .trim()
    .replace(/\\/g, "/")
    .replace(/^project:\/\//i, "")
    .replace(/^\.\//, "")
    .replace(/^\/+|\/+$/g, "");
  if (!normalized || normalized.split("/").includes("..")) return "";
  const parts = normalized.split("/");
  const sourceIndex = parts.findIndex((part) => (
    ["source", "plugins"].includes(part.toLowerCase())
  ));
  if (sourceIndex < 0) return "";
  return parts.slice(sourceIndex).join("/");
}

function deriveValidationScope(taskState, mutationGeneration, options = {}) {
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
  const normalizedSelected = selected.map(normalizeValidationScopePath).filter(Boolean);
  const normalizedModified = checkpointFiles.map(normalizeValidationScopePath).filter(Boolean);
  const modifiedKeys = new Set(normalizedModified.map((item) => item.toLowerCase()));
  const selectedModified = normalizedSelected.filter(
    (item) => modifiedKeys.has(item.toLowerCase())
  );
  const targets = [...new Set(
    normalizedSelected.length ? selectedModified : normalizedModified
  )].slice(0, 4);
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
