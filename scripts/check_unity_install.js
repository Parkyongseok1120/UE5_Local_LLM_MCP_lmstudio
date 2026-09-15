#!/usr/bin/env node
"use strict";
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
    for (const name of ["unity_status", "unity_symbols", "unity_snapshot", "unity_prefab", "unity_approval", "unity_tests"]) if (!list.tools.some(t => t.name === name)) throw Error("Missing required tool: " + name);
    const status = (await client.callTool({ name: "unity_status", arguments: {} })).structuredContent;
    const symbols = (await client.callTool({ name: "unity_symbols", arguments: { action: "status" } })).structuredContent;
    if (!symbols?.configured) throw Error("C# worker configuration missing");
    console.log(JSON.stringify({ initialized: true, tools: list.tools.length, workerConfigured: true, connection: status.connection, reason: status.reason, modelConnection: "not_verified" }));
  } finally { await client.close(); }
}
main().catch(e => { console.error(e.message); process.exitCode = 1; });
