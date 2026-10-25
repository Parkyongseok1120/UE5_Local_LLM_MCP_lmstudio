"use strict";

const { snapshotResultFields } = require("./direct-file-version-policy.js");
const { canonicalAbsolutePathIdentity } = require("./filesystem-path-identity.js");
const path = require("node:path");
const { failure } = require("./direct-response.js");
const { displayPath, pathMetadata } = require("./read-path-resolver.js");
const { checkReadCancellation } = require("./direct-file-snapshot.js");

function unavailableReadSnapshot(resolution, code, message, options = {}) {
  return failure(code, message, { ...options, details: { ...options.details,
    path: displayPath(resolution), ...pathMetadata(resolution),
    canonicalProject: resolution.activeProject, observationState: "unavailable",
  } });
}

function prepareReadSnapshot(fileSnapshots, resolution, read, requestContext = {}) {
  checkReadCancellation(requestContext.signal);
  // Engine source is read-only evidence: it never receives an edit receipt.
  if (resolution.resolvedRootType === "installed_engine" || !resolution.activeProject || !read?.hash || !read?.stat)
    return { fields: {}, commit: () => checkReadCancellation(requestContext.signal) };
  const snapshot = fileSnapshots.prepare({
    projectPath: resolution.activeProject,
    filePath: resolution.absolutePath,
    hash: read.hash,
    stat: read.stat,
    requestContext,
  });
  return { fields: snapshotResultFields(snapshot), commit: () => {
    checkReadCancellation(requestContext.signal);
    fileSnapshots.commitPrepared(snapshot);
  } };
}

// Source freshness is the hash already computed by the stable reader. A stat
// signature cannot distinguish same-sized writes that preserve timestamps.
function sourceReadState(resolution, read) {
  return JSON.stringify([resolution.resolvedRootType, canonicalAbsolutePathIdentity(resolution.activeProject || ""),
    canonicalAbsolutePathIdentity(resolution.absolutePath), read.hash]);
}

  // Reader messages name the caller's URI, never the resolved host path.
function readMessage(read, args, resolution) {
    if (read.errorCode === "NOT_FOUND") return `File not found: ${args.path}`;
    const absolute = String(resolution.absolutePath || "");
    let message = String(read.message || "");
    if (absolute) for (const form of new Set([absolute, absolute.split(path.sep).join("/")])) message = message.split(form).join(String(args.path));
    return message;
  }

module.exports = { prepareReadSnapshot, unavailableReadSnapshot, sourceReadState, readMessage };
