"use strict";

const fs = require("node:fs");
const path = require("node:path");

const { atomicCreateText, atomicWriteJson } = require("./atomic-io.js");
const { resolveAgentStateRoot } = require("./runtime-state-root.js");
const { releasePathLock, tryAcquirePathLock } = require("./write-locks.js");

const SESSION_ID = /^strict-[0-9a-f-]{36}$/i;
const MAX_SESSION_BYTES = 128 * 1024;

function revisionOf(session) {
  const value = session?.revision === undefined ? 0 : Number(session.revision);
  if (!Number.isInteger(value) || value < 0) throw new Error("Strict session revision is invalid");
  return value;
}

function createStrictSessionStore(options = {}) {
  const stateRoot = resolveAgentStateRoot({
    env: options.env || process.env,
    stateRoot: options.stateRoot,
  });
  const sessionsRoot = path.resolve(stateRoot, "strict-sessions-v1");
  if (path.dirname(sessionsRoot) !== stateRoot || path.basename(sessionsRoot) !== "strict-sessions-v1") {
    throw new Error(`Unexpected Strict session root: ${sessionsRoot}`);
  }
  fs.mkdirSync(sessionsRoot, { recursive: true });

  function sessionPath(sessionId) {
    const safe = String(sessionId || "").trim();
    if (!SESSION_ID.test(safe)) throw new Error("strictSessionId has an invalid format");
    const target = path.resolve(sessionsRoot, `${safe}.json`);
    if (path.dirname(target) !== sessionsRoot) throw new Error("strictSessionId escapes the session root");
    return target;
  }

  function readFile(target) {
    let stat;
    try {
      stat = fs.statSync(target);
    } catch (error) {
      if (error.code === "ENOENT") throw new Error("Strict session was not found");
      throw error;
    }
    if (!stat.isFile() || stat.size > MAX_SESSION_BYTES) {
      throw new Error("Strict session state is invalid");
    }
    return JSON.parse(fs.readFileSync(target, "utf8"));
  }

  function create(session) {
    const target = sessionPath(session.id);
    const stored = { ...session, revision: 1 };
    atomicCreateText(target, `${JSON.stringify(stored, null, 2)}\n`, "utf8");
    return stored;
  }

  function acquire(sessionId, heartbeat = false) {
    const target = sessionPath(sessionId);
    const acquired = tryAcquirePathLock(target, "strict-session", { stateRoot, heartbeat });
    if (!acquired.ok) {
      const error = new Error("Strict session is busy in another runtime; retry after state change");
      error.code = "STRICT_SESSION_BUSY";
      throw error;
    }
    let current;
    try {
      current = readFile(target);
    } catch (error) {
      releasePathLock(target);
      throw error;
    }
    let revision = revisionOf(current);
    const commit = (next) => {
      const diskRevision = revisionOf(readFile(target));
      if (diskRevision !== revision) {
        const error = new Error("Strict session changed during a locked transition");
        error.code = "STRICT_SESSION_STALE";
        throw error;
      }
      current = { ...next, revision: revision + 1 };
      atomicWriteJson(target, current);
      revision = current.revision;
      return current;
    };
    return { target, current: () => current, commit };
  }

  function withSession(sessionId, handler) {
    const transaction = acquire(sessionId);
    try {
      return handler(transaction.current(), transaction.commit);
    } finally {
      releasePathLock(transaction.target);
    }
  }

  async function withSessionOperation(sessionId, handler) {
    const transaction = acquire(sessionId, true);
    try {
      return await handler(transaction.current(), transaction.commit);
    } finally {
      releasePathLock(transaction.target);
    }
  }

  function listSessionIds() {
    return fs.readdirSync(sessionsRoot, { withFileTypes: true })
      .filter((entry) => entry.isFile() && /^strict-[0-9a-f-]{36}\.json$/i.test(entry.name))
      .map((entry) => entry.name.slice(0, -5));
  }

  return {
    stateRoot,
    sessionsRoot,
    create,
    listSessionIds,
    withSession,
    withSessionOperation,
  };
}

module.exports = { createStrictSessionStore };
