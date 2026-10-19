"use strict";
const { normalizedChange, boundChangeBodies } = require("./change-evidence-memory.js");

const path = require("node:path");

const { sanitizeStructuredDurableValue } = require("./durable-memory-sanitizer.js");
const { normalizeRanges, sourceCoverage, normalizeSourceCoverage } = require("./evidence-ranges.js");

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

// The one owner of what a retained file observation lets the next edit do.
// Unreal's server accepts expectedHash and itself re-checks the current hash and
// the exact oldText, so a usable observed hash needs no fresh read first (like
// Claude Code's exact old_string check). Unity edits need a live HMAC receipt,
// and a conflict, deletion or unavailable read always needs a fresh read.
const EXPECTED_HASH_EDIT_OK = "expectedHash_edit_ok";
const FRESH_READ_REQUIRED = "fresh_read_required";

function mutationSnapshotState(item, descriptor = projectDescriptor(item)) {
  const state = normalizedObservationState(item || {}, "observed");
  const hash = item?.sha256AtObservation || item?.sha256;
  const usable = /^[A-Fa-f0-9]{64}$/u.test(String(hash || "")) && item?.ok !== false && !item?.errorCode
    && !["deleted", "unavailable", "conflict_observed"].includes(state);
  return usable && String(descriptor || "").toLowerCase().endsWith(".uproject") ? EXPECTED_HASH_EDIT_OK : FRESH_READ_REQUIRED;
}

// How a retained expectedHash_edit_ok observation is used; shown only when one is present.
function editWithoutRereadNote(files) {
  return (files || []).some(file => file?.mutationSnapshotState === EXPECTED_HASH_EDIT_OK)
    ? "expectedHash_edit_ok: edit with expectedHash=sha256AtObservation and the exact oldText; the server re-checks both. Re-read only that range after FILE_VERSION_CONFLICT or OCCURRENCE_MISMATCH, or when the exact oldText is not in view."
    : "";
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
  if (["observed", "modified", "deleted", "conflict_observed", "unavailable"].includes(explicit)) return explicit;
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
  const merged = normalizeRanges(ranges.map(r => [r.startLine, r.endLine]))
    .map(([startLine, endLine]) => ({ startLine, endLine }));
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
  const observationState = normalizedObservationState(item, fallbackOperation);
  const sha256AtObservation = observationState === "unavailable" ? undefined : item.sha256AtObservation || item.sha256 || undefined;
  const usableRead = sha256AtObservation && item.ok !== false && !item.errorCode
    && !["deleted", "unavailable", "conflict_observed"].includes(observationState);
  const observedLineRanges = usableRead ? normalizeObservedLineRanges(item) : [];
  const totalLinesAtObservation = positiveInteger(item.totalLinesAtObservation ?? item.totalLines);
  const coverage = usableRead ? sourceCoverage(item) : { schemaVersion: 1, entries: [] };
  return sanitizeStructuredDurableValue({
    canonicalProject: descriptor || undefined,
    canonicalProjectRoot: projectRoot(descriptor) || undefined,
    canonicalPath: canonicalPath || undefined,
    path: String(item.path),
    observationState,
    sha256AtObservation,
    previousSha256AtObservation: item.previousSha256AtObservation || item.previousSha256 || undefined,
    lastObservedAt: item.lastObservedAt || item.snapshotCapturedAt || undefined,
    observedLineRanges: observedLineRanges.length ? observedLineRanges : undefined,
    totalLinesAtObservation: observedLineRanges.length ? totalLinesAtObservation || undefined : undefined,
    readCoverageState: lineCoverageState(observedLineRanges, totalLinesAtObservation),
    sourceCoverage: coverage.entries.length ? coverage : undefined,
    mutationSnapshotState: mutationSnapshotState({ ...item, sha256AtObservation }, descriptor),
    changeEvidence: observationState === "unavailable" ? undefined : normalizedChange(item.changeEvidence, item),
    changeReadVerified: item.changeReadVerified === true ? true : undefined,
    changeEvidenceInvalidated: item.changeEvidenceInvalidated === true ? true : undefined,
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
    const sameObservedVersion = Boolean(observation.observationState !== "unavailable"
      && previous && previousHash && (!currentHash || currentHash === previousHash));
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
      mutationSnapshotState: mutationSnapshotState(observation, observation.canonicalProject),
    };
    // Byte/source coverage requires an explicit equal version. Missing versions
    // never inherit an old interval or continuation cursor.
    const source = normalizeSourceCoverage([
      ...(sameObservedVersion && currentHash === previousHash ? previous?.sourceCoverage?.entries || [] : []),
      ...(observation.sourceCoverage?.entries || []),
    ]);
    delete merged.sourceCoverage;
    if (source.entries.length && !["deleted", "unavailable", "conflict_observed"].includes(observation.observationState))
      merged.sourceCoverage = source;
    if (observation.observationState === "unavailable") {
      delete merged.sha256AtObservation; delete merged.changeReadVerified;
    }
    // Read coverage has a more permissive legacy contract; change evidence
    // requires an explicit matching hash and a paired read, never spread carry.
    delete merged.changeEvidence;
    if (!observation.changeEvidenceInvalidated && !["deleted", "conflict_observed", "outcome_unknown", "unavailable"].includes(observation.observationState)) {
      if (observation.changeEvidence) merged.changeEvidence = observation.changeEvidence;
      else if (observation.changeReadVerified && currentHash && previousHash === currentHash && previous?.changeEvidence)
        merged.changeEvidence = previous.changeEvidence;
    }
    if (observedLineRanges.length) {
      merged.observedLineRanges = observedLineRanges;
      if (totalLinesAtObservation !== null) merged.totalLinesAtObservation = totalLinesAtObservation;
      merged.readCoverageState = lineCoverageState(observedLineRanges, totalLinesAtObservation);
    } else {
      delete merged.observedLineRanges;
      delete merged.totalLinesAtObservation;
      delete merged.readCoverageState;
    }
    // New coverage owns the compatibility view too. Never merge two query or
    // provider scopes back into a misleading legacy "complete" line range.
    if (previous?.sourceCoverage || observation.sourceCoverage || item.sourceCoverage) {
      delete merged.observedLineRanges;
      delete merged.totalLinesAtObservation;
      delete merged.readCoverageState;
      const entries = merged.sourceCoverage?.entries || [];
      const lines = entries.filter(entry => entry.unit === "line");
      if (lines.length === 1) {
        merged.observedLineRanges = lines[0].ranges.map(([startLine, endLine]) => ({ startLine, endLine }));
        if (lines[0].total !== null) merged.totalLinesAtObservation = lines[0].total;
      }
      if (entries.length) merged.readCoverageState = entries.some(entry => entry.state === "complete") ? "complete" : "partial";
    }
    observations.set(identity, merged);
  }
  // One relative path seen in two checkouts: which hash an edit means is
  // ambiguous, so neither observation authorizes an edit without a fresh read.
  const all = [...observations.values()];
  const projectsByPath = new Map();
  for (const item of all) projectsByPath.set(item.path, new Set([...(projectsByPath.get(item.path) || []), item.canonicalProject]));
  const settled = all.map(item => (projectsByPath.get(item.path).size > 1 && item.mutationSnapshotState === EXPECTED_HASH_EDIT_OK
    ? { ...item, mutationSnapshotState: FRESH_READ_REQUIRED } : item));
  return boundChangeBodies(settled.slice(-maxItems));
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
  editWithoutRereadNote,
  mutationSnapshotState,
  projectDescriptor,
  projectRoot,
};
