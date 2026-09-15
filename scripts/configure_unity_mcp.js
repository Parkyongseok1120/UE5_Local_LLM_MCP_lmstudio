#!/usr/bin/env node
"use strict";
// Emit an isolated Unity MCP configuration; never edit an existing host config implicitly.
const path = require("node:path");
const { projectPolicy } = require("../lmstudio-unity-mcp/src/project");
function configuration(projectRoot, node = process.execPath, workerEnv = process.env) {
  const project = projectPolicy(projectRoot);
  return { mcpServers: { "unity-tools": { command: node,
    args: [path.resolve(__dirname, "../lmstudio-unity-mcp/src/server.js")],
    env: { UNITY_PROJECT_ROOT: project.root, ALLOW_WRITE: "0", ALLOW_COMMANDS: "0",
      ...(workerEnv.UNITY_DOTNET ? { UNITY_DOTNET: path.resolve(workerEnv.UNITY_DOTNET) } : {}),
      ...(workerEnv.UNITY_SYMBOL_WORKER ? { UNITY_SYMBOL_WORKER: path.resolve(workerEnv.UNITY_SYMBOL_WORKER) } : {}) } } } };
}
module.exports = { configuration };
if (require.main === module) {
  try {
    if (process.argv.length !== 3) throw new Error("Usage: node scripts/configure_unity_mcp.js /absolute/path/to/UnityProject");
    console.log(JSON.stringify(configuration(process.argv[2]), null, 2));
  } catch (error) { console.error(error.message); process.exitCode = 1; }
}
