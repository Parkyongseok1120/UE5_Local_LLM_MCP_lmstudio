#!/usr/bin/env node
"use strict";
const Ajv = require("ajv");
const jsonc = require("jsonc-parser");
const { Server } = require("@modelcontextprotocol/sdk/server/index.js");
const { StdioServerTransport } = require("@modelcontextprotocol/sdk/server/stdio.js");
const { ListToolsRequestSchema, CallToolRequestSchema } = require("@modelcontextprotocol/sdk/types.js");
const { DEFAULT_BYTE_BUDGET, Files, bounded, fail } = require("../../shared-tool-core/files");
const { dataTools } = require("../../shared-tool-core/data");
const { projectPolicy } = require("./project");
const { BridgeClient } = require("./bridge-client");
const { tools: legacyTools } = require("./catalog");
const { workspaceToolDefinitions, createWorkspaceCapabilities, fileObservation } = require("../../shared-tool-core/workspace");
const { canonicalAbsolutePathIdentity } = require("../../shared-tool-core/filesystem-path-identity");
const { DirectRepeatCache } = require("../../shared-tool-core/repeat-cache");
const { staticValidate } = require("./static-validate");
const { Symbols } = require("./symbols");
const { VersionControl } = require("./version-control");
const PROJECT_FILE_TOOLS = new Set([
  "read_file", "list_directory", "search_files", "patch_file", "create_file",
  "structured_data_read", "structured_data_patch",
  "unity_git",
]);
// Each permission has a server half (install flag) and an Editor half (menu).
// A refusal names the half that is off and how to turn it on.
const PERMISSION_STEPS = {
  edit_disabled: "Server write permission is off (ALLOW_WRITE=0). Reinstall with --unity-allow-write, then also enable Unity menu Tools/Evidence First/Allow Edit for this Editor session.",
  execute_disabled: "Server execute permission is off (ALLOW_COMMANDS=0). Reinstall with --unity-allow-commands, then also enable Unity menu Tools/Evidence First/Allow Execute for this Editor session.",
  permission_denied: "The Unity Editor permission is off: enable the Tools/Evidence First menu item the message names. The server permission is already on.",
};
// Tools that answer without a running Editor.
const OFFLINE_TOOLS = new Set(["read_file", "list_directory", "search_files", "structured_data_read", "patch_file", "unity_symbols",
  "create_file", "structured_data_patch", "unity_git", "unity_static_validate", "workspace_status", "git_status", "git_log",
  "git_changed_files", "git_diff_file", "git_read_file", "unity_status"]);
const READ_OBSERVATION_ERRORS = new Set([
  "ENOENT", "not_found", "EACCES", "EPERM", "EIO", "NOT_FOUND", "NOT_A_FILE", "FILE_TOO_LARGE",
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
      "operation", "path", "hash", "previousHash", "receipt", "saved", "compilation", "import",
      // Paging facts let the caller continue instead of re-requesting the page.
      "total", "truncated", "hasMore", "nextCursor", "nextStartLine"]) {
      if (payload[key] === undefined) continue;
      const candidate = { ...compact, [key]: payload[key] };
      if (Buffer.byteLength(JSON.stringify(candidate)) <= budget) compact[key] = payload[key];
    }
    if (compact.receipt) delete compact.delivery.receiptOmitted;
    return bounded(compact, budget);
  }
}
// The one call that continues a partial file observation: the same request
// with the returned cursor or next line. Never a different scope.
function continuation(name, args, result) {
  if (!result || result.errorCode) return null;
  const { project: _binding, cursor: _cursor, ...same } = args;
  if ((name === "list_directory" || name === "search_files" || name === "unity_static_validate") && typeof result.nextCursor === "string" && result.nextCursor)
    return { tool: name, args: { ...same, cursor: result.nextCursor } };
  if (name === "read_file" && result.hasMore === true && Number.isInteger(result.nextStartLine))
    return { tool: name, args: { ...same, startLine: result.nextStartLine } };
  return null;
}
// What a failure tells the caller beyond its code: the roots the policy
// allows, an observed path for the same tool, or one different call.
function failureGuidance(name, args, error) {
  const extra = {};
  if (error.details && typeof error.details === "object") Object.assign(extra, error.details);
  if (typeof error.retryPath === "string") {
    const { project: _binding, ...same } = args;
    extra.suggestion = { tool: name, args: { ...same, path: error.retryPath } };
  } else if (error.suggestion && typeof error.suggestion === "object") extra.suggestion = error.suggestion;
  if (typeof error.nextStep === "string") extra.nextStep = error.nextStep;
  else if (PERMISSION_STEPS[error.code]) extra.nextStep = PERMISSION_STEPS[error.code];
  return extra;
}
function createRuntime(env = process.env, injectedBridge) {
  const policy = projectPolicy(env.UNITY_PROJECT_ROOT);
  const workspaceEnabled = env.WORKSPACE_CAPABILITIES !== "0";
  const tools = [...legacyTools.filter(t => !workspaceEnabled || t.name !== "unity_git"),
    ...(workspaceEnabled ? workspaceToolDefinitions() : [])].map(t => ({ ...t, inputSchema: { ...t.inputSchema,
      properties: { ...t.inputSchema.properties, project: { type: "string", minLength: 1, description: "Optional exact bound Unity root; a different root is rejected." } } } }));
  const workspace = createWorkspaceCapabilities({ resolveBinding: () => ({ root: policy.root, engine: "unity", projectIdentity: policy.projectIdentity }) });
  const files = new Files(policy, { allowWrite: env.ALLOW_WRITE === "1" });
  // Server flags are one half of each permission; the Editor menu is the other.
  const serverPermissions = { write: env.ALLOW_WRITE === "1", commands: env.ALLOW_COMMANDS === "1" };
  const data = dataTools(files, jsonc, Ajv);
  const bridge = injectedBridge || new BridgeClient(policy);
  const symbols = new Symbols(policy, bridge, env);
  let closed = false;
  const inFlight = new Set();
  const failures = new DirectRepeatCache();
  const versionControl = new VersionControl(policy);
  const ajv = new Ajv({ strict: false });
  const validators = new Map([...legacyTools, ...tools].map(t => [t.name, ajv.compile(t.inputSchema)]));
  async function call(name, args = {}, requestContext = {}) {
    const defaultedBounds = [];
    if (name === "unity_tests" && args.action === "run") {
      args = { ...args };
      for (const [key, value] of Object.entries({ maxDurationMs: 60000, maxTests: 32 }))
        if (args[key] === undefined) { args[key] = value; defaultedBounds.push(key); }
    }
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
      // File pages are fitted to their final envelope: scope fields and the
      // continuation call are measured with the page, not added after it.
      const scoped = value => {
        const observed = fileObservation({ ...value, canonicalProjectRoot: policy.root, projectIdentity: policy.projectIdentity });
        const next = continuation(name, args, observed);
        return next ? { ...observed, suggestion: next } : observed;
      };
      // Reserve the outcome envelope before any operation is dispatched.
      bounded({ ...responseScope, status: "outcome_unknown",
        delivery: { status: "truncated", reason: "response_budget_exceeded", bodyOmitted: true } }, (args.byteBudget ?? DEFAULT_BYTE_BUDGET) - 128);
      let result;
      if (workspaceEnabled && (name === "workspace_status" || name.startsWith("git_"))) result = await workspace(name, args, { signal });
      else if (name === "unity_symbols" && args.action === "source") {
        // The declaration's lines, read through the same file policy as read_file.
        const located = symbols.locate(args);
        const readArgs = { path: located.uri, startLine: Math.max(1, located.line - 2), limit: args.limit ?? 60, byteBudget: args.byteBudget };
        return await files.read(readArgs, value => {
          const next = continuation("read_file", readArgs, value);
          return fileObservation({ ...value, ...responseScope, symbol: located.symbol, declarationLine: located.line,
            indexVersion: located.indexVersion, indexSource: located.indexSource,
            sourceMatchesIndex: located.sourceHash ? value.hash === located.sourceHash : "unknown",
            ...(next ? { suggestion: next } : {}) });
        });
      }
      else if (name === "unity_symbols") result = await symbols.call(args, { signal });
      else if (name === "unity_static_validate") result = await staticValidate(policy, files, args, scoped, { signal });
      else if (name === "unity_git") result = await versionControl.call(args, { signal });
      else if (name === "read_file") return await files.read(args, scoped);
      else if (name === "list_directory") result = files.list(args, scoped);
      else if (name === "search_files") result = await files.search(args, scoped);
      else if (name === "patch_file") result = await files.patch(args, { signal });
      else if (name === "create_file") result = await files.mutate(args, () => args.content, true, { signal });
      else if (name === "structured_data_read") result = await data.read(args);
      else if (name === "structured_data_patch") {
        if (args.format === "json" && !args.changes?.length || args.format === "csv" && !args.cells?.length) fail("invalid_arguments", "Supply changes (JSON) or cells (CSV)");
        result = await data.patch(args, { signal });
      } else {
        const edit = name === "unity_object_patch" || name === "unity_asset" || name === "unity_scene" && args.action !== "list" || name === "unity_prefab" && !["read", "contents", "overrides"].includes(args.action) || name === "unity_approval" && args.action === "request";
        const execute = name === "unity_editor" || name === "unity_debug_action" || name === "unity_tests" && !["status", "results", "list"].includes(args.action) || name === "unity_operation" && args.action === "cancel";
        if (edit && env.ALLOW_WRITE !== "1") fail("edit_disabled", "Server write permission is off (ALLOW_WRITE=0)");
        if (execute && env.ALLOW_COMMANDS !== "1") fail("execute_disabled", "Server execute permission is off (ALLOW_COMMANDS=0)");
        result = await bridge.call(name, args, { signal });
        if (result.errorCode) result = { ...result, ...failureGuidance(name, args, { code: result.errorCode }) };
        // The adapter has verified the server/Bridge binding. Include its
        // canonical scope with runtime observations as well as file results.
        result = { ...result, canonicalProjectRoot: policy.root, projectIdentity: policy.projectIdentity,
          ...(name === "unity_status" ? { serverPermissions } : {}) };
      }
      if (PROJECT_FILE_TOOLS.has(name) && result && !result.errorCode) {
        result = { ...result, canonicalProjectRoot: policy.root, projectIdentity: policy.projectIdentity };
      }
      if (defaultedBounds.length) result = { ...result, defaultedBounds };
      return deliverResult(fileObservation(result), args.byteBudget ?? DEFAULT_BYTE_BUDGET, responseScope);
    } catch (error) {
      if (name === "unity_status" && !["invalid_arguments", "project_scope_mismatch", "request_cancelled"].includes(error.code)) return { status: "observed",
        connection: error.code === "editor_reloading" ? "reloading" : "disconnected", projectIdentity: policy.projectIdentity, canonicalProjectRoot: policy.root,
        fileTools: "available", unityObjects: "unavailable", lastKnownSession: bridge.lastSession,
        reason: error.code || "bridge_unavailable", reasonCode: error.code || "bridge_unavailable",
        ...(error.nextStep ? { nextStep: error.nextStep } : {}),
        ...(Number.isInteger(error.retryAfterSeconds) ? { retryAfterSeconds: error.retryAfterSeconds } : {}),
        offlineTools: tools.map(tool => tool.name).filter(tool => OFFLINE_TOOLS.has(tool)), serverPermissions };
      // Observe the failure again before deduplicating it. A fixed file, Editor
      // reconnect or changed permission is never suppressed by stale cache state.
      const failureState = JSON.stringify([error.code, error.message, error.details, error.suggestion, error.retryPath, error.nextStep]);
      const repeat = failures.lookup(name, args, failureState);
      failures.remember(name, args, failureState, { ok: false, errorCode: error.code, message: error.message });
      return { ...(repeat || {}), status: repeat ? "no_new_information" : error.status || "not_applied", errorCode: error.code || "tool_error", message: String(error.path ? `File access failed: ${args.path || "requested path"} (${error.code || "io_error"})` : error.message).slice(0, 1000),
        ...failureGuidance(name, args, error),
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
