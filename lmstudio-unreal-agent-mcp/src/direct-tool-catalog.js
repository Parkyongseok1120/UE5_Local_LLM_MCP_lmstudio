"use strict";

const { HARD_MUTATION_LIMITS } = require("./direct-mutation-limits");

const DIRECT_TOOL_EFFECT = Symbol("directToolEffect");
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

function projectArgument() {
  return {
    type: "string",
    description: "Optional exact .uproject path or exact discovered project name. Overrides the active project for this call only.",
  };
}

function repeatable(properties = {}) {
  return {
    ...properties,
    repeatReceipt: {
      type: "string",
      description: "Optional opaque receipt from this same observation. Echo it only to request a concise unchanged-result response.",
    },
  };
}

function fileVersionArguments() {
  return {
    expectedHash: {
      type: "string",
      pattern: "^[A-Fa-f0-9]{64}$",
      description: "Explicit raw 64-character SHA-256 from a preceding read. Use this or fileVersionReceipt for every existing-file mutation.",
    },
    fileVersionReceipt: {
      type: "string",
      minLength: 1,
      description: "Preferred explicit opaque receipt from a read or immediately preceding mutation. Use this or expectedHash for every existing-file mutation.",
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
      description: "Exact current text for one focused region; do not include later edits.",
    },
    newText: {
      type: "string",
      maxLength: HARD_MUTATION_LIMITS.maxPatchNewTextChars,
      description: `Replacement for only this focused region, server-enforced to at most ${HARD_MUTATION_LIMITS.maxPatchLines} lines. Empty text is allowed for a focused deletion.`,
    },
    expectedOccurrences: {
      type: "integer",
      const: 1,
      description: "Focused edits must match exactly once.",
    },
    ...fileVersionArguments(),
  };
}

function toolDefinitions() {
  const definitions = [
    {
      name: "get_workspace_info",
      description: "Return Direct Model Mode status, safety flags, workspace, active Unreal project, and project browse roots.",
      inputSchema: schema(repeatable()),
    },
    {
      name: "list_unreal_projects",
      description: "Discover Unreal projects across configured search roots.",
      inputSchema: schema({ maxDepth: { type: "number", description: "Discovery depth, default 4 and maximum 8." } }),
    },
    {
      name: "get_active_project",
      description: "Return the selected .uproject and derived project roots.",
      inputSchema: schema(),
    },
    {
      name: "set_active_project",
      description: "Select one exact .uproject path or exact project name, or clear the selection.",
      inputSchema: schema({
        projectPath: { type: "string" },
        hint: { type: "string", description: "Exact project name or .uproject stem." },
        clear: { type: "boolean" },
      }),
    },
    {
      name: "detect_unreal_project",
      description: "Resolve project, target, engine association, platform, and configuration for any supported installed Unreal version.",
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
      description: "List immediate children of a workspace:// or project:// directory.",
      inputSchema: schema(repeatable({
        path: { type: "string" },
        project: projectArgument(),
        maxEntries: { type: "number", description: "Default 200, maximum 2000." },
      }), ["path"]),
    },
    {
      name: "search_files",
      description: "Search file contents or basenames below a workspace:// or project:// path. Results are bounded but never route-filtered.",
      inputSchema: schema(repeatable({
        query: { type: "string" },
        path: { type: "string", description: "Default workspace://." },
        project: projectArgument(),
        regex: { type: "boolean" },
        matchFileNames: { type: "boolean" },
        caseSensitive: { type: "boolean" },
        maxResults: { type: "number", description: "Default 100, maximum 1000." },
        maxFiles: { type: "number", description: "Default 5000, maximum 50000." },
      }), ["query"]),
    },
    {
      name: "read_file",
      description: "Read a bounded UTF-8 file and register its whole-file version. Returns a SHA-256 and a shorter opaque fileVersionReceipt for conflict-safe edits.",
      inputSchema: schema(repeatable({
        path: { type: "string" },
        project: projectArgument(),
        maxBytes: { type: "number", description: "Default 64 KiB, maximum 2 MiB." },
        offsetBytes: { type: "number", description: "Optional byte cursor, default 0." },
      }), ["path"]),
    },
    {
      name: "read_file_range",
      description: "Read an inclusive line range, register the whole-file version, and return its SHA-256 plus opaque fileVersionReceipt for conflict-safe edits.",
      inputSchema: schema(repeatable({
        path: { type: "string" },
        project: projectArgument(),
        startLine: { type: "number" },
        endLine: { type: "number" },
      }), ["path", "startLine", "endLine"]),
    },
    {
      name: "read_symbol",
      description: "Read one C/C++ function body with a small context window and return whole-file version evidence.",
      inputSchema: schema(repeatable({
        path: { type: "string" },
        project: projectArgument(),
        symbol: { type: "string" },
        contextLines: { type: "number" },
      }), ["path", "symbol"]),
    },
    {
      name: "read_unreal_logs",
      description: "Read bounded Unreal or MCP build logs by tail, first error, or byte range.",
      inputSchema: schema(repeatable({
        mode: { type: "string", enum: ["tail", "first_error", "range"] },
        project: projectArgument(),
        fileName: { type: "string", description: "Optional exact basename; directory components are rejected." },
        cursorByte: { type: "number" },
        maxBytes: { type: "number" },
        maxLines: { type: "number" },
        maxFiles: { type: "number" },
        filter: { type: "string" },
      })),
    },
    {
      name: "write_file",
      description: `Create one brand-new standalone file in the selected project's Source, Config, plugin Source, or exact plugin-descriptor scope (maximum ${HARD_MUTATION_LIMITS.maxNewFileChars} characters and ${HARD_MUTATION_LIMITS.maxNewFileLines} lines). apply_edit_bundle never creates files. Content and protected generated/state directories are not writable. Requires ALLOW_WRITE=1; existing files are never overwritten. Semantic denylist findings are returned as non-blocking advisories.`,
      inputSchema: schema({
        path: { type: "string", minLength: 1 },
        project: projectArgument(),
        content: { type: "string", maxLength: HARD_MUTATION_LIMITS.maxNewFileChars },
        createDirs: { type: "boolean" },
      }, ["path", "content"]),
    },
    {
      name: "replace_in_file",
      description: `Atomically replace one focused exact-text region in selected-project Source, Config, plugin Source, the exact plugin descriptor, or the active project descriptor. Emit this tool call immediately instead of serializing future patches in reasoning or prose. Every call must pass an explicit fileVersionReceipt from a read or immediately preceding mutation, or a valid raw expectedHash; no same-session evidence is selected automatically. For another region in the same file, wait for this tool result and use its new fileVersionReceipt in the next prediction round. Limits: oldText ${HARD_MUTATION_LIMITS.maxPatchOldTextChars} characters, newText ${HARD_MUTATION_LIMITS.maxPatchNewTextChars} characters, ${HARD_MUTATION_LIMITS.maxPatchChars} combined characters, and a server-enforced ${HARD_MUTATION_LIMITS.maxPatchLines} newText lines. Content and protected generated/state directories are not writable; semantic denylist findings are advisory only.`,
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
      description: `Atomically patch one focused region in each of at most ${HARD_MUTATION_LIMITS.maxBundleOperations} distinct existing files. The schema enforces one or two patches; the server additionally enforces unique normalized paths, at most ${HARD_MUTATION_LIMITS.maxPatchLines} newText lines per patch, and at most ${HARD_MUTATION_LIMITS.maxBundleChangedLines} aggregate changed lines. Every patch must pass its explicit fileVersionReceipt or valid raw expectedHash. Do not bundle non-contiguous regions from the same file; use replace_in_file once, wait for its result, then continue with the returned receipt in the next prediction round. Each patch uses the same ${HARD_MUTATION_LIMITS.maxPatchOldTextChars}-character oldText, ${HARD_MUTATION_LIMITS.maxPatchNewTextChars}-character newText, and ${HARD_MUTATION_LIMITS.maxPatchChars}-character combined bounds as replace_in_file. New files are created only with standalone write_file. Content and protected generated/state directories are not writable; semantic denylist findings are non-blocking advisories.`,
      inputSchema: schema({
        project: projectArgument(),
        patches: {
          type: "array",
          minItems: 1,
          maxItems: HARD_MUTATION_LIMITS.maxBundleOperations,
          description: `One or two focused existing-file patches. Distinct normalized paths and the ${HARD_MUTATION_LIMITS.maxBundleChangedLines}-line aggregate cap are server-enforced because JSON Schema cannot express them portably.`,
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
      description: "Create short-lived per-file approval tokens and fileVersionReceipts. This tool does not delete anything.",
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
      description: "Move an approved source-like file under project Source or plugin Source to recoverable project trash. Use the proposal's fileVersionReceipt; raw expectedHash remains compatible. Requires explicit userApproved=true, ALLOW_WRITE=1, and ALLOW_SOURCE_DELETE=1.",
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
      description: "Run advisory static compile-readiness checks. Findings never authorize or block a later build.",
      inputSchema: schema({
        projectRoot: { type: "string" },
        project: projectArgument(),
        scopeTargets: { type: "array", items: { type: "string" } },
        timeoutMs: { type: "number" },
      }),
    },
    {
      name: "build_unreal_project",
      description: "Immediately resolve and run UBT/UHT for the selected project/version. Omit target for the preferred project target, or use the portable Editor alias for the selected project's editor target. Requires ALLOW_UNREAL_BUILD=1.",
      inputSchema: schema({
        hint: { type: "string" }, project: { type: "string" }, engineRoot: { type: "string" },
        target: { type: "string" }, platform: { type: "string" }, configuration: { type: "string" },
        timeoutMs: { type: "number" }, verboseOutput: { type: "boolean" }, allowEngineFallback: { type: "boolean" },
      }),
    },
    {
      name: "run_unreal_automation_tests",
      description: "Run declared Unreal Automation tests for a selected project/version. Requires ALLOW_UNREAL_BUILD=1.",
      inputSchema: schema({
        testFilter: { type: "string" }, project: { type: "string" }, hint: { type: "string" }, engineRoot: { type: "string" },
        timeoutMs: { type: "number" }, verboseOutput: { type: "boolean" }, scopeTargets: { type: "array", items: { type: "string" } },
      }, ["testFilter"]),
    },
    {
      name: "run_command",
      description: "Run a narrow allowlisted command in a contained workspace/project directory. Requires ALLOW_COMMANDS=1.",
      inputSchema: schema({ command: { type: "string" }, cwd: { type: "string" }, project: projectArgument(), timeoutMs: { type: "number" } }, ["command"]),
    },
  ];
  const definitionNames = new Set(definitions.map((definition) => definition.name));
  const staleEffects = Object.keys(TOOL_EFFECTS).filter((name) => !definitionNames.has(name));
  if (staleEffects.length) {
    throw new Error(`Direct tool effects have no definition: ${staleEffects.join(", ")}`);
  }
  return definitions.map((definition) => (
    classifyToolDefinition(definition, TOOL_EFFECTS[definition.name])
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
