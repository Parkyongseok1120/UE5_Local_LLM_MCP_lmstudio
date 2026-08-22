#!/usr/bin/env node
"use strict";

/**
 * Direct Model Mode composition root.
 *
 * The server owns a static capability catalog, compact MCP transport, and
 * safety-runtime assembly. Capability implementations live behind narrow
 * project/read/mutation/diagnostic boundaries; this file owns no workflow.
 */

const { Server } = require("@modelcontextprotocol/sdk/server/index.js");
const { StdioServerTransport } = require("@modelcontextprotocol/sdk/server/stdio.js");
const { CallToolRequestSchema, ListToolsRequestSchema } = require("@modelcontextprotocol/sdk/types.js");

const { createDirectRuntimeContext } = require("./direct-runtime-context.js");
const { createProjectCapabilities } = require("./direct-project-capabilities.js");
const { createReadCapabilities } = require("./direct-read-capabilities.js");
const { createLogCapabilities } = require("./direct-log-capabilities.js");
const { createMutationCapabilities } = require("./direct-mutation-capabilities.js");
const { createDiagnosticCapabilities } = require("./direct-diagnostic-capabilities.js");
const { toolDefinitions } = require("./direct-tool-catalog.js");
const { cleanArgs, envFlag, errorFromException, statSignature } = require("./direct-runtime-shared.js");
const { failure } = require("./direct-response.js");
const { probeMutationSemanticGuard } = require("./mutation-semantic-guard.js");
const { recoverRuntimeTransactions } = require("./direct-transaction-recovery.js");

function createDirectRuntime(options = {}) {
  const context = createDirectRuntimeContext(options);
  const handlers = Object.freeze({
    ...createProjectCapabilities(context),
    ...createReadCapabilities(context),
    ...createLogCapabilities(context),
    ...createMutationCapabilities(context),
    ...createDiagnosticCapabilities(context),
  });
  const tools = toolDefinitions();
  const toolsByName = new Map(tools.map((tool) => [tool.name, tool]));
  const missingHandlers = tools.map((tool) => tool.name).filter((name) => typeof handlers[name] !== "function");
  if (missingHandlers.length) throw new Error(`Direct catalog has no handler: ${missingHandlers.join(", ")}`);

  async function callTool(name, rawArgs = {}) {
    const args = cleanArgs(rawArgs);
    try {
      const handler = handlers[name];
      const definition = toolsByName.get(name);
      const allowed = new Set(Object.keys(definition?.inputSchema?.properties || {}));
      const unsupported = Object.keys(args).filter((key) => !allowed.has(key));
      if (handler && unsupported.length) {
        return context.directResult(name, failure(
          "INVALID_ARGUMENT",
          `${name} received unsupported argument(s): ${unsupported.join(", ")}`,
          { retryAllowed: true, retryMode: "different_arguments" },
        ));
      }
      const payload = handler
        ? await handler(args)
        : failure("UNKNOWN_TOOL", `Unknown tool: ${name}`);
      return context.directResult(name, payload);
    } catch (error) {
      return context.directResult(name, errorFromException(error));
    }
  }

  return {
    executionMode: context.runtimeOwner,
    runtimeOwner: context.runtimeOwner,
    workspaceRoot: context.workspaceRoot,
    configPath: context.configPath,
    stateRoot: context.stateRoot,
    limits: context.limits,
    tools,
    callTool,
    resolveProject: (selector) => context.resolveCallProject(selector),
    recoverTransactions: () => recoverRuntimeTransactions(context.stateRoot, context.runtimeOwner),
    probeSafety: () => ({ semanticGuard: probeMutationSemanticGuard() }),
  };
}

async function serveRuntime(runtime, serverName) {
  let version = "unknown";
  try {
    version = String(require("../package.json").version || "unknown");
  } catch {
    // package metadata is optional in embedded test runtimes
  }
  const server = new Server(
    { name: serverName || "lmstudio-unreal-agent-mcp", version },
    { capabilities: { tools: {} } },
  );
  server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: runtime.tools }));
  server.setRequestHandler(CallToolRequestSchema, async (request) => runtime.callTool(request.params.name, request.params.arguments || {}));
  const transport = new StdioServerTransport();
  const priorClose = transport.onclose;
  transport.onclose = () => {
    try {
      runtime.close?.("connection_closed");
    } catch {
      // best effort lifecycle cleanup
    }
    if (typeof priorClose === "function") priorClose();
  };
  await server.connect(transport);
  return server;
}

async function main() {
  const runtime = createDirectRuntime();
  try {
    const recovery = await runtime.recoverTransactions();
    if (recovery.recoveryRequired.length) {
      console.error(`[unreal-agent-${runtime.runtimeOwner}] transaction recovery requires attention: ${JSON.stringify(recovery.recoveryRequired)}`);
    }
  } catch (error) {
    console.error(`[unreal-agent-${runtime.runtimeOwner}] transaction recovery scan failed: ${error.message || error}`);
  }
  const safety = runtime.probeSafety();
  if (!safety.semanticGuard.ok) {
    console.warn(`[unreal-agent-direct] advisory semantic guard unavailable: ${safety.semanticGuard.reason}; hard mutation gates remain active`);
  }
  await serveRuntime(runtime, "lmstudio-unreal-agent-mcp");
}

module.exports = {
  createDirectRuntime,
  envFlag,
  main,
  serveRuntime,
  statSignature,
  toolDefinitions,
};

if (require.main === module) {
  main().catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
}
