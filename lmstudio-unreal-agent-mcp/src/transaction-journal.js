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
]);
const PENDING_BUILD_STATUSES = new Set(["write_armed", "committed", "awaiting_build", "build_failed"]);

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

function createJournal(
  { transactionId = createTransactionId(), operation = "apply_edit_bundle" } = {},
  stateRoot = resolveAgentStateRoot(),
) {
  const journal = {
    transactionId,
    operation,
    status: "planned",
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    entries: [],
  };
  saveJournal(journal, stateRoot);
  return journal;
}

function backupPath(stateRoot, transactionId, relativePath) {
  const digest = crypto.createHash("sha256").update(String(relativePath || "")).digest("hex").slice(0, 16);
  return path.join(ensureStateRootLayout(stateRoot), "backups", `${transactionId}-${digest}.bak`);
}

function prepareSingleFileJournal({
  operation,
  absolutePath,
  relativePath,
  priorContent,
  postContent,
  deletedAfter = false,
  taskSessionId = "",
  projectPath = "",
}, stateRoot = resolveAgentStateRoot()) {
  const journal = createJournal({ operation: String(operation || "mutation") }, stateRoot);
  const existedBefore = priorContent !== null && priorContent !== undefined;
  let preContentBackupPath = null;
  if (existedBefore) {
    preContentBackupPath = backupPath(stateRoot, journal.transactionId, relativePath);
    atomicWriteText(preContentBackupPath, String(priorContent));
  }
  journal.taskSessionId = String(taskSessionId || "");
  journal.projectPath = String(projectPath || "");
  journal.status = "write_armed";
  upsertEntry(journal, {
    relativePath: String(relativePath || "").replace(/\\/g, "/"),
    canonicalAbsolutePath: path.resolve(absolutePath),
    operation: String(operation || "mutation"),
    existedBefore,
    preHash: existedBefore ? sha256Text(String(priorContent)) : "",
    preContentBackupPath,
    postHash: deletedAfter ? "" : sha256Text(String(postContent || "")),
    deletedAfter: Boolean(deletedAfter),
    writeStarted: true,
    writeCompleted: true,
    restored: false,
  }, stateRoot);
  saveJournal(journal, stateRoot);
  return journal;
}

function markJournalAwaitingBuild(journal, metadata = {}, stateRoot = resolveAgentStateRoot()) {
  Object.assign(journal, metadata || {});
  journal.status = "awaiting_build";
  journal.updatedAt = new Date().toISOString();
  saveJournal(journal, stateRoot);
  return journal;
}

function projectPathIdentity(value, platform = process.platform) {
  const resolved = path.resolve(String(value || "."));
  return platform === "win32" ? resolved.toLowerCase() : resolved;
}

function listPendingJournals({ taskSessionId = "", projectPath = "" } = {}, stateRoot = resolveAgentStateRoot()) {
  const dir = journalDir(stateRoot);
  const task = String(taskSessionId || "");
  const project = projectPathIdentity(projectPath);
  const rows = [];
  for (const name of fs.readdirSync(dir)) {
    if (!name.endsWith(".json")) continue;
    let journal;
    try {
      journal = JSON.parse(fs.readFileSync(path.join(dir, name), "utf8"));
    } catch {
      continue;
    }
    if (!PENDING_BUILD_STATUSES.has(String(journal.status || ""))) continue;
    if (task && String(journal.taskSessionId || "") !== task) continue;
    if (
      projectPath
      && projectPathIdentity(journal.projectPath) !== project
    ) continue;
    rows.push(journal);
  }
  return rows.sort((left, right) => String(left.createdAt || "").localeCompare(String(right.createdAt || "")));
}

async function finalizePendingJournals(selector = {}, stateRoot = resolveAgentStateRoot()) {
  const journals = listPendingJournals(selector, stateRoot);
  const finalized = [];
  for (const journal of journals) {
    journal.status = "completed";
    journal.updatedAt = new Date().toISOString();
    saveJournal(journal, stateRoot);
    await archiveJournal(journal.transactionId, stateRoot);
    finalized.push(journal.transactionId);
  }
  return { ok: true, finalized };
}

function markPendingBuildFailed(selector = {}, details = {}, stateRoot = resolveAgentStateRoot()) {
  const journals = listPendingJournals(selector, stateRoot);
  for (const journal of journals) {
    journal.status = "build_failed";
    journal.buildFailure = { ...(details || {}), at: new Date().toISOString() };
    journal.updatedAt = new Date().toISOString();
    saveJournal(journal, stateRoot);
  }
  return { ok: true, transactionIds: journals.map((item) => item.transactionId) };
}

function upsertEntry(journal, entry, stateRoot = resolveAgentStateRoot()) {
  const idx = journal.entries.findIndex((item) => item.relativePath === entry.relativePath);
  if (idx >= 0) {
    journal.entries[idx] = { ...journal.entries[idx], ...entry };
  } else {
    journal.entries.push(entry);
  }
  journal.updatedAt = new Date().toISOString();
  saveJournal(journal, stateRoot);
  return journal;
}

function completedEntries(journal) {
  return (journal.entries || []).filter((entry) => entry.writeCompleted);
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
      const existsNow = fs.existsSync(abs);
      let currentHash = "";
      if (existsNow) {
        currentHash = sha256Text(await fsp.readFile(abs, "utf8"));
      }
      const postStateMatches = entry.deletedAfter === true
        ? !existsNow
        : Boolean(entry.postHash) && currentHash === entry.postHash;
      if (postStateMatches) {
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
    saveJournal(journal, stateRoot);
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
  TERMINAL_JOURNAL_STATUSES,
  PENDING_BUILD_STATUSES,
  prepareSingleFileJournal,
  markJournalAwaitingBuild,
  listPendingJournals,
  finalizePendingJournals,
  markPendingBuildFailed,
  projectPathIdentity,
};
