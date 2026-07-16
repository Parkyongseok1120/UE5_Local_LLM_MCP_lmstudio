"use strict";

const fs = require("node:fs/promises");
const path = require("node:path");
const os = require("node:os");
const crypto = require("node:crypto");

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
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  const temp = `${filePath}.tmp-${process.pid}-${Date.now()}-${crypto.randomBytes(4).toString("hex")}`;
  await fs.writeFile(temp, `${JSON.stringify(value, null, 2)}\n`, "utf8");
  try {
    await fs.rename(temp, filePath);
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
}

async function loadCheckpoint(sessionId, root = defaultRoot()) {
  const filePath = path.join(sessionDir(sessionId, root), "active-checkpoint.json");
  try {
    return JSON.parse(await fs.readFile(filePath, "utf8"));
  } catch (error) {
    if (error && error.code === "ENOENT") return null;
    if (error instanceof SyntaxError) {
      const quarantine = path.join(
        path.dirname(filePath),
        `active-checkpoint.corrupt-${Date.now()}.json`,
      );
      await fs.rename(filePath, quarantine).catch(() => undefined);
      return null;
    }
    throw error;
  }
}

async function saveCheckpoint(sessionId, checkpoint, root = defaultRoot()) {
  const dir = sessionDir(sessionId, root);
  await fs.mkdir(dir, { recursive: true });
  const generation = Number(checkpoint?.checkpointGeneration || Date.now());
  await atomicWrite(path.join(dir, `checkpoint-${String(generation).padStart(6, "0")}.json`), checkpoint);
  await atomicWrite(path.join(dir, "active-checkpoint.json"), checkpoint);
  await pruneFiles(dir, (name) => /^checkpoint-\d+\.json$/.test(name), 20);
}

module.exports = {
  defaultRoot,
  safeSessionId,
  sessionDir,
  appendEvent,
  loadCheckpoint,
  saveCheckpoint,
};
