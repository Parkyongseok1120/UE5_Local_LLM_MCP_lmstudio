"use strict";

const { snapshotResultFields } = require("./direct-file-version-policy.js");

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

module.exports = { registerReadSnapshot };
