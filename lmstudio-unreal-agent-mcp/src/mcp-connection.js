"use strict";

const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const { resolveAgentStateRoot, ensureStateRootLayout } = require("./state-root");

let connectionId = "";

function bridgeConnectionPath() {
  const root = String(process.env.AGENT_STATE_ROOT || "").trim();
  if (!root) return "";
  return path.join(path.resolve(root), "mcp-bridge-connection.id");
}

function readOrCreateBridgeId() {
  const filePath = bridgeConnectionPath();
  if (!filePath) return "";
  try {
    ensureStateRootLayout(resolveAgentStateRoot());
    if (fs.existsSync(filePath)) {
      const existing = String(fs.readFileSync(filePath, "utf8") || "").trim();
      if (existing) return existing;
    }
    const value = `mcp-bridge-${crypto.randomUUID().replace(/-/g, "")}`;
    try {
      const fd = fs.openSync(filePath, "wx");
      fs.writeFileSync(fd, value, "utf8");
      fs.closeSync(fd);
      return value;
    } catch (error) {
      if (error && error.code === "EEXIST") {
        return String(fs.readFileSync(filePath, "utf8") || "").trim();
      }
      throw error;
    }
  } catch {
    return "";
  }
}

function getMcpConnectionId() {
  if (connectionId) return connectionId;
  for (const key of ["MCP_CONNECTION_ID", "MCP_SESSION_ID"]) {
    const fromEnv = String(process.env[key] || "").trim();
    if (fromEnv) {
      connectionId = fromEnv;
      return connectionId;
    }
  }
  const bridge = readOrCreateBridgeId();
  if (bridge) {
    connectionId = bridge;
    return connectionId;
  }
  connectionId = `mcp-${process.pid}-${crypto.randomUUID().replace(/-/g, "").slice(0, 12)}`;
  return connectionId;
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
  taskOwnsActiveToolRoute,
  taskConnectionMatches,
  taskIsForeignHealthy,
};
