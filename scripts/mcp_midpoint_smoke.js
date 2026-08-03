"use strict";
/**
 * MCP smoke: active project, read, write probe, read-back freshness, patch, cleanup.
 * Does NOT modify game code permanently (uses Saved/ probe file under O-Mock).
 */
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const { spawn } = require("node:child_process");

const REPO = path.resolve(__dirname, "..");
const WORKSPACE = process.env.E2E_WORKSPACE || "C:\\Users\\sster\\Documents\\Git\\O-Mock";
const PROBE_REL = "Source/O_Mock/Tests/_mcp_midpoint_probe.txt";
const PROBE_ABS = path.join(WORKSPACE, PROBE_REL.replace(/\//g, path.sep));
const DEBUG = path.join(REPO, "debug-821b0f.log");

function log(message, data, hypothesisId = "H-smoke") {
  fs.appendFileSync(
    DEBUG,
    JSON.stringify({
      sessionId: "821b0f",
      runId: "mcp-smoke",
      hypothesisId,
      location: "mcp_midpoint_smoke.js",
      message,
      data,
      timestamp: Date.now(),
    }) + "\n",
  );
  console.log(message, JSON.stringify(data));
}

/** MCP wraps tool JSON inside content[].text; JSON.stringify escapes quotes as \". */
function toolPayloadOk(toolResult) {
  if (!toolResult || toolResult.isError) return false;
  const text =
    Array.isArray(toolResult.content) &&
    toolResult.content
      .filter((c) => c && c.type === "text" && typeof c.text === "string")
      .map((c) => c.text)
      .join("\n");
  if (!text) {
    const raw = JSON.stringify(toolResult);
    return /"ok"\s*:\s*true/.test(raw) || /\\"ok\\"\s*:\s*true/.test(raw);
  }
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

  // Match supervisor_local_ai_turn.js isolation: fresh AGENT_STATE_ROOT + plan-auth off.
  // Shared ~/.lmstudio/state/unreal-agent can be TASK_ROUTE_BLOCKED by stale LM Studio chats.
  const agentStateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "midpoint-smoke-state-"));
  const env = {
    ...process.env,
    ...(unreal.env || {}),
    WORKSPACE_ROOT: WORKSPACE,
    E2E_WORKSPACE: WORKSPACE,
    AGENT_STATE_ROOT: agentStateRoot,
    MCP_REQUIRE_PLAN_AUTH: "0",
    ALLOW_WRITE: "1",
    ALLOW_EXISTING_SOURCE_WRITE: "1",
    MCP_EXTENDED_TOOLS: "1",
    MCP_ESSENTIAL_TOOLS: "0",
  };

  const transport = new StdioClientTransport({
    command: unreal.command,
    args: unreal.args || [],
    env,
    cwd: unreal.cwd || REPO,
  });
  const client = new Client({ name: "midpoint-smoke", version: "0.0.1" });
  await client.connect(transport);

  const result = {
    activeProject: null,
    writeOk: false,
    readFresh: false,
    patchOk: false,
    readAfterPatch: false,
    cleaned: false,
    errors: [],
  };

  try {
    const active = await client.callTool({
      name: "get_active_project",
      arguments: {},
    });
    const activeText = JSON.stringify(active);
    result.activeProject = activeText.includes("O-Mock");
    log("active_project", { ok: result.activeProject, snippet: activeText.slice(0, 300) });

    const token1 = `PROBE1_${Date.now()}`;
    const write1 = await client.callTool({
      name: "write_file",
      arguments: {
        path: PROBE_REL,
        content: token1 + "\n",
      },
    });
    // #region agent log
    result.writeOk = toolPayloadOk(write1);
    log(
      "write1",
      {
        ok: result.writeOk,
        isError: !!write1.isError,
        snippet: JSON.stringify(write1).slice(0, 400),
      },
      "H2",
    );
    // #endregion

    const read1 = await client.callTool({
      name: "read_file",
      arguments: { path: PROBE_REL },
    });
    // #region agent log
    result.readFresh = toolTextIncludes(read1, token1);
    log("read1_fresh", { ok: result.readFresh, hasToken: result.readFresh }, "H2");
    // #endregion

    const token2 = `PROBE2_${Date.now()}`;
    const patch = await client.callTool({
      name: "replace_in_file",
      arguments: {
        path: PROBE_REL,
        oldText: token1,
        newText: token2,
      },
    });
    // #region agent log
    result.patchOk = toolPayloadOk(patch);
    log(
      "patch",
      {
        ok: result.patchOk,
        isError: !!patch.isError,
        snippet: JSON.stringify(patch).slice(0, 400),
      },
      "H2",
    );
    // #endregion

    const read2 = await client.callTool({
      name: "read_file",
      arguments: { path: PROBE_REL },
    });
    // #region agent log
    result.readAfterPatch =
      toolTextIncludes(read2, token2) && !toolTextIncludes(read2, token1);
    log("read2_after_patch", { ok: result.readAfterPatch }, "H2");
    // #endregion

    try {
      fs.unlinkSync(PROBE_ABS);
      result.cleaned = true;
    } catch (e) {
      result.cleaned = false;
      result.errors.push(String(e.message || e));
    }
  } catch (e) {
    result.errors.push(String(e && e.stack ? e.stack : e));
    log("smoke_exception", { error: result.errors[0].slice(0, 500) });
  } finally {
    try {
      await client.close();
    } catch {
      /* ignore */
    }
  }

  fs.writeFileSync(
    path.join(__dirname, "mcp_midpoint_smoke_report.json"),
    JSON.stringify(result, null, 2),
  );
  log("smoke_done", result);
  const ok =
    result.activeProject &&
    result.writeOk &&
    result.readFresh &&
    result.patchOk &&
    result.readAfterPatch;
  process.exit(ok ? 0 : 2);
}

main();
