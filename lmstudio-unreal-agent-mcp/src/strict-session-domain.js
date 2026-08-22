"use strict";

const ACTIVE_STATES = new Set(["running", "waiting_user", "waiting_external"]);
const TERMINAL_STATES = new Set(["completed", "failed", "cancelled"]);
const ALL_STATES = new Set([...ACTIVE_STATES, ...TERMINAL_STATES, "orphaned"]);

function boundedText(value, maxChars) {
  const text = String(value || "").trim();
  return text.length <= maxChars ? text : text.slice(0, maxChars);
}

function clampTtlSeconds(value, fallback = 6 * 60 * 60) {
  const parsed = Number(value);
  const seconds = Number.isFinite(parsed) ? Math.trunc(parsed) : fallback;
  return Math.max(60, Math.min(7 * 24 * 60 * 60, seconds));
}

function validIdentifier(value, field) {
  const text = String(value || "").trim();
  if (!text || text.length > 200 || !/^[A-Za-z0-9._:@-]+$/.test(text)) {
    throw new Error(`${field} must be 1-200 safe identifier characters`);
  }
  return text;
}

function normalizeStoredSession(raw, expectedId) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    throw new Error("Strict session state is invalid");
  }
  const version = raw.schemaVersion === undefined ? 0 : Number(raw.schemaVersion);
  const revision = raw.revision === undefined ? 0 : Number(raw.revision);
  if (!Number.isInteger(version) || version < 0 || version > 2) {
    throw new Error(`Unsupported Strict session schemaVersion: ${raw.schemaVersion}`);
  }
  const session = { ...raw, schemaVersion: 2, revision };
  if (
    session.id !== String(expectedId)
    || !/^strict-[0-9a-f-]{36}$/i.test(session.id)
    || !ALL_STATES.has(session.status)
    || typeof session.conversationId !== "string"
    || !session.conversationId
    || typeof session.objective !== "string"
    || !Number.isFinite(Number(session.expiresAtMs))
    || !Number.isFinite(Number(session.ttlSeconds))
    || !Number.isInteger(revision)
    || revision < 0
  ) {
    throw new Error("Strict session state is invalid");
  }
  return { session, migrated: version !== 2 || raw.revision === undefined };
}

function publicSession(session) {
  return {
    id: session.id,
    conversationId: session.conversationId,
    status: session.status,
    objective: session.objective,
    project: session.project || null,
    createdAt: session.createdAt,
    updatedAt: session.updatedAt,
    expiresAt: new Date(session.expiresAtMs).toISOString(),
    waitingReason: session.waitingReason || null,
    terminalSummary: session.terminalSummary || null,
    orphanReason: session.orphanReason || null,
  };
}

module.exports = {
  ACTIVE_STATES,
  TERMINAL_STATES,
  boundedText,
  clampTtlSeconds,
  normalizeStoredSession,
  publicSession,
  validIdentifier,
};
