"use strict";

const fs = require("node:fs");

const { failure } = require("./direct-response.js");

const fsp = fs.promises;

function resolveVersionEvidence(context, resolution, args, requestContext = {}) {
  return context.fileSnapshots.resolve({
    projectPath: resolution.activeProject,
    filePath: resolution.absolutePath,
    expectedHash: args.expectedHash,
    fileVersionReceipt: args.fileVersionReceipt,
    requestContext,
  });
}

function versionEvidenceFailure(resolved, extra = {}) {
  return failure(
    resolved.errorCode || "FILE_SNAPSHOT_REQUIRED",
    resolved.message || "A current file snapshot is required.",
    {
      ...extra,
      details: {
        ...(extra.details || {}),
        malformedExpectedHash: resolved.malformedExpectedHash === true,
      },
      retryAllowed: true,
      retryMode: "different_arguments",
    },
  );
}

function versionConflict(resolved, currentHash, extra = {}) {
  return failure(
    "FILE_VERSION_CONFLICT",
    "The file no longer matches the selected read snapshot. Re-read it and recompute the exact edit; the server will not advance stale evidence automatically.",
    {
      ...extra,
      details: {
        ...(extra.details || {}),
        hashSource: resolved.hashSource,
        expectedSha256: resolved.expectedHash,
        currentSha256: currentHash,
      },
      retryAllowed: true,
      retryMode: "after_state_change",
    },
  );
}

function snapshotResultFields(snapshot) {
  if (!snapshot) return {};
  return {
    fileVersionReceipt: snapshot.fileVersionReceipt,
    snapshotVersion: snapshot.snapshotVersion,
    snapshotCapturedAt: snapshot.snapshotCapturedAt,
    snapshotExpiresAt: snapshot.snapshotExpiresAt,
    snapshotOwner: snapshot.snapshotOwner,
  };
}

async function registerCurrentVersion(context, resolution, hash, requestContext = {}) {
  const stat = await fsp.stat(resolution.absolutePath);
  const snapshot = context.fileSnapshots.register({
    projectPath: resolution.activeProject,
    filePath: resolution.absolutePath,
    hash,
    stat,
    requestContext,
  });
  return snapshotResultFields(snapshot);
}

module.exports = {
  registerCurrentVersion,
  resolveVersionEvidence,
  snapshotResultFields,
  versionConflict,
  versionEvidenceFailure,
};
