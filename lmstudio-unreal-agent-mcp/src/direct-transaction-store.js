"use strict";

const fs = require("node:fs");
const fsp = fs.promises;
const path = require("node:path");
const crypto = require("node:crypto");
const { atomicWriteText } = require("./atomic-io");
const { resolveAgentStateRoot } = require("./runtime-state-root");

const RUNTIME_OWNERS = new Set(["direct", "strict"]);
const JOURNAL_STATUSES = new Set([
  "prepared",
  "locked",
  "committing",
  "committed",
  "completed",
  "aborted",
  "rolled_back",
  "rollback_incomplete",
]);
const JOURNAL_FIELDS = new Set([
  "schemaVersion",
  "runtimeOwner",
  "transactionId",
  "operation",
  "status",
  "projectRoot",
  "projectPath",
  "createdAt",
  "updatedAt",
  "entries",
  "error",
]);
const ENTRY_FIELDS = new Set([
  "relativePath",
  "canonicalAbsolutePath",
  "operation",
  "existedBefore",
  "preHash",
  "preContentBackupPath",
  "postHash",
  "intendedPostHashes",
  "writeStarted",
  "writeCompleted",
  "restored",
  "rollbackSkippedReason",
]);
const CREATE_TRANSACTION_FIELDS = new Set([
  "runtimeOwner",
  "stateRoot",
  "projectRoot",
  "projectPath",
  "transactionId",
]);

function normalizeRuntimeOwner(owner) {
  const value = String(owner || "").trim().toLowerCase();
  if (!RUNTIME_OWNERS.has(value)) throw new Error(`Unsupported transaction runtime owner: ${owner}`);
  return value;
}

function runtimeTransactionPaths(stateRoot, owner) {
  const runtimeOwner = normalizeRuntimeOwner(owner);
  const root = path.join(path.resolve(stateRoot || resolveAgentStateRoot()), `${runtimeOwner}-transactions`);
  return {
    runtimeOwner,
    root,
    pending: path.join(root, "pending"),
    archive: path.join(root, "archive"),
    backups: path.join(root, "backups"),
  };
}

function ensureRuntimeTransactionLayout(stateRoot, owner) {
  const paths = runtimeTransactionPaths(stateRoot, owner);
  for (const directory of [paths.pending, paths.archive, paths.backups]) {
    fs.mkdirSync(directory, { recursive: true });
  }
  return paths;
}

function safeTransactionId(transactionId) {
  const value = String(transactionId || "").trim();
  if (!/^[a-zA-Z0-9-]{8,128}$/u.test(value)) throw new Error("Invalid transactionId");
  return value;
}

function assertOnlyFields(value, allowed, label) {
  for (const key of Object.keys(value || {})) {
    if (!allowed.has(key)) throw new Error(`${label} contains unsupported field: ${key}`);
  }
}

function validateEntry(entry) {
  if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
    throw new Error("Transaction entry must be an object");
  }
  assertOnlyFields(entry, ENTRY_FIELDS, "Transaction entry");
  if (!String(entry.relativePath || "").trim()) throw new Error("Transaction entry requires relativePath");
  if (!path.isAbsolute(String(entry.canonicalAbsolutePath || ""))) {
    throw new Error("Transaction entry requires an absolute target path");
  }
  if (!new Set(["baseline", "patch", "create"]).has(entry.operation)) {
    throw new Error(`Unsupported transaction entry operation: ${entry.operation}`);
  }
  if (!Array.isArray(entry.intendedPostHashes)) throw new Error("intendedPostHashes must be an array");
  if (entry.intendedPostHashes.length > 128) throw new Error("Too many intended post-image hashes");
  for (const hash of [entry.preHash, entry.postHash, ...entry.intendedPostHashes].filter(Boolean)) {
    if (!/^[a-f0-9]{64}$/iu.test(String(hash))) throw new Error("Transaction entry contains an invalid SHA-256");
  }
}

function validateRuntimeTransaction(journal, expectedOwner = journal?.runtimeOwner) {
  if (!journal || typeof journal !== "object" || Array.isArray(journal)) {
    throw new Error("Transaction journal must be an object");
  }
  assertOnlyFields(journal, JOURNAL_FIELDS, "Transaction journal");
  const owner = normalizeRuntimeOwner(expectedOwner);
  if (journal.schemaVersion !== 1) throw new Error("Unsupported transaction schemaVersion");
  if (normalizeRuntimeOwner(journal.runtimeOwner) !== owner) {
    throw new Error(`Transaction owner mismatch: expected ${owner}`);
  }
  safeTransactionId(journal.transactionId);
  if (journal.operation !== "edit_bundle") throw new Error("Unsupported transaction operation");
  if (!JOURNAL_STATUSES.has(journal.status)) {
    throw new Error(`Unsupported transaction status: ${journal.status}`);
  }
  if (!path.isAbsolute(String(journal.projectRoot || ""))) {
    throw new Error("Transaction projectRoot must be absolute");
  }
  if (!Array.isArray(journal.entries) || journal.entries.length > 32) {
    throw new Error("Transaction entries must be a bounded array");
  }
  journal.entries.forEach(validateEntry);
  return journal;
}

function transactionFilePath(stateRoot, owner, transactionId, archived = false) {
  const paths = ensureRuntimeTransactionLayout(stateRoot, owner);
  return path.join(archived ? paths.archive : paths.pending, `${safeTransactionId(transactionId)}.json`);
}

function createRuntimeTransaction(options = {}) {
  assertOnlyFields(options, CREATE_TRANSACTION_FIELDS, "Transaction options");
  const runtimeOwner = normalizeRuntimeOwner(options.runtimeOwner);
  const now = new Date().toISOString();
  const journal = {
    schemaVersion: 1,
    runtimeOwner,
    transactionId: String(options.transactionId || crypto.randomUUID()),
    operation: "edit_bundle",
    status: "prepared",
    projectRoot: path.resolve(options.projectRoot),
    projectPath: String(options.projectPath || ""),
    createdAt: now,
    updatedAt: now,
    entries: [],
    error: "",
  };
  saveRuntimeTransaction(journal, options.stateRoot);
  return journal;
}

function saveRuntimeTransaction(journal, stateRoot) {
  journal.updatedAt = new Date().toISOString();
  validateRuntimeTransaction(journal, journal.runtimeOwner);
  const target = transactionFilePath(stateRoot, journal.runtimeOwner, journal.transactionId);
  atomicWriteText(target, `${JSON.stringify(journal, null, 2)}\n`, "utf8");
  return target;
}

function updateRuntimeTransactionEntry(journal, patch, stateRoot) {
  const relativePath = String(patch.relativePath || "").replace(/\\/g, "/");
  const index = journal.entries.findIndex((entry) => entry.relativePath === relativePath);
  const prior = index >= 0 ? journal.entries[index] : {
    relativePath,
    canonicalAbsolutePath: path.resolve(patch.canonicalAbsolutePath),
    operation: "baseline",
    existedBefore: false,
    preHash: "",
    preContentBackupPath: "",
    postHash: "",
    intendedPostHashes: [],
    writeStarted: false,
    writeCompleted: false,
    restored: false,
    rollbackSkippedReason: "",
  };
  const next = { ...prior, ...patch, relativePath };
  if (index >= 0) journal.entries[index] = next;
  else journal.entries.push(next);
  saveRuntimeTransaction(journal, stateRoot);
  return next;
}

function transactionBackupPath(stateRoot, owner, transactionId, relativePath) {
  const paths = ensureRuntimeTransactionLayout(stateRoot, owner);
  const digest = crypto.createHash("sha256").update(String(relativePath)).digest("hex").slice(0, 20);
  return path.join(paths.backups, `${safeTransactionId(transactionId)}-${digest}.bak`);
}

async function readRuntimeTransaction(filePath, expectedOwner) {
  const stat = await fsp.stat(filePath);
  if (!stat.isFile() || stat.size > 4 * 1024 * 1024) throw new Error("Invalid transaction journal size");
  const journal = JSON.parse(await fsp.readFile(filePath, "utf8"));
  return validateRuntimeTransaction(journal, expectedOwner);
}

async function listRuntimeTransactionFiles(stateRoot, owner) {
  const paths = ensureRuntimeTransactionLayout(stateRoot, owner);
  const entries = await fsp.readdir(paths.pending, { withFileTypes: true });
  return entries
    .filter((entry) => entry.isFile() && entry.name.endsWith(".json"))
    .map((entry) => path.join(paths.pending, entry.name))
    .sort()
    .slice(0, 1000);
}

async function archiveRuntimeTransaction(journal, stateRoot) {
  validateRuntimeTransaction(journal, journal.runtimeOwner);
  const source = transactionFilePath(stateRoot, journal.runtimeOwner, journal.transactionId);
  const destination = transactionFilePath(stateRoot, journal.runtimeOwner, journal.transactionId, true);
  await fsp.rename(source, destination);
  return destination;
}

module.exports = {
  archiveRuntimeTransaction,
  createRuntimeTransaction,
  ensureRuntimeTransactionLayout,
  listRuntimeTransactionFiles,
  normalizeRuntimeOwner,
  readRuntimeTransaction,
  runtimeTransactionPaths,
  saveRuntimeTransaction,
  transactionBackupPath,
  transactionFilePath,
  updateRuntimeTransactionEntry,
  validateRuntimeTransaction,
};
