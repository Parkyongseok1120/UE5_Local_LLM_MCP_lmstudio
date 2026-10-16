"use strict";

const { workspaceToolDefinitions } = require("../../shared-tool-core/workspace");
const { HARD_MUTATION_LIMITS } = require("./direct-mutation-limits");

const DIRECT_TOOL_EFFECT = Symbol("directToolEffect");
const readByteBudget = () => ({ type: "integer", minimum: 2048, maximum: 65536,
  $comment: "direct-json-text-utf8" });
const VALID_TOOL_EFFECTS = new Set(["read", "ephemeral", "mutation", "long_running"]);
const TOOL_EFFECTS = Object.freeze({
  get_workspace_info: "read",
  list_unreal_projects: "read",
  get_active_project: "read",
  set_active_project: "mutation",
  detect_unreal_project: "read",
  list_directory: "read",
  search_files: "read",
  read_file: "read",
  read_file_range: "read",
  read_symbol: "read",
  read_unreal_logs: "read",
  write_file: "mutation",
  replace_in_file: "mutation",
  apply_edit_bundle: "mutation",
  propose_file_deletions: "ephemeral",
  delete_file: "mutation",
  static_validate_project: "long_running",
  build_unreal_project: "long_running",
  run_unreal_automation_tests: "long_running",
  run_command: "long_running",
});

function classifyToolDefinition(definition, effect) {
  if (!definition || typeof definition !== "object" || !String(definition.name || "").trim()) {
    throw new Error("Direct tool classification requires a named definition");
  }
  if (!VALID_TOOL_EFFECTS.has(effect)) {
    throw new Error(`Direct tool ${definition.name} has no valid effect classification`);
  }
  Object.defineProperty(definition, DIRECT_TOOL_EFFECT, {
    value: effect,
    enumerable: false,
    writable: false,
    configurable: false,
  });
  return definition;
}

function toolEffect(definition) {
  const effect = definition?.[DIRECT_TOOL_EFFECT];
  if (!VALID_TOOL_EFFECTS.has(effect)) {
    throw new Error(`Direct tool ${definition?.name || "<unknown>"} has no valid effect classification`);
  }
  return effect;
}

function requiresStrictSession(definition) {
  return new Set(["mutation", "long_running"]).has(toolEffect(definition));
}

function schema(properties = {}, required = []) {
  return {
    type: "object",
    properties,
    required,
    additionalProperties: false,
  };
}

// Descriptions are a fixed per-round prompt cost: name each tool's contract
// once, tersely; argument names and JSON Schema bounds carry the rest.
function projectArgument() {
  return { type: "string" };
}

function repeatable(properties = {}) {
  return {
    ...properties,
    repeatReceipt: { type: "string" },
  };
}

function fileVersionArguments() {
  return {
    expectedHash: {
      type: "string",
      pattern: "^[A-Fa-f0-9]{64}$",
    },
    fileVersionReceipt: {
      type: "string",
      minLength: 1,
    },
  };
}

function explicitFileVersionEvidence() {
  return [
    { required: ["fileVersionReceipt"] },
    { required: ["expectedHash"] },
  ];
}

function focusedPatchArguments() {
  return {
    path: { type: "string", minLength: 1 },
    oldText: {
      type: "string",
      minLength: 1,
      maxLength: HARD_MUTATION_LIMITS.maxPatchOldTextChars,
      description: "Exact current text of one region.",
    },
    newText: {
      type: "string",
      maxLength: HARD_MUTATION_LIMITS.maxPatchNewTextChars,
    },
    expectedOccurrences: {
      type: "integer",
      const: 1,
    },
    ...fileVersionArguments(),
  };
}

function toolDefinitions() {
  const L = HARD_MUTATION_LIMITS;
  const definitions = [
    {
      name: "get_workspace_info",
      description: "Mode, ALLOW_* flags, workspace, active project, browse roots. Early authority check when flags are unknown; advisory only.",
      inputSchema: schema(repeatable()),
    },
    {
      name: "list_unreal_projects",
      description: "Discover Unreal projects under configured search roots.",
      inputSchema: schema({ maxDepth: { type: "number", description: "Default 4, max 8." } }),
    },
    {
      name: "get_active_project",
      description: "Selected .uproject and derived roots.",
      inputSchema: schema(),
    },
    {
      name: "set_active_project",
      description: "Select an exact .uproject path or project name, or clear.",
      inputSchema: schema({
        projectPath: { type: "string" },
        hint: { type: "string" },
        clear: { type: "boolean" },
      }),
    },
    {
      name: "detect_unreal_project",
      description: "Resolve project, target, engine, platform and configuration.",
      inputSchema: schema({
        hint: { type: "string" },
        project: { type: "string" },
        engineRoot: { type: "string" },
        target: { type: "string" },
        platform: { type: "string" },
        configuration: { type: "string" },
        resolveBuildDefaults: { type: "boolean" },
      }),
    },
    {
      name: "list_directory",
      description: "List a workspace:// or project:// directory; page with offset=nextOffset.",
      inputSchema: schema(repeatable({
        path: { type: "string" },
        project: projectArgument(),
        maxEntries: { type: "number", description: "Default 200, max 2000." },
        offset: { type: "number" },
        byteBudget: readByteBudget(),
      }), ["path"]),
    },
    {
      name: "search_files",
      description: "Search contents or basenames under a workspace:// or project:// path (default workspace://). Results carry a reusable uri; with project:// URIs pass the response's activeProject as project. Partial on deadline.",
      inputSchema: schema(repeatable({
        query: { type: "string" },
        path: { type: "string" },
        project: projectArgument(),
        regex: { type: "boolean" },
        matchFileNames: { type: "boolean" },
        caseSensitive: { type: "boolean" },
        maxResults: { type: "number", description: "Default 100, max 1000." },
        maxFiles: { type: "number", description: "Default 5000, max 50000." },
        byteBudget: readByteBudget(),
      }), ["query"]),
    },
    {
      name: "read_file",
      description: "Read a UTF-8 byte window (maxBytes default 64 KiB, max 2 MiB; continue at nextOffsetBytes) with sha256 and fileVersionReceipt.",
      inputSchema: schema(repeatable({
        path: { type: "string" },
        project: projectArgument(),
        maxBytes: { type: "number" },
        offsetBytes: { type: "number" },
        byteBudget: readByteBudget(),
      }), ["path"]),
    },
    {
      name: "read_file_range",
      description: "Read whole lines startLine..endLine; continue at nextStartLine. Hash/receipt prove version, not coverage.",
      inputSchema: schema(repeatable({
        path: { type: "string" },
        project: projectArgument(),
        startLine: { type: "number" },
        endLine: { type: "number" },
        byteBudget: readByteBudget(),
      }), ["path", "startLine", "endLine"]),
    },
    {
      name: "read_symbol",
      description: "Lexical C/C++ body candidate, not a verified definition. Continue via read_file_range at nextStartLine.",
      inputSchema: schema(repeatable({
        path: { type: "string" },
        project: projectArgument(),
        symbol: { type: "string" },
        contextLines: { type: "number" },
        byteBudget: readByteBudget(),
      }), ["path", "symbol"]),
    },
    {
      name: "read_unreal_logs",
      description: "Read Unreal/MCP logs: tail, first_error, or range at cursorByte. fileName = exact basename (e.g. of fullLogPath).",
      inputSchema: schema(repeatable({
        mode: { type: "string", enum: ["tail", "first_error", "range"] },
        project: projectArgument(),
        fileName: { type: "string" },
        cursorByte: { type: "number" },
        maxBytes: { type: "number" },
        maxLines: { type: "number" },
        maxFiles: { type: "number" },
        filter: { type: "string" },
        byteBudget: readByteBudget(),
      })),
    },
    {
      name: "write_file",
      description: `Create one new file (max ${L.maxNewFileChars} chars, ${L.maxNewFileLines} lines) in project Source, Config, plugin Source or descriptor; never overwrites. Requires ALLOW_WRITE=1.`,
      inputSchema: schema({
        path: { type: "string", minLength: 1 },
        project: projectArgument(),
        content: { type: "string", maxLength: L.maxNewFileChars },
        createDirs: { type: "boolean" },
      }, ["path", "content"]),
    },
    {
      name: "replace_in_file",
      description: `Atomically replace one exact region in project Source/Config/plugin files. Requires ALLOW_WRITE=1 and an explicit fileVersionReceipt or expectedHash (never auto-selected). Max ${L.maxPatchChars} combined chars, ${L.maxPatchLines} newText lines. Emit the call immediately; edit another region next round with the returned receipt.`,
      inputSchema: {
        ...schema({
          ...focusedPatchArguments(),
          project: projectArgument(),
        }, ["path", "oldText", "newText", "expectedOccurrences"]),
        anyOf: explicitFileVersionEvidence(),
      },
    },
    {
      name: "apply_edit_bundle",
      description: `Atomically patch one region in each of at most ${L.maxBundleOperations} distinct existing files (unique paths, max ${L.maxBundleChangedLines} changed lines; replace_in_file limits and receipts per patch). Same-file regions: replace_in_file. Never creates files. Requires ALLOW_WRITE=1.`,
      inputSchema: schema({
        project: projectArgument(),
        patches: {
          type: "array",
          minItems: 1,
          maxItems: L.maxBundleOperations,
          items: {
            ...schema(
              focusedPatchArguments(),
              ["path", "oldText", "newText", "expectedOccurrences"],
            ),
            anyOf: explicitFileVersionEvidence(),
          },
        },
      }, ["patches"]),
    },
    {
      name: "propose_file_deletions",
      description: "Issue short-lived per-file deletion approval tokens and receipts. Deletes nothing.",
      inputSchema: schema({
        completedEditsSummary: { type: "string", minLength: 1 },
        project: projectArgument(),
        files: {
          type: "array",
          minItems: 1,
          maxItems: 32,
          items: {
            type: "object",
            properties: {
              path: { type: "string", minLength: 1 },
              reason: { type: "string", minLength: 1 },
              ifNotDeleted: { type: "string", minLength: 1 },
              ifDeleted: { type: "string", minLength: 1 },
            },
            required: ["path", "reason", "ifNotDeleted", "ifDeleted"],
            additionalProperties: false,
          },
        },
      }, ["completedEditsSummary", "files"]),
    },
    {
      name: "delete_file",
      description: "Move an approved Source file to recoverable trash with the proposal's token and receipt. Requires userApproved=true, ALLOW_WRITE=1, ALLOW_SOURCE_DELETE=1.",
      inputSchema: {
        ...schema({
          path: { type: "string", minLength: 1 },
          approvalToken: { type: "string", minLength: 1 },
          userApproved: { type: "boolean" },
          project: projectArgument(),
          ...fileVersionArguments(),
          completedEditsSummary: { type: "string", minLength: 1 },
          reason: { type: "string", minLength: 1 },
          ifNotDeleted: { type: "string", minLength: 1 },
          ifDeleted: { type: "string", minLength: 1 },
        }, ["path", "approvalToken", "userApproved", "completedEditsSummary", "reason", "ifNotDeleted", "ifDeleted"]),
        anyOf: explicitFileVersionEvidence(),
      },
    },
    {
      name: "static_validate_project",
      description: "Advisory static compile-readiness checks; never authorize or block a build.",
      inputSchema: schema({
        projectRoot: { type: "string" },
        project: projectArgument(),
        scopeTargets: { type: "array", items: { type: "string" } },
        timeoutMs: { type: "number" },
      }),
    },
    {
      name: "build_unreal_project",
      description: "Immediately resolve and run UBT/UHT (omit target for the default; Editor = editor target). Returns summary, diagnostics, fullLogPath. Requires ALLOW_UNREAL_BUILD=1.",
      inputSchema: schema({
        hint: { type: "string" }, project: { type: "string" }, engineRoot: { type: "string" },
        target: { type: "string" }, platform: { type: "string" }, configuration: { type: "string" },
        timeoutMs: { type: "number" }, verboseOutput: { type: "boolean" }, allowEngineFallback: { type: "boolean" },
      }),
    },
    {
      name: "run_unreal_automation_tests",
      description: "Run declared Unreal Automation tests. Requires ALLOW_UNREAL_BUILD=1.",
      inputSchema: schema({
        testFilter: { type: "string" }, project: { type: "string" }, hint: { type: "string" }, engineRoot: { type: "string" },
        timeoutMs: { type: "number" }, verboseOutput: { type: "boolean" }, scopeTargets: { type: "array", items: { type: "string" } },
      }, ["testFilter"]),
    },
    {
      name: "run_command",
      description: "Run an allowlisted command in a contained dir; returns output tails, fullLogPath. Requires ALLOW_COMMANDS=1.",
      inputSchema: schema({ command: { type: "string" }, cwd: { type: "string" }, project: projectArgument(), timeoutMs: { type: "number" } }, ["command"]),
    },
  ];
  const definitionNames = new Set(definitions.map((definition) => definition.name));
  const staleEffects = Object.keys(TOOL_EFFECTS).filter((name) => !definitionNames.has(name));
  if (staleEffects.length) {
    throw new Error(`Direct tool effects have no definition: ${staleEffects.join(", ")}`);
  }
  return [...definitions, ...workspaceToolDefinitions().map(definition => classifyToolDefinition(definition, "read"))].map((definition) => (
    TOOL_EFFECTS[definition.name] ? classifyToolDefinition(definition, TOOL_EFFECTS[definition.name]) : definition
  ));
}

module.exports = {
  classifyToolDefinition,
  projectArgument,
  requiresStrictSession,
  schema,
  toolDefinitions,
  toolEffect,
};
