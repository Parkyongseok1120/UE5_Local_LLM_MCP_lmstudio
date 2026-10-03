"use strict";

const { snapshotResultFields } = require("./direct-file-version-policy.js");
const { failure } = require("./direct-response.js");
const { displayPath, pathMetadata } = require("./read-path-resolver.js");

function unavailableReadSnapshot(resolution, code, message, options = {}) {
  return failure(code, message, { ...options, details: { ...options.details,
    path: displayPath(resolution), ...pathMetadata(resolution),
    canonicalProject: resolution.activeProject, observationState: "unavailable",
  } });
}

function registerReadSnapshot(fileSnapshots, resolution, read, requestContext = {}) {
  if (!resolution.activeProject || !read?.hash || !read?.stat) return {};
  const snapshot = fileSnapshots.register({
    projectPath: resolution.activeProject,
    filePath: resolution.absolutePath,
    hash: read.hash,
    stat: read.stat,
    requestContext,
  });
  return snapshotResultFields(snapshot);
}

module.exports = { registerReadSnapshot, unavailableReadSnapshot };
