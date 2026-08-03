"use strict";

const path = require("path");
const os = require("os");
const REPO = path.resolve(__dirname, "..");
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

(async () => {
  const nodeBin = path.join(os.homedir(), ".evidence-first", "runtimes", "node-v20.20.2", "node.exe");
  const transport = new StdioClientTransport({
    command: nodeBin,
    args: [path.join(REPO, "lmstudio-unreal-agent-mcp", "src", "server.js")],
    env: process.env,
  });
  const client = new Client({ name: "smoke", version: "1.0.0" }, { capabilities: {} });
  await client.connect(transport);

  const active = await client.callTool({ name: "get_active_project", arguments: {} });
  const activeText = active.content?.[0]?.text || JSON.stringify(active);
  console.log("ACTIVE_OK", /O_Mock|O-Mock/.test(activeText));

  const read = await client.callTool({
    name: "read_file",
    arguments: { path: "Source/O_Mock/GomokuGameState.cpp" },
  });
  const readText = read.content?.[0]?.text || "";
  console.log("READ_OK", readText.length > 100);

  const hugeNew = Array.from({ length: 90 }, (_, i) => `// pad line ${i}`).join("\n");
  const patch = await client.callTool({
    name: "replace_in_file",
    arguments: {
      path: "Source/O_Mock/GomokuGameState.cpp",
      oldText: "void AGomokuGameState::",
      newText: hugeNew,
      expectedOccurrences: 1,
    },
  });
  const patchText = patch.content?.[0]?.text || JSON.stringify(patch);
  let parsed;
  try {
    parsed = JSON.parse(patchText);
  } catch {
    parsed = { raw: patchText.slice(0, 500) };
  }
  console.log("ERROR_CODE", parsed.errorCode || "none");
  console.log("NEXT_ACTION", parsed.nextAction || "none");
  console.log("DO_NOT_RETRY", JSON.stringify(parsed.doNotRetry || []));
  console.log("INSTRUCTION_HAS_NO_REREAD", /Do NOT re-read/i.test(String(parsed.agentInstruction || "")));
  const ok = (
    (parsed.errorCode === "BOUNDED_PATCH_REQUIRED" || /too large/i.test(patchText))
    && parsed.nextAction === "replace_in_file"
    && Array.isArray(parsed.doNotRetry)
    && parsed.doNotRetry.includes("read_file_range")
    && /Do NOT re-read/i.test(String(parsed.agentInstruction || ""))
  );
  console.log("SMOKE_BOUNDED_RECOVERY", ok ? "PASS" : "FAIL");
  console.log("PREVIEW", JSON.stringify(parsed).slice(0, 700));
  await client.close?.();
  process.exit(ok ? 0 : 2);
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
