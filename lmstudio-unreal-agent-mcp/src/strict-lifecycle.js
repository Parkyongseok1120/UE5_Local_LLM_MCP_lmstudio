"use strict";

const crypto = require("node:crypto");

const {
  ACTIVE_STATES,
  TERMINAL_STATES,
  boundedText,
  clampTtlSeconds,
  normalizeStoredSession,
  publicSession,
  validIdentifier,
} = require("./strict-session-domain.js");
const { createStrictSessionStore } = require("./strict-session-store.js");

function createStrictLifecycle(options = {}) {
  const clock = typeof options.clock === "function" ? options.clock : () => Date.now();
  const processId = Number.isInteger(options.processId) ? options.processId : process.pid;
  const processStartedAtMs = Number.isFinite(Number(options.processStartedAtMs))
    ? Number(options.processStartedAtMs)
    : Date.now() - Math.trunc(process.uptime() * 1000);
  const runtimeOwnerId = String(options.runtimeOwnerId || (
    `${processId}:${processStartedAtMs}:${crypto.randomUUID()}`
  ));
  const isProcessAlive = typeof options.isProcessAlive === "function"
    ? options.isProcessAlive
    : (pid) => {
      if (!Number.isInteger(pid) || pid <= 0) return false;
      if (pid === processId) return true;
      try {
        process.kill(pid, 0);
        return true;
      } catch (error) {
        return error?.code === "EPERM";
      }
    };
  const store = createStrictSessionStore(options);
  const ownedSessionIds = new Set();
  const pendingOrphans = new Map();

  function ownerIsCurrent(session) {
    return session.ownerId === runtimeOwnerId
      && Number(session.processId) === processId
      && Number(session.processStartedAtMs) === processStartedAtMs;
  }

  function commitOrphan(session, commit, reason) {
    session.status = "orphaned";
    session.orphanReason = boundedText(reason, 200) || "connection_closed";
    session.orphanedAt = new Date(clock()).toISOString();
    session.updatedAt = session.orphanedAt;
    delete session.operationId;
    delete session.operationName;
    delete session.operationStartedAt;
    ownedSessionIds.delete(session.id);
    pendingOrphans.delete(session.id);
    return commit(session);
  }

  function materializeExpiry(session, commit) {
    if (ACTIVE_STATES.has(session.status) && Number(session.expiresAtMs || 0) <= clock()) {
      return commitOrphan(session, commit, "ttl_expired");
    }
    return session;
  }

  function materializeOwnership(session, commit) {
    if (!ACTIVE_STATES.has(session.status) || ownerIsCurrent(session)) return session;
    ownedSessionIds.delete(session.id);
    const ownerPid = Number(session.processId);
    if (ownerPid === processId || !isProcessAlive(ownerPid)) {
      return commitOrphan(session, commit, "owner_process_restarted");
    }
    return session;
  }

  function prepareSession(raw, sessionId, commitRaw) {
    let { session, migrated } = normalizeStoredSession(raw, sessionId);
    const commit = (next) => commitRaw({ ...next, schemaVersion: 2 });
    if (migrated) session = commit(session);
    session = materializeExpiry(session, commit);
    session = materializeOwnership(session, commit);
    return { session, commit };
  }

  function withSession(sessionId, handler) {
    return store.withSession(sessionId, (raw, commitRaw) => {
      const prepared = prepareSession(raw, sessionId, commitRaw);
      return handler(prepared.session, prepared.commit);
    });
  }

  async function withSessionOperation(sessionId, handler) {
    return store.withSessionOperation(sessionId, async (raw, commitRaw) => {
      const prepared = prepareSession(raw, sessionId, commitRaw);
      return handler(prepared.session, prepared.commit);
    });
  }

  function assertConversation(session, conversationId) {
    const expected = validIdentifier(conversationId, "conversationId");
    if (session.conversationId !== expected) {
      throw new Error("Strict session belongs to a different conversation");
    }
    return session;
  }

  function assertActive(session, conversationId) {
    assertConversation(session, conversationId);
    if (!ACTIVE_STATES.has(session.status)) {
      throw new Error(`Strict session is ${session.status}; explicitly resume an orphaned session or begin a new one`);
    }
    if (!ownedSessionIds.has(session.id) || !ownerIsCurrent(session)) {
      throw new Error("Strict session is owned by another live runtime process and cannot authorize this connection");
    }
    return session;
  }

  function assertRunning(session, conversationId) {
    assertActive(session, conversationId);
    if (session.status !== "running") {
      throw new Error(`Strict session is ${session.status}; send strict_heartbeat before state-changing work`);
    }
    return session;
  }

  function read(sessionId) {
    return withSession(sessionId, (session) => session);
  }

  function requireConversation(sessionId, conversationId) {
    return withSession(sessionId, (session) => assertConversation(session, conversationId));
  }

  function requireRunning(sessionId, conversationId) {
    return withSession(sessionId, (session) => assertRunning(session, conversationId));
  }

  function begin(args = {}) {
    const conversationId = validIdentifier(args.conversationId, "conversationId");
    const objective = boundedText(args.objective, 16_000);
    if (!objective) throw new Error("objective is required");
    const ttlSeconds = clampTtlSeconds(args.ttlSeconds);
    const now = clock();
    const session = store.create({
      schemaVersion: 2,
      id: `strict-${crypto.randomUUID()}`,
      conversationId,
      status: "running",
      objective,
      project: boundedText(args.project, 4096),
      createdAt: new Date(now).toISOString(),
      updatedAt: new Date(now).toISOString(),
      expiresAtMs: now + ttlSeconds * 1000,
      ttlSeconds,
      processId,
      processStartedAtMs,
      ownerId: runtimeOwnerId,
    });
    ownedSessionIds.add(session.id);
    return publicSession(session);
  }

  function status(args = {}) {
    return publicSession(requireConversation(args.strictSessionId, args.conversationId));
  }

  function touch(args = {}) {
    return withSession(args.strictSessionId, (session, commit) => {
      assertActive(session, args.conversationId);
      const now = clock();
      session.status = "running";
      session.waitingReason = "";
      session.updatedAt = new Date(now).toISOString();
      session.expiresAtMs = now + session.ttlSeconds * 1000;
      session.processId = processId;
      session.processStartedAtMs = processStartedAtMs;
      session.ownerId = runtimeOwnerId;
      return publicSession(commit(session));
    });
  }

  function wait(args = {}) {
    return withSession(args.strictSessionId, (session, commit) => {
      assertRunning(session, args.conversationId);
      const statusValue = String(args.status || "");
      if (!new Set(["waiting_user", "waiting_external"]).has(statusValue)) {
        throw new Error("status must be waiting_user or waiting_external");
      }
      session.status = statusValue;
      session.waitingReason = boundedText(args.reason, 4000);
      session.updatedAt = new Date(clock()).toISOString();
      return publicSession(commit(session));
    });
  }

  function finish(args = {}, terminalStatus) {
    if (!TERMINAL_STATES.has(terminalStatus)) throw new Error("invalid terminal Strict status");
    return withSession(args.strictSessionId, (session, commit) => {
      assertActive(session, args.conversationId);
      session.status = terminalStatus;
      session.terminalSummary = boundedText(args.summary || args.reason, 16_000);
      session.updatedAt = new Date(clock()).toISOString();
      session.completedAt = session.updatedAt;
      const stored = commit(session);
      ownedSessionIds.delete(session.id);
      return publicSession(stored);
    });
  }

  function resume(args = {}) {
    if (args.userApproved !== true) throw new Error("resume requires explicit userApproved=true");
    return withSession(args.strictSessionId, (session, commit) => {
      assertConversation(session, args.conversationId);
      if (session.status !== "orphaned") {
        throw new Error(`Only an orphaned Strict session can be resumed; current status is ${session.status}`);
      }
      const now = clock();
      session.status = "running";
      session.orphanReason = "";
      session.resumedAt = new Date(now).toISOString();
      session.updatedAt = session.resumedAt;
      session.expiresAtMs = now + session.ttlSeconds * 1000;
      session.processId = processId;
      session.processStartedAtMs = processStartedAtMs;
      session.ownerId = runtimeOwnerId;
      const stored = commit(session);
      ownedSessionIds.add(session.id);
      return publicSession(stored);
    });
  }

  async function runOperation(sessionId, conversationId, operationName, action) {
    return withSessionOperation(sessionId, async (session, commit) => {
      assertRunning(session, conversationId);
      session.operationId = crypto.randomUUID();
      session.operationName = boundedText(operationName, 200);
      session.operationStartedAt = new Date(clock()).toISOString();
      session.updatedAt = session.operationStartedAt;
      session = commit(session);
      let result;
      let operationError;
      try {
        result = await action(publicSession(session));
      } catch (error) {
        operationError = error;
      }
      const closeReason = pendingOrphans.get(session.id);
      if (closeReason) {
        session = commitOrphan(session, commit, closeReason);
      } else {
        const now = clock();
        delete session.operationId;
        delete session.operationName;
        delete session.operationStartedAt;
        session.updatedAt = new Date(now).toISOString();
        session.expiresAtMs = now + session.ttlSeconds * 1000;
        session = commit(session);
      }
      if (operationError) throw operationError;
      return { result, session: publicSession(session) };
    });
  }

  function orphanOwned(reason = "connection_closed") {
    for (const sessionId of [...ownedSessionIds]) {
      try {
        withSession(sessionId, (session, commit) => {
          if (ACTIVE_STATES.has(session.status) && ownerIsCurrent(session)) {
            commitOrphan(session, commit, reason);
          } else {
            ownedSessionIds.delete(sessionId);
          }
        });
      } catch (error) {
        if (error?.code === "STRICT_SESSION_BUSY") pendingOrphans.set(sessionId, reason);
        else ownedSessionIds.delete(sessionId);
      }
    }
  }

  for (const sessionId of store.listSessionIds()) {
    try {
      read(sessionId);
    } catch {
      // Invalid/future-schema state never authorizes work and remains inspectable.
    }
  }

  return {
    stateRoot: store.stateRoot,
    sessionsRoot: store.sessionsRoot,
    begin,
    status,
    touch,
    wait,
    complete: (args) => finish(args, "completed"),
    fail: (args) => finish(args, "failed"),
    cancel: (args) => finish(args, "cancelled"),
    resume,
    requireRunning,
    runOperation,
    orphanOwned,
  };
}

module.exports = {
  ACTIVE_STATES,
  TERMINAL_STATES,
  createStrictLifecycle,
};
