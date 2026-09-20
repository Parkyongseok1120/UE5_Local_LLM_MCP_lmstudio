"use strict";

const path = require("node:path");

const { sanitizeStructuredDurableValue } = require("./durable-memory-sanitizer.js");

function isRecord(value) {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function projectDescriptor(item, fallback = "") {
  const candidate = item?.canonicalProject || item?.activeProject || item?.projectPath
    || (String(item?.project || "").toLowerCase().endsWith(".uproject") ? item.project : "")
    || (item?.projectIdentity && pathApiFor(item.canonicalProjectRoot).isAbsolute(String(item.canonicalProjectRoot || ""))
      ? item.canonicalProjectRoot : "")
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

const MAX_OBSERVED_LINE_RANGES = 16;

function positiveInteger(value) {
  const number = Number(value);
  return Number.isSafeInteger(number) && number >= 1 ? number : null;
}

function normalizeObservedLineRanges(item) {
  const candidates = Array.isArray(item?.observedLineRanges)
    ? item.observedLineRanges.slice(0, 80)
    : [];
  const startLine = positiveInteger(item?.startLine);
  const endLine = positiveInteger(item?.endLine);
  if (startLine !== null && endLine !== null && endLine >= startLine) {
    candidates.push({ startLine, endLine });
  }
  const ranges = candidates.map((range) => ({
    startLine: positiveInteger(range?.startLine),
    endLine: positiveInteger(range?.endLine),
  })).filter((range) => (
    range.startLine !== null && range.endLine !== null && range.endLine >= range.startLine
  )).sort((left, right) => left.startLine - right.startLine || left.endLine - right.endLine);
  const merged = [];
  for (const range of ranges) {
    const previous = merged.at(-1);
    if (previous && range.startLine <= previous.endLine + 1) {
      previous.endLine = Math.max(previous.endLine, range.endLine);
    } else {
      merged.push({ startLine: range.startLine, endLine: range.endLine });
    }
  }
  if (merged.length <= MAX_OBSERVED_LINE_RANGES) return merged;
  const headCount = Math.ceil(MAX_OBSERVED_LINE_RANGES / 2);
  return [...merged.slice(0, headCount), ...merged.slice(-(MAX_OBSERVED_LINE_RANGES - headCount))];
}

function lineCoverageState(ranges, totalLines) {
  if (!ranges.length) return undefined;
  return ranges.length === 1 && ranges[0].startLine === 1
    && totalLines !== null && ranges[0].endLine >= totalLines
    ? "complete"
    : "partial";
}

function fileObservation(item, fallbackProject = "", fallbackOperation = "observed") {
  if (!isRecord(item) || !item.path) return null;
  const descriptor = projectDescriptor(item, fallbackProject);
  const canonicalPath = canonicalFilePath(item, descriptor);
  if (!descriptor || !canonicalPath) return null;
  const sha256AtObservation = item.sha256AtObservation || item.sha256 || undefined;
  const observedLineRanges = sha256AtObservation ? normalizeObservedLineRanges(item) : [];
  const totalLinesAtObservation = positiveInteger(item.totalLinesAtObservation ?? item.totalLines);
  return sanitizeStructuredDurableValue({
    canonicalProject: descriptor || undefined,
    canonicalProjectRoot: projectRoot(descriptor) || undefined,
    canonicalPath: canonicalPath || undefined,
    path: String(item.path),
    observationState: normalizedObservationState(item, fallbackOperation),
    sha256AtObservation,
    previousSha256AtObservation: item.previousSha256AtObservation || item.previousSha256 || undefined,
    lastObservedAt: item.lastObservedAt || item.snapshotCapturedAt || undefined,
    observedLineRanges: observedLineRanges.length ? observedLineRanges : undefined,
    totalLinesAtObservation: observedLineRanges.length ? totalLinesAtObservation || undefined : undefined,
    readCoverageState: lineCoverageState(observedLineRanges, totalLinesAtObservation),
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
    const previousHash = String(previous?.sha256AtObservation || "");
    const currentHash = String(observation.sha256AtObservation || "");
    const sameObservedVersion = Boolean(previous && previousHash && (!currentHash || currentHash === previousHash));
    const observedLineRanges = normalizeObservedLineRanges({
      observedLineRanges: [
        ...(sameObservedVersion ? previous.observedLineRanges || [] : []),
        ...(observation.observedLineRanges || []),
      ],
    });
    const totalLinesAtObservation = positiveInteger(observation.totalLinesAtObservation)
      || (sameObservedVersion ? positiveInteger(previous.totalLinesAtObservation) : null);
    if (observations.has(identity)) observations.delete(identity);
    const merged = {
      ...previous,
      ...observation,
      mutationSnapshotState: "fresh_read_required",
    };
    if (observedLineRanges.length) {
      merged.observedLineRanges = observedLineRanges;
      if (totalLinesAtObservation !== null) merged.totalLinesAtObservation = totalLinesAtObservation;
      merged.readCoverageState = lineCoverageState(observedLineRanges, totalLinesAtObservation);
    } else {
      delete merged.observedLineRanges;
      delete merged.totalLinesAtObservation;
      delete merged.readCoverageState;
    }
    observations.set(identity, merged);
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
  normalizeObservedLineRanges,
  migratePriorFileObservations,
  projectDescriptor,
  projectRoot,
};
