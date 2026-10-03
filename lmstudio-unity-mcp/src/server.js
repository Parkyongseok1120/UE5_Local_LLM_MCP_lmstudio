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
const { tools: legacyTools } = require("./catalog");
const { workspaceToolDefinitions, createWorkspaceCapabilities, fileObservation } = require("../../shared-tool-core/workspace");
const { canonicalAbsolutePathIdentity } = require("../../lmstudio-unreal-agent-mcp/src/filesystem-path-identity");
const { Symbols } = require("./symbols");
const { VersionControl } = require("./version-control");
const PROJECT_FILE_TOOLS = new Set([
  "read_file", "list_directory", "search_files", "patch_file", "create_file",
  "structured_data_read", "structured_data_patch",
  "unity_git",
]);
const READ_OBSERVATION_ERRORS = new Set([
  "ENOENT", "EACCES", "EPERM", "EIO", "NOT_FOUND", "NOT_A_FILE", "FILE_TOO_LARGE",
  "BINARY_FILE", "READ_CHANGED_DURING_CALL", "unsupported_encoding", "response_budget_exceeded",
]);
// Execution status survives optional response-body reduction. A delivery limit
// cannot turn a completed Editor/file operation into a rejected operation.
function deliverResult(payload, budget, scope) {
  try { return bounded(payload, budget); }
  catch (error) {
    if (error.code !== "response_budget_exceeded") throw error;
    const compact = {
      ...scope,
      status: typeof payload.status === "string" && payload.status.length < 64 ? payload.status : "outcome_unknown",
      ...(payload.journalPersistence?.status === "unavailable" ? { journalPersistence: { status: "unavailable" } } : {}),
      ...(payload.metadataPersistence?.status === "unavailable" ? { metadataPersistence: { status: "unavailable" } } : {}),
      delivery: { status: "truncated", reason: "response_budget_exceeded", bodyOmitted: true,
        ...(payload.receipt ? { receiptOmitted: true } : {}) },
    };
    // Preserve small evidence/receipt fields when they fit; never invent them.
    for (const key of ["operationId", "snapshotId", "recordingId", "indexId", "jobId", "manifestId", "indexVersion", "id", "errorCode", "journalPersistence", "metadataPersistence",
      "origin", "editorSessionId", "domainGeneration", "observedAt", "freshness",
      "operation", "path", "hash", "previousHash", "receipt", "saved", "compilation", "import"]) {
      if (payload[key] === undefined) continue;
      const candidate = { ...compact, [key]: payload[key] };
      if (Buffer.byteLength(JSON.stringify(candidate)) <= budget) compact[key] = payload[key];
    }
    if (compact.receipt) delete compact.delivery.receiptOmitted;
    return bounded(compact, budget);
  }
}
function createRuntime(env = process.env, injectedBridge) {
  const policy = projectPolicy(env.UNITY_PROJECT_ROOT);
  const workspaceEnabled = env.WORKSPACE_CAPABILITIES !== "0";
  const tools = [...legacyTools.filter(t => !workspaceEnabled || t.name !== "unity_git"),
    ...(workspaceEnabled ? workspaceToolDefinitions() : [])].map(t => ({ ...t, inputSchema: { ...t.inputSchema,
      properties: { ...t.inputSchema.properties, project: { type: "string", minLength: 1, description: "Optional exact bound Unity root; a different root is rejected." } } } }));
  const workspace = createWorkspaceCapabilities({ resolveBinding: () => ({ root: policy.root, engine: "unity", projectIdentity: policy.projectIdentity }) });
  const files = new Files(policy, { allowWrite: env.ALLOW_WRITE === "1" });
  const data = dataTools(files, jsonc, Ajv);
  const bridge = injectedBridge || new BridgeClient(policy);
  const symbols = new Symbols(policy, bridge, env);
  let closed = false;
  const inFlight = new Set();
  const versionControl = new VersionControl(policy);
  const ajv = new Ajv({ strict: false });
  const validators = new Map([...legacyTools, ...tools].map(t => [t.name, ajv.compile(t.inputSchema)]));
  async function call(name, args = {}, requestContext = {}) {
    const controller = new AbortController();
    const signal = controller.signal;
    const cancel = () => controller.abort(requestContext.signal?.reason);
    if (closed || requestContext.signal?.aborted) cancel();
    requestContext.signal?.addEventListener("abort", cancel, { once: true });
    inFlight.add(controller);
    try {
      if (signal.aborted) fail("request_cancelled", "Request ended before dispatch");
      if (args.project !== undefined && canonicalAbsolutePathIdentity(args.project) !== canonicalAbsolutePathIdentity(policy.root))
        fail("project_scope_mismatch", "Requested project does not match the server-bound Unity root");
      const validate = validators.get(name);
      if (!validate || !validate(args)) fail("invalid_arguments", validate ? ajv.errorsText(validate.errors) : "Unknown tool");
      if (name === "unity_approval" && args.action === "request") {
        const nested = validators.get(args.method);
        if (!nested || !nested(args.arguments) || args.arguments?.approvalId) fail("invalid_arguments", "Approval must contain valid final operation arguments, without approvalId");
      }
      const { project: _binding, ...operationArgs } = args;
      args = operationArgs;
      const responseScope = { canonicalProjectRoot: policy.root, projectIdentity: policy.projectIdentity,
        ...(args.operationId ? { operationId: args.operationId } : {}) };
      // Reserve the outcome envelope before any operation is dispatched.
      bounded({ ...responseScope, status: "outcome_unknown",
        delivery: { status: "truncated", reason: "response_budget_exceeded", bodyOmitted: true } }, (args.byteBudget ?? 65536) - 128);
      let result;
      if (workspaceEnabled && (name === "workspace_status" || name.startsWith("git_"))) result = await workspace(name, args);
      else if (name === "unity_symbols") result = await symbols.call(args, { signal });
      else if (name === "unity_git") result = versionControl.call(args);
      else if (name === "read_file") return await files.read(args, value => fileObservation({
        ...value, canonicalProjectRoot: policy.root, projectIdentity: policy.projectIdentity,
      }));
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
        result = await bridge.call(name, args, { signal });
        // The adapter has verified the server/Bridge binding. Include its
        // canonical scope with runtime observations as well as file results.
        result = { ...result, canonicalProjectRoot: policy.root, projectIdentity: policy.projectIdentity };
      }
      if (PROJECT_FILE_TOOLS.has(name) && result && !result.errorCode) {
        result = { ...result, canonicalProjectRoot: policy.root, projectIdentity: policy.projectIdentity };
      }
      return deliverResult(fileObservation(result), args.byteBudget ?? 65536, responseScope);
    } catch (error) {
      if (name === "unity_status" && !["invalid_arguments", "project_scope_mismatch", "request_cancelled"].includes(error.code)) return { status: "observed", connection: "disconnected", projectIdentity: policy.projectIdentity, canonicalProjectRoot: policy.root,
        fileTools: "available", unityObjects: "unavailable", lastKnownSession: bridge.lastSession, reason: error.code || "bridge_unavailable" };
      return { status: error.status || "not_applied", errorCode: error.code || "tool_error", message: String(error.message).slice(0, 1000),
        ...(name === "read_file" && READ_OBSERVATION_ERRORS.has(error.code) && typeof args.path === "string"
          ? { path: args.path, observationState: "unavailable" } : {}),
        ...(error.code === "receipt_conflict" && typeof args.path === "string"
          ? { path: args.path, observationState: "conflict_observed" } : {}),
        ...(error.code !== "project_scope_mismatch" ? { canonicalProjectRoot: policy.root, projectIdentity: policy.projectIdentity,
          bindingSource: "server_configuration" } : {}), ...(args.operationId ? { operationId: args.operationId } : {}) };
    } finally {
      inFlight.delete(controller);
      requestContext.signal?.removeEventListener("abort", cancel);
    }
  }
  function close(reason = "connection_closed") {
    closed = true;
    for (const controller of inFlight) controller.abort(reason);
    symbols.close(reason);
  }
  return { tools, call, policy, close };
}
async function main() {
  const runtime = createRuntime();
  const server = new Server({ name: "lmstudio-unity-mcp", version: require("../package.json").version }, { capabilities: { tools: {} } });
  server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: runtime.tools }));
  server.setRequestHandler(CallToolRequestSchema, async (request, extra) => {
    const payload = await runtime.call(request.params.name, request.params.arguments ?? {}, { signal: extra.signal });
    return { content: [{ type: "text", text: JSON.stringify(payload) }], isError: Boolean(payload.errorCode), structuredContent: payload };
  });
  server.onclose = () => runtime.close();
  await server.connect(new StdioServerTransport());
}
module.exports = { createRuntime };
if (require.main === module) main().catch(error => { console.error(error.message); process.exitCode = 1; });
