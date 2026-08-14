"use strict";

const crypto = require("crypto");
const fs = require("fs");
const fsp = fs.promises;
const path = require("path");
const { atomicWriteText } = require("./atomic-io");
const { sha256Text } = require("./safe-write");
const { ensureStateRootLayout, resolveAgentStateRoot, taskStateDir } = require("./state-root");
const {
  compensateMutationBatch,
  inspectMutationCompensation,
  reconcileMutationPathsFromDisk,
} = require("./mutation-generation");

const MAX_ARCHIVED = 50;
const TERMINAL_JOURNAL_STATUSES = new Set([
  "completed",
  "archived",
  "recovered",
  "superseded",
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
  "write_armed",
  "committed",
  "mutation_state_pending",
  "checkpoint_pending",
  "rollback_disk_pending",
  "rollback_state_pending",
  "awaiting_build",
  "validation_failed",
  "build_failed",
  "built_awaiting_automation",
]);
const CHECKPOINT_GUARDED_STATUSES = new Set([
  "completed",
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
  if (journal?.requiresAtomicCheckpoint === true) {
    if (
      journal.checkpointCommitted !== true
      && CHECKPOINT_GUARDED_STATUSES.has(String(journal.status || ""))
    ) {
      const attemptedStatus = String(journal.status);
      if (["completed", "awaiting_build"].includes(attemptedStatus)) {
        journal.postCheckpointStatus = attemptedStatus;
      } else {
        journal.prematureTerminalStatus = attemptedStatus;
      }
      journal.status = journal.mutationStateRecorded === true
        ? "checkpoint_pending"
        : "mutation_state_pending";
    }
    if (
      String(journal.status || "") === "rolled_back"
      && (
        journal.mutationStateRecorded === true
        || Boolean(journal.mutationCompensationReceipt)
      )
      && journal.rollbackStateReconciled !== true
    ) {
      journal.status = "rollback_state_pending";
    }
  }
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
  // Legacy single-file journals have already recorded a completed filesystem
  // write and must be rolled back after an interrupted workflow. RC2 journals
  // intentionally keep a validated mutation pending across process restart;
  // those do not set this compatibility flag.
  journal.recoverOnRestart = true;
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
  checkpointRequired = true,
} = {}) {
  const journal = createJournal({ operation: operation || "mutation" });
  journal.projectRoot = pathIdentity(projectRoot || path.dirname(canonicalAbsolutePath));
  journal.taskSessionId = String(taskSessionId || "").trim();
  journal.requiresAtomicCheckpoint = true;
  journal.checkpointRequired = checkpointRequired !== false;
  journal.checkpointCommitted = false;
  journal.mutationStateRecorded = false;
  journal.mutationStateRequired = true;
  journal.rollbackStateReconciled = false;
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
  journal.postCheckpointStatus = requiresBuildValidation(entry.relativePath)
    ? "awaiting_build"
    : "completed";
  journal.status = journal.mutationStateRecorded === true
    ? "checkpoint_pending"
    : "mutation_state_pending";
  journal.mutationGeneration = Number(metadata.mutationGeneration || 0);
  journal.taskSessionId = String(metadata.taskSessionId || journal.taskSessionId || "").trim();
  journal.projectRoot = pathIdentity(metadata.projectRoot || journal.projectRoot);
  journal.updatedAt = new Date().toISOString();
  saveJournal(journal);
  return journal;
}

function atomicJournal(journalOrId) {
  const journal = typeof journalOrId === "string" ? loadJournal(journalOrId) : journalOrId;
  if (!journal) throw new Error("mutation journal disappeared");
  return journal;
}

function armAtomicMutationJournal(journalOrId, metadata = {}) {
  const journal = atomicJournal(journalOrId);
  journal.requiresAtomicCheckpoint = true;
  journal.checkpointRequired = metadata.checkpointRequired !== false;
  journal.checkpointCommitted = false;
  journal.mutationStateRecorded = false;
  journal.mutationStateRequired = metadata.mutationStateRequired !== false;
  journal.rollbackStateReconciled = false;
  journal.projectRoot = pathIdentity(metadata.projectRoot || journal.projectRoot);
  journal.taskSessionId = String(metadata.taskSessionId || journal.taskSessionId || "").trim();
  journal.status = "mutation_state_pending";
  journal.updatedAt = new Date().toISOString();
  saveJournal(journal);
  return journal;
}

function stageMutationCompensation(journalOrId, receipt, metadata = {}) {
  const journal = atomicJournal(journalOrId);
  journal.requiresAtomicCheckpoint = true;
  journal.mutationCompensationReceipt = JSON.parse(JSON.stringify(receipt));
  journal.mutationStateRecorded = false;
  journal.mutationGeneration = Number(
    metadata.mutationGeneration || receipt?.expectedMutationGeneration || 0
  );
  journal.mutationRevision = Number(
    metadata.mutationRevision || receipt?.expectedMutationRevision || 0
  );
  journal.projectRoot = pathIdentity(metadata.projectRoot || journal.projectRoot);
  journal.taskSessionId = String(metadata.taskSessionId || journal.taskSessionId || "").trim();
  journal.status = "mutation_state_pending";
  journal.updatedAt = new Date().toISOString();
  saveJournal(journal);
  return journal;
}

function markMutationStateRecorded(journalOrId, metadata = {}) {
  const journal = atomicJournal(journalOrId);
  journal.mutationStateRequired = metadata.mutationStateRequired !== false;
  journal.mutationStateRecorded = true;
  journal.mutationGeneration = Number(metadata.mutationGeneration || journal.mutationGeneration || 0);
  journal.mutationRevision = Number(metadata.mutationRevision || journal.mutationRevision || 0);
  journal.status = "checkpoint_pending";
  journal.updatedAt = new Date().toISOString();
  saveJournal(journal);
  return journal;
}

async function completeMutationJournalCheckpoint(journalOrId, checkpoint = {}) {
  const journal = atomicJournal(journalOrId);
  journal.checkpointCommitted = true;
  journal.checkpoint = {
    status: checkpoint.skipped === true ? "not_required" : "recorded",
    checkpointHash: String(checkpoint.checkpointHash || ""),
    phase: String(checkpoint.phase || "executor"),
    recordedAt: new Date().toISOString(),
  };
  journal.status = String(journal.postCheckpointStatus || "completed");
  journal.updatedAt = new Date().toISOString();
  saveJournal(journal);
  if (journal.status === "completed") await archiveJournal(journal.transactionId);
  return journal;
}

function armMutationRollback(journalOrId, metadata = {}, stateRoot = resolveAgentStateRoot()) {
  const journal = atomicJournal(journalOrId);
  journal.requiresAtomicCheckpoint = true;
  journal.rollbackStateReconciled = false;
  journal.rollbackCheckpointCommitted = false;
  journal.rollbackIntent = {
    active: true,
    reason: String(metadata.reason || "workflow_rollback"),
    armedAt: new Date().toISOString(),
  };
  journal.status = "rollback_disk_pending";
  journal.updatedAt = new Date().toISOString();
  saveJournal(journal, stateRoot);
  return journal;
}

async function completeMutationRollback(
  journalOrId,
  reconciliation = {},
  stateRoot = resolveAgentStateRoot()
) {
  const journal = atomicJournal(journalOrId);
  journal.rollbackStateReconciled = true;
  journal.rollbackCheckpointCommitted = reconciliation.checkpointCommitted !== false;
  if (journal.rollbackIntent && typeof journal.rollbackIntent === "object") {
    journal.rollbackIntent.active = false;
    journal.rollbackIntent.completedAt = new Date().toISOString();
  }
  journal.rollbackReconciliation = {
    ...reconciliation,
    reconciledAt: new Date().toISOString(),
  };
  journal.status = "rolled_back";
  journal.updatedAt = new Date().toISOString();
  saveJournal(journal, stateRoot);
  await archiveJournal(journal.transactionId, stateRoot);
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
  return (journal.entries || []).filter((entry) => (
    entry.writeCompleted || (entry.writeStarted && (entry.postHash || entry.deletedAfter))
  ));
}

function intendedPostHashes(entry) {
  const hashes = [
    ...(Array.isArray(entry?.intendedPostHashes) ? entry.intendedPostHashes : []),
    entry?.postHash,
  ];
  return [...new Set(hashes.map((item) => String(item || "").trim()).filter(Boolean))];
}

function entryMatchesOwnedPostImage(entry, existsNow, currentHash) {
  if (entry?.deletedAfter === true) return !existsNow;
  if (!existsNow) return false;
  const current = String(currentHash || "").trim();
  return Boolean(current && intendedPostHashes(entry).includes(current));
}

async function archiveJournal(transactionId, stateRoot = resolveAgentStateRoot()) {
  const src = journalPath(transactionId, stateRoot);
  if (!fs.existsSync(src)) {
    return false;
  }
  try {
    const current = JSON.parse(fs.readFileSync(src, "utf8"));
    const incompleteAtomic = (
      current.requiresAtomicCheckpoint === true
      && current.checkpointCommitted !== true
      && current.rollbackStateReconciled !== true
      && !["aborted", "recovered", "rolled_back", "superseded"].includes(String(current.status || ""))
    );
    if (incompleteAtomic) return false;
  } catch {
    // Leave a corrupt active journal in place for startup diagnostics.
    return false;
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
  return true;
}

function normalizedJournalRelativePath(value) {
  const normalized = String(value || "").replace(/\\/g, "/").replace(/^\.\//, "");
  return process.platform === "win32" ? normalized.toLowerCase() : normalized;
}

async function classifyAtomicDisk(journal) {
  const entries = completedEntries(journal);
  const classifications = [];
  for (const entry of entries) {
    const absolutePath = path.resolve(entry.canonicalAbsolutePath);
    const existsNow = fs.existsSync(absolutePath);
    const currentHash = existsNow ? sha256Text(await fsp.readFile(absolutePath, "utf8")) : "";
    const preImage = entry.existedBefore
      ? existsNow && Boolean(entry.preHash && currentHash === entry.preHash)
      : !existsNow;
    const postImage = entry.deletedAfter
      ? !existsNow
      : existsNow && Boolean(entry.postHash && currentHash === entry.postHash);
    const ownedPostImage = entryMatchesOwnedPostImage(entry, existsNow, currentHash);
    classifications.push({ entry, preImage, postImage, ownedPostImage });
  }
  return {
    entries,
    classifications,
    allPre: classifications.every((item) => item.preImage),
    allPost: classifications.length > 0 && classifications.every((item) => item.postImage),
    hasExternal: classifications.some((item) => !item.preImage && !item.ownedPostImage),
  };
}

async function rollbackAtomicDisk(journal, stateRoot = resolveAgentStateRoot()) {
  const restoredPaths = [];
  const unrestoredPaths = [];
  const rollbackErrors = [];
  const externalChangeDetected = [];
  for (const entry of [...completedEntries(journal)].reverse()) {
    const relPath = String(entry.relativePath || "");
    const absolutePath = path.resolve(entry.canonicalAbsolutePath);
    try {
      const existsNow = fs.existsSync(absolutePath);
      const currentHash = existsNow ? sha256Text(await fsp.readFile(absolutePath, "utf8")) : "";
      const preImage = entry.existedBefore
        ? existsNow && Boolean(entry.preHash && currentHash === entry.preHash)
        : !existsNow;
      if (preImage) {
        restoredPaths.push(relPath);
        entry.restored = true;
        continue;
      }
      const postImage = entryMatchesOwnedPostImage(entry, existsNow, currentHash);
      if (!postImage) {
        unrestoredPaths.push(relPath);
        externalChangeDetected.push(relPath);
        entry.rollbackSkippedReason = "external_change_detected";
        continue;
      }
      if (entry.existedBefore) {
        if (!entry.preContentBackupPath || !fs.existsSync(entry.preContentBackupPath)) {
          throw new Error(`missing backup for ${relPath}`);
        }
        atomicWriteText(absolutePath, fs.readFileSync(entry.preContentBackupPath, "utf8"));
      } else if (fs.existsSync(absolutePath)) {
        await fsp.unlink(absolutePath);
      }
      restoredPaths.push(relPath);
      entry.restored = true;
    } catch (error) {
      unrestoredPaths.push(relPath);
      rollbackErrors.push({ path: relPath, error: String(error.message || error) });
    }
  }
  const rolledBack = unrestoredPaths.length === 0 && rollbackErrors.length === 0;
  journal.status = rolledBack ? "rollback_state_pending" : "recovery_required";
  journal.updatedAt = new Date().toISOString();
  saveJournal(journal, stateRoot);
  return {
    rolledBack,
    rollbackIncomplete: !rolledBack,
    restoredPaths,
    unrestoredPaths: [...new Set(unrestoredPaths)],
    rollbackErrors,
    externalChangeDetected,
  };
}

function checkpointMatchForJournal(journal, stateRoot) {
  if (journal.checkpointRequired === false) {
    return { matched: true, status: "not_required" };
  }
  const taskSessionId = String(journal.taskSessionId || "").trim();
  if (!taskSessionId) return { matched: false, status: "missing" };
  let statePath;
  try {
    statePath = path.join(taskStateDir(taskSessionId, stateRoot), "state.json");
  } catch {
    return { matched: false, status: "corrupt", reason: "invalid_task_session_id" };
  }
  if (!fs.existsSync(statePath)) return { matched: false, status: "missing" };
  let state;
  try {
    state = JSON.parse(fs.readFileSync(statePath, "utf8"));
  } catch {
    return { matched: false, status: "corrupt", reason: "task_state_corrupt" };
  }
  const checkpoint = state?.continuity?.checkpoint;
  if (!checkpoint || typeof checkpoint !== "object" || checkpoint.status !== "recorded") {
    return { matched: false, status: "missing" };
  }
  const expectedGeneration = Number(
    journal.mutationCompensationReceipt?.expectedMutationGeneration
    || journal.mutationGeneration
    || 0
  );
  const checkpointPaths = new Set(
    (checkpoint.modifiedFiles || []).map(normalizedJournalRelativePath)
  );
  const journalPaths = completedEntries(journal)
    .map((entry) => normalizedJournalRelativePath(entry.relativePath));
  const generationMatches = Number(checkpoint.mutationGeneration || 0) === expectedGeneration;
  const pathsMatch = journalPaths.every((relPath) => checkpointPaths.has(relPath));
  const taskProject = String(state.projectFile || state.projectPath || "").trim();
  const taskProjectRoot = taskProject
    ? (path.extname(taskProject).toLowerCase() === ".uproject" ? path.dirname(taskProject) : taskProject)
    : "";
  const projectMatches = !taskProjectRoot
    || pathIdentity(taskProjectRoot) === pathIdentity(journal.projectRoot);
  return {
    matched: generationMatches && pathsMatch && projectMatches,
    status: generationMatches && pathsMatch && projectMatches ? "recorded" : "mismatch",
    generationMatches,
    pathsMatch,
    projectMatches,
  };
}

function journalMutationReceipt(journal) {
  const receipt = journal?.mutationCompensationReceipt;
  return receipt && typeof receipt === "object" ? receipt : null;
}

function journalMutationRevision(journal) {
  const receipt = journalMutationReceipt(journal);
  return Number(receipt?.expectedMutationRevision || journal?.mutationRevision || 0);
}

function journalMutationGeneration(journal) {
  const receipt = journalMutationReceipt(journal);
  return Number(receipt?.expectedMutationGeneration || journal?.mutationGeneration || 0);
}

function mutationPathMap(state) {
  const result = new Map();
  const paths = state?.paths && typeof state.paths === "object" && !Array.isArray(state.paths)
    ? state.paths
    : {};
  for (const [relPath, digest] of Object.entries(paths)) {
    result.set(normalizedJournalRelativePath(relPath), String(digest || ""));
  }
  return result;
}

function mutationStatePreservesJournalPostImage(state, journal) {
  const paths = mutationPathMap(state);
  return completedEntries(journal).every((entry) => {
    const relPath = normalizedJournalRelativePath(entry.relativePath);
    if (entry.deletedAfter === true) return !paths.has(relPath);
    return Boolean(entry.postHash) && paths.get(relPath) === String(entry.postHash);
  });
}

function descendantCheckpointMatchForJournal(journal, mutationState, stateRoot) {
  if (journal.checkpointRequired === false) {
    return { matched: true, status: "not_required" };
  }
  const taskSessionId = String(journal.taskSessionId || "").trim();
  if (!taskSessionId) return { matched: false, status: "missing" };
  let statePath;
  try {
    statePath = path.join(taskStateDir(taskSessionId, stateRoot), "state.json");
  } catch {
    return { matched: false, status: "corrupt", reason: "invalid_task_session_id" };
  }
  if (!fs.existsSync(statePath)) return { matched: false, status: "missing" };
  let state;
  try {
    state = JSON.parse(fs.readFileSync(statePath, "utf8"));
  } catch {
    return { matched: false, status: "corrupt", reason: "task_state_corrupt" };
  }
  const checkpoint = state?.continuity?.checkpoint;
  if (!checkpoint || typeof checkpoint !== "object" || checkpoint.status !== "recorded") {
    return { matched: false, status: "missing" };
  }
  const expectedGeneration = journalMutationGeneration(journal);
  const currentMutationGeneration = Number(mutationState?.mutationGeneration || 0);
  const checkpointGeneration = Number(checkpoint.mutationGeneration || 0);
  const checkpointPaths = new Set(
    (checkpoint.modifiedFiles || []).map(normalizedJournalRelativePath)
  );
  const journalPaths = completedEntries(journal)
    .map((entry) => normalizedJournalRelativePath(entry.relativePath));
  const generationMatches = (
    checkpointGeneration >= expectedGeneration
    && checkpointGeneration === currentMutationGeneration
    && Number(state.mutationGeneration || checkpointGeneration) === currentMutationGeneration
  );
  const pathsMatch = journalPaths.every((relPath) => checkpointPaths.has(relPath));
  const taskProject = String(state.projectFile || state.projectPath || "").trim();
  const taskProjectRoot = taskProject
    ? (path.extname(taskProject).toLowerCase() === ".uproject" ? path.dirname(taskProject) : taskProject)
    : "";
  const projectMatches = !taskProjectRoot
    || pathIdentity(taskProjectRoot) === pathIdentity(journal.projectRoot);
  return {
    matched: generationMatches && pathsMatch && projectMatches,
    status: generationMatches && pathsMatch && projectMatches ? "recorded_descendant" : "mismatch",
    generationMatches,
    pathsMatch,
    projectMatches,
  };
}

function isMonotonicMutationDescendant(journal, mutationState) {
  const receipt = journalMutationReceipt(journal);
  if (!receipt || !mutationState || typeof mutationState !== "object") return false;
  return (
    Number(mutationState.mutationRevision || 0) > journalMutationRevision(journal)
    && Number(mutationState.mutationGeneration || 0) >= journalMutationGeneration(journal)
    && mutationStatePreservesJournalPostImage(mutationState, journal)
  );
}

function journalPathsOverlap(left, right) {
  const leftPaths = new Set(
    completedEntries(left).map((entry) => normalizedJournalRelativePath(entry.relativePath))
  );
  return completedEntries(right).some(
    (entry) => leftPaths.has(normalizedJournalRelativePath(entry.relativePath))
  );
}

async function verifiedSupersedingDescendant(journal, stateRoot, options = {}) {
  const initialReceipt = journalMutationReceipt(journal);
  if (!initialReceipt) return null;
  const projectKey = pathIdentity(journal.projectRoot);
  const taskKey = String(journal.taskSessionId || "").trim();
  const candidates = (options.recoveryJournals || [])
    .filter((candidate) => (
      candidate !== journal
      && candidate?.requiresAtomicCheckpoint === true
      && pathIdentity(candidate.projectRoot) === projectKey
      && String(candidate.taskSessionId || "").trim() === taskKey
      && journalMutationRevision(candidate) > journalMutationRevision(journal)
      && journalMutationReceipt(candidate)
    ))
    .sort((left, right) => journalMutationRevision(left) - journalMutationRevision(right));

  let expectedDigest = String(initialReceipt.expectedSemanticStateDigest || "");
  let expectedRevision = journalMutationRevision(journal);
  let expectedGeneration = journalMutationGeneration(journal);
  const lineage = [];
  for (const candidate of candidates) {
    const receipt = journalMutationReceipt(candidate);
    const previousState = receipt?.previousState && typeof receipt.previousState === "object"
      ? receipt.previousState
      : {};
    if (
      String(receipt.previousSemanticStateDigest || "") !== expectedDigest
      || Number(previousState.mutationRevision || 0) !== expectedRevision
      || Number(previousState.mutationGeneration || 0) !== expectedGeneration
    ) {
      continue;
    }
    lineage.push(candidate);
    expectedDigest = String(receipt.expectedSemanticStateDigest || "");
    expectedRevision = journalMutationRevision(candidate);
    expectedGeneration = journalMutationGeneration(candidate);
  }
  if (!lineage.length || !lineage.some((candidate) => journalPathsOverlap(journal, candidate))) {
    return null;
  }
  const tip = lineage[lineage.length - 1];
  let mutation;
  try {
    mutation = await inspectMutationCompensation(tip.projectRoot, tip.mutationCompensationReceipt);
  } catch {
    return null;
  }
  if (mutation.status !== "expected_post") return null;
  const disk = await classifyAtomicDisk(tip);
  if (!disk.allPost || disk.hasExternal) return null;
  if (tip.checkpointCommitted !== true && tip.checkpointRequired !== false) return null;
  const checkpoint = descendantCheckpointMatchForJournal(journal, mutation.state, stateRoot);
  if (!checkpoint.matched) return null;
  return {
    tip,
    lineage,
    mutationState: mutation.state,
    checkpoint,
  };
}

function verifiedRolledBackChildReceipt(journal, mutationState, options = {}) {
  const receipt = journalMutationReceipt(journal);
  const lastCompensatedReceiptId = String(mutationState?.lastCompensatedReceiptId || "");
  if (!receipt || !lastCompensatedReceiptId) return null;
  const expectedRevision = journalMutationRevision(journal);
  const expectedGeneration = journalMutationGeneration(journal);
  const expectedDigest = String(receipt.expectedSemanticStateDigest || "");
  return journalMutationReceipt(
    (options.recoveryJournals || []).find((candidate) => {
      const childReceipt = journalMutationReceipt(candidate);
      const compensation = candidate?.rollbackReconciliation?.compensation;
      return (
        candidate !== journal
        && String(candidate?.status || "") === "rolled_back"
        && compensation?.compensated === true
        && String(childReceipt?.receiptId || "") === lastCompensatedReceiptId
        && String(childReceipt?.previousSemanticStateDigest || "") === expectedDigest
        && Number(childReceipt?.previousState?.mutationRevision || 0) === expectedRevision
        && Number(childReceipt?.previousState?.mutationGeneration || 0) === expectedGeneration
      );
    })
  );
}

async function completeRecoveredCommit(journal, recovery, stateRoot, strategy, metadata = {}) {
  journal.mutationStateRecorded = true;
  journal.checkpointCommitted = true;
  journal.status = String(journal.postCheckpointStatus || "completed");
  journal.recoveredCommit = {
    strategy,
    ...metadata,
    recoveredAt: new Date().toISOString(),
  };
  journal.updatedAt = new Date().toISOString();
  saveJournal(journal, stateRoot);
  if (journal.status === "completed") await archiveJournal(journal.transactionId, stateRoot);
  recovery.committed.push(journal.transactionId);
}

async function completeSupersededJournal(journal, supersession, recovery, stateRoot) {
  journal.status = "superseded";
  journal.supersededBy = supersession.tip.transactionId;
  journal.supersession = {
    strategy: "durable_receipt_lineage",
    transactionIds: supersession.lineage.map((item) => item.transactionId),
    mutationGeneration: Number(supersession.mutationState?.mutationGeneration || 0),
    mutationRevision: Number(supersession.mutationState?.mutationRevision || 0),
    supersededAt: new Date().toISOString(),
  };
  journal.updatedAt = new Date().toISOString();
  saveJournal(journal, stateRoot);
  await archiveJournal(journal.transactionId, stateRoot);
  recovery.superseded.push(journal.transactionId);
}

async function markAtomicRecoveryRequired(journal, recovery, details, stateRoot) {
  const item = {
    transactionId: journal.transactionId,
    ...(details || {}),
  };
  recovery.recoveryRequired.push(item);
  journal.status = "recovery_required";
  journal.recoveryFailure = item;
  journal.updatedAt = new Date().toISOString();
  saveJournal(journal, stateRoot);
}

function markAtomicRollbackPending(journal, recovery, details, stateRoot) {
  const item = {
    transactionId: journal.transactionId,
    ...(details || {}),
  };
  recovery.recoveryRequired.push(item);
  journal.status = "rollback_state_pending";
  journal.recoveryFailure = item;
  journal.updatedAt = new Date().toISOString();
  saveJournal(journal, stateRoot);
}

async function recoverPendingAtomicRollback(journal, recovery, stateRoot, options, initialDisk) {
  let disk = initialDisk;
  if (disk.hasExternal) {
    await markAtomicRecoveryRequired(journal, recovery, {
      reason: "external_change_detected_during_rollback",
      paths: disk.classifications
        .filter((item) => !item.preImage && !item.postImage)
        .map((item) => item.entry.relativePath),
    }, stateRoot);
    return;
  }
  if (!disk.allPre) {
    const rollback = await rollbackAtomicDisk(journal, stateRoot);
    if (!rollback.rolledBack) {
      await markAtomicRecoveryRequired(journal, recovery, {
        reason: "disk_rollback_resume_failed",
        rollback,
      }, stateRoot);
      return;
    }
    disk = await classifyAtomicDisk(journal);
  }
  if (!disk.allPre) {
    await markAtomicRecoveryRequired(journal, recovery, {
      reason: "disk_preimage_not_restored",
    }, stateRoot);
    return;
  }

  const relativePaths = disk.entries.map((entry) => String(entry.relativePath || ""));
  let reconciliation;
  try {
    reconciliation = await reconcileMutationPathsFromDisk(
      journal.projectRoot,
      relativePaths
    );
  } catch (error) {
    markAtomicRollbackPending(journal, recovery, {
      reason: "rollback_mutation_reconciliation_failed",
      error: String(error.message || error),
    }, stateRoot);
    return;
  }

  let checkpoint = { ok: true, skipped: true, reason: "checkpoint_not_required" };
  const checkpointRequired = (
    journal.checkpointRequired !== false
    && Boolean(String(journal.taskSessionId || "").trim())
  );
  if (checkpointRequired) {
    if (typeof options.checkpointRollback !== "function") {
      markAtomicRollbackPending(journal, recovery, {
        reason: "rollback_checkpoint_callback_unavailable",
        mutationGeneration: reconciliation.mutationGeneration,
      }, stateRoot);
      return;
    }
    try {
      checkpoint = await options.checkpointRollback({
        journal,
        reconciliation,
        absolutePaths: disk.entries.map((entry) => path.resolve(entry.canonicalAbsolutePath)),
        relativePaths,
        stateRoot,
      });
    } catch (error) {
      checkpoint = {
        ok: false,
        errorCode: "ROLLBACK_CHECKPOINT_FAILED",
        error: String(error.message || error),
      };
    }
    if (!checkpoint || checkpoint.ok !== true) {
      markAtomicRollbackPending(journal, recovery, {
        reason: "rollback_checkpoint_failed",
        mutationGeneration: reconciliation.mutationGeneration,
        errorCode: String(checkpoint?.errorCode || "ROLLBACK_CHECKPOINT_FAILED"),
        error: String(checkpoint?.error || "Rollback task checkpoint failed."),
      }, stateRoot);
      return;
    }
  }

  await completeMutationRollback(journal, {
    strategy: "startup_disk_reconciliation",
    reason: String(journal.rollbackIntent?.reason || "workflow_rollback_recovery"),
    mutationGeneration: reconciliation.mutationGeneration,
    mutationRevision: reconciliation.mutationRevision,
    checkpointCommitted: checkpoint.ok === true,
    checkpointHash: String(checkpoint.checkpointHash || ""),
  }, stateRoot);
  recovery.recovered.push(...relativePaths);
}

async function recoverAtomicJournal(journal, recovery, stateRoot, options = {}) {
  const disk = await classifyAtomicDisk(journal);
  if (["rollback_disk_pending", "rollback_state_pending"].includes(String(journal.status || ""))) {
    await recoverPendingAtomicRollback(journal, recovery, stateRoot, options, disk);
    return;
  }
  let mutation = journal.mutationStateRequired === false
    ? { status: "not_required" }
    : { status: journal.mutationStateRecorded === true ? "unknown" : "previous" };
  if (journal.mutationCompensationReceipt) {
    try {
      mutation = await inspectMutationCompensation(
        journal.projectRoot,
        journal.mutationCompensationReceipt
      );
    } catch (error) {
      await markAtomicRecoveryRequired(journal, recovery, {
        reason: "mutation_state_unavailable",
        error: String(error.message || error),
      }, stateRoot);
      return;
    }
  }
  const rolledBackChildReceipt = mutation.status === "conflict"
    ? verifiedRolledBackChildReceipt(journal, mutation.state, options)
    : null;
  if (rolledBackChildReceipt) {
    mutation = {
      ...mutation,
      status: "expected_post_after_descendant_rollback",
    };
  }
  const checkpoint = checkpointMatchForJournal(journal, stateRoot);
  if (checkpoint.status === "corrupt") {
    await markAtomicRecoveryRequired(journal, recovery, {
      reason: checkpoint.reason || "task_state_corrupt",
    }, stateRoot);
    return;
  }
  if (
    disk.allPost
    && ["expected_post", "not_required"].includes(mutation.status)
    && checkpoint.matched
  ) {
    await completeRecoveredCommit(journal, recovery, stateRoot, "exact_postimage", {
      checkpointStatus: checkpoint.status,
    });
    return;
  }
  if (
    disk.allPost
    && mutation.status === "conflict"
    && isMonotonicMutationDescendant(journal, mutation.state)
  ) {
    const descendantCheckpoint = descendantCheckpointMatchForJournal(
      journal,
      mutation.state,
      stateRoot
    );
    if (descendantCheckpoint.matched) {
      await completeRecoveredCommit(journal, recovery, stateRoot, "monotonic_descendant", {
        checkpointStatus: descendantCheckpoint.status,
        descendantMutationGeneration: Number(mutation.state?.mutationGeneration || 0),
        descendantMutationRevision: Number(mutation.state?.mutationRevision || 0),
      });
      return;
    }
  }
  if (disk.hasExternal) {
    const supersession = await verifiedSupersedingDescendant(journal, stateRoot, options);
    if (supersession) {
      await completeSupersededJournal(journal, supersession, recovery, stateRoot);
      return;
    }
    await markAtomicRecoveryRequired(journal, recovery, {
      reason: "external_change_detected",
      paths: disk.classifications
        .filter((item) => !item.preImage && !item.ownedPostImage)
        .map((item) => item.entry.relativePath),
    }, stateRoot);
    return;
  }
  if (checkpoint.status === "recorded" && mutation.status !== "previous") {
    await markAtomicRecoveryRequired(journal, recovery, {
      reason: "checkpoint_postimage_mismatch",
      diskAllPost: disk.allPost,
      mutationState: mutation.status,
    }, stateRoot);
    return;
  }
  if (mutation.status === "conflict" || mutation.status === "unknown") {
    await markAtomicRecoveryRequired(journal, recovery, {
      reason: "mutation_state_conflict",
      mutationState: mutation.status,
    }, stateRoot);
    return;
  }
  let compensation = { compensated: true, alreadyCompensated: true };
  if (["expected_post", "expected_post_after_descendant_rollback"].includes(mutation.status)) {
    try {
      compensation = await compensateMutationBatch(
        journal.projectRoot,
        journal.mutationCompensationReceipt,
        rolledBackChildReceipt
          ? { descendantCompensationReceipt: rolledBackChildReceipt }
          : {}
      );
    } catch (error) {
      compensation = { compensated: false, error: String(error.message || error) };
    }
    if (!compensation.compensated) {
      await markAtomicRecoveryRequired(journal, recovery, {
        reason: "mutation_compensation_failed",
        compensation,
      }, stateRoot);
      return;
    }
  }
  const rollback = await rollbackAtomicDisk(journal, stateRoot);
  if (!rollback.rolledBack) {
    await markAtomicRecoveryRequired(journal, recovery, {
      reason: "disk_rollback_failed",
      rollback,
    }, stateRoot);
    return;
  }
  journal.rollbackStateReconciled = true;
  journal.rollbackReconciliation = {
    strategy: "compensation_receipt",
    compensation,
    reconciledAt: new Date().toISOString(),
  };
  journal.status = "rolled_back";
  journal.updatedAt = new Date().toISOString();
  saveJournal(journal, stateRoot);
  await archiveJournal(journal.transactionId, stateRoot);
  recovery.recovered.push(...disk.entries.map((entry) => entry.relativePath));
}

async function recoverIncompleteJournals(
  stateRoot = resolveAgentStateRoot(),
  options = {}
) {
  const dir = journalDir(stateRoot);
  const recovery = {
    recovered: [],
    recoveryRequired: [],
    skippedCorrupt: [],
    skippedTerminal: [],
    committed: [],
    superseded: [],
    scanned: 0,
  };
  const loaded = [];
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
    loaded.push({ name, journal });
  }
  loaded.sort((left, right) => {
    const revisionDelta = journalMutationRevision(right.journal)
      - journalMutationRevision(left.journal);
    if (revisionDelta) return revisionDelta;
    const createdDelta = String(right.journal.createdAt || "")
      .localeCompare(String(left.journal.createdAt || ""));
    return createdDelta || right.name.localeCompare(left.name);
  });
  const recoveryJournals = loaded.map((item) => item.journal);
  for (const { name, journal } of loaded) {
    if (
      journal.requiresAtomicCheckpoint === true
      && TERMINAL_JOURNAL_STATUSES.has(String(journal.status || ""))
      && (
        journal.checkpointCommitted === true
        || journal.rollbackStateReconciled === true
        || ["recovery_required", "superseded"].includes(String(journal.status || ""))
      )
    ) {
      if (["completed", "rolled_back", "recovered", "superseded"].includes(String(journal.status || ""))) {
        await archiveJournal(journal.transactionId, stateRoot);
      }
      recovery.skippedTerminal.push(journal.transactionId || name);
      continue;
    }
    if (journal.requiresAtomicCheckpoint === true) {
      await recoverAtomicJournal(journal, recovery, stateRoot, {
        ...options,
        recoveryJournals,
      });
      continue;
    }
    if (
      TERMINAL_JOURNAL_STATUSES.has(journal.status)
      && journal.recoverOnRestart !== true
    ) {
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
      const preImageIntact = entry.existedBefore
        ? existsNow && Boolean(entry.preHash && currentHash === entry.preHash)
        : !existsNow;
      if (preImageIntact) {
        recovery.recovered.push(entry.relativePath);
        continue;
      }
      const expectedPostImage = entryMatchesOwnedPostImage(entry, existsNow, currentHash);
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
  intendedPostHashes,
  entryMatchesOwnedPostImage,
  archiveJournal,
  recoverIncompleteJournals,
  pathIdentity,
  requiresBuildValidation,
  beginMutationJournal,
  commitMutationJournal,
  armAtomicMutationJournal,
  stageMutationCompensation,
  markMutationStateRecorded,
  completeMutationJournalCheckpoint,
  armMutationRollback,
  completeMutationRollback,
  abandonMutationJournal,
  pendingBuildJournals,
  finalizePendingBuildJournals,
  TERMINAL_JOURNAL_STATUSES,
  PENDING_BUILD_STATUSES,
  prepareSingleFileJournal,
  markJournalAwaitingBuild,
  listPendingJournals,
  finalizePendingJournals,
  markPendingBuildFailed,
  projectPathIdentity,
};
