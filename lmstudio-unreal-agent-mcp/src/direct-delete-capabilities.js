"use strict";

const fs = require("node:fs");
const fsp = fs.promises;
const path = require("node:path");
const crypto = require("node:crypto");
const { sha256Buffer } = require("./safe-write");
const { isDeleteAllowedPath } = require("./write-guards");
const { withPathLock } = require("./write-locks");
const { readStableTextFile } = require("./direct-file-snapshot");
const { failure, success } = require("./direct-response");
const { envFlag, nowStamp } = require("./direct-runtime-shared");

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
      summary: String(summary),
      path: path.resolve(absolutePath),
      reason: String(item.reason),
      ifNotDeleted: String(item.ifNotDeleted),
      ifDeleted: String(item.ifDeleted),
    });
  }

  async function proposeFileDeletions(args) {
    const files = Array.isArray(args.files) ? args.files : [];
    if (!files.length) {
      return failure("INVALID_ARGUMENT", "files must contain at least one deletion candidate", {
        retryAllowed: true,
      });
    }
    const now = Date.now();
    const expiresAt = now + 15 * 60 * 1000;
    const payload = [];
    for (const item of files.slice(0, 32)) {
      const resolution = await mutationResolution(item.path, args.project);
      const allowed = isDeleteAllowedPath(
        resolution.absolutePath,
        workspaceRoot,
        resolution.activeProject,
      );
      if (!allowed.ok) return failure("DELETE_TARGET_BLOCKED", `${item.path}: ${allowed.message}`);
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
      });
    }
    for (const [token, proposal] of proposals) {
      if (proposal.expiresAt <= now) proposals.delete(token);
    }
    return success({
      deletesNothing: true,
      candidateCount: payload.length,
      proposals: payload,
      instruction: "Show this exact plan to the user. Call delete_file only after explicit approval, with userApproved=true and the current file hash.",
    });
  }

  async function deleteFile(args) {
    if (!envFlag(env, "ALLOW_WRITE", false)) {
      return failure("WRITE_DISABLED", "Writes are disabled. Start the MCP with ALLOW_WRITE=1.");
    }
    if (!envFlag(env, "ALLOW_SOURCE_DELETE", false)) {
      return failure("DELETE_DISABLED", "Source deletion is disabled. Start the MCP with ALLOW_SOURCE_DELETE=1.");
    }
    if (args.userApproved !== true) {
      return failure("USER_APPROVAL_REQUIRED", "delete_file requires userApproved=true after the exact proposal is shown to the user.");
    }
    const token = String(args.approvalToken || "");
    const proposal = proposals.get(token);
    if (!proposal || proposal.expiresAt <= Date.now()) {
      return failure("APPROVAL_TOKEN_INVALID", "Deletion approval token is missing, expired, or already used.");
    }
    const resolution = await mutationResolution(args.path, args.project);
    const identity = proposalIdentity(args.completedEditsSummary, {
      reason: args.reason,
      ifNotDeleted: args.ifNotDeleted,
      ifDeleted: args.ifDeleted,
    }, resolution.absolutePath);
    if (identity !== proposal.identity
      || path.resolve(resolution.absolutePath) !== path.resolve(proposal.absolutePath)) {
      return failure("APPROVAL_SCOPE_MISMATCH", "Deletion arguments do not exactly match the approved proposal.");
    }
    const read = await readStableTextFile(resolution.absolutePath, limits.maxSourceBytes);
    if (!read.ok) return failure(read.errorCode, read.message);
    if (!/^[a-f0-9]{64}$/iu.test(String(args.expectedHash || ""))
      || read.hash.toLowerCase() !== String(args.expectedHash).toLowerCase()) {
      return failure(
        "READ_CONFLICT",
        "The file hash differs from the approved/read version. Re-read and propose again.",
        { retryAllowed: false },
      );
    }
    const trashPath = path.join(
      resolution.projectDir,
      ".agent-trash",
      nowStamp(),
      resolution.relativePath,
    );
    const locked = await withPathLock(resolution.absolutePath, `${runtimeOwner}_delete`, async () => {
      const currentHash = sha256Buffer(await fsp.readFile(resolution.absolutePath));
      if (currentHash !== read.hash) return { ok: false };
      await fsp.mkdir(path.dirname(trashPath), { recursive: true });
      await fsp.rename(resolution.absolutePath, trashPath);
      return { ok: true };
    }, { stateRoot });
    if (locked.locked) {
      return failure("WRITE_LOCKED", "Another write is in progress for this path.", {
        retryAllowed: true,
        retryMode: "after_state_change",
      });
    }
    if (!locked.result.ok) {
      return failure("READ_CONFLICT", "The file changed while deletion was being committed.");
    }
    proposals.delete(token);
    return success({
      operation: "moved_to_trash",
      path: `project://${resolution.relativePath}`,
      sha256: read.hash,
      recoverable: true,
      restorePath: trashPath,
    });
  }

  return { delete_file: deleteFile, propose_file_deletions: proposeFileDeletions };
}

module.exports = { createDeleteCapabilities };
