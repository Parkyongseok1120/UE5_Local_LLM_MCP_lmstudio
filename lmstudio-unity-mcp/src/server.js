#!/usr/bin/env node
"use strict";
const Ajv = require("ajv");
const jsonc = require("jsonc-parser");
const { Server } = require("@modelcontextprotocol/sdk/server/index.js");
const { StdioServerTransport } = require("@modelcontextprotocol/sdk/server/stdio.js");
const { ListToolsRequestSchema, CallToolRequestSchema } = require("@modelcontextprotocol/sdk/types.js");
const { Files, bounded, fail } = require("../../shared-tool-core/files");
const { dataTools } = require("../../shared-tool-core/data");
const { projectPolicy } = require("./project");
const { BridgeClient } = require("./bridge-client");
const { tools } = require("./catalog");
const { Symbols } = require("./symbols");
const PROJECT_FILE_TOOLS = new Set([
  "read_file", "list_directory", "search_files", "patch_file", "create_file",
  "structured_data_read", "structured_data_patch",
]);
function createRuntime(env = process.env, injectedBridge) {
  const policy = projectPolicy(env.UNITY_PROJECT_ROOT);
  const files = new Files(policy, { allowWrite: env.ALLOW_WRITE === "1" });
  const data = dataTools(files, jsonc, Ajv);
  const bridge = injectedBridge || new BridgeClient(policy);
  const symbols = new Symbols(policy, bridge, env);
  const ajv = new Ajv({ strict: false });
  const validators = new Map(tools.map(t => [t.name, ajv.compile(t.inputSchema)]));
  async function call(name, args = {}) {
    try {
      const validate = validators.get(name);
      if (!validate || !validate(args)) fail("invalid_arguments", validate ? ajv.errorsText(validate.errors) : "Unknown tool");
      if (name === "unity_approval" && args.action === "request") {
        const nested = validators.get(args.method);
        if (!nested || !nested(args.arguments) || args.arguments?.approvalId) fail("invalid_arguments", "Approval must contain valid final operation arguments, without approvalId");
      }
      let result;
      if (name === "unity_symbols") result = await symbols.call(args);
      else if (name === "read_file") result = await files.read(args);
      else if (name === "list_directory") result = files.list(args);
      else if (name === "search_files") result = await files.search(args);
      else if (name === "patch_file") result = await files.patch(args);
      else if (name === "create_file") result = await files.mutate(args, () => args.content, true);
      else if (name === "structured_data_read") result = await data.read(args);
      else if (name === "structured_data_patch") {
        if (args.format === "json" && !args.changes?.length || args.format === "csv" && !args.cells?.length) fail("invalid_arguments", "Supply changes (JSON) or cells (CSV)");
        result = await data.patch(args);
      } else {
        const edit = name === "unity_object_patch" || name === "unity_asset" || name === "unity_scene" && args.action !== "list" || name === "unity_prefab" && !["read", "contents", "overrides"].includes(args.action) || name === "unity_approval" && args.action === "request";
        const execute = name === "unity_editor" || name === "unity_debug_action" || name === "unity_tests" && !["status", "results"].includes(args.action) || name === "unity_operation" && args.action === "cancel";
        if (edit && env.ALLOW_WRITE !== "1") fail("edit_disabled", "Adapter Edit permission is disabled");
        if (execute && env.ALLOW_COMMANDS !== "1") fail("execute_disabled", "Adapter Execute permission is disabled");
        result = await bridge.call(name, args);
      }
      if (PROJECT_FILE_TOOLS.has(name) && result && !result.errorCode) {
        result = { ...result, canonicalProjectRoot: policy.root, projectIdentity: policy.projectIdentity };
      }
      return bounded(result, args.byteBudget ?? 65536);
    } catch (error) {
      if (name === "unity_status" && error.code !== "invalid_arguments") return { status: "observed", connection: "disconnected", projectIdentity: policy.projectIdentity, canonicalProjectRoot: policy.root,
        fileTools: "available", unityObjects: "unavailable", lastKnownSession: bridge.lastSession, reason: error.code || "bridge_unavailable" };
      return { status: error.status || "not_applied", errorCode: error.code || "tool_error", message: String(error.message).slice(0, 1000), ...(args.operationId ? { operationId: args.operationId } : {}) };
    }
  }
  return { tools, call, policy };
}
async function main() {
  const runtime = createRuntime();
  const server = new Server({ name: "lmstudio-unity-mcp", version: "1.4.0-beta.1" }, { capabilities: { tools: {} } });
  server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: runtime.tools }));
  server.setRequestHandler(CallToolRequestSchema, async request => {
    const payload = await runtime.call(request.params.name, request.params.arguments ?? {});
    return { content: [{ type: "text", text: JSON.stringify(payload) }], isError: Boolean(payload.errorCode), structuredContent: payload };
  });
  await server.connect(new StdioServerTransport());
}
module.exports = { createRuntime };
if (require.main === module) main().catch(error => { console.error(error.message); process.exitCode = 1; });
