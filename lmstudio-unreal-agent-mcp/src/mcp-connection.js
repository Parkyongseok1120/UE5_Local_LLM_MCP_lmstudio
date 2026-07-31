"use strict";

const crypto = require("crypto");

let connectionId = "";

function getMcpConnectionId() {
  if (connectionId) return connectionId;
  const fromEnv = String(process.env.MCP_CONNECTION_ID || "").trim();
  if (fromEnv) {
    connectionId = fromEnv;
    return connectionId;
  }
  connectionId = `mcp-${process.pid}-${crypto.randomUUID().replace(/-/g, "").slice(0, 12)}`;
  return connectionId;
}

function taskOwnsActiveToolRoute(state) {
  if (!state || typeof state !== "object" || Array.isArray(state)) {
    return false;
  }
  if (String(state.status || "") !== "running") {
    return false;
  }
  const writeGate = state.writeGate && typeof state.writeGate === "object"
    ? state.writeGate
    : {};
  const writesAllowed = writeGate.writesAllowed === true || state.writesAllowed === true;
  if (!writesAllowed) {
    return false;
  }
  const taskConnection = String(state.mcpConnectionId || "").trim();
  if (!taskConnection) {
    return false;
  }
  return taskConnection === getMcpConnectionId();
}

module.exports = {
  getMcpConnectionId,
  taskOwnsActiveToolRoute,
};
