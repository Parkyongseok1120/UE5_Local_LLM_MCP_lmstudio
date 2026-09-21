"use strict";

const path = require("node:path");

const { clip } = require("./continuity-text.js");
const {
  coalesceFileObservations,
  fileObservation,
  pathApiFor,
  projectDescriptor,
  projectRoot,
} = require("./continuity-file-observations.js");
const {
  isEphemeralCapabilityKey,
  sanitizeDerivedOperationalRecord,
  sanitizeDerivedOperationalText,
  sanitizeRawCapabilityText,
  sanitizeStructuredDurableValue,
} = require("./durable-memory-sanitizer.js");

const INTERNAL_KEYS = new Set([
  "activeSliceId", "allowedTools", "authorizationBound", "claimLedger",
  "commitEligible", "control", "controlEpoch", "controlFingerprint",
  "evidenceBundle", "evidenceStateHash", "foreignHealthy", "ownerCapability",
  "pendingGates", "phase", "phaseState", "planRevision", "promptContract", "requiredTool",
  "routeHash", "routeOwnership", "serverControl", "sourceEvidence",
  "state", "stateHash", "synthesisEvidence", "synthesisLatch", "synthesisReadiness",
  "taskAuthorization", "taskLifecycle", "taskRouteOwnership", "toolRoute",
]);

const CONTROL_DIRECTIVES = new Set([
  "agentInstruction", "doNotRetry", "doNotRetryTools", "nextAction",
  "nextActionArgs", "nextActionIsTool", "recoveryActionRequired",
  "requiredNextAction", "requiredNextTool", "requiredNextToolArgs",
  "requiredSequence", "retryPolicy", "stopCurrentPhase", "stopCurrentWorkflow",
]);

const CONTROL_KEY_ROOTS = new Set(["control", "phase", "route", "state", "synthesis", "task"]);

function normalizeKeyToken(value) {
  return String(value || "").replace(/[^A-Za-z0-9]/g, "").toLowerCase();
}

const CONTROL_KEY_TOKENS = new Set(
  [...INTERNAL_KEYS, ...CONTROL_DIRECTIVES].map((key) => normalizeKeyToken(key)),
);

function isRecord(value) {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function keyRoot(value) {
  const separated = String(value || "").replace(/([a-z0-9])([A-Z])/g, "$1_$2");
  return String(separated.split(/[^A-Za-z0-9]+/, 1)[0] || "").toLowerCase();
}

function isControlKey(value) {
  const normalized = normalizeKeyToken(value);
  return CONTROL_KEY_TOKENS.has(normalized) || CONTROL_KEY_ROOTS.has(keyRoot(value));
}

function sanitizeRetainedString(value, maxChars = 4000) {
  const sanitized = String(value ?? "").replace(
    /[A-Za-z][A-Za-z0-9_]*/g,
    (token) => isControlKey(token) ? "[control-token-omitted]" : token,
  );
  return clip(sanitizeDerivedOperationalText(sanitized), maxChars);
}

function sanitizeRetainedStructureString(value) {
  return sanitizeRawCapabilityText(value);
}

function retainedDiagnosticList(value, maxItems = 8, maxChars = 320) {
  if (!Array.isArray(value)) return [];
  return value
    .slice(0, Math.max(0, maxItems))
    .map((item) => sanitizeRetainedString(item, maxChars))
    .filter(Boolean);
}

function exactProjectIdentity(value) {
  const descriptor = String(value || "");
  const pathApi = pathApiFor(descriptor);
  if (!pathApi.isAbsolute(descriptor)) return "";
  const resolved = pathApi.resolve(descriptor);
  return pathApi === path.win32 ? resolved.toLowerCase() : resolved;
}

function hasBoundUnityProject(value) {
  const root = String(value?.canonicalProjectRoot || "");
  return Boolean(value?.projectIdentity && pathApiFor(root).isAbsolute(root));
}

function stripControl(value, depth = 0) {
  if (depth > 8) return "[depth limited]";
  if (Array.isArray(value)) return value.slice(0, 40).map((item) => stripControl(item, depth + 1));
  if (!isRecord(value)) return typeof value === "string" ? sanitizeRetainedStructureString(value) : value;
  const out = {};
  for (const [key, item] of Object.entries(value)) {
    if (isControlKey(key) || isEphemeralCapabilityKey(key, item)) continue;
    out[key] = stripControl(item, depth + 1);
  }
  return out;
}

function retainedFileFact(file, fallbackOperation = "observed") {
  if (!isRecord(file) || !file.path) return null;
  const fact = {};
  for (const key of [
    "path", "operation", "observationState", "sha256", "sha256AtObservation",
    "previousSha256", "previousSha256AtObservation", "canonicalProject",
    "canonicalProjectRoot", "canonicalPath", "absolutePath", "projectRelativePath",
    "workspaceRelativePath", "resolvedRootType", "errorCode", "startLine", "endLine",
    "returnedLineCount", "nextStartLine", "totalLines", "hasMore", "observedLineRanges",
    "totalLinesAtObservation", "readCoverageState",
  ]) {
    if (file[key] !== undefined) fact[key] = file[key];
  }
  if (!fact.operation && !fact.observationState) fact.operation = fallbackOperation;
  if (file.lastObservedAt !== undefined || file.snapshotCapturedAt !== undefined) {
    fact.lastObservedAt = file.lastObservedAt || file.snapshotCapturedAt;
  }
  fact.mutationSnapshotState = "fresh_read_required";
  return sanitizeStructuredDurableValue(fact);
}

function scopeToolOutcome(parsed, fallbackProject = "", options = {}) {
  const scoped = { ...parsed };
  const explicitProject = projectDescriptor(scoped);
  const inconsistentProjectScope = options.inconsistentProjectScope === true;
  const descriptor = inconsistentProjectScope ? "" : (explicitProject || fallbackProject);
  const perCallProject = options.perCallProject === true;
  const fallbackSource = options.fallbackSource || "prior_checkpoint_fact";
  if (options.activeProjectCleared === true) {
    scoped.activeProject = null;
    scoped.activeProjectCleared = true;
  }
  if (inconsistentProjectScope) {
    for (const key of ["activeProject", "projectPath", "project", "canonicalProject", "canonicalProjectRoot", "projectIdentity"]) delete scoped[key];
    scoped.projectScopeState = "omitted_inconsistent_project_scope";
  }
  const hasFileIdentity = Boolean(
    scoped.path || scoped.canonicalPath || scoped.projectRelativePath || scoped.workspaceRelativePath,
  );
  let retainedObservation = null;
  if (descriptor) {
    scoped.canonicalProject = descriptor;
    scoped.canonicalProjectSource = perCallProject
      ? "tool_request_fact"
      : (explicitProject ? (hasBoundUnityProject(scoped) ? "unity_tool_result_fact" : "tool_result_fact") : fallbackSource);
    retainedObservation = fileObservation(scoped, descriptor);
    if (retainedObservation) {
      scoped.canonicalProjectRoot = retainedObservation.canonicalProjectRoot;
      scoped.canonicalPath = retainedObservation.canonicalPath;
      if (scoped.operation || scoped.sha256 || scoped.previousSha256
        || scoped.errorCode === "FILE_VERSION_CONFLICT") {
        scoped.mutationSnapshotState = "fresh_read_required";
      }
    }
  }
  if (hasFileIdentity && !retainedObservation) {
    for (const key of [
      "path", "canonicalProject", "canonicalProjectRoot", "canonicalPath", "projectRelativePath",
      "workspaceRelativePath", "sha256", "previousSha256", "lastObservedAt", "mutationSnapshotState",
    ]) delete scoped[key];
    scoped.fileObservationState = "omitted_non_project_scope";
  }
  if (!descriptor && perCallProject) {
    scoped.canonicalProjectSource = fallbackSource;
  }
  if (Array.isArray(scoped.files)) {
    const files = descriptor
      ? scoped.files.map((file) => fileObservation({
        resolvedRootType: scoped.resolvedRootType,
        ...file,
      }, descriptor, scoped.operation || "observed")).filter(Boolean)
      : [];
    if (files.length) scoped.files = files;
    else delete scoped.files;
  }
  return sanitizeStructuredDurableValue(scoped);
}

const MAX_TOOL_RESULT_CONTENT_CHARS = 4 * 1024 * 1024;

function decodeToolResultRecord(content) {
  const text = String(content || "").trim();
  if (!text) return { error: "empty" };
  if (text.length > MAX_TOOL_RESULT_CONTENT_CHARS) return { error: "oversized" };
  let value;
  try {
    value = JSON.parse(text);
  } catch {
    return { error: "malformed" };
  }

  // LM Studio serializes MCP CallToolResult.content as an array of content
  // blocks. File facts live in the JSON string of one text block. Accept only
  // one unambiguous block so facts from separate results or projects can never
  // be merged by the continuity layer.
  if (Array.isArray(value)) {
    if (value.length !== 1 || !isRecord(value[0]) || value[0].type !== "text"
      || typeof value[0].text !== "string" || value[0].text.length > MAX_TOOL_RESULT_CONTENT_CHARS) {
      return { error: "unsupported_envelope" };
    }
    try {
      value = JSON.parse(value[0].text);
    } catch {
      return { error: "malformed_embedded_json" };
    }
  } else if (isRecord(value) && isRecord(value.structuredContent)) {
    value = value.structuredContent;
  } else if (isRecord(value) && Array.isArray(value.content)) {
    if (value.content.length !== 1 || !isRecord(value.content[0])
      || value.content[0].type !== "text" || typeof value.content[0].text !== "string"
      || value.content[0].text.length > MAX_TOOL_RESULT_CONTENT_CHARS) {
      return { error: "unsupported_envelope" };
    }
    try {
      value = JSON.parse(value.content[0].text);
    } catch {
      return { error: "malformed_embedded_json" };
    }
  }
  return isRecord(value) ? { value } : { error: "unsupported_value" };
}

function parseToolResult(content) {
  const decoded = decodeToolResultRecord(content);
  if (decoded.error === "empty") return { summary: "empty tool result" };
  if (decoded.error) {
    return { summary: decoded.error === "malformed" || decoded.error === "malformed_embedded_json"
      ? "malformed tool result omitted"
      : "unsupported tool result envelope omitted" };
  }
  try {
    const source = stripControl(decoded.value);
    const out = {};
    // Git comparisons are repository observations, never file read/review coverage.
    if (source.kind === "git_observation" || source.action && source.comparison && source.canonicalProjectRoot) {
      const git = {};
      for (const key of ["action", "comparison", "base", "head", "currentHead", "blobOid", "path",
        "repositoryIdentity", "workspaceIdentity", "snapshotId", "hasMore", "complete", "incomplete",
        "pageStart", "pageEnd", "pageHasMore", "sourceResultComplete",
        "sha256", "hashSource", "startLine", "endLine", "totalLines", "returnedCount", "total", "observedAt", "sourceConsistency", "submoduleWorktrees",
        "since", "until", "authorQuery", "authorQuerySemantics", "identitySemantics",
        "queryPathBase", "requestedPathsCount", "requestedPathsOmitted", "requestedPathsSha256",
        "resolvedRepositoryPathsCount", "resolvedRepositoryPathsOmitted", "resolvedRepositoryPathsSha256"]) {
        if (source[key] !== undefined) git[key] = source[key];
      }
      for (const key of ["requestedPaths", "resolvedRepositoryPaths"]) {
        if (!Array.isArray(source[key])) continue;
        git[key] = source[key].slice(0, 8).map(value => String(value).slice(0, 400));
        const omitted = source[key].length - git[key].length;
        if (omitted > 0) git[`${key}Omitted`] = Number(git[`${key}Omitted`] || 0) + omitted;
      }
      if (Array.isArray(source.items)) {
        git.items = source.items.slice(0, 8).map(item => Object.fromEntries(
          ["status", "path", "oldPath", "similarity", "commit", "authoredAt", "authorName", "authorEmail",
            "committedAt", "committerName", "committerEmail", "subject"].filter(k => item?.[k] !== undefined)
            .map(k => [k, typeof item[k] === "string" ? item[k].slice(0, 400) : item[k]])));
        git.sourceOmittedItems = Math.max(0, (decoded.value.items?.length || source.items.length) - git.items.length);
        git.omittedItems = git.sourceOmittedItems;
      }
      out.gitObservation = git;
      // Commit blob lines are not current-worktree coverage or a write receipt.
      for (const key of ["status", "canonicalProject", "canonicalProjectRoot", "projectIdentity"])
        if (source[key] !== undefined) out[key] = source[key];
      return out;
    }
    for (const key of [
      "ok", "status", "summary", "message", "errorCode", "path", "operation", "mode",
      "sha256", "previousSha256", "size", "truncated", "hasMore",
      "startLine", "endLine", "returnedLineCount", "nextStartLine", "totalLines", "filesScanned", "findingCount",
      "validationOk", "blocksBuild", "exitCode", "likelyErrors", "fullLogPath",
      "upToDate", "actionsExecuted", "proofLevel", "failedCount", "succeededCount",
      "claimCount", "errorCount", "warningCount", "errorShapeCount", "warningShapeCount",
      "omittedErrorShapeCount", "omittedWarningShapeCount", "schemaVersion",
      "activeProject", "projectPath", "project", "engineAssociation", "resolvedEngineVersion",
      "requestedEngineAssociation", "resolvedRootType", "projectRelativePath", "workspaceRelativePath",
      "hashSource", "canonicalProject", "canonicalProjectRoot", "projectIdentity", "canonicalPath",
      "observation", "attachmentId", "attachmentName", "attachmentType", "parser",
      "startOffset", "endOffset", "totalChars", "parsedTextSha256", "rangeUnit",
    ]) {
      if (source[key] !== undefined) out[key] = source[key];
    }
    for (const key of ["errors", "warnings"]) {
      const diagnostics = retainedDiagnosticList(source[key]);
      if (diagnostics.length) out[key] = diagnostics;
    }
    const descriptor = projectDescriptor(source);
    if (descriptor && out.canonicalProject === undefined) out.canonicalProject = descriptor;
    if (source.absolutePath && out.canonicalPath === undefined) {
      out.canonicalPath = pathApiFor(source.absolutePath).resolve(String(source.absolutePath));
    }
    if (hasBoundUnityProject(source) && source.hash !== undefined && out.sha256 === undefined) {
      out.sha256 = source.hash;
    }
    if (hasBoundUnityProject(source) && source.previousHash !== undefined && out.previousSha256 === undefined) {
      out.previousSha256 = source.previousHash;
    }
    if (source.lastObservedAt !== undefined || source.snapshotCapturedAt !== undefined
      || (hasBoundUnityProject(source) && source.observedAt !== undefined)) {
      out.lastObservedAt = source.lastObservedAt || source.snapshotCapturedAt || source.observedAt;
    }
    if (out.path && (out.operation || out.sha256 || out.previousSha256 || out.canonicalPath)) {
      out.mutationSnapshotState = "fresh_read_required";
    }
    if (Array.isArray(source.files)) {
      out.files = source.files
        .map((file) => retainedFileFact(file, source.operation || "observed"))
        .filter(Boolean);
    }
    for (const key of ["summary", "message", "status", "errorCode"]) {
      if (typeof out[key] === "string") out[key] = sanitizeRetainedString(out[key]);
    }
    if (!Object.keys(out).length) out.summary = "tool result contained no retained factual fields";
    return sanitizeStructuredDurableValue(out);
  } catch {
    return { summary: "malformed tool result omitted" };
  }
}

function searchRequestFact(request) {
  const name = String(request?.name || "");
  if (name !== "search_files" && name !== "unity_find") return null;
  const args = isRecord(request?.arguments) ? request.arguments : {};
  const fact = {
    tool: name,
    query: typeof args.query === "string" ? args.query.slice(0, 500) : "",
    matchMode: args.regex === true ? "regex" : "literal",
    requestedRoot: typeof args.path === "string" ? args.path.slice(0, 500) : undefined,
    requestedKind: typeof args.kind === "string" ? args.kind.slice(0, 80) : undefined,
    requestedType: typeof args.type === "string" ? args.type.slice(0, 200) : undefined,
    extensions: Array.isArray(args.extensions)
      ? args.extensions.slice(0, 16).map(value => String(value).slice(0, 40)) : undefined,
    contentSearch: args.content === true,
    matchFileNames: typeof args.matchFileNames === "boolean" ? args.matchFileNames : undefined,
    caseSensitive: typeof args.caseSensitive === "boolean" ? args.caseSensitive : undefined,
    maxResults: Number.isSafeInteger(args.maxResults) ? args.maxResults : undefined,
    maxFiles: Number.isSafeInteger(args.maxFiles) ? args.maxFiles : undefined,
    pageContinuationRequested: typeof args.cursor === "string" && args.cursor.length > 0,
  };
  return sanitizeStructuredDurableValue(fact);
}

function searchObservation(requestFact, rawValue, parsed) {
  if (!requestFact || !isRecord(rawValue)) return null;
  const resultItems = Array.isArray(rawValue.results)
    ? rawValue.results : Array.isArray(rawValue.items) ? rawValue.items : null;
  const matchCount = resultItems ? resultItems.length
    : Number.isSafeInteger(rawValue.findingCount) ? rawValue.findingCount : null;
  const partial = rawValue.incomplete === true || rawValue.maxFilesReached === true
    || rawValue.truncated === true || rawValue.hasMore === true || Boolean(rawValue.nextCursor);
  const explicitlyComplete = rawValue.incomplete === false
    || rawValue.maxFilesReached === false || rawValue.truncated === false || rawValue.hasMore === false;
  const completeness = partial ? "partial" : explicitlyComplete ? "complete_for_requested_scope" : "unknown";
  return sanitizeStructuredDurableValue({
    ...requestFact,
    matchCount: matchCount === null ? undefined : matchCount,
    reportedTotal: Number.isSafeInteger(rawValue.total) ? rawValue.total : undefined,
    filesScanned: Number.isSafeInteger(rawValue.filesScanned)
      ? rawValue.filesScanned : Number.isSafeInteger(rawValue.scanned) ? rawValue.scanned : parsed.filesScanned,
    incomplete: rawValue.incomplete === true,
    truncated: rawValue.truncated === true,
    hasMore: rawValue.hasMore === true || Boolean(rawValue.nextCursor),
    completeness,
    zeroMatchMeaning: matchCount === 0
      ? completeness === "complete_for_requested_scope"
        ? "no_matches_in_requested_scope"
        : "zero_reported_not_repository_absence"
      : undefined,
  });
}

function toolOutcomeRecords(messages, beforeIndex, options = {}) {
  const maxItems = Math.max(1, Number(options.maxItems || 12));
  const includeMessageIndexes = options.includeMessageIndexes instanceof Set
    ? options.includeMessageIndexes
    : null;
  const outcomes = [];
  let activeProject = String(options.initialActiveProject || "");
  const requestsById = new Map();
  const duplicateRequestIds = new Set();
  const pendingRequests = [];
  let ambiguousRequestBatch = false;
  let acceptsIdlessResults = false;
  const abandonRequestBatch = () => {
    requestsById.clear();
    duplicateRequestIds.clear();
    pendingRequests.length = 0;
    ambiguousRequestBatch = false;
    acceptsIdlessResults = false;
  };
  const rememberRequest = (request) => {
    const args = isRecord(request?.arguments) ? request.arguments : {};
    const descriptor = projectDescriptor(args);
    const record = {
      descriptor,
      searchRequest: searchRequestFact(request),
      changesActiveProject: String(request?.name || "") === "set_active_project",
      clearsActiveProject: String(request?.name || "") === "set_active_project" && args.clear === true,
      hasExplicitProject: Object.prototype.hasOwnProperty.call(args, "project")
        || Object.prototype.hasOwnProperty.call(args, "projectPath")
        || Object.prototype.hasOwnProperty.call(args, "activeProject"),
      consumed: false,
    };
    if (request?.id) {
      const requestId = String(request.id);
      if (requestsById.has(requestId)) {
        requestsById.delete(requestId);
        duplicateRequestIds.add(requestId);
        ambiguousRequestBatch = true;
      } else if (!duplicateRequestIds.has(requestId)) {
        requestsById.set(requestId, record);
      }
    }
    pendingRequests.push(record);
  };
  const projectHintFor = (result) => {
    const resultId = result?.toolCallId ? String(result.toolCallId) : "";
    if (resultId && duplicateRequestIds.has(resultId)) {
      return { descriptor: "", hasExplicitProject: true, consumed: true };
    }
    let record = resultId ? requestsById.get(resultId) : null;
    if (resultId && !record) {
      ambiguousRequestBatch = true;
      return { descriptor: "", hasExplicitProject: true, consumed: true };
    }
    if (!resultId && ambiguousRequestBatch) {
      return { descriptor: "", hasExplicitProject: true, consumed: true };
    }
    if (!record) {
      const candidates = pendingRequests.filter((candidate) => !candidate.consumed);
      if (!resultId && !acceptsIdlessResults && candidates.length) {
        return { descriptor: "", hasExplicitProject: true, consumed: true };
      }
      if (candidates.length > 1) {
        const reference = candidates[0];
        const equivalentScope = reference.changesActiveProject !== true && candidates.every((candidate) => (
          candidate.changesActiveProject !== true
          && candidate.clearsActiveProject === reference.clearsActiveProject
          && candidate.hasExplicitProject === reference.hasExplicitProject
          && candidate.descriptor === reference.descriptor
        ));
        if (!equivalentScope) {
          ambiguousRequestBatch = true;
          return { descriptor: "", hasExplicitProject: true, consumed: true };
        }
      }
      record = candidates[0] || null;
    }
    if (!record) return null;
    record.consumed = true;
    if (resultId) requestsById.delete(resultId);
    return record;
  };
  const retain = (content, requestScope = null, includeOutcome = true) => {
    const parsed = parseToolResult(content);
    const decoded = decodeToolResultRecord(content);
    const explicitProject = projectDescriptor(parsed);
    const activeProjectCleared = requestScope?.clearsActiveProject === true && parsed.ok !== false;
    const requestOwnsScope = requestScope?.hasExplicitProject === true
      && requestScope?.changesActiveProject !== true;
    if (activeProjectCleared) activeProject = "";
    else if (explicitProject && !requestOwnsScope && !hasBoundUnityProject(parsed)) activeProject = explicitProject;
    const fallbackProject = activeProjectCleared ? "" : (requestOwnsScope ? requestScope.descriptor : activeProject);
    const fallbackSource = requestOwnsScope
      ? (requestScope.descriptor ? "tool_request_fact" : "unresolved_tool_request_project")
      : "prior_checkpoint_fact";
    const requestIdentity = exactProjectIdentity(requestScope?.descriptor);
    const resultIdentity = exactProjectIdentity(explicitProject);
    const inconsistentProjectScope = requestOwnsScope
      && requestIdentity
      && resultIdentity
      && requestIdentity !== resultIdentity;
    const scopedOutcome = scopeToolOutcome(parsed, fallbackProject, {
      fallbackSource,
      perCallProject: requestOwnsScope,
      activeProjectCleared,
      inconsistentProjectScope,
    });
    const search = searchObservation(requestScope?.searchRequest, decoded.value, parsed);
    if (search) scopedOutcome.searchObservation = search;
    if (includeOutcome) outcomes.push(scopedOutcome);
  };
  for (const message of messages.slice(0, beforeIndex)) {
    if ((message.toolRequests || []).length) {
      // Tool results belong to the immediately preceding request batch. An
      // unmatched older request must never scope a later ID-less result.
      abandonRequestBatch();
      for (const request of message.toolRequests) rememberRequest(request);
      acceptsIdlessResults = true;
    }
    if (message.role !== "tool") continue;
    const includeOutcome = includeMessageIndexes === null
      || includeMessageIndexes.has(message.index);
    for (const result of message.toolResults) {
      retain(result?.content ?? result, projectHintFor(result), includeOutcome);
    }
    if (!message.toolResults.length && message.text) {
      retain(message.text, projectHintFor(null), includeOutcome);
    }
    // Only exact unique IDs may correlate across multiple tool messages. An
    // ID-less result is accepted only in the immediate result message.
    acceptsIdlessResults = false;
  }
  return options.aggregateAll === true ? outcomes : outcomes.slice(-maxItems);
}

const DERIVED_BUILD_FIELDS = new Set([
  "exitCode",
  "proofLevel",
  "upToDate",
  "actionsExecuted",
  "likelyErrors",
  "fullLogPath",
]);

const DERIVED_FILE_FIELDS = new Set([
  "path",
  "canonicalPath",
  "canonicalProject",
  "canonicalProjectRoot",
  "projectRelativePath",
  "sha256",
  "previousSha256",
  "lastObservedAt",
  "startLine",
  "endLine",
  "returnedLineCount",
  "nextStartLine",
  "totalLines",
  "hasMore",
  "observedLineRanges",
  "totalLinesAtObservation",
  "readCoverageState",
  "mutationSnapshotState",
  "files",
]);

function outcomeDisplayRecord(record) {
  const display = { ...record };
  const hasBuildState = record.exitCode !== undefined
    || record.proofLevel
    || record.fullLogPath
    || record.likelyErrors;
  const hasFileState = Array.isArray(record.files)
    || (record.path && (
      (record.ok !== false && (record.operation || record.sha256 || record.previousSha256))
      || record.errorCode === "FILE_VERSION_CONFLICT"
    ));
  if (hasBuildState) {
    for (const key of DERIVED_BUILD_FIELDS) delete display[key];
  }
  if (hasFileState) {
    for (const key of DERIVED_FILE_FIELDS) delete display[key];
  }
  return display;
}

function serializeToolOutcome(record, maxChars) {
  const displayRecord = outcomeDisplayRecord(record);
  const serialized = JSON.stringify(displayRecord);
  if (serialized.length <= maxChars) return serialized;

  const bounded = {};
  for (const key of [
    "ok", "status", "operation", "errorCode", "mode", "proofLevel", "exitCode", "upToDate",
    "actionsExecuted", "failedCount", "succeededCount", "activeProjectCleared",
    "claimCount", "errorCount", "warningCount", "errorShapeCount", "warningShapeCount",
    "omittedErrorShapeCount", "omittedWarningShapeCount", "schemaVersion",
    "canonicalProject", "canonicalProjectSource", "fileObservationState",
  ]) {
    if (displayRecord[key] !== undefined) bounded[key] = displayRecord[key];
  }
  if (Array.isArray(record.files)) {
    bounded.fileFactCount = record.files.length;
    bounded.fileFactsExtractedSeparately = true;
  }
  bounded.outcomeDisplayState = "bounded_after_factual_extraction";
  if (displayRecord.gitObservation) {
    const { items, ...identity } = displayRecord.gitObservation;
    bounded.gitObservation = { ...identity, ...(items ? { omittedItems: (identity.omittedItems || 0) + items.length } : {}) };
    // Preserve comparison identity before optional names, diagnostics or prose.
    for (const item of items || []) {
      const candidate = { ...bounded, gitObservation: { ...bounded.gitObservation,
        items: [...(bounded.gitObservation.items || []), item], omittedItems: bounded.gitObservation.omittedItems - 1 } };
      if (JSON.stringify(candidate).length > maxChars) break;
      bounded.gitObservation = candidate.gitObservation;
    }
  }
  if (displayRecord.searchObservation) bounded.searchObservation = displayRecord.searchObservation;
  for (const key of ["errors", "warnings"]) {
    if (!Array.isArray(displayRecord[key])) continue;
    const retained = [];
    for (const diagnostic of displayRecord[key]) {
      const candidate = { ...bounded, [key]: [...retained, diagnostic] };
      if (JSON.stringify(candidate).length > maxChars) break;
      retained.push(diagnostic);
    }
    if (retained.length) bounded[key] = retained;
  }
  const compact = JSON.stringify(bounded);
  if (compact.length <= maxChars) return compact;
  return JSON.stringify({ outcomeDisplayState: "bounded_after_factual_extraction" });
}

function serializeToolOutcomeRecords(records, options = {}) {
  const maxChars = Math.max(200, Number(options.maxToolResultChars || 1200));
  return (records || []).map((record) => serializeToolOutcome(record, maxChars));
}

function toolOutcomeMemory(messages, beforeIndex, options = {}) {
  return serializeToolOutcomeRecords(toolOutcomeRecords(messages, beforeIndex, options), options);
}

function parsedOutcomes(outcomes) {
  const parsed = [];
  for (const outcome of outcomes || []) {
    if (isRecord(outcome)) {
      parsed.push(sanitizeDerivedOperationalRecord(outcome));
      continue;
    }
    try {
      const value = JSON.parse(outcome);
      if (isRecord(value)) parsed.push(value);
    } catch {
      // A response-size clip is not valid evidence and is ignored.
    }
  }
  return parsed;
}

function durableGitObservation(value) {
  if (!isRecord(value)) return null;
  const retained = {};
  for (const key of [
    "action", "comparison", "base", "head", "currentHead", "blobOid", "path",
    "repositoryIdentity", "workspaceIdentity", "hasMore", "complete", "incomplete",
    "pageStart", "pageEnd", "pageHasMore", "sourceResultComplete",
    "sha256", "hashSource", "startLine", "endLine", "totalLines", "returnedCount", "total",
    "since", "until", "authorQuery", "authorQuerySemantics", "identitySemantics",
    "sourceConsistency", "submoduleWorktrees", "queryPathBase", "requestedPaths",
    "requestedPathsCount", "requestedPathsOmitted", "requestedPathsSha256", "resolvedRepositoryPaths",
    "resolvedRepositoryPathsCount", "resolvedRepositoryPathsOmitted", "resolvedRepositoryPathsSha256",
    "canonicalProjectRoot", "canonicalProject", "projectIdentity",
  ]) {
    if (value[key] !== undefined) retained[key] = value[key];
  }
  const sourceOmittedItems = Number(value.sourceOmittedItems ?? value.omittedItems ?? 0);
  const memoryOmittedItems = Number(value.memoryOmittedItems || 0);
  retained.sourceOmittedItems = Math.max(0, sourceOmittedItems);
  if (Array.isArray(value.items)) {
    retained.items = value.items.slice(0, 4);
    retained.memoryOmittedItems = Math.max(0, memoryOmittedItems + value.items.length - retained.items.length);
  } else if (value.omittedItems !== undefined) {
    retained.memoryOmittedItems = memoryOmittedItems;
  }
  retained.omittedItems = retained.sourceOmittedItems + (retained.memoryOmittedItems || 0);
  return sanitizeStructuredDurableValue(retained);
}

function stateMemory(outcomes) {
  const files = [];
  const builds = [];
  const gitObservations = [];
  let activeProject = null;
  for (const item of parsedOutcomes(outcomes)) {
    if (isRecord(item.gitObservation)) {
      gitObservations.push(durableGitObservation({
        ...item.gitObservation,
        ...(item.canonicalProjectRoot ? { canonicalProjectRoot: item.canonicalProjectRoot } : {}),
        ...(item.canonicalProject ? { canonicalProject: item.canonicalProject } : {}),
        ...(item.projectIdentity ? { projectIdentity: item.projectIdentity } : {}),
      }));
    }
    if (item.activeProjectCleared === true) {
      activeProject = { cleared: true, source: "tool_result_fact" };
      continue;
    }
    const projectCandidate = projectDescriptor(item);
    if (projectCandidate && item.canonicalProjectSource !== "tool_request_fact"
      && item.canonicalProjectSource !== "unity_tool_result_fact") {
      activeProject = {
        descriptor: String(projectCandidate),
        root: projectRoot(projectCandidate),
        source: item.canonicalProjectSource || "tool_result_fact",
        ...(item.engineAssociation ? { engineAssociation: item.engineAssociation } : {}),
        ...(item.resolvedEngineVersion ? { engineVersion: item.resolvedEngineVersion } : {}),
      };
    }
    const fallbackProject = item.canonicalProjectSource === "unresolved_tool_request_project"
      ? ""
      : (projectCandidate || activeProject?.descriptor || "");
    const observedFileFact = item.ok !== false && (item.operation || item.sha256 || item.previousSha256);
    const conflictFact = item.errorCode === "FILE_VERSION_CONFLICT";
    if (item.path && (observedFileFact || conflictFact)) {
      files.push(fileObservation(item, fallbackProject));
    }
    if (Array.isArray(item.files)) {
      for (const file of item.files) {
        files.push(fileObservation(file, fallbackProject, item.operation || "observed"));
      }
    }
    if (item.exitCode !== undefined || item.proofLevel || item.fullLogPath || item.likelyErrors) {
      builds.push({
        ok: item.ok,
        exitCode: item.exitCode,
        proofLevel: item.proofLevel,
        upToDate: item.upToDate,
        actionsExecuted: item.actionsExecuted,
        likelyErrors: item.likelyErrors,
        fullLogPath: item.fullLogPath,
      });
    }
  }
  return {
    files: coalesceFileObservations(files.filter(Boolean), 64),
    builds: builds.slice(-4),
    gitObservations: gitObservations.slice(-8),
    activeProject,
  };
}

module.exports = {
  CONTROL_DIRECTIVES,
  INTERNAL_KEYS,
  decodeToolResultRecord,
  parseToolResult,
  durableGitObservation,
  retainedFileFact,
  scopeToolOutcome,
  stateMemory,
  stripControl,
  serializeToolOutcomeRecords,
  toolOutcomeMemory,
  toolOutcomeRecords,
};
