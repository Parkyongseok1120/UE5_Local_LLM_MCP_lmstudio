#!/usr/bin/env node
"use strict";
// Post-install diagnosis of one unity-tools MCP configuration. It reports what
// is ready and, for anything that is not, the step that fixes it. A missing
// Editor connection is a state to report, not an install failure; the
// installer decides which findings block installation.
const fs = require("node:fs");
const { Client } = require("../lmstudio-unity-mcp/node_modules/@modelcontextprotocol/sdk/dist/cjs/client/index.js");
const { StdioClientTransport } = require("../lmstudio-unity-mcp/node_modules/@modelcontextprotocol/sdk/dist/cjs/client/stdio.js");
async function main() {
  const entry = JSON.parse(fs.readFileSync(process.argv[2], "utf8")).mcpServers?.["unity-tools"];
  if (!entry) throw Error("unity-tools configuration missing");
  const client = new Client({ name: "unity-install-check", version: "1" });
  try {
    await client.connect(new StdioClientTransport({ ...entry, env: { ...process.env, ...entry.env }, stderr: "pipe" }));
    const list = await client.listTools();
    for (const name of ["unity_status", "list_directory", "search_files", "unity_symbols", "unity_snapshot", "unity_prefab", "unity_approval", "unity_tests"]) if (!list.tools.some(t => t.name === name)) throw Error("Missing required tool: " + name);
    if (entry.env?.WORKSPACE_CAPABILITIES !== "0") {
      for (const name of ["workspace_status", "git_status", "git_log", "git_changed_files", "git_diff_file", "git_read_file"])
        if (!list.tools.some(t => t.name === name)) throw Error("Missing Workspace tool: " + name);
      const binding = (await client.callTool({ name: "workspace_status", arguments: { project: entry.env.UNITY_PROJECT_ROOT } })).structuredContent;
      if (binding?.status !== "observed" || binding.engine !== "unity" || binding.independentWriter !== false) throw Error("Workspace binding or mutation owner mismatch");
    }
    const status = (await client.callTool({ name: "unity_status", arguments: {} })).structuredContent;
    const symbols = (await client.callTool({ name: "unity_symbols", arguments: { action: "status" } })).structuredContent;
    const workerPath = entry.env?.UNITY_SYMBOL_WORKER || "";
    const worker = symbols?.configured && workerPath && fs.existsSync(workerPath)
      ? { state: "configured" }
      : { state: "missing", remedy: "Reinstall the Unity component so the C# symbol worker is built, or pass --unity-symbol-worker and --unity-dotnet." };
    const connected = status.connection === "connected";
    console.log(JSON.stringify({
      initialized: true, tools: list.tools.length, modelConnection: "not_verified",
      connection: status.connection, reason: status.reason, reasonCode: status.reasonCode || status.reason,
      ...(status.nextStep ? { nextStep: status.nextStep } : {}),
      serverPermissions: status.serverPermissions || { write: entry.env?.ALLOW_WRITE === "1", commands: entry.env?.ALLOW_COMMANDS === "1" },
      ...(connected ? {
        bridge: { version: status.bridgeVersion, protocolVersion: status.protocolVersion },
        editorVersion: status.editorVersion,
        editorPermissions: { edit: status.permissions?.edit === true, execute: status.permissions?.execute === true },
        testFramework: status.capabilities?.testExecution === true ? "available" : "unavailable",
        compilation: { compiling: status.compiling === true, outcome: status.compilationOutcome || "unknown" },
      } : {}),
      worker: worker.state, ...(worker.remedy ? { workerRemedy: worker.remedy } : {}),
      // Kept for older installer reports.
      workerConfigured: worker.state === "configured",
    }));
  } finally { await client.close(); }
}
main().catch(e => { console.error(e.message); process.exitCode = 1; });
