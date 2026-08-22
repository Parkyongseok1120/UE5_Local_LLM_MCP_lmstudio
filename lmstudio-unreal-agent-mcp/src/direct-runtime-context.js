"use strict";

const path = require("node:path");

const {
  getActiveProject,
  resolveExactProjectNameSelection,
  resolveProjectSelection,
} = require("./unreal-detect.js");
const { resolveReadPath, isWithin } = require("./read-path-resolver.js");
const { DirectRepeatCache } = require("./direct-repeat-cache.js");
const { toMcpResult } = require("./direct-response.js");
const { clamp, relativeSlash } = require("./direct-runtime-shared.js");
const { resolveMutationLimits } = require("./direct-mutation-limits.js");
const { assertDirectMutationScope } = require("./direct-mutation-scope.js");
const { resolveAgentStateRoot } = require("./runtime-state-root.js");
const { normalizeRuntimeOwner } = require("./direct-transaction-store.js");
const { FileSnapshotRegistry } = require("./file-snapshot-registry.js");

function createDirectRuntimeContext(options = {}) {
  const env = options.env || process.env;
  const runtimeOwner = normalizeRuntimeOwner(options.runtimeOwner || "direct");
  const workspaceRoot = path.resolve(options.workspaceRoot || env.WORKSPACE_ROOT || process.cwd());
  const configPath = path.resolve(options.configPath || env.AGENT_MCP_CONFIG || path.join(__dirname, "..", "config", "agent-mcp.json"));
  const stateRoot = resolveAgentStateRoot({ env, stateRoot: options.stateRoot });
  const repeatCache = options.repeatCache || new DirectRepeatCache({
    maxEntries: clamp(env.DIRECT_REPEAT_CACHE_ENTRIES, 256, 8, 4096),
    ttlMs: clamp(env.DIRECT_REPEAT_CACHE_TTL_MS, 10 * 60 * 1000, 1000, 60 * 60 * 1000),
  });
  const fileSnapshots = options.fileSnapshots || new FileSnapshotRegistry({
    maxEntries: clamp(env.DIRECT_FILE_SNAPSHOT_ENTRIES, 512, 8, 4096),
    ttlMs: clamp(env.DIRECT_FILE_SNAPSHOT_TTL_MS, 30 * 60 * 1000, 1000, 24 * 60 * 60 * 1000),
    hostPlatform: options.hostPlatform || process.platform,
  });
  const limits = {
    ...resolveMutationLimits(env),
    maxReadBytes: clamp(env.MAX_READ_BYTES, 64 * 1024, 4096, 2 * 1024 * 1024),
    maxSourceBytes: clamp(env.DIRECT_MAX_SOURCE_FILE_BYTES, 8 * 1024 * 1024, 64 * 1024, 64 * 1024 * 1024),
    commandTimeoutMs: clamp(env.COMMAND_TIMEOUT_MS, 10 * 60 * 1000, 1000, 60 * 60 * 1000),
    maxResponseChars: clamp(env.DIRECT_MAX_RESPONSE_CHARS, 256_000, 16_000, 1_000_000),
    maxCommandOutputBytes: clamp(env.DIRECT_MAX_COMMAND_OUTPUT_BYTES, 1_000_000, 16_384, 8_000_000),
  };

  const getActive = () => (options.getActiveProject || getActiveProject)(configPath);

  async function resolveCallProject(projectSelector = "") {
    const selector = String(projectSelector || "").trim();
    if (!selector) return getActive();
    const resolutionOptions = {
      env,
      ...(options.hostPlatform ? { hostPlatform: options.hostPlatform } : {}),
    };
    const selection = selector.toLowerCase().endsWith(".uproject")
      ? await resolveProjectSelection(workspaceRoot, configPath, {
        ...resolutionOptions,
        project: selector,
      })
      : await resolveExactProjectNameSelection(workspaceRoot, configPath, {
        ...resolutionOptions,
        name: selector,
      });
    if (!selection.selected?.projectPath) {
      throw new Error(selection.error || `No unambiguous Unreal project matched: ${selector}`);
    }
    return path.resolve(selection.selected.projectPath);
  }

  async function resolveToolPath(input, projectSelector = "") {
    return resolveReadPath(input, { workspaceRoot, activeProject: await resolveCallProject(projectSelector) });
  }

  async function mutationResolution(input, projectSelector = "") {
    const activeProject = await resolveCallProject(projectSelector);
    if (!activeProject) throw new Error("An active .uproject or per-call project is required for project mutation.");
    const resolution = await resolveReadPath(input, { workspaceRoot, activeProject });
    const projectDir = path.dirname(path.resolve(activeProject));
    if (!isWithin(path.resolve(resolution.absolutePath), projectDir)) {
      throw new Error("Mutation path is outside the selected Unreal project.");
    }
    const relativePath = relativeSlash(projectDir, resolution.absolutePath);
    const realRelativePath = relativeSlash(resolution.allowedRealRoot, resolution.realPath);
    const mutationScope = assertDirectMutationScope({
      absolutePath: resolution.absolutePath,
      activeProject,
      realPath: resolution.realPath,
      relativePath,
      realRelativePath,
    });
    return {
      ...resolution,
      activeProject,
      projectDir,
      relativePath,
      mutationScope,
    };
  }

  function directResult(toolName, payload) {
    return toMcpResult(payload, { currentTool: toolName, maxChars: limits.maxResponseChars });
  }

  function payloadFits(payload, reserveChars = 0) {
    return JSON.stringify(payload, null, 2).length + reserveChars <= limits.maxResponseChars;
  }

  function projectScopedSuggestionArgs(args, suggestionArgs) {
    const selector = String(args?.project || "").trim();
    return selector ? { ...suggestionArgs, project: selector } : suggestionArgs;
  }

  function fitUtf8Prefix(buffer, makePayload) {
    let low = 0;
    let high = buffer.length;
    let best = null;
    while (low <= high) {
      const middle = Math.floor((low + high) / 2);
      const candidate = makePayload(buffer.subarray(0, middle), middle);
      if (payloadFits(candidate, 256)) {
        best = { payload: candidate, bytes: middle };
        low = middle + 1;
      } else {
        high = middle - 1;
      }
    }
    if (!best) return null;
    for (let bytes = best.bytes; bytes >= Math.max(0, best.bytes - 3); bytes -= 1) {
      try {
        const content = new TextDecoder("utf-8", { fatal: true }).decode(buffer.subarray(0, bytes));
        const payload = makePayload(Buffer.from(content, "utf8"), bytes, content);
        if (payloadFits(payload, 256)) return { payload, bytes };
      } catch {
        // Back up to the prior complete UTF-8 unit.
      }
    }
    return best;
  }

  function dedupe(toolName, args, state, payload) {
    const duplicate = repeatCache.lookup(toolName, args, state);
    if (duplicate) return duplicate;
    const receipt = repeatCache.remember(toolName, args, state, payload);
    return payload?.ok === false ? payload : { ...payload, ...receipt };
  }

  return {
    configPath,
    dedupe,
    directResult,
    env,
    fileSnapshots,
    fitUtf8Prefix,
    getActive,
    limits,
    mutationResolution,
    options,
    payloadFits,
    projectScopedSuggestionArgs,
    repeatCache,
    resolveCallProject,
    resolveToolPath,
    runtimeOwner,
    stateRoot,
    workspaceRoot,
  };
}

module.exports = { createDirectRuntimeContext };
