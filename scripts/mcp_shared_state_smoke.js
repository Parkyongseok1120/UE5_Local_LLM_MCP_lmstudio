"use strict";
/**
 * Smoke against SHARED LM Studio MCP state (no AGENT_STATE_ROOT isolation).
 * Verifies routeContext is unblocked after quarantine.
 */
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");

const REPO = path.resolve(__dirname, "..");
const WORKSPACE = process.env.E2E_WORKSPACE || "C:\\Users\\sster\\Documents\\Git\\O-Mock";
const PROBE_REL = "Source/O_Mock/Tests/_mcp_shared_smoke_probe.txt";
const PROBE_ABS = path.join(WORKSPACE, PROBE_REL.replace(/\//g, path.sep));
const DEBUG = path.join(REPO, "debug-821b0f.log");

function log(message, data, hypothesisId = "H1") {
  fs.appendFileSync(
    DEBUG,
    JSON.stringify({
      sessionId: "821b0f",
      runId: "mcp-shared-smoke",
      hypothesisId,
      location: "mcp_shared_state_smoke.js",
      message,
      data,
      timestamp: Date.now(),
    }) + "\n",
  );
  console.log(message, JSON.stringify(data));
}

function toolPayloadOk(toolResult) {
  if (!toolResult || toolResult.isError) return false;
  const text =
    Array.isArray(toolResult.content) &&
    toolResult.content
      .filter((c) => c && c.type === "text" && typeof c.text === "string")
      .map((c) => c.text)
      .join("\n");
  if (!text) return false;
  try {
    const parsed = JSON.parse(text);
    if (parsed && parsed.ok === true) return true;
  } catch {
    /* fall through */
  }
  return /"ok"\s*:\s*true/.test(text);
}

function toolTextIncludes(toolResult, needle) {
  const text =
    Array.isArray(toolResult.content) &&
    toolResult.content
      .filter((c) => c && c.type === "text" && typeof c.text === "string")
      .map((c) => c.text)
      .join("\n");
  return typeof text === "string" && text.includes(needle);
}

async function main() {
  const { Client } = require(path.join(
    REPO,
    "lmstudio-unreal-agent-mcp",
    "node_modules",
    "@modelcontextprotocol",
    "sdk",
    "dist",
    "cjs",
    "client",
    "index.js",
  ));
  const { StdioClientTransport } = require(path.join(
    REPO,
    "lmstudio-unreal-agent-mcp",
    "node_modules",
    "@modelcontextprotocol",
    "sdk",
    "dist",
    "cjs",
    "client",
    "stdio.js",
  ));

  const mcpJson = JSON.parse(
    fs.readFileSync(path.join(os.homedir(), ".lmstudio", "mcp.json"), "utf8"),
  );
  const unreal = mcpJson.mcpServers && (mcpJson.mcpServers["unreal-agent"] || mcpJson.mcpServers.unreal);
  if (!unreal) throw new Error("unreal-agent missing from mcp.json");

  // Intentionally use SHARED state (do not set AGENT_STATE_ROOT).
  const env = {
    ...process.env,
    ...(unreal.env || {}),
    WORKSPACE_ROOT: WORKSPACE,
    E2E_WORKSPACE: WORKSPACE,
    MCP_REQUIRE_PLAN_AUTH: "0",
    ALLOW_WRITE: "1",
    ALLOW_EXISTING_SOURCE_WRITE: "1",
    MCP_EXTENDED_TOOLS: "1",
    MCP_ESSENTIAL_TOOLS: "0",
  };
  delete env.AGENT_STATE_ROOT;

  const transport = new StdioClientTransport({
    command: unreal.command,
    args: unreal.args || [],
    env,
    cwd: unreal.cwd || REPO,
  });
  const client = new Client({ name: "shared-smoke", version: "0.0.1" });
  await client.connect(transport);

  const result = {
    routeContextStatus: null,
    activeProject: false,
    writeOk: false,
    readFresh: false,
    patchOk: false,
    readAfterPatch: false,
    cleaned: false,
    errors: [],
  };

  try {
    // Catalog init line is on stderr; also call get_active_project / write.
    const active = await client.callTool({ name: "get_active_project", arguments: {} });
    const activeText = JSON.stringify(active);
    result.activeProject = activeText.includes("O-Mock");
    // #region agent log
    log("shared_active_project", { ok: result.activeProject, snippet: activeText.slice(0, 300) }, "H1");
    // #endregion

    const token1 = `SHARED1_${Date.now()}`;
    const write1 = await client.callTool({
      name: "write_file",
      arguments: { path: PROBE_REL, content: token1 + "\n" },
    });
    result.writeOk = toolPayloadOk(write1);
    // #region agent log
    log(
      "shared_write1",
      { ok: result.writeOk, isError: !!write1.isError, snippet: JSON.stringify(write1).slice(0, 500) },
      "H1",
    );
    // #endregion

    const read1 = await client.callTool({ name: "read_file", arguments: { path: PROBE_REL } });
    result.readFresh = toolTextIncludes(read1, token1);

    const token2 = `SHARED2_${Date.now()}`;
    const patch = await client.callTool({
      name: "replace_in_file",
      arguments: { path: PROBE_REL, oldText: token1, newText: token2 },
    });
    result.patchOk = toolPayloadOk(patch);

    const read2 = await client.callTool({ name: "read_file", arguments: { path: PROBE_REL } });
    result.readAfterPatch = toolTextIncludes(read2, token2) && !toolTextIncludes(read2, token1);

    try {
      fs.unlinkSync(PROBE_ABS);
      result.cleaned = true;
    } catch (e) {
      result.errors.push(String(e.message || e));
    }
  } catch (e) {
    result.errors.push(String(e && e.stack ? e.stack : e));
  } finally {
    try {
      await client.close();
    } catch {
      /* ignore */
    }
  }

  fs.writeFileSync(path.join(__dirname, "mcp_shared_state_smoke_report.json"), JSON.stringify(result, null, 2));
  // #region agent log
  log("shared_smoke_done", result, "H1");
  // #endregion
  const ok =
    result.activeProject &&
    result.writeOk &&
    result.readFresh &&
    result.patchOk &&
    result.readAfterPatch;
  process.exit(ok ? 0 : 2);
}

main();
