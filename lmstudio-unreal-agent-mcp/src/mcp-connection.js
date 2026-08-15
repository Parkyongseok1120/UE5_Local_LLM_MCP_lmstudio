"use strict";

const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const { resolveAgentStateRoot, ensureStateRootLayout } = require("./state-root");
const {
  tryAcquireCrossProcessLock,
  releaseCrossProcessLock,
} = require("./write-locks");
const {
  resolveOrCreateBootInstanceId,
  validId,
} = require("./mcp-boot-instance");

let ownerId = "";
let bridgePairId = "";
let clientInstanceId = "";
let localSessionId = "";
let bridgePairSource = "";
let clientInstanceSource = "";

const BRIDGE_ID_RE = /^[A-Za-z0-9_.:-]{8,128}$/;

function validBridgeId(value) {
  return Boolean(value && BRIDGE_ID_RE.test(String(value)));
}

function isLocalFallbackId(value) {
  return /^mcp-(?:bridge|boot|client)-local-/.test(String(value || ""));
}

function stateRootOrEmpty() {
  try {
    return ensureStateRootLayout(resolveAgentStateRoot());
  } catch {
    return "";
  }
}

function bridgeConnectionPath() {
  const root = stateRootOrEmpty();
  return root ? path.join(root, "mcp-bridge-pair.id") : "";
}

function legacyBridgeConnectionPath() {
  const root = stateRootOrEmpty();
  return root ? path.join(root, "mcp-bridge-connection.id") : "";
}

function atomicWriteText(filePath, value) {
  const temp = `${filePath}.tmp-${crypto.randomUUID().replace(/-/g, "").slice(0, 8)}`;
  fs.writeFileSync(temp, value, "utf8");
  fs.renameSync(temp, filePath);
}

function sleepMs(ms) {
  const sab = new SharedArrayBuffer(4);
  const view = new Int32Array(sab);
  Atomics.wait(view, 0, 0, Math.max(1, ms));
}

function lockedSharedId(filePath, label, prefix, legacyPath = "") {
  const root = stateRootOrEmpty();
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    const acquired = tryAcquireCrossProcessLock(filePath, label, root || undefined);
    if (!acquired.ok) {
      sleepMs(50);
      continue;
    }
    try {
      if (fs.existsSync(filePath)) {
        const existing = String(fs.readFileSync(filePath, "utf8") || "").trim();
        if (validBridgeId(existing)) return existing;
      } else if (legacyPath && fs.existsSync(legacyPath)) {
        const existing = String(fs.readFileSync(legacyPath, "utf8") || "").trim();
        if (validBridgeId(existing)) {
          atomicWriteText(filePath, existing);
          return String(fs.readFileSync(filePath, "utf8") || "").trim();
        }
      }
      const value = `${prefix}${crypto.randomUUID().replace(/-/g, "")}`;
      atomicWriteText(filePath, value);
      const final = String(fs.readFileSync(filePath, "utf8") || "").trim();
      return validBridgeId(final) ? final : value;
    } catch {
      return "";
    } finally {
      releaseCrossProcessLock(filePath);
    }
  }
  return "";
}

function getMcpBridgePairId() {
  if (bridgePairId) return bridgePairId;
  const fromEnv = String(process.env.MCP_BRIDGE_PAIR_ID || "").trim();
  if (validBridgeId(fromEnv)) {
    bridgePairId = fromEnv;
    bridgePairSource = "environment";
    return bridgePairId;
  }
  const filePath = bridgeConnectionPath();
  const legacyPath = legacyBridgeConnectionPath();
  if (!filePath) {
    bridgePairId = `mcp-bridge-local-${process.pid}-${crypto.randomUUID().replace(/-/g, "").slice(0, 8)}`;
    bridgePairSource = "local-fallback";
    return bridgePairId;
  }
  try {
    if (fs.existsSync(filePath)) {
      const existing = String(fs.readFileSync(filePath, "utf8") || "").trim();
      if (validBridgeId(existing)) {
        bridgePairId = existing;
        bridgePairSource = "state-root";
        return bridgePairId;
      }
    }
    const repaired = lockedSharedId(filePath, "mcp_bridge_pair", "mcp-bridge-", legacyPath);
    if (repaired) {
      bridgePairId = repaired;
      bridgePairSource = "state-root";
      return bridgePairId;
    }
  } catch {
    // fall through
  }
  bridgePairId = `mcp-bridge-local-${process.pid}-${crypto.randomUUID().replace(/-/g, "").slice(0, 8)}`;
  bridgePairSource = "local-fallback";
  return bridgePairId;
}

function getMcpClientInstanceId() {
  const fromEnv = String(process.env.MCP_CLIENT_INSTANCE_ID || "").trim();
  if (validId(fromEnv) || validBridgeId(fromEnv)) {
    clientInstanceId = fromEnv;
    clientInstanceSource = "environment";
    return clientInstanceId;
  }
  const renewed = resolveOrCreateBootInstanceId();
  if (renewed) {
    if (clientInstanceId && clientInstanceId !== renewed) ownerId = "";
    clientInstanceId = renewed;
    clientInstanceSource = isLocalFallbackId(renewed) ? "local-fallback" : "state-root";
    return clientInstanceId;
  }
  if (!clientInstanceId) {
    clientInstanceId = `mcp-client-local-${process.pid}-${crypto.randomUUID().replace(/-/g, "").slice(0, 8)}`;
    clientInstanceSource = "local-fallback";
  }
  return clientInstanceId;
}

function getMcpIdentityStatus() {
  // Deliberately return sources, not actual identifiers: startup diagnostics
  // need to reveal a broken shared-state bridge without exposing task-owner
  // material to client logs.
  getMcpBridgePairId();
  getMcpClientInstanceId();
  const degradedSources = [bridgePairSource, clientInstanceSource]
    .filter((source) => source === "local-fallback");
  return {
    bridgePairSource: bridgePairSource || "unknown",
    clientInstanceSource: clientInstanceSource || "unknown",
    degraded: degradedSources.length > 0,
    degradationReasons: [
      ...(bridgePairSource === "local-fallback" ? ["MCP_BRIDGE_LOCAL_FALLBACK"] : []),
      ...(clientInstanceSource === "local-fallback" ? ["MCP_CLIENT_LOCAL_FALLBACK"] : []),
    ],
  };
}

function getMcpConversationId(explicit = "") {
  const value = String(explicit || "").trim();
  if (/^[A-Za-z0-9_.:-]{4,128}$/.test(value)) return value;
  for (const key of ["MCP_SESSION_ID", "MCP_CONVERSATION_ID"]) {
    const envValue = String(process.env[key] || "").trim();
    if (/^[A-Za-z0-9_.:-]{4,128}$/.test(envValue)) return envValue;
  }
  return "";
}

function buildMcpConnectionId(conversationId = "") {
  const conv = getMcpConversationId(conversationId);
  const bridge = getMcpBridgePairId();
  const instance = getMcpClientInstanceId();
  if (conv) return `${bridge}:${instance}:${conv}`;
  const explicit = String(process.env.MCP_CONNECTION_ID || "").trim();
  if (explicit && !conversationId) return explicit;
  return `${bridge}:${instance}`;
}

function getMcpClientSessionId() {
  const session = getMcpConversationId();
  if (session) return session;
  if (localSessionId) return localSessionId;
  localSessionId = `local-${process.pid}-${crypto.randomUUID().replace(/-/g, "").slice(0, 12)}`;
  return localSessionId;
}

function getMcpConnectionId(conversationId = "") {
  const conv = getMcpConversationId(conversationId);
  if (conv || conversationId) {
    return buildMcpConnectionId(conv || conversationId);
  }
  const built = buildMcpConnectionId();
  // Invalidate stale process cache when bridge/boot identity changes (tests, restart).
  if (ownerId && ownerId !== built) ownerId = "";
  ownerId = built;
  return ownerId;
}

function ownerCapabilityMatches(state, ownerCapability = "") {
  if (!state || typeof state !== "object" || Array.isArray(state)) return false;
  const expected = String(state.ownerCapability || "").trim();
  const provided = String(ownerCapability || "").trim();
  if (!expected || !provided || expected.length !== provided.length) return false;
  return crypto.timingSafeEqual(Buffer.from(expected), Buffer.from(provided));
}

function taskConnectionMatches(state, conversationId = "", ownerCapability = "") {
  if (!state || typeof state !== "object" || Array.isArray(state)) return false;
  if (ownerCapabilityMatches(state, ownerCapability)) return true;
  // An explicit capability claim never falls back to legacy connection matching.
  if (String(ownerCapability || "").trim()) return false;
  const taskCapability = String(state.ownerCapability || "").trim();
  const taskConv = String(state.conversationId || "").trim();
  if (taskCapability || taskConv) return false;
  const taskConnection = String(state.mcpConnectionId || "").trim();
  if (!taskConnection) return false;
  return taskConnection === buildMcpConnectionId();
}

function taskOwnsActiveToolRoute(state, conversationId = "", ownerCapability = "") {
  if (!state || typeof state !== "object" || Array.isArray(state)) return false;
  if (String(state.status || "") !== "running") return false;
  const mode = String(state.mode || "").trim().toLowerCase();
  if (mode === "plan_only" || mode === "detached") return false;
  return taskConnectionMatches(state, conversationId, ownerCapability);
}

function taskIsForeignHealthy(state, conversationId = "", ownerCapability = "") {
  if (!state || typeof state !== "object" || Array.isArray(state)) return false;
  if (String(state.status || "") !== "running") return false;
  if (taskConnectionMatches(state, conversationId, ownerCapability)) return false;
  if (!String(state.mcpConnectionId || "").trim()) return false;
  const continuity = state.continuity && typeof state.continuity === "object" ? state.continuity : {};
  const lease = continuity.lease && typeof continuity.lease === "object" ? continuity.lease : null;
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
  if (Array.isArray(recovery.conflicts) && recovery.conflicts.length) return false;
  const supervisor = state.autonomySupervisor && typeof state.autonomySupervisor === "object"
    ? state.autonomySupervisor
    : {};
  if (Array.isArray(supervisor.blockers) && supervisor.blockers.length) return false;
  return true;
}

module.exports = {
  getMcpConnectionId,
  buildMcpConnectionId,
  getMcpBridgePairId,
  getMcpClientInstanceId,
  getMcpConversationId,
  getMcpClientSessionId,
  getMcpIdentityStatus,
  ownerCapabilityMatches,
  taskOwnsActiveToolRoute,
  taskConnectionMatches,
  taskIsForeignHealthy,
};
