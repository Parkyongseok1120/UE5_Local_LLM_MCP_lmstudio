"use strict";

const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const { resolveAgentStateRoot, ensureStateRootLayout } = require("./state-root");

let ownerId = "";
let bridgePairId = "";
let localSessionId = "";

const BRIDGE_ID_RE = /^[A-Za-z0-9_.:-]{8,128}$/;

function validBridgeId(value) {
  return Boolean(value && BRIDGE_ID_RE.test(String(value)));
}

function bridgeConnectionPath() {
  try {
    const root = ensureStateRootLayout(resolveAgentStateRoot());
    return path.join(root, "mcp-bridge-pair.id");
  } catch {
    return "";
  }
}

function legacyBridgeConnectionPath() {
  try {
    const root = ensureStateRootLayout(resolveAgentStateRoot());
    return path.join(root, "mcp-bridge-connection.id");
  } catch {
    return "";
  }
}

function atomicWriteText(filePath, value) {
  const temp = `${filePath}.tmp-${crypto.randomUUID().replace(/-/g, "").slice(0, 8)}`;
  fs.writeFileSync(temp, value, "utf8");
  fs.renameSync(temp, filePath);
}

function getMcpBridgePairId() {
  if (bridgePairId) return bridgePairId;
  const fromEnv = String(process.env.MCP_BRIDGE_PAIR_ID || "").trim();
  if (validBridgeId(fromEnv)) {
    bridgePairId = fromEnv;
    return bridgePairId;
  }
  const filePath = bridgeConnectionPath();
  const legacyPath = legacyBridgeConnectionPath();
  if (!filePath) {
    bridgePairId = `mcp-bridge-local-${process.pid}-${crypto.randomUUID().replace(/-/g, "").slice(0, 8)}`;
    return bridgePairId;
  }
  try {
    if (fs.existsSync(filePath)) {
      const existing = String(fs.readFileSync(filePath, "utf8") || "").trim();
      if (validBridgeId(existing)) {
        bridgePairId = existing;
        return bridgePairId;
      }
    } else if (legacyPath && fs.existsSync(legacyPath)) {
      const existing = String(fs.readFileSync(legacyPath, "utf8") || "").trim();
      if (validBridgeId(existing)) {
        atomicWriteText(filePath, existing);
        bridgePairId = existing;
        return bridgePairId;
      }
    }
    const value = `mcp-bridge-${crypto.randomUUID().replace(/-/g, "")}`;
    if (fs.existsSync(filePath) || (legacyPath && fs.existsSync(legacyPath))) {
      atomicWriteText(filePath, value);
      bridgePairId = value;
      return bridgePairId;
    }
    try {
      const fd = fs.openSync(filePath, "wx");
      fs.writeFileSync(fd, value, "utf8");
      fs.closeSync(fd);
      bridgePairId = value;
      return bridgePairId;
    } catch (error) {
      if (error && error.code === "EEXIST") {
        const existing = String(fs.readFileSync(filePath, "utf8") || "").trim();
        if (validBridgeId(existing)) {
          bridgePairId = existing;
          return bridgePairId;
        }
        atomicWriteText(filePath, value);
        bridgePairId = value;
        return bridgePairId;
      }
      throw error;
    }
  } catch {
    bridgePairId = `mcp-bridge-local-${process.pid}-${crypto.randomUUID().replace(/-/g, "").slice(0, 8)}`;
    return bridgePairId;
  }
}

function getMcpClientSessionId() {
  const session = String(process.env.MCP_SESSION_ID || "").trim();
  if (session) return session;
  if (localSessionId) return localSessionId;
  localSessionId = `local-${process.pid}-${crypto.randomUUID().replace(/-/g, "").slice(0, 12)}`;
  return localSessionId;
}

function getMcpConnectionId() {
  if (ownerId) return ownerId;
  const session = String(process.env.MCP_SESSION_ID || "").trim();
  if (session) {
    ownerId = `${getMcpBridgePairId()}:${session}`;
    return ownerId;
  }
  const explicit = String(process.env.MCP_CONNECTION_ID || "").trim();
  if (explicit) {
    ownerId = explicit;
    return ownerId;
  }
  // Never use install-wide bridge file alone for ownership.
  ownerId = `mcp-local-${process.pid}-${crypto.randomUUID().replace(/-/g, "").slice(0, 12)}`;
  return ownerId;
}

function taskConnectionMatches(state) {
  if (!state || typeof state !== "object" || Array.isArray(state)) {
    return false;
  }
  const taskConnection = String(state.mcpConnectionId || "").trim();
  if (!taskConnection) return false;
  return taskConnection === getMcpConnectionId();
}

function taskOwnsActiveToolRoute(state) {
  if (!state || typeof state !== "object" || Array.isArray(state)) {
    return false;
  }
  if (String(state.status || "") !== "running") {
    return false;
  }
  const mode = String(state.mode || "").trim().toLowerCase();
  if (mode === "plan_only" || mode === "detached") {
    return false;
  }
  return taskConnectionMatches(state);
}

function taskIsForeignHealthy(state) {
  if (!state || typeof state !== "object" || Array.isArray(state)) {
    return false;
  }
  if (String(state.status || "") !== "running") {
    return false;
  }
  if (taskConnectionMatches(state)) {
    return false;
  }
  if (!String(state.mcpConnectionId || "").trim()) {
    return false;
  }
  const continuity = state.continuity && typeof state.continuity === "object"
    ? state.continuity
    : {};
  const lease = continuity.lease && typeof continuity.lease === "object"
    ? continuity.lease
    : null;
  if (lease) {
    const expiresAt = Date.parse(String(lease.expiresAt || ""));
    if (
      String(lease.status || "") !== "active"
      || !Number.isFinite(expiresAt)
      || expiresAt <= Date.now()
    ) {
      return false;
    }
  }
  const recovery = continuity.recovery && typeof continuity.recovery === "object"
    ? continuity.recovery
    : {};
  if (Array.isArray(recovery.conflicts) && recovery.conflicts.length) {
    return false;
  }
  const supervisor = state.autonomySupervisor
    && typeof state.autonomySupervisor === "object"
    ? state.autonomySupervisor
    : {};
  if (Array.isArray(supervisor.blockers) && supervisor.blockers.length) {
    return false;
  }
  return true;
}

module.exports = {
  getMcpConnectionId,
  getMcpBridgePairId,
  getMcpClientSessionId,
  taskOwnsActiveToolRoute,
  taskConnectionMatches,
  taskIsForeignHealthy,
};
