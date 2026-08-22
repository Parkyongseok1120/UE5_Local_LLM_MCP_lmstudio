"use strict";

const crypto = require("node:crypto");

const { canonicalAbsolutePathIdentity } = require("./filesystem-path-identity.js");
const { statSignature } = require("./direct-runtime-shared.js");

const SHA256_PATTERN = /^[a-f0-9]{64}$/iu;

function validSha256(value) {
  return typeof value === "string" && SHA256_PATTERN.test(value.trim());
}

function requestOwner(requestContext = {}) {
  const sessionId = String(requestContext.sessionId || "").trim();
  if (sessionId) return { kind: "mcp_transport_session", id: sessionId.slice(0, 512) };
  const conversationId = String(requestContext.conversationId || "").trim();
  if (conversationId) return { kind: "strict_conversation", id: conversationId.slice(0, 512) };
  return null;
}

function ownerKey(owner) {
  return owner ? `${owner.kind}:${owner.id}` : "";
}

function sameOwner(left, right) {
  return ownerKey(left) === ownerKey(right);
}

class FileSnapshotRegistry {
  constructor(options = {}) {
    this.maxEntries = Math.max(1, Math.trunc(Number(options.maxEntries || 512)));
    this.ttlMs = Math.max(1000, Math.trunc(Number(options.ttlMs || 30 * 60 * 1000)));
    this.hostPlatform = options.hostPlatform || process.platform;
    this.now = typeof options.now === "function" ? options.now : () => Date.now();
    this.randomBytes = typeof options.randomBytes === "function" ? options.randomBytes : crypto.randomBytes;
    this.entries = new Map();
    this.version = 0;
  }

  canonicalIdentity(projectPath, filePath) {
    const canonicalProject = canonicalAbsolutePathIdentity(projectPath, this.hostPlatform);
    const canonicalPath = canonicalAbsolutePathIdentity(filePath, this.hostPlatform);
    if (!canonicalProject || !canonicalPath) {
      throw new Error("File snapshot requires canonical project and file paths");
    }
    return {
      canonicalProject,
      canonicalPath,
      pathKey: `${canonicalProject}\n${canonicalPath}`,
    };
  }

  deleteReceipt(receipt) {
    this.entries.delete(receipt);
  }

  prune(now = this.now()) {
    for (const [receipt, entry] of this.entries) {
      if (entry.expiresAt <= now) this.deleteReceipt(receipt);
    }
    while (this.entries.size > this.maxEntries) {
      this.deleteReceipt(this.entries.keys().next().value);
    }
  }

  touch(receipt, entry) {
    this.entries.delete(receipt);
    entry.lastAccessedAt = this.now();
    this.entries.set(receipt, entry);
  }

  publicSnapshot(entry) {
    return {
      fileVersionReceipt: entry.receipt,
      snapshotVersion: entry.version,
      snapshotCapturedAt: entry.capturedAt,
      snapshotExpiresAt: new Date(entry.expiresAt).toISOString(),
      snapshotOwner: entry.owner ? entry.owner.kind : "opaque_receipt",
    };
  }

  register({ projectPath, filePath, hash, stat, requestContext = {} }) {
    if (!validSha256(hash)) throw new Error("File snapshot hash must be a 64-character SHA-256");
    const identity = this.canonicalIdentity(projectPath, filePath);
    const owner = requestOwner(requestContext);
    const now = this.now();
    this.prune(now);
    while (this.entries.size >= this.maxEntries) {
      this.deleteReceipt(this.entries.keys().next().value);
    }
    const receipt = `fvr1_${this.randomBytes(16).toString("base64url")}`;
    const entry = {
      receipt,
      version: ++this.version,
      projectPath: String(projectPath),
      filePath: String(filePath),
      canonicalProject: identity.canonicalProject,
      canonicalPath: identity.canonicalPath,
      pathKey: identity.pathKey,
      hash: String(hash).toLowerCase(),
      size: Number(stat?.size || 0),
      signature: statSignature(stat),
      mtimeMs: Number(stat?.mtimeMs || 0),
      capturedAt: new Date(now).toISOString(),
      expiresAt: now + this.ttlMs,
      lastAccessedAt: now,
      owner,
    };
    this.entries.set(receipt, entry);
    return { entry: { ...entry }, ...this.publicSnapshot(entry) };
  }

  entryForReceipt(receipt, identity, owner) {
    this.prune();
    const entry = this.entries.get(receipt);
    if (!entry) {
      return {
        ok: false,
        errorCode: "FILE_SNAPSHOT_INVALID",
        message: "fileVersionReceipt is missing, expired, evicted, or not issued by this runtime.",
      };
    }
    if (entry.pathKey !== identity.pathKey || !sameOwner(entry.owner, owner)) {
      return {
        ok: false,
        errorCode: "FILE_SNAPSHOT_SCOPE_MISMATCH",
        message: "fileVersionReceipt belongs to a different project, path, or reliable conversation/session owner.",
      };
    }
    this.touch(receipt, entry);
    return { ok: true, entry };
  }

  resolve({
    projectPath,
    filePath,
    expectedHash,
    fileVersionReceipt,
    requestContext = {},
  }) {
    if (expectedHash !== undefined && typeof expectedHash !== "string") {
      return {
        ok: false,
        errorCode: "FILE_SNAPSHOT_REQUIRED",
        message: "expectedHash must be a string when provided.",
        malformedExpectedHash: true,
      };
    }
    if (fileVersionReceipt !== undefined && typeof fileVersionReceipt !== "string") {
      return {
        ok: false,
        errorCode: "FILE_SNAPSHOT_REQUIRED",
        message: "fileVersionReceipt must be a string when provided.",
        malformedExpectedHash: false,
      };
    }
    const rawHash = (expectedHash || "").trim();
    if (validSha256(rawHash)) {
      return {
        ok: true,
        expectedHash: rawHash.toLowerCase(),
        hashSource: "explicit_expected_hash",
        malformedExpectedHash: false,
      };
    }
    const malformedExpectedHash = Boolean(rawHash);
    if (malformedExpectedHash) {
      return {
        ok: false,
        errorCode: "FILE_SNAPSHOT_REQUIRED",
        message: "expectedHash must be a valid 64-character SHA-256, or omit it and pass fileVersionReceipt.",
        malformedExpectedHash: true,
      };
    }
    const identity = this.canonicalIdentity(projectPath, filePath);
    const owner = requestOwner(requestContext);
    const receipt = (fileVersionReceipt || "").trim();
    if (receipt) {
      const resolved = this.entryForReceipt(receipt, identity, owner);
      if (!resolved.ok) return { ...resolved, malformedExpectedHash };
      return {
        ok: true,
        expectedHash: resolved.entry.hash,
        hashSource: "file_version_receipt",
        malformedExpectedHash,
        snapshot: this.publicSnapshot(resolved.entry),
      };
    }
    return {
      ok: false,
      errorCode: "FILE_SNAPSHOT_REQUIRED",
      message: "Pass the explicit fileVersionReceipt returned by a read or immediately preceding mutation, or a valid raw expectedHash.",
      malformedExpectedHash,
    };
  }

  invalidatePath(projectPath, filePath) {
    const identity = this.canonicalIdentity(projectPath, filePath);
    for (const [receipt, entry] of this.entries) {
      if (entry.pathKey === identity.pathKey) this.deleteReceipt(receipt);
    }
  }

  stats() {
    this.prune();
    return {
      entries: this.entries.size,
      maxEntries: this.maxEntries,
      ttlMs: this.ttlMs,
    };
  }
}

module.exports = {
  FileSnapshotRegistry,
  requestOwner,
  validSha256,
};
