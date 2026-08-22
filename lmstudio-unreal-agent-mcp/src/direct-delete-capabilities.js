"use strict";

const fs = require("node:fs");
const fsp = fs.promises;
const path = require("node:path");
const crypto = require("node:crypto");
const { sha256Buffer } = require("./safe-write");
const { canonicalAbsolutePathIdentity } = require("./filesystem-path-identity");
const { isDeleteAllowedPath } = require("./write-guards");
const { withPathLock } = require("./write-locks");
const { readStableTextFile } = require("./direct-file-snapshot");
const { failure, success } = require("./direct-response");
const { envFlag, nowStamp } = require("./direct-runtime-shared");
const {
  resolveVersionEvidence,
  versionConflict,
  versionEvidenceFailure,
} = require("./direct-file-version-policy.js");

const PROPOSE_FIELDS = new Set(["completedEditsSummary", "project", "files"]);
const PROPOSAL_ITEM_FIELDS = new Set(["path", "reason", "ifNotDeleted", "ifDeleted"]);
const DELETE_FIELDS = new Set([
  "path",
  "approvalToken",
  "userApproved",
  "project",
  "expectedHash",
  "fileVersionReceipt",
  "completedEditsSummary",
  "reason",
  "ifNotDeleted",
  "ifDeleted",
]);

function plainObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function unsupportedField(value, allowed) {
  return Object.keys(value).find((key) => !allowed.has(key)) || "";
}

function stringFieldError(value, field, { optional = false } = {}) {
  if (optional && value[field] === undefined) return "";
  if (typeof value[field] !== "string") return `${field} must be a string`;
  if (!value[field].trim()) return `${field} must be a non-empty string`;
  return "";
}

function proposalArgumentsError(args) {
  if (!plainObject(args)) return "propose_file_deletions arguments must be an object";
  const unsupported = unsupportedField(args, PROPOSE_FIELDS);
  if (unsupported) return `propose_file_deletions contains unsupported field: ${unsupported}`;
  const topError = stringFieldError(args, "completedEditsSummary")
    || stringFieldError(args, "project", { optional: true });
  if (topError) return topError;
  if (!Array.isArray(args.files) || !args.files.length || args.files.length > 32) {
    return "files must be an array containing 1 to 32 deletion candidates";
  }
  for (const item of args.files) {
    if (!plainObject(item)) return "files[] must contain objects";
    const itemUnsupported = unsupportedField(item, PROPOSAL_ITEM_FIELDS);
    if (itemUnsupported) return `files[] contains unsupported field: ${itemUnsupported}`;
    for (const field of PROPOSAL_ITEM_FIELDS) {
      const error = stringFieldError(item, field);
      if (error) return `files[].${error}`;
    }
  }
  return "";
}

function deleteArgumentsError(args) {
  if (!plainObject(args)) return "delete_file arguments must be an object";
  const unsupported = unsupportedField(args, DELETE_FIELDS);
  if (unsupported) return `delete_file contains unsupported field: ${unsupported}`;
  for (const field of [
    "path",
    "approvalToken",
    "completedEditsSummary",
    "reason",
    "ifNotDeleted",
    "ifDeleted",
  ]) {
    const error = stringFieldError(args, field);
    if (error) return error;
  }
  for (const field of ["project", "expectedHash", "fileVersionReceipt"]) {
    const error = stringFieldError(args, field, { optional: true });
    if (error) return error;
  }
  if (typeof args.userApproved !== "boolean") return "userApproved must be a boolean";
  return "";
}

function frozenPathIdentity(value) {
  return canonicalAbsolutePathIdentity(value, process.platform, { realpath: false });
}

function frozenMutationTarget(resolution) {
  return {
    absolutePath: frozenPathIdentity(resolution.absolutePath),
    realPath: frozenPathIdentity(resolution.realPath),
    allowedRealRoot: frozenPathIdentity(resolution.allowedRealRoot),
  };
}

function sameMutationTarget(initialIdentity, refreshed) {
  const refreshedIdentity = frozenMutationTarget(refreshed);
  return refreshedIdentity.absolutePath === initialIdentity.absolutePath
    && refreshedIdentity.realPath === initialIdentity.realPath
    && refreshedIdentity.allowedRealRoot === initialIdentity.allowedRealRoot;
}

function pathIsWithinOrEqual(candidate, root) {
  const candidateIdentity = frozenPathIdentity(candidate);
  const rootIdentity = frozenPathIdentity(root);
  const separator = process.platform === "win32" ? "/" : path.sep;
  const rootPrefix = rootIdentity.endsWith(separator)
    ? rootIdentity
    : `${rootIdentity}${separator}`;
  return Boolean(candidateIdentity && rootIdentity)
    && (candidateIdentity === rootIdentity || candidateIdentity.startsWith(rootPrefix));
}

async function nearestExistingAncestorRealPath(candidate) {
  let current = path.resolve(candidate);
  while (true) {
    try {
      return await fsp.realpath(current);
    } catch (error) {
      if (!["ENOENT", "ENOTDIR"].includes(error.code)) throw error;
      const parent = path.dirname(current);
      if (parent === current) throw error;
      current = parent;
    }
  }
}

function createDeleteCapabilities(context) {
  const {
    env,
    limits,
    mutationResolution,
    runtimeOwner,
    stateRoot,
    workspaceRoot,
  } = context;
  const proposals = new Map();

  function proposalIdentity(summary, item, absolutePath) {
    return JSON.stringify({
      summary,
      path: path.resolve(absolutePath),
      reason: item.reason,
      ifNotDeleted: item.ifNotDeleted,
      ifDeleted: item.ifDeleted,
    });
  }

  async function proposeFileDeletions(args, requestContext = {}) {
    const argumentError = proposalArgumentsError(args);
    if (argumentError) {
      return failure("INVALID_ARGUMENT", argumentError, {
        retryAllowed: true,
        retryMode: "different_arguments",
      });
    }
    const { files } = args;
    const now = Date.now();
    const expiresAt = now + 15 * 60 * 1000;
    const payload = [];
    for (const item of files) {
      const resolution = await mutationResolution(item.path, args.project);
      const allowed = isDeleteAllowedPath(
        resolution.absolutePath,
        workspaceRoot,
        resolution.activeProject,
      );
      if (!allowed.ok) return failure("DELETE_TARGET_BLOCKED", `${item.path}: ${allowed.message}`);
      const read = await readStableTextFile(resolution.absolutePath, limits.maxSourceBytes);
      if (!read.ok) return failure(read.errorCode, read.message);
      const snapshot = context.fileSnapshots.register({
        projectPath: resolution.activeProject,
        filePath: resolution.absolutePath,
        hash: read.hash,
        stat: read.stat,
        requestContext,
      });
      const token = crypto.randomBytes(24).toString("hex");
      proposals.set(token, {
        identity: proposalIdentity(args.completedEditsSummary, item, resolution.absolutePath),
        expiresAt,
        absolutePath: resolution.absolutePath,
        relativePath: resolution.relativePath,
      });
      payload.push({
        path: `project://${resolution.relativePath}`,
        approvalToken: token,
        expiresAt: new Date(expiresAt).toISOString(),
        sha256: read.hash,
        fileVersionReceipt: snapshot.fileVersionReceipt,
        snapshotVersion: snapshot.snapshotVersion,
      });
    }
    for (const [token, proposal] of proposals) {
      if (proposal.expiresAt <= now) proposals.delete(token);
    }
    return success({
      deletesNothing: true,
      candidateCount: payload.length,
      proposals: payload,
      instruction: "Show this exact plan to the user. After explicit approval, call delete_file with userApproved=true and this fileVersionReceipt; raw expectedHash remains compatible.",
    });
  }

  async function deleteFile(args, requestContext = {}) {
    if (!envFlag(env, "ALLOW_WRITE", false)) {
      return failure("WRITE_DISABLED", "Writes are disabled. Start the MCP with ALLOW_WRITE=1.");
    }
    if (!envFlag(env, "ALLOW_SOURCE_DELETE", false)) {
      return failure("DELETE_DISABLED", "Source deletion is disabled. Start the MCP with ALLOW_SOURCE_DELETE=1.");
    }
    const argumentError = deleteArgumentsError(args);
    if (argumentError) {
      return failure("INVALID_ARGUMENT", argumentError, {
        retryAllowed: true,
        retryMode: "different_arguments",
      });
    }
    if (args.userApproved !== true) {
      return failure("USER_APPROVAL_REQUIRED", "delete_file requires userApproved=true after the exact proposal is shown to the user.");
    }
    const token = args.approvalToken;
    const proposal = proposals.get(token);
    if (!proposal || proposal.expiresAt <= Date.now()) {
      return failure("APPROVAL_TOKEN_INVALID", "Deletion approval token is missing, expired, or already used.");
    }
    const resolution = await mutationResolution(args.path, args.project);
    const targetIdentity = frozenMutationTarget(resolution);
    const identity = proposalIdentity(args.completedEditsSummary, {
      reason: args.reason,
      ifNotDeleted: args.ifNotDeleted,
      ifDeleted: args.ifDeleted,
    }, resolution.absolutePath);
    if (identity !== proposal.identity
      || path.resolve(resolution.absolutePath) !== path.resolve(proposal.absolutePath)) {
      return failure("APPROVAL_SCOPE_MISMATCH", "Deletion arguments do not exactly match the approved proposal.");
    }
    const version = resolveVersionEvidence(context, resolution, args, requestContext);
    if (!version.ok) return versionEvidenceFailure(version);
    const read = await readStableTextFile(resolution.absolutePath, limits.maxSourceBytes);
    if (!read.ok) return failure(read.errorCode, read.message);
    if (read.hash.toLowerCase() !== version.expectedHash) return versionConflict(version, read.hash);
    const trashStamp = nowStamp();
    let trashPath = "";
    let locked;
    try {
      locked = await withPathLock(resolution.absolutePath, `${runtimeOwner}_delete`, async () => {
        const refreshed = await mutationResolution(args.path, args.project);
        if (!sameMutationTarget(targetIdentity, refreshed)) {
          throw new Error("delete_file real target or containment root changed during locked revalidation");
        }
        const refreshedAllowed = isDeleteAllowedPath(
          refreshed.absolutePath,
          workspaceRoot,
          refreshed.activeProject,
        );
        if (!refreshedAllowed.ok) throw new Error(refreshedAllowed.message);
        const currentHash = sha256Buffer(await fsp.readFile(refreshed.absolutePath));
        if (currentHash !== version.expectedHash) return { ok: false, currentHash };
        trashPath = path.join(
          refreshed.projectDir,
          ".agent-trash",
          trashStamp,
          refreshed.relativePath,
        );
        const trashParent = path.dirname(trashPath);
        const realProjectRoot = await fsp.realpath(refreshed.projectDir);
        const existingTrashAncestor = await nearestExistingAncestorRealPath(trashParent);
        if (!pathIsWithinOrEqual(existingTrashAncestor, realProjectRoot)) {
          throw new Error("recoverable trash ancestor escapes the selected project through a symlink/junction");
        }
        await fsp.mkdir(trashParent, { recursive: true });
        const realTrashParent = await fsp.realpath(trashParent);
        if (!pathIsWithinOrEqual(realTrashParent, realProjectRoot)) {
          throw new Error("recoverable trash parent escapes the selected project through a symlink/junction");
        }
        await fsp.rename(refreshed.absolutePath, trashPath);
        return { ok: true };
      }, { stateRoot });
    } catch (error) {
      return failure(
        "DELETE_TARGET_BLOCKED",
        `delete_file locked containment revalidation failed: ${String(error.message || error)}`,
        { retryAllowed: true, retryMode: "after_state_change" },
      );
    }
    if (locked.locked) {
      return failure("WRITE_LOCKED", "Another write is in progress for this path.", {
        retryAllowed: true,
        retryMode: "after_state_change",
      });
    }
    if (!locked.result.ok) {
      return versionConflict(version, locked.result.currentHash || "unavailable");
    }
    proposals.delete(token);
    context.fileSnapshots.invalidatePath(resolution.activeProject, resolution.absolutePath);
    return success({
      operation: "moved_to_trash",
      path: `project://${resolution.relativePath}`,
      sha256: read.hash,
      hashSource: version.hashSource,
      recoverable: true,
      restorePath: trashPath,
    });
  }

  return { delete_file: deleteFile, propose_file_deletions: proposeFileDeletions };
}

module.exports = { createDeleteCapabilities };
