"use strict";

const os = require("node:os");
const path = require("node:path");

function resolveSharedConfigPath(env = process.env) {
  const configured = String(env.SHARED_UNREAL_CONFIG || "").trim();
  return configured
    ? path.resolve(configured)
    : path.join(os.homedir(), ".lmstudio", "config", "unreal-workspace.json");
}

function resolveAgentStateRoot(options = {}) {
  const env = options.env || process.env;
  const configured = String(options.stateRoot || env.AGENT_STATE_ROOT || "").trim();
  if (configured) return path.resolve(configured);
  const configDir = path.dirname(resolveSharedConfigPath(env));
  const appRoot = path.basename(configDir).toLowerCase() === "config"
    ? path.dirname(configDir)
    : configDir;
  return path.join(appRoot, "state", "unreal-agent");
}

module.exports = { resolveAgentStateRoot, resolveSharedConfigPath };
