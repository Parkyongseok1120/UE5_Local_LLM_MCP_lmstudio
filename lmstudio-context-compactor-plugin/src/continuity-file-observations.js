"use strict";

const path = require("node:path");

const { sanitizeStructuredDurableValue } = require("./durable-memory-sanitizer.js");

function isRecord(value) {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function projectDescriptor(item, fallback = "") {
  const candidate = item?.canonicalProject || item?.activeProject || item?.projectPath
    || (String(item?.project || "").toLowerCase().endsWith(".uproject") ? item.project : "")
    || fallback;
  return candidate ? String(candidate) : "";
}

function pathApiFor(value) {
  return /^[A-Za-z]:[\\/]|^\\\\/u.test(String(value || "")) ? path.win32 : path;
}

function projectRoot(descriptor) {
  if (!descriptor) return "";
  return String(descriptor).toLowerCase().endsWith(".uproject")
    ? pathApiFor(descriptor).dirname(descriptor)
    : String(descriptor);
}

function isContainedPath(pathApi, root, candidate) {
  const relative = pathApi.relative(root, candidate);
  return relative === ""
    || (relative !== ".." && !relative.startsWith(`..${pathApi.sep}`) && !pathApi.isAbsolute(relative));
}

function canonicalFilePath(item, descriptor) {
  const display = String(item?.path || "");
  const pathApi = pathApiFor(descriptor || display);
  const projectBase = projectRoot(descriptor);
  if (String(item?.resolvedRootType || "").toLowerCase() === "workspace"
    || /^workspace:\/\//iu.test(display)) return "";
  const explicit = item?.canonicalPath || item?.absolutePath;
  if (explicit) {
    const candidate = pathApi.resolve(String(explicit));
    if (!descriptor || !pathApi.isAbsolute(projectBase)) return "";
    return isContainedPath(pathApi, projectBase, candidate) ? candidate : "";
  }
  if (pathApi.isAbsolute(display)) {
    const candidate = pathApi.resolve(display);
    return descriptor && pathApi.isAbsolute(projectBase) && isContainedPath(pathApi, projectBase, candidate)
      ? candidate
      : "";
  }
  const projectScoped = /^project:\/\//iu.test(display);
  const foreignScheme = /^[A-Za-z][A-Za-z0-9+.-]*:\/\//u.test(display) && !projectScoped;
  if (descriptor && pathApi.isAbsolute(projectBase) && !foreignScheme) {
    const pathApi = pathApiFor(descriptor);
    const relative = display.replace(/^project:\/\//iu, "").replace(/[\\/]+/g, pathApi.sep);
    const candidate = pathApi.resolve(projectBase, relative);
    return isContainedPath(pathApi, projectBase, candidate) ? candidate : "";
  }
  return "";
}

function normalizedObservationState(item, fallbackOperation) {
  const explicit = String(item?.observationState || "").toLowerCase();
  if (["observed", "modified", "deleted", "conflict_observed"].includes(explicit)) return explicit;
  const operation = String(item?.operation || fallbackOperation || "observed").toLowerCase();
  const errorCode = String(item?.errorCode || "").toUpperCase();
  if (errorCode === "FILE_VERSION_CONFLICT" || operation.includes("conflict")) return "conflict_observed";
  if (/(?:delete|deleted|trash)/u.test(operation)) return "deleted";
  if (/(?:create|created|replace|replaced|patch|write|written|bundle_applied|modified)/u.test(operation)) {
    return "modified";
  }
  return "observed";
}

function fileObservation(item, fallbackProject = "", fallbackOperation = "observed") {
  if (!isRecord(item) || !item.path) return null;
  const descriptor = projectDescriptor(item, fallbackProject);
  const canonicalPath = canonicalFilePath(item, descriptor);
  if (!descriptor || !canonicalPath) return null;
  return sanitizeStructuredDurableValue({
    canonicalProject: descriptor || undefined,
    canonicalProjectRoot: projectRoot(descriptor) || undefined,
    canonicalPath: canonicalPath || undefined,
    path: String(item.path),
    observationState: normalizedObservationState(item, fallbackOperation),
    sha256AtObservation: item.sha256AtObservation || item.sha256 || undefined,
    previousSha256AtObservation: item.previousSha256AtObservation || item.previousSha256 || undefined,
    lastObservedAt: item.lastObservedAt || item.snapshotCapturedAt || undefined,
    mutationSnapshotState: "fresh_read_required",
  });
}

function coalesceFileObservations(items, maxItems = 16, fallbackProject = "") {
  const observations = new Map();
  for (const item of items || []) {
    const observation = fileObservation(item, fallbackProject);
    if (!observation) continue;
    const identity = JSON.stringify([
      observation.canonicalProject || "",
      observation.canonicalPath || observation.path || "",
    ]);
    const previous = observations.get(identity);
    if (observations.has(identity)) observations.delete(identity);
    observations.set(identity, {
      ...previous,
      ...observation,
      mutationSnapshotState: "fresh_read_required",
    });
  }
  return [...observations.values()].slice(-maxItems);
}

function migratePriorFileObservations(items, previousState, maxItems = 16) {
  const fallbackProject = String(previousState?.activeProject?.descriptor || "");
  const schemaVersion = Number(previousState?.schemaVersion || 1);
  const candidates = (items || []).map((item) => {
    if (!isRecord(item)) return null;
    if (schemaVersion >= 2 || projectDescriptor(item)) return item;

    // A v1 project:// path plus the checkpoint's final active project is not
    // enough to prove clone identity: v1 could observe A and later activate B.
    // An item-local absolute path contained by that exact project is evidence.
    const explicitPath = item.canonicalPath || item.absolutePath;
    if (!explicitPath || !fallbackProject || !canonicalFilePath(item, fallbackProject)) return null;
    return { ...item, canonicalProject: fallbackProject };
  }).filter(Boolean);
  return coalesceFileObservations(
    candidates,
    maxItems,
    schemaVersion >= 2 ? fallbackProject : "",
  );
}

module.exports = {
  canonicalFilePath,
  coalesceFileObservations,
  fileObservation,
  normalizedObservationState,
  pathApiFor,
  isContainedPath,
  migratePriorFileObservations,
  projectDescriptor,
  projectRoot,
};
