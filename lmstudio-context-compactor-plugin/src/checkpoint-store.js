"use strict";

const fs = require("node:fs/promises");
const path = require("node:path");
const os = require("node:os");
const crypto = require("node:crypto");
const { validateCheckpoint } = require("./compaction-core.js");

const DAY_MS = 24 * 60 * 60 * 1000;
const DEFAULT_COMPLETED_RETENTION_DAYS = 90;
const DEFAULT_CANCELLED_RETENTION_DAYS = 30;
const DEFAULT_INACTIVE_RETENTION_DAYS = 90;
const DEFAULT_GC_INTERVAL_MS = DAY_MS;
const DEFAULT_GC_MAX_SESSIONS = 10_000;
let lastCleanupAt = 0;
let cleanupInFlight = null;

function defaultRoot() {
  return process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR || path.join(os.homedir(), ".lmstudio", "unreal-context-compactor", "sessions");
}

function safeSessionId(sessionId) {
  return String(sessionId || "unknown").replace(/[^A-Za-z0-9._-]/g, "_").slice(0, 96) || "unknown";
}

function sessionDir(sessionId, root = defaultRoot()) {
  return path.join(root, safeSessionId(sessionId));
}

async function atomicWrite(filePath, value) {
  const directory = path.dirname(filePath);
  await fs.mkdir(directory, { recursive: true });
  const temp = `${filePath}.tmp-${process.pid}-${Date.now()}-${crypto.randomBytes(4).toString("hex")}`;
  try {
    const handle = await fs.open(temp, "w");
    try {
      await handle.writeFile(`${JSON.stringify(value, null, 2)}\n`, "utf8");
      await handle.sync();
    } finally {
      await handle.close();
    }
    await fs.rename(temp, filePath);
    try {
      const directoryHandle = await fs.open(directory, "r");
      try {
        await directoryHandle.sync();
      } finally {
        await directoryHandle.close();
      }
    } catch {
      // Directory fsync is not supported uniformly (notably on Windows).
    }
  } catch (error) {
    await fs.unlink(temp).catch(() => undefined);
    throw error;
  }
}

async function pruneFiles(dir, predicate, keep) {
  const entries = (await fs.readdir(dir, { withFileTypes: true }))
    .filter((entry) => entry.isFile() && predicate(entry.name))
    .map((entry) => entry.name)
    .sort();
  const obsolete = entries.slice(0, Math.max(0, entries.length - keep));
  await Promise.all(obsolete.map((name) => fs.unlink(path.join(dir, name)).catch(() => undefined)));
}

function boundedNumber(value, fallback, minimum, maximum) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(minimum, Math.min(maximum, Math.trunc(parsed)));
}

async function readSessionStatus(dir) {
  try {
    const raw = await fs.readFile(path.join(dir, "session-status.json"), "utf8");
    if (Buffer.byteLength(raw, "utf8") > 64 * 1024) return "inactive";
    const payload = JSON.parse(raw);
    const status = String(payload?.status || "").trim().toLowerCase();
    return ["active", "running", "completed", "cancelled"].includes(status)
      ? status
      : "inactive";
  } catch (error) {
    if (error instanceof SyntaxError || error?.code === "ENOENT") return "inactive";
    throw error;
  }
}

async function sessionActivity(dir) {
  const entries = await fs.readdir(dir, { withFileTypes: true });
  let latest = Number((await fs.stat(dir)).mtimeMs || 0);
  let hasCorruptArtifact = false;
  for (const entry of entries) {
    if (!entry.isFile()) continue;
    if (entry.name.includes(".corrupt-")) hasCorruptArtifact = true;
    try {
      const info = await fs.stat(path.join(dir, entry.name));
      latest = Math.max(latest, Number(info.mtimeMs || 0));
    } catch (error) {
      if (error?.code !== "ENOENT") throw error;
    }
  }
  return { latest, hasCorruptArtifact };
}

async function markSessionStatus(sessionId, status, root = defaultRoot()) {
  const normalized = String(status || "").trim().toLowerCase();
  if (!["active", "running", "completed", "cancelled"].includes(normalized)) {
    throw new TypeError(`unsupported session status: ${status}`);
  }
  const dir = sessionDir(sessionId, root);
  await atomicWrite(path.join(dir, "session-status.json"), {
    status: normalized,
    updatedAt: new Date().toISOString(),
  });
}

async function cleanupSessions(root = defaultRoot(), options = {}) {
  const resolvedRoot = path.resolve(root);
  const nowMs = Number.isFinite(Number(options.nowMs))
    ? Number(options.nowMs)
    : Date.now();
  const retention = {
    completed: boundedNumber(
      options.completedRetentionDays ?? process.env.LMS_CONTEXT_COMPACTOR_COMPLETED_RETENTION_DAYS,
      DEFAULT_COMPLETED_RETENTION_DAYS,
      30,
      3650,
    ),
    cancelled: boundedNumber(
      options.cancelledRetentionDays ?? process.env.LMS_CONTEXT_COMPACTOR_CANCELLED_RETENTION_DAYS,
      DEFAULT_CANCELLED_RETENTION_DAYS,
      14,
      3650,
    ),
    inactive: boundedNumber(
      options.inactiveRetentionDays ?? process.env.LMS_CONTEXT_COMPACTOR_INACTIVE_RETENTION_DAYS,
      DEFAULT_INACTIVE_RETENTION_DAYS,
      30,
      3650,
    ),
  };
  const excluded = new Set(
    (Array.isArray(options.excludeSessionIds) ? options.excludeSessionIds : [])
      .map((value) => safeSessionId(value)),
  );
  const maxSessions = boundedNumber(
    options.maxSessions ?? process.env.LMS_CONTEXT_COMPACTOR_GC_MAX_SESSIONS,
    DEFAULT_GC_MAX_SESSIONS,
    1,
    100_000,
  );
  let entries;
  try {
    entries = (await fs.readdir(resolvedRoot, { withFileTypes: true }))
      .filter((entry) => (
        entry.isDirectory()
        && !entry.isSymbolicLink()
        && !String(entry.name || "").startsWith(".")
        && String(entry.name || "") !== "_base"
      ))
      .slice(0, maxSessions);
  } catch (error) {
    if (error?.code === "ENOENT") {
      return { scanned: 0, deleted: 0, skippedCorrupt: 0, skippedActive: 0 };
    }
    throw error;
  }
  const result = {
    scanned: 0,
    deleted: 0,
    skippedCorrupt: 0,
    skippedActive: 0,
    scanLimited: entries.length >= maxSessions,
  };
  for (const entry of entries) {
    try {
      result.scanned += 1;
      if (excluded.has(entry.name)) {
        result.skippedActive += 1;
        continue;
      }
      const dir = path.join(resolvedRoot, entry.name);
      const status = await readSessionStatus(dir);
      if (status === "active" || status === "running") {
        result.skippedActive += 1;
        continue;
      }
      const before = await sessionActivity(dir);
      if (before.hasCorruptArtifact) {
        result.skippedCorrupt += 1;
        continue;
      }
      const bucket = status === "completed" || status === "cancelled"
        ? status
        : "inactive";
      if (before.latest >= nowMs - retention[bucket] * DAY_MS) continue;
      // Recheck immediately before removal so a concurrently resumed session is
      // never deleted based on an old directory listing.
      const after = await sessionActivity(dir);
      if (after.hasCorruptArtifact || after.latest !== before.latest) continue;
      await fs.rm(dir, { recursive: true, force: false });
      result.deleted += 1;
    } catch (error) {
      if (error?.code !== "ENOENT") {
        result.skippedErrors = Number(result.skippedErrors || 0) + 1;
      }
    }
  }
  return result;
}

function maybeCleanupSessions(root, currentSessionId) {
  const intervalMs = boundedNumber(
    process.env.LMS_CONTEXT_COMPACTOR_GC_INTERVAL_MS,
    DEFAULT_GC_INTERVAL_MS,
    60_000,
    30 * DAY_MS,
  );
  const now = Date.now();
  if (cleanupInFlight || now - lastCleanupAt < intervalMs) return;
  lastCleanupAt = now;
  cleanupInFlight = cleanupSessions(root, {
    excludeSessionIds: [currentSessionId],
  }).catch((error) => {
    console.warn(`[unreal-context-compactor] Session cleanup failed: ${error?.message || error}`);
  }).finally(() => {
    cleanupInFlight = null;
  });
}

async function appendEvent(sessionId, event, root = defaultRoot()) {
  const dir = sessionDir(sessionId, root);
  await fs.mkdir(dir, { recursive: true });
  const eventPath = path.join(dir, "events.jsonl");
  try {
    const info = await fs.stat(eventPath);
    if (info.size >= 5 * 1024 * 1024) {
      await fs.rename(eventPath, path.join(dir, `events-${Date.now()}.jsonl`));
    }
  } catch (error) {
    if (!error || error.code !== "ENOENT") throw error;
  }
  await fs.appendFile(eventPath, `${JSON.stringify(event)}\n`, "utf8");
  await pruneFiles(dir, (name) => /^events-\d+\.jsonl$/.test(name), 3);
  maybeCleanupSessions(root, sessionId);
}

async function loadCheckpoint(sessionId, root = defaultRoot()) {
  const dir = sessionDir(sessionId, root);
  const filePath = path.join(dir, "active-checkpoint.json");
  let active = null;
  try {
    active = JSON.parse(await fs.readFile(filePath, "utf8"));
    if (!validateCheckpoint(active)) {
      throw new SyntaxError("active checkpoint schema is invalid");
    }
  } catch (error) {
    if (error instanceof SyntaxError) {
      const quarantine = path.join(
        path.dirname(filePath),
        `active-checkpoint.corrupt-${Date.now()}.json`,
      );
      await fs.rename(filePath, quarantine).catch(() => undefined);
    } else if (!error || error.code !== "ENOENT") {
      throw error;
    }
  }
  let newest = null;
  try {
    const generations = (await fs.readdir(dir))
      .filter((name) => /^checkpoint-\d+\.json$/.test(name))
      .sort((left, right) => {
        const leftGeneration = Number(left.match(/\d+/u)?.[0] || 0);
        const rightGeneration = Number(right.match(/\d+/u)?.[0] || 0);
        return rightGeneration - leftGeneration;
      });
    for (const name of generations) {
      try {
        const candidate = JSON.parse(await fs.readFile(path.join(dir, name), "utf8"));
        if (validateCheckpoint(candidate)) {
          newest = candidate;
          break;
        }
      } catch {
        // Try the prior atomically-written generation.
      }
    }
  } catch (error) {
    if (!error || error.code !== "ENOENT") throw error;
  }
  const activeGeneration = Number(active?.checkpointGeneration || -1);
  const newestGeneration = Number(newest?.checkpointGeneration || -1);
  return newest && newestGeneration > activeGeneration ? newest : active;
}

async function saveCheckpoint(sessionId, checkpoint, root = defaultRoot()) {
  const dir = sessionDir(sessionId, root);
  await fs.mkdir(dir, { recursive: true });
  const parsedGeneration = Number(checkpoint?.checkpointGeneration);
  const generation = Number.isFinite(parsedGeneration) && parsedGeneration >= 0
    ? Math.trunc(parsedGeneration)
    : Date.now();
  await atomicWrite(path.join(dir, `checkpoint-${String(generation).padStart(6, "0")}.json`), checkpoint);
  await atomicWrite(path.join(dir, "active-checkpoint.json"), checkpoint);
  await pruneFiles(dir, (name) => /^checkpoint-\d+\.json$/.test(name), 20);
  maybeCleanupSessions(root, sessionId);
}

async function readJsonSafe(filePath, fallback = null) {
  try {
    const raw = await fs.readFile(filePath, "utf8");
    return JSON.parse(raw);
  } catch {
    return fallback;
  }
}

function forkIndexRoot(root = defaultRoot()) {
  return `${path.resolve(root)}__forks`;
}

function baseIndexDir(baseKey, root = defaultRoot()) {
  return path.join(forkIndexRoot(root), safeSessionId(baseKey));
}

async function resolveSessionFork({
  baseKey,
  lineage = [],
  envSessionId = "",
  root = defaultRoot(),
} = {}) {
  const explicit = String(envSessionId || "").trim();
  const key = safeSessionId(baseKey);
  if (explicit) {
    return {
      sessionId: safeSessionId(explicit),
      reason: "env",
      minted: false,
      baseKey: key,
    };
  }
  try {
    const indexPath = path.join(baseIndexDir(key, root), "forks.json");
    const payload = await readJsonSafe(indexPath, { forks: [] });
    const forks = Array.isArray(payload?.forks) ? payload.forks : [];
    const { lineageContinues } = require("./compaction-core.js");
    for (let index = forks.length - 1; index >= 0; index -= 1) {
      const fork = forks[index];
      const prior = Array.isArray(fork?.lineage) ? fork.lineage : [];
      if (lineageContinues(prior, lineage)) {
        fork.lineage = lineage;
        fork.updatedAt = new Date().toISOString();
        await atomicWrite(indexPath, { forks, updatedAt: fork.updatedAt });
        return {
          sessionId: safeSessionId(fork.sessionId),
          reason: "lineage",
          minted: false,
          baseKey: key,
        };
      }
    }
    const sessionId = forks.length === 0
      ? key
      : crypto.createHash("sha256")
        .update(`${key}\n${Date.now()}\n${crypto.randomBytes(8).toString("hex")}`, "utf8")
        .digest("hex")
        .slice(0, 32);
    forks.push({
      sessionId,
      lineage: Array.isArray(lineage) ? lineage : [],
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    });
    await atomicWrite(indexPath, { forks, updatedAt: new Date().toISOString() });
    return {
      sessionId: safeSessionId(sessionId),
      reason: forks.length === 1 ? "primary" : "fork",
      minted: true,
      baseKey: key,
    };
  } catch {
    return {
      sessionId: key,
      reason: "fallback",
      minted: true,
      baseKey: key,
    };
  }
}

async function touchSessionFork(baseKey, sessionId, lineage = [], root = defaultRoot()) {
  try {
    const key = safeSessionId(baseKey);
    const indexPath = path.join(baseIndexDir(key, root), "forks.json");
    const payload = await readJsonSafe(indexPath, { forks: [] });
    const forks = Array.isArray(payload?.forks) ? payload.forks : [];
    const id = safeSessionId(sessionId);
    let found = forks.find((fork) => safeSessionId(fork?.sessionId) === id);
    if (!found) {
      found = {
        sessionId: id,
        lineage: [],
        createdAt: new Date().toISOString(),
      };
      forks.push(found);
    }
    found.lineage = Array.isArray(lineage) ? lineage : [];
    found.updatedAt = new Date().toISOString();
    await atomicWrite(indexPath, { forks, updatedAt: found.updatedAt });
    return found;
  } catch {
    return null;
  }
}

module.exports = {
  defaultRoot,
  safeSessionId,
  sessionDir,
  appendEvent,
  cleanupSessions,
  loadCheckpoint,
  markSessionStatus,
  saveCheckpoint,
  resolveSessionFork,
  touchSessionFork,
};
