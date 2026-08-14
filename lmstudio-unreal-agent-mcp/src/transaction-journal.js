"use strict";

const crypto = require("crypto");
const fs = require("fs");
const fsp = fs.promises;
const path = require("path");
const { atomicWriteText } = require("./atomic-io");
const { sha256Text } = require("./safe-write");
const { ensureStateRootLayout, resolveAgentStateRoot } = require("./state-root");

const MAX_ARCHIVED = 50;
const TERMINAL_JOURNAL_STATUSES = new Set([
  "completed",
  "archived",
  "recovered",
  "rolled_back",
  "aborted",
  "recovery_required",
  "awaiting_build",
  "validation_failed",
  "build_failed",
  "built_awaiting_automation",
]);
const BUILD_SENSITIVE_EXTENSIONS = new Set([
  ".h", ".hpp", ".inl", ".cpp", ".c", ".cc", ".cxx", ".cs",
  ".ini", ".uplugin", ".uproject",
]);
const PENDING_BUILD_STATUSES = new Set([
  "awaiting_build",
  "validation_failed",
  "build_failed",
  "built_awaiting_automation",
]);

function journalDir(stateRoot = resolveAgentStateRoot()) {
  return path.join(ensureStateRootLayout(stateRoot), "transactions");
}

function journalPath(transactionId, stateRoot = resolveAgentStateRoot()) {
  return path.join(journalDir(stateRoot), `${transactionId}.json`);
}

function createTransactionId() {
  return crypto.randomUUID();
}

function loadJournal(transactionId, stateRoot = resolveAgentStateRoot()) {
  const file = journalPath(transactionId, stateRoot);
  if (!fs.existsSync(file)) {
    return null;
  }
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

function saveJournal(journal, stateRoot = resolveAgentStateRoot()) {
  const file = journalPath(journal.transactionId, stateRoot);
  atomicWriteText(file, JSON.stringify(journal, null, 2));
}

function createJournal({ transactionId = createTransactionId(), operation = "apply_edit_bundle" } = {}) {
  const journal = {
    transactionId,
    operation,
    status: "planned",
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    entries: [],
  };
  saveJournal(journal);
  return journal;
}

function pathIdentity(value) {
  const resolved = path.resolve(String(value || ""));
  const normalized = resolved.replace(/\\/g, "/");
  return process.platform === "win32" ? normalized.toLowerCase() : normalized;
}

function requiresBuildValidation(filePath) {
  const normalized = String(filePath || "").replace(/\\/g, "/").toLowerCase();
  return BUILD_SENSITIVE_EXTENSIONS.has(path.posix.extname(normalized))
    || normalized.endsWith(".build.cs")
    || normalized.endsWith(".target.cs");
}

function backupPathFor(transactionId, relativePath, stateRoot = resolveAgentStateRoot()) {
  const digest = crypto.createHash("sha256").update(String(relativePath)).digest("hex").slice(0, 16);
  return path.join(ensureStateRootLayout(stateRoot), "backups", `${transactionId}-${digest}.bak`);
}

function beginMutationJournal({
  operation,
  projectRoot,
  taskSessionId = "",
  relativePath,
  canonicalAbsolutePath,
  existedBefore,
  preContent = null,
  intendedPostContent = null,
  deleteTarget = false,
} = {}) {
  const journal = createJournal({ operation: operation || "mutation" });
  journal.projectRoot = pathIdentity(projectRoot || path.dirname(canonicalAbsolutePath));
  journal.taskSessionId = String(taskSessionId || "").trim();
  const backup = existedBefore
    ? backupPathFor(journal.transactionId, relativePath)
    : null;
  if (backup) atomicWriteText(backup, String(preContent ?? ""));
  upsertEntry(journal, {
    relativePath: String(relativePath || "").replace(/\\/g, "/"),
    canonicalAbsolutePath: path.resolve(canonicalAbsolutePath),
    operation: operation || "mutation",
    existedBefore: Boolean(existedBefore),
    preHash: existedBefore ? sha256Text(String(preContent ?? "")) : "",
    preContentBackupPath: backup,
    // Persist the intended post-image before touching the target.  If the
    // process dies after the filesystem write but before commit, startup
    // recovery can still perform a compare-and-swap rollback safely.
    postHash: intendedPostContent == null
      ? ""
      : sha256Text(String(intendedPostContent)),
    deletedAfter: Boolean(deleteTarget),
    writeStarted: intendedPostContent != null || deleteTarget,
    writeCompleted: false,
  });
  journal.status = "prepared";
  saveJournal(journal);
  return journal;
}

function commitMutationJournal(journalOrId, postContent, metadata = {}) {
  const journal = typeof journalOrId === "string" ? loadJournal(journalOrId) : journalOrId;
  if (!journal) throw new Error("mutation journal disappeared before commit");
  const entry = journal.entries?.[0];
  if (!entry) throw new Error("mutation journal has no baseline entry");
  upsertEntry(journal, {
    relativePath: entry.relativePath,
    postHash: metadata.deletedAfter ? "" : sha256Text(String(postContent ?? "")),
    deletedAfter: Boolean(metadata.deletedAfter),
    writeStarted: true,
    writeCompleted: true,
    restored: false,
  });
  journal.status = requiresBuildValidation(entry.relativePath) ? "awaiting_build" : "completed";
  journal.mutationGeneration = Number(metadata.mutationGeneration || 0);
  journal.taskSessionId = String(metadata.taskSessionId || journal.taskSessionId || "").trim();
  journal.projectRoot = pathIdentity(metadata.projectRoot || journal.projectRoot);
  journal.updatedAt = new Date().toISOString();
  saveJournal(journal);
  return journal;
}

async function abandonMutationJournal(journalOrId, status = "aborted") {
  const journal = typeof journalOrId === "string" ? loadJournal(journalOrId) : journalOrId;
  if (!journal) return;
  journal.status = String(status || "aborted");
  journal.updatedAt = new Date().toISOString();
  saveJournal(journal);
  await archiveJournal(journal.transactionId);
}

function pendingBuildJournals({
  projectRoot,
  taskSessionId = "",
  mutationGeneration = null,
  statuses = null,
} = {}) {
  const dir = journalDir();
  const projectKey = projectRoot ? pathIdentity(projectRoot) : "";
  const taskKey = String(taskSessionId || "").trim();
  const maxGeneration = mutationGeneration == null ? null : Number(mutationGeneration);
  const requestedStatuses = Array.isArray(statuses) && statuses.length
    ? new Set(statuses.map((item) => String(item || "")))
    : PENDING_BUILD_STATUSES;
  const pending = [];
  for (const name of fs.readdirSync(dir)) {
    if (!name.endsWith(".json")) continue;
    try {
      const journal = JSON.parse(fs.readFileSync(path.join(dir, name), "utf8"));
      if (!requestedStatuses.has(String(journal.status || ""))) continue;
      if (projectKey && pathIdentity(journal.projectRoot) !== projectKey) continue;
      if (taskKey && String(journal.taskSessionId || "") !== taskKey) continue;
      if (
        maxGeneration != null
        && Number(journal.mutationGeneration || 0) > maxGeneration
      ) continue;
      pending.push(journal);
    } catch {
      // Corrupt journals are handled by startup recovery diagnostics.
    }
  }
  return pending.sort((left, right) => String(left.createdAt || "").localeCompare(String(right.createdAt || "")));
}

async function finalizePendingBuildJournals(query, status = "completed") {
  const finalized = [];
  for (const journal of pendingBuildJournals(query)) {
    journal.status = status;
    journal.updatedAt = new Date().toISOString();
    saveJournal(journal);
    if (status === "completed") await archiveJournal(journal.transactionId);
    finalized.push(journal.transactionId);
  }
  return finalized;
}

function upsertEntry(journal, entry) {
  const idx = journal.entries.findIndex((item) => item.relativePath === entry.relativePath);
  if (idx >= 0) {
    journal.entries[idx] = { ...journal.entries[idx], ...entry };
  } else {
    journal.entries.push(entry);
  }
  journal.updatedAt = new Date().toISOString();
  saveJournal(journal);
  return journal;
}

function completedEntries(journal) {
  return (journal.entries || []).filter((entry) => (
    entry.writeCompleted || (entry.writeStarted && (entry.postHash || entry.deletedAfter))
  ));
}

async function archiveJournal(transactionId, stateRoot = resolveAgentStateRoot()) {
  const src = journalPath(transactionId, stateRoot);
  if (!fs.existsSync(src)) {
    return;
  }
  const archiveRoot = path.join(journalDir(stateRoot), "archive");
  fs.mkdirSync(archiveRoot, { recursive: true });
  const dest = path.join(archiveRoot, `${transactionId}.json`);
  fs.renameSync(src, dest);
  const archives = fs.readdirSync(archiveRoot).sort().reverse();
  for (const extra of archives.slice(MAX_ARCHIVED)) {
    try {
      fs.unlinkSync(path.join(archiveRoot, extra));
    } catch {
      // ignore
    }
  }
}

async function recoverIncompleteJournals(stateRoot = resolveAgentStateRoot()) {
  const dir = journalDir(stateRoot);
  const recovery = {
    recovered: [],
    recoveryRequired: [],
    skippedCorrupt: [],
    skippedTerminal: [],
    scanned: 0,
  };
  for (const name of fs.readdirSync(dir)) {
    if (!name.endsWith(".json")) {
      continue;
    }
    recovery.scanned += 1;
    let journal;
    try {
      journal = JSON.parse(fs.readFileSync(path.join(dir, name), "utf8"));
    } catch {
      recovery.skippedCorrupt.push(name);
      continue;
    }
    if (TERMINAL_JOURNAL_STATUSES.has(journal.status)) {
      recovery.skippedTerminal.push(journal.transactionId || name);
      continue;
    }
    const localRequired = [];
    for (const entry of completedEntries(journal)) {
      const abs = entry.canonicalAbsolutePath;
      let currentHash = "";
      const existsNow = fs.existsSync(abs);
      if (existsNow) {
        currentHash = sha256Text(await fsp.readFile(abs, "utf8"));
      }
      const preImageIntact = entry.existedBefore
        ? existsNow && Boolean(entry.preHash && currentHash === entry.preHash)
        : !existsNow;
      if (preImageIntact) {
        recovery.recovered.push(entry.relativePath);
        continue;
      }
      const expectedPostImage = entry.deletedAfter
        ? !existsNow
        : Boolean(entry.postHash && currentHash === entry.postHash);
      if (expectedPostImage) {
        try {
          if (entry.existedBefore) {
            if (entry.preContentBackupPath && fs.existsSync(entry.preContentBackupPath)) {
              atomicWriteText(abs, fs.readFileSync(entry.preContentBackupPath, "utf8"));
            } else if (entry.preContent != null) {
              atomicWriteText(abs, entry.preContent);
            }
          } else if (fs.existsSync(abs)) {
            await fsp.unlink(abs);
          }
          recovery.recovered.push(entry.relativePath);
        } catch (err) {
          const item = { path: entry.relativePath, error: String(err.message || err) };
          localRequired.push(item);
          recovery.recoveryRequired.push(item);
        }
      } else {
        const item = { path: entry.relativePath, reason: "external_change_detected" };
        localRequired.push(item);
        recovery.recoveryRequired.push(item);
      }
    }
    journal.status = localRequired.length ? "recovery_required" : "recovered";
    journal.updatedAt = new Date().toISOString();
    saveJournal(journal);
    if (!localRequired.length) {
      await archiveJournal(journal.transactionId, stateRoot);
    }
  }
  return recovery;
}

module.exports = {
  createTransactionId,
  createJournal,
  loadJournal,
  saveJournal,
  upsertEntry,
  completedEntries,
  archiveJournal,
  recoverIncompleteJournals,
  pathIdentity,
  requiresBuildValidation,
  beginMutationJournal,
  commitMutationJournal,
  abandonMutationJournal,
  pendingBuildJournals,
  finalizePendingBuildJournals,
  TERMINAL_JOURNAL_STATUSES,
  PENDING_BUILD_STATUSES,
};
