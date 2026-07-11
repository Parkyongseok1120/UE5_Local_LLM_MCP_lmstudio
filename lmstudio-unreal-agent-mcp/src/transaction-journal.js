"use strict";

const crypto = require("crypto");
const fs = require("fs");
const fsp = fs.promises;
const path = require("path");
const { atomicWriteText } = require("./atomic-io");
const { sha256Text } = require("./safe-write");
const { ensureStateRootLayout, resolveAgentStateRoot } = require("./state-root");

const MAX_ARCHIVED = 50;

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
  const recovery = { recovered: [], recoveryRequired: [], scanned: 0 };
  for (const name of fs.readdirSync(dir)) {
    if (!name.endsWith(".json")) {
      continue;
    }
    recovery.scanned += 1;
    const journal = JSON.parse(fs.readFileSync(path.join(dir, name), "utf8"));
    if (journal.status === "completed" || journal.status === "archived") {
      continue;
    }
    for (const entry of completedEntries(journal)) {
      const abs = entry.canonicalAbsolutePath;
      let currentHash = "";
      if (fs.existsSync(abs)) {
        currentHash = sha256Text(await fsp.readFile(abs, "utf8"));
      }
      if (entry.postHash && currentHash === entry.postHash) {
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
          recovery.recoveryRequired.push({ path: entry.relativePath, error: String(err.message || err) });
        }
      } else {
        recovery.recoveryRequired.push({ path: entry.relativePath, reason: "external_change_detected" });
      }
    }
    journal.status = recovery.recoveryRequired.length ? "recovery_required" : "recovered";
    saveJournal(journal);
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
};
