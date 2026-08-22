#!/usr/bin/env node
"use strict";

/**
 * Explicit Strict Autonomous Workflow Mode.
 *
 * Strict owns only a small conversation-scoped lifecycle around the same safe
 * capability runtime used by Direct mode. Reads/searches stay task-free. Only
 * state-changing/long-running calls require a live Strict session, and an
 * orphan never blocks another conversation or project.
 */

const { createDirectRuntime, serveRuntime } = require("./direct-server.js");
const { createStrictLifecycle } = require("./strict-lifecycle.js");
const { success, failure, toMcpResult } = require("./direct-response.js");
const { requiresStrictSession } = require("./direct-tool-catalog.js");
const { createStrictProjectBinding } = require("./strict-project-binding.js");

function objectSchema(properties, required = []) {
  return { type: "object", properties, required, additionalProperties: false };
}

function lifecycleToolDefinitions() {
  const owner = {
    strictSessionId: { type: "string" },
    conversationId: { type: "string", description: "Stable ID for only the current LM Studio conversation." },
  };
  return [
    {
      name: "strict_begin",
      description: "Explicitly begin one isolated autonomous session. This never locks Direct reads or another conversation.",
      inputSchema: objectSchema({
        conversationId: owner.conversationId,
        objective: { type: "string" },
        project: { type: "string", description: "Optional exact .uproject path or exact discovered project name." },
        ttlSeconds: { type: "number", description: "60 seconds to 7 days; default 6 hours." },
      }, ["conversationId", "objective"]),
    },
    { name: "strict_status", description: "Read this conversation's Strict session state.", inputSchema: objectSchema(owner, ["strictSessionId", "conversationId"]) },
    { name: "strict_heartbeat", description: "Keep an active Strict session running and renew its TTL.", inputSchema: objectSchema(owner, ["strictSessionId", "conversationId"]) },
    {
      name: "strict_wait",
      description: "Mark this session as waiting for the user or an external process without blocking other work.",
      inputSchema: objectSchema({ ...owner, status: { type: "string", enum: ["waiting_user", "waiting_external"] }, reason: { type: "string" } }, ["strictSessionId", "conversationId", "status"]),
    },
    { name: "strict_complete", description: "Mark this session completed. No final-response acknowledgement gate is created.", inputSchema: objectSchema({ ...owner, summary: { type: "string" } }, ["strictSessionId", "conversationId"]) },
    { name: "strict_fail", description: "Mark this session failed without blocking a later conversation.", inputSchema: objectSchema({ ...owner, summary: { type: "string" } }, ["strictSessionId", "conversationId"]) },
    { name: "strict_cancel", description: "Cancel only this conversation's session.", inputSchema: objectSchema({ ...owner, reason: { type: "string" } }, ["strictSessionId", "conversationId"]) },
    {
      name: "strict_resume",
      description: "Resume an orphaned session only after the user explicitly approves it.",
      inputSchema: objectSchema({ ...owner, userApproved: { type: "boolean" } }, ["strictSessionId", "conversationId", "userApproved"]),
    },
  ];
}

function strictCapabilityCatalog(directTools) {
  const gatedToolNames = new Set();
  const tools = directTools.map((source) => {
    const tool = JSON.parse(JSON.stringify(source));
    if (!requiresStrictSession(source)) return tool;
    gatedToolNames.add(tool.name);
    tool.inputSchema.properties.strictSessionId = { type: "string" };
    tool.inputSchema.properties.conversationId = { type: "string" };
    tool.inputSchema.required = [...new Set([...(tool.inputSchema.required || []), "strictSessionId", "conversationId"] )];
    tool.description = `${tool.description} Strict mode requires a live session owned by this conversation.`;
    return tool;
  });
  return { gatedToolNames, tools };
}

function strictCapabilityDefinitions(directTools) {
  return strictCapabilityCatalog(directTools).tools;
}

function lifecycleFailure(toolName, error) {
  const busy = error?.code === "STRICT_SESSION_BUSY";
  return toMcpResult(failure(busy ? "STRICT_SESSION_BUSY" : "STRICT_SESSION_INVALID", String(error?.message || error), {
    retryAllowed: true,
    retryMode: busy ? "after_state_change" : "different_arguments",
  }), { currentTool: toolName });
}

function createStrictRuntime(options = {}) {
  const direct = options.directRuntime || createDirectRuntime({ ...options, runtimeOwner: "strict" });
  if (direct.runtimeOwner !== "strict") {
    throw new Error("Strict runtime requires a capability runtime owned by the strict transaction namespace");
  }
  const lifecycle = options.lifecycle || createStrictLifecycle({
    ...options,
    stateRoot: direct.stateRoot,
  });
  const capabilities = strictCapabilityCatalog(direct.tools);
  const projectBinding = createStrictProjectBinding(direct);
  const tools = [...lifecycleToolDefinitions(), ...capabilities.tools];

  async function callTool(name, rawArgs = {}) {
    const args = rawArgs && typeof rawArgs === "object" && !Array.isArray(rawArgs) ? { ...rawArgs } : {};
    try {
      let session;
      if (name === "strict_begin") {
        session = lifecycle.begin(await projectBinding.canonicalizeBeginArgs(args));
      }
      else if (name === "strict_status") session = lifecycle.status(args);
      else if (name === "strict_heartbeat") session = lifecycle.touch(args);
      else if (name === "strict_wait") session = lifecycle.wait(args);
      else if (name === "strict_complete") session = lifecycle.complete(args);
      else if (name === "strict_fail") session = lifecycle.fail(args);
      else if (name === "strict_cancel") session = lifecycle.cancel(args);
      else if (name === "strict_resume") session = lifecycle.resume(args);
      if (session) return toMcpResult(success({ executionMode: "strict", strictSession: session }), { currentTool: name });

      let result;
      if (capabilities.gatedToolNames.has(name)) {
        const strictSessionId = args.strictSessionId;
        const conversationId = args.conversationId;
        delete args.strictSessionId;
        delete args.conversationId;
        const operation = await lifecycle.runOperation(
          strictSessionId,
          conversationId,
          name,
          async (ownedSession) => {
            await projectBinding.bindToolArguments(name, args, ownedSession.project);
            return direct.callTool(name, args);
          },
        );
        result = operation.result;
        session = operation.session;
      } else {
        result = await direct.callTool(name, args);
      }
      const payload = {
        ...result.structuredContent,
        executionMode: "strict",
      };
      if (session) payload.strictSession = { id: session.id, conversationId: session.conversationId, status: session.status };
      return toMcpResult(payload, { currentTool: name });
    } catch (error) {
      return lifecycleFailure(name, error);
    }
  }

  return {
    executionMode: "strict",
    runtimeOwner: "strict",
    stateRoot: direct.stateRoot,
    workspaceRoot: direct.workspaceRoot,
    tools,
    callTool,
    recoverTransactions: () => direct.recoverTransactions(),
    close: (reason) => lifecycle.orphanOwned(reason),
    lifecycle,
  };
}

async function main() {
  const runtime = createStrictRuntime();
  try {
    const recovery = await runtime.recoverTransactions();
    if (recovery.recoveryRequired.length) {
      console.error(`[unreal-agent-strict] transaction recovery requires attention: ${JSON.stringify(recovery.recoveryRequired)}`);
    }
  } catch (error) {
    console.error(`[unreal-agent-strict] transaction recovery scan failed: ${error.message || error}`);
  }
  await serveRuntime(runtime, "lmstudio-unreal-agent-mcp-strict");
}

module.exports = {
  createStrictRuntime,
  lifecycleToolDefinitions,
  strictCapabilityCatalog,
  strictCapabilityDefinitions,
  main,
};

if (require.main === module) {
  main().catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
}
