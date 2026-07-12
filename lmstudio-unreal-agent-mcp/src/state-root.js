"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");

function resolveSharedConfigPath() {
  const fromEnv = String(process.env.SHARED_UNREAL_CONFIG || "").trim();
  if (fromEnv) {
    return path.resolve(fromEnv);
  }
  return path.join(os.homedir(), ".lmstudio", "config", "unreal-workspace.json");
}

function resolveAgentStateRoot(_workspaceRoot = "") {
  const override = String(process.env.AGENT_STATE_ROOT || "").trim();
  if (override) {
    return path.resolve(override);
  }
  const configPath = resolveSharedConfigPath();
  const configDir = path.dirname(configPath);
  return path.join(path.dirname(configDir), "state", "unreal-agent");
}

function ensureStateRootLayout(stateRoot = resolveAgentStateRoot()) {
  const root = path.resolve(stateRoot);
  for (const sub of ["locks", "transactions", "tasks", "jobs", "backups"]) {
    fs.mkdirSync(path.join(root, sub), { recursive: true });
  }
  return root;
}

function taskStateDir(taskSessionId, stateRoot = resolveAgentStateRoot()) {
  const safe = String(taskSessionId || "").trim();
  if (!safe || safe.includes("..") || safe.includes("/") || safe.includes("\\")) {
    throw new Error("invalid taskSessionId");
  }
  const dir = path.resolve(stateRoot, "tasks", safe);
  const tasksRoot = path.resolve(stateRoot, "tasks");
  if (!dir.startsWith(tasksRoot + path.sep) && dir !== tasksRoot) {
    throw new Error("taskSessionId resolves outside state tasks root");
  }
  return dir;
}

function jobsSqlitePath(stateRoot = resolveAgentStateRoot()) {
  return path.join(path.resolve(stateRoot), "jobs", "jobs.sqlite");
}

module.exports = {
  resolveSharedConfigPath,
  resolveAgentStateRoot,
  ensureStateRootLayout,
  taskStateDir,
  jobsSqlitePath,
};
