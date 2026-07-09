#!/usr/bin/env node
"use strict";

/**
 * LM Studio Unreal Agent MCP
 *
 * Safe-ish local tools for using a local LLM as a coding agent.
 *
 * Security model:
 * - File access is restricted to WORKSPACE_ROOT.
 * - Writes are disabled unless ALLOW_WRITE=1.
 * - Command execution is disabled unless ALLOW_COMMANDS=1.
 * - Commands are allowlisted.
 * - Unreal build command is separately gated by ALLOW_UNREAL_BUILD=1.
 */

const fs = require("fs");
const fsp = fs.promises;
const path = require("path");
const cp = require("child_process");
const os = require("os");
const crypto = require("crypto");

const {
  Server
} = require("@modelcontextprotocol/sdk/server/index.js");

const {
  StdioServerTransport
} = require("@modelcontextprotocol/sdk/server/stdio.js");

const {
  CallToolRequestSchema,
  ListToolsRequestSchema
} = require("@modelcontextprotocol/sdk/types.js");

const {
  discoverProjects,
  resolveBuildPlan,
  resolveProjectSelection,
  findEngineInstalls,
  defaultPlatform,
  getActiveProject,
  setActiveProject,
  listUnrealProjects,
  buildProjectBrowsePaths
} = require("./unreal-detect.js");
const {
  scanSymbolImpact,
  validateRefactorPlan
} = require("./refactor-tools.js");
const {
  validateAfterWrite,
  formatValidationResult,
  runStaticValidation,
  resolveValidateOnWrite
} = require("./validate-write.js");
const {
  validateWriteTarget,
  isDeleteAllowedPath,
  isPatchOnlyExistingFile: isPatchOnlyFile
} = require("./write-guards.js");

function numberEnv(name, fallback, min = 0) {
  const value = Number(process.env[name]);
  return Number.isFinite(value) ? Math.max(min, value) : fallback;
}

const WORKSPACE_ROOT = path.resolve(process.env.WORKSPACE_ROOT || process.cwd());
const CONFIG_PATH = path.resolve(
  process.env.AGENT_MCP_CONFIG
  || path.join(__dirname, "..", "config", "agent-mcp.json")
);
const ALLOW_WRITE = process.env.ALLOW_WRITE === "1" || process.env.ALLOW_WRITE === "true";
const ALLOW_COMMANDS = process.env.ALLOW_COMMANDS === "1" || process.env.ALLOW_COMMANDS === "true";
const ALLOW_UNREAL_BUILD = process.env.ALLOW_UNREAL_BUILD === "1" || process.env.ALLOW_UNREAL_BUILD === "true";
const ALLOW_EXISTING_SOURCE_WRITE = ["1", "true", "yes", "on"].includes(
  String(process.env.ALLOW_EXISTING_SOURCE_WRITE || "").trim().toLowerCase()
);
const MAX_READ_BYTES = Number(process.env.MAX_READ_BYTES || 64 * 1024);
const FILE_CACHE_MAX_ENTRIES = numberEnv("FILE_CACHE_MAX_ENTRIES", 20, 0);
const FILE_CACHE_MAX_BYTES = numberEnv("FILE_CACHE_MAX_BYTES", MAX_READ_BYTES, 0);
const WORKSPACE_INFO_CACHE_TTL_MS = numberEnv("WORKSPACE_INFO_CACHE_TTL_MS", 60 * 1000, 0);
const CODE_DETAIL_READ_BYTES = {
  compact: 16 * 1024,
  medium: 32 * 1024,
  large: MAX_READ_BYTES,
  full: MAX_READ_BYTES
};
const CODE_DETAIL_LINE_CAP = {
  compact: 150,
  medium: 400,
  large: 1200,
  full: 2000
};

function resolveCodeDetail(raw) {
  const key = String(raw || "compact").trim().toLowerCase();
  return Object.prototype.hasOwnProperty.call(CODE_DETAIL_READ_BYTES, key) ? key : "compact";
}
const MAX_OUTPUT_BYTES = Number(process.env.MAX_OUTPUT_BYTES || 1024 * 256);
const COMMAND_TIMEOUT_MS = Number(process.env.COMMAND_TIMEOUT_MS || 1000 * 60 * 10);
const SEARCH_MAX_FILES = Number(process.env.SEARCH_MAX_FILES || 5000);
const ALLOW_SOURCE_DELETE = ["1", "true", "yes", "on"].includes(
  String(process.env.ALLOW_SOURCE_DELETE || "").trim().toLowerCase()
);
const VALIDATE_ON_WRITE = resolveValidateOnWrite();
const MCP_ESSENTIAL_TOOLS = ["1", "true", "yes", "on"].includes(
  String(process.env.MCP_ESSENTIAL_TOOLS || "").trim().toLowerCase()
);
const MCP_EXTENDED_TOOLS = ["1", "true", "yes", "on"].includes(
  String(process.env.MCP_EXTENDED_TOOLS || "").trim().toLowerCase()
);
const ESSENTIAL_AGENT_TOOL_NAMES = new Set([
  "get_workspace_info",
  "get_active_project",
  "list_directory",
  "read_file",
  "read_file_range",
  "replace_in_file",
  "write_file",
  "search_files",
  "build_unreal_project",
  "read_unreal_logs"
]);
const EXTENDED_AGENT_TOOL_NAMES = new Set([
  "set_active_project",
  "detect_unreal_project",
  "list_unreal_projects",
  "open_active_project_picker",
  "run_command",
  "refactor_impact_scan",
  "refactor_plan_validate",
  "propose_file_deletions",
  "delete_file",
  "static_validate_project"
]);
const PATCH_ONLY_EXISTING_EXTENSIONS = new Set([".h", ".hpp", ".cpp", ".c", ".cc", ".cxx", ".cs"]);
const fileCache = new Map();
let workspaceInfoCache = null;

const server = new Server(
  {
    name: "lmstudio-unreal-agent-mcp",
    version: "0.3.0"
  },
  {
    capabilities: {
      tools: {}
    }
  }
);

function launchProjectPicker(explorer = false) {
  if (process.platform !== "win32") {
    return {
      ok: false,
      error: "project_picker_windows_only",
      message: "The project picker requires Windows (PowerShell). Use rag.ps1 pick-project manually or set activeProject in the shared config."
    };
  }
  const ragRoot = process.env.UNREAL58_ROOT
    ? path.resolve(process.env.UNREAL58_ROOT)
    : path.join(os.homedir(), ".lmstudio", "Unreal58-RAG");
  const script = path.join(ragRoot, "scripts", "pick_active_project.ps1");
  const args = ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script];
  if (explorer) {
    args.push("-Explorer");
  }
  const child = cp.spawn("powershell.exe", args, {
    detached: true,
    stdio: "ignore",
    windowsHide: false
  });
  child.unref();
  return {
    ok: true,
    message: explorer
      ? "Opened Windows file picker for .uproject on your desktop."
      : "Opened Windows project list picker on your desktop.",
    script
  };
}

function text(content) {
  return { content: [{ type: "text", text: String(content) }] };
}

function fail(message) {
  return text(`ERROR: ${message}`);
}

function normalizeForToken(value) {
  return String(value || "").replace(/\r\n/g, "\n").trim();
}

function requireDeletionText(value, fieldName) {
  const textValue = normalizeForToken(value);
  if (textValue.length < 12) {
    throw new Error(`${fieldName} must be a concrete sentence of at least 12 characters.`);
  }
  return textValue;
}

function deletionApprovalToken({ relPath, completedEditsSummary, reason, ifNotDeleted, ifDeleted }) {
  const payload = JSON.stringify({
    relPath: normalizeForToken(relPath).replace(/\\/g, "/"),
    completedEditsSummary: normalizeForToken(completedEditsSummary),
    reason: normalizeForToken(reason),
    ifNotDeleted: normalizeForToken(ifNotDeleted),
    ifDeleted: normalizeForToken(ifDeleted),
  });
  return crypto.createHash("sha256").update(payload).digest("hex").slice(0, 24);
}

async function buildDeletionProposal(rawFiles, completedEditsSummary, activeProject) {
  const summary = requireDeletionText(completedEditsSummary, "completedEditsSummary");
  if (!Array.isArray(rawFiles) || rawFiles.length === 0) {
    throw new Error("files must contain at least one deletion candidate.");
  }
  if (rawFiles.length > 20) {
    throw new Error("files may contain at most 20 deletion candidates.");
  }

  const seen = new Set();
  const files = [];
  for (const item of rawFiles) {
    const target = normalizeRelPath(String(item && item.path || ""));
    const guard = isDeleteAllowedPath(target, WORKSPACE_ROOT, activeProject);
    if (!guard.ok) {
      throw new Error(guard.message);
    }
    const delStat = await statSafe(target);
    if (!delStat || !delStat.isFile()) {
      throw new Error(`not found or not file: ${item && item.path}`);
    }
    const relPath = path.relative(WORKSPACE_ROOT, target).replace(/\\/g, "/");
    const relKey = relPath.toLowerCase();
    if (seen.has(relKey)) {
      throw new Error(`duplicate deletion candidate: ${relPath}`);
    }
    seen.add(relKey);

    const reason = requireDeletionText(item.reason, `reason for ${relPath}`);
    const ifNotDeleted = requireDeletionText(item.ifNotDeleted, `ifNotDeleted for ${relPath}`);
    const ifDeleted = requireDeletionText(item.ifDeleted, `ifDeleted for ${relPath}`);
    files.push({
      path: relPath,
      fileName: path.basename(target),
      sizeBytes: delStat.size,
      reason,
      ifNotDeleted,
      ifDeleted,
      approvalToken: deletionApprovalToken({
        relPath,
        completedEditsSummary: summary,
        reason,
        ifNotDeleted,
        ifDeleted,
      }),
    });
  }

  return {
    fileCount: files.length,
    completedEditsSummary: summary,
    files,
    deleted: false,
    instruction: "No files were deleted. Explain this plan to the user and wait for explicit approval before calling delete_file with the matching per-file approvalToken.",
  };
}

function normalizeRelPath(p) {
  if (!p || typeof p !== "string") {
    throw new Error("path must be a non-empty string");
  }
  const workspace = path.resolve(WORKSPACE_ROOT);
  const resolved = path.resolve(workspace, p);

  // Primary check: resolved path must start with workspace + separator (or equal workspace).
  // This is more robust than relative().startsWith("..") on Windows with symlinks.
  if (resolved !== workspace && !resolved.startsWith(workspace + path.sep)) {
    throw new Error(`path escapes WORKSPACE_ROOT: ${p}`);
  }

  return resolved;
}

async function exists(p) {
  try {
    await fsp.access(p);
    return true;
  } catch {
    return false;
  }
}

async function statSafe(p) {
  try {
    return await fsp.stat(p);
  } catch {
    return null;
  }
}

function isPatchOnlyExistingFile(p) {
  return isPatchOnlyFile(p);
}

function validationFailed(validation) {
  return Boolean(validation && validation.ok === false);
}

function filterAgentTools(tools) {
  if (MCP_EXTENDED_TOOLS) {
    return tools;
  }
  if (MCP_ESSENTIAL_TOOLS) {
    return tools.filter((tool) => ESSENTIAL_AGENT_TOOL_NAMES.has(tool.name));
  }
  return tools.filter((tool) => !EXTENDED_AGENT_TOOL_NAMES.has(tool.name));
}

function truncateOutput(s, maxBytes = MAX_OUTPUT_BYTES) {
  const buf = Buffer.from(String(s), "utf8");
  if (buf.length <= maxBytes) return String(s);
  return buf.subarray(0, maxBytes).toString("utf8") + `\n\n[TRUNCATED: output exceeded ${maxBytes} bytes]`;
}

function isTextLikely(buffer) {
  if (!buffer || buffer.length === 0) return true;
  const sample = buffer.subarray(0, Math.min(buffer.length, 4096));
  let zeros = 0;
  for (const b of sample) {
    if (b === 0) zeros++;
  }
  return zeros === 0;
}

function allowedCommandBase(commandLine) {
  const trimmed = String(commandLine || "").trim();
  if (!trimmed) return false;

  const lower = trimmed.toLowerCase();

  const denyPatterns = [
    /\bdel\b/i,
    /\berase\b/i,
    /\brmdir\b/i,
    /\brd\b/i,
    /\bformat\b/i,
    /\breg\s+delete\b/i,
    /\bshutdown\b/i,
    /\btaskkill\b/i,
    /\bsetx\b/i,
    /\bmklink\b/i,
    /\btakeown\b/i,
    /\bicacls\b/i,
    /\bpowershell\b.*\b(iwr|irm|invoke-webrequest|invoke-restmethod)\b/i,
    /\bcurl\b.*\|\s*(powershell|cmd|sh|bash)/i
  ];

  if (denyPatterns.some((re) => re.test(lower))) return false;

  const allowPatterns = [
    /^dir(\s|$)/i,
    /^type(\s|$)/i,
    /^where(\s|$)/i,
    /^git\s+(status|diff|log|show|rev-parse|branch)(\s|$)/i,
    /^findstr(\s|$)/i,
    /^cl(\s|$)/i,
    /^msbuild(\s|$)/i,
    /^dotnet\s+build(\s|$)/i,
    /^node\s+--version$/i,
    /^npm\s+--version$/i,
    /^python\s+--version$/i,
    /^py\s+--version$/i
  ];

  return allowPatterns.some((re) => re.test(trimmed));
}

function execCommand(commandLine, cwd = WORKSPACE_ROOT, timeoutMs = COMMAND_TIMEOUT_MS) {
  return new Promise((resolve) => {
    cp.exec(
      commandLine,
      {
        cwd,
        windowsHide: true,
        timeout: timeoutMs,
        maxBuffer: MAX_OUTPUT_BYTES * 4
      },
      (error, stdout, stderr) => {
        resolve({
          ok: !error,
          exitCode: error && typeof error.code === "number" ? error.code : 0,
          signal: error && error.signal ? error.signal : null,
          stdout: truncateOutput(stdout || ""),
          stderr: truncateOutput(stderr || ""),
          error: error ? String(error.message || error) : ""
        });
      }
    );
  });
}

function extractLikelyCompileErrors(stdout, stderr) {
  const combined = `${stdout || ""}\n${stderr || ""}`;
  const lines = combined.split(/\r?\n/);

  const interesting = lines.filter((line) => {
    return (
      /\berror\s+(C\d+|LNK\d+|MSB\d+|UHT\d*)\b/i.test(line) ||
      /\bfatal error\b/i.test(line) ||
      /\bUnrealHeaderTool failed\b/i.test(line) ||
      /\bUBT ERROR\b/i.test(line) ||
      /\bBuild failed\b/i.test(line) ||
      /\berror:/i.test(line)
    );
  });

  return interesting.slice(0, 120).join("\n");
}

function makeJsonSchema(properties, required = []) {
  return {
    type: "object",
    properties,
    required
  };
}

function fileStatSignature(stat) {
  return `${stat.size}:${stat.mtimeMs}`;
}

function rememberCachedFile(target, stat, buffer) {
  if (FILE_CACHE_MAX_ENTRIES <= 0 || FILE_CACHE_MAX_BYTES <= 0 || buffer.length > FILE_CACHE_MAX_BYTES) {
    return;
  }
  const key = path.resolve(target);
  fileCache.delete(key);
  fileCache.set(key, {
    signature: fileStatSignature(stat),
    buffer
  });
  while (fileCache.size > FILE_CACHE_MAX_ENTRIES) {
    const oldest = fileCache.keys().next().value;
    fileCache.delete(oldest);
  }
}

async function readCachedBufferFile(target, stat) {
  if (FILE_CACHE_MAX_ENTRIES <= 0 || FILE_CACHE_MAX_BYTES <= 0 || stat.size > FILE_CACHE_MAX_BYTES) {
    return fsp.readFile(target);
  }
  const key = path.resolve(target);
  const signature = fileStatSignature(stat);
  const cached = fileCache.get(key);
  if (cached && cached.signature === signature && Buffer.isBuffer(cached.buffer)) {
    fileCache.delete(key);
    fileCache.set(key, cached);
    return cached.buffer;
  }
  const buffer = await fsp.readFile(target);
  rememberCachedFile(target, stat, buffer);
  return buffer;
}

async function readLeadingFileBuffer(target, stat, maxBytes) {
  if (stat.size <= FILE_CACHE_MAX_BYTES) {
    const raw = await readCachedBufferFile(target, stat);
    return raw.subarray(0, Math.min(maxBytes, raw.length));
  }
  const fd = await fsp.open(target, "r");
  try {
    const buffer = Buffer.alloc(Math.min(maxBytes, stat.size));
    await fd.read(buffer, 0, buffer.length, 0);
    return buffer;
  } finally {
    await fd.close();
  }
}

async function readCachedTextFile(target, stat) {
  const raw = await readCachedBufferFile(target, stat);
  if (!isTextLikely(raw.subarray(0, Math.min(raw.length, MAX_READ_BYTES)))) {
    const err = new Error("file appears binary");
    err.code = "BINARY_FILE";
    throw err;
  }
  return raw.toString("utf8");
}

function invalidateFileCache(target) {
  fileCache.delete(path.resolve(target));
}

function invalidateWorkspaceInfoCache() {
  workspaceInfoCache = null;
}

async function buildWorkspaceInfo() {
  const activeProject = getActiveProject(CONFIG_PATH);
  const cacheKey = `${WORKSPACE_ROOT}|${CONFIG_PATH}|${activeProject || ""}`;
  const now = Date.now();
  if (
    WORKSPACE_INFO_CACHE_TTL_MS > 0
    && workspaceInfoCache
    && workspaceInfoCache.key === cacheKey
    && now < workspaceInfoCache.expiresAt
  ) {
    return workspaceInfoCache.value;
  }

  const engines = await findEngineInstalls();
  const discovery = await discoverProjects(WORKSPACE_ROOT, CONFIG_PATH);
  let projectContext = null;
  if (activeProject) {
    projectContext = {
      ok: true,
      ...buildProjectBrowsePaths(activeProject, WORKSPACE_ROOT)
    };
  } else {
    projectContext = {
      ok: false,
      error: "activeProject is not set. Call set_active_project first.",
      browseAvailable: false,
      suggestedToolCalls: [{ tool: "set_active_project", args: {} }]
    };
  }
  const payload = {
    workspaceRoot: WORKSPACE_ROOT,
    configPath: CONFIG_PATH,
    activeProject,
    projectContext,
    allowWrite: ALLOW_WRITE,
    allowCommands: ALLOW_COMMANDS,
    allowUnrealBuild: ALLOW_UNREAL_BUILD,
    validateOnWrite: VALIDATE_ON_WRITE,
    allowSourceDelete: ALLOW_SOURCE_DELETE,
    mcpEssentialTools: MCP_ESSENTIAL_TOOLS,
    mcpExtendedTools: MCP_EXTENDED_TOOLS,
    maxReadBytes: MAX_READ_BYTES,
    maxOutputBytes: MAX_OUTPUT_BYTES,
    commandTimeoutMs: COMMAND_TIMEOUT_MS,
    defaultPlatform: defaultPlatform(),
    projectSearchRoots: discovery.roots,
    discoveredProjectCount: discovery.projects.length,
    installedEngines: engines.map((e) => e.engineRoot),
    recentProjects: discovery.projects.slice(0, 8).map((p) => ({
      projectFile: p.projectFile,
      projectPath: p.projectPath,
      preferredTarget: p.preferredTarget,
      allTargets: p.allTargets,
      engineAssociation: p.engineAssociation,
      modifiedAt: p.modifiedAt
    }))
  };
  if (WORKSPACE_INFO_CACHE_TTL_MS > 0) {
    workspaceInfoCache = {
      key: cacheKey,
      expiresAt: now + WORKSPACE_INFO_CACHE_TTL_MS,
      value: payload
    };
  }
  return payload;
}

function allAgentTools() {
  return [
      {
        name: "get_workspace_info",
        description: "Show workspace root, safety flags, configured search roots, and recently discovered Unreal projects.",
        inputSchema: makeJsonSchema({})
      },
      {
        name: "list_unreal_projects",
        description: "List discovered Unreal projects and show which one is currently active.",
        inputSchema: makeJsonSchema({
          maxDepth: { type: "number", description: "Search depth for .uproject discovery. Default 4." }
        })
      },
      {
        name: "get_active_project",
        description: "Return the selected active Unreal project and projectDir. Use this instead of listing WORKSPACE_ROOT when activeProject is already set.",
        inputSchema: makeJsonSchema({})
      },
      {
        name: "set_active_project",
        description: "Choose the active Unreal project by .uproject path or hint. Pass clear=true to unset.",
        inputSchema: makeJsonSchema({
          projectPath: { type: "string", description: "Absolute or workspace-relative .uproject path." },
          hint: { type: "string", description: "Project name fragment, e.g. JRPG or CiciToon." },
          clear: { type: "boolean", description: "If true, clear activeProject and return to free selection." }
        })
      },
      {
        name: "open_active_project_picker",
        description: "Open a Windows GUI to pick the active .uproject. Default shows a selectable project list; set explorer=true for a file dialog.",
        inputSchema: makeJsonSchema({
          explorer: { type: "boolean", description: "If true, open file explorer dialog instead of grid list." }
        })
      },
      {
        name: "refactor_impact_scan",
        description: "Scan the active or hinted Unreal project for references to a class/symbol. Use before R0-R4 refactors.",
        inputSchema: makeJsonSchema({
          symbol: { type: "string", description: "Class or symbol name to search for." },
          hint: { type: "string", description: "Optional project hint if activeProject is unset." },
          maxFiles: { type: "number", description: "Max matching files. Default 40." }
        }, ["symbol"])
      },
      {
        name: "refactor_plan_validate",
        description: "Validate an R0-R4 refactor plan against stage rules (SSOT, no code on R0, file limits).",
        inputSchema: makeJsonSchema({
          stage: { type: "string", description: "R0, R1, R2, R3, or R4." },
          planText: { type: "string", description: "The refactor plan markdown/text to validate." }
        }, ["stage", "planText"])
      },
      {
        name: "detect_unreal_project",
        description: "Detect Unreal .uproject files, editor targets, engine association, and default build settings. Use before build_unreal_project when project/target are unknown.",
        inputSchema: makeJsonSchema({
          hint: { type: "string", description: "Optional project folder or .uproject name fragment, e.g. JRPG or JRPG.uproject." },
          resolveBuildDefaults: { type: "boolean", description: "If true, also resolve engineRoot/target/platform/configuration. Default true." }
        })
      },
      {
        name: "read_unreal_logs",
        description: "Read recent Unreal Editor or build logs from the active project's Saved/Logs folder.",
        inputSchema: makeJsonSchema({
          maxLines: { type: "number", description: "Max tail lines per log file. Default 120." },
          filter: { type: "string", description: "Optional case-insensitive substring filter (Error, Assert, etc.)." }
        })
      },
      {
        name: "list_directory",
        description: "List files/directories under WORKSPACE_ROOT. When activeProject is set, prefer projectContext.sourceBrowsePath from get_active_project instead of broad root browsing.",
        inputSchema: makeJsonSchema({
          path: { type: "string", description: "Relative path inside workspace, e.g. '.', 'Source'." },
          maxEntries: { type: "number", description: "Max entries to show. Default 200." }
        }, ["path"])
      },
      {
        name: "read_file",
        description: "Read a UTF-8 text file inside WORKSPACE_ROOT. Required before any write to that file. Use detailLevel compact/medium/large/full (default compact ~16 KiB) or maxBytes override.",
        inputSchema: makeJsonSchema({
          path: { type: "string", description: "Relative path inside workspace." },
          maxBytes: { type: "number", description: "Optional max bytes. Capped by detailLevel tier." },
          detailLevel: {
            type: "string",
            enum: ["compact", "medium", "large", "full"],
            description: "Read size tier: compact ~16 KiB, medium ~32 KiB, large/full up to 64 KiB."
          }
        }, ["path"])
      },
      {
        name: "read_file_range",
        description: "Read a line range from a UTF-8 text file inside WORKSPACE_ROOT. Prefer this over read_file for large sources. Line span capped by detailLevel.",
        inputSchema: makeJsonSchema({
          path: { type: "string", description: "Relative path inside workspace." },
          startLine: { type: "number", description: "1-based start line (inclusive)." },
          endLine: { type: "number", description: "1-based end line (inclusive)." },
          detailLevel: {
            type: "string",
            enum: ["compact", "medium", "large", "full"],
            description: "Max lines per request: compact 150, medium 400, large 1200, full 2000."
          }
        }, ["path", "startLine", "endLine"])
      },
      {
        name: "write_file",
        description: "Write a UTF-8 file inside WORKSPACE_ROOT. Requires ALLOW_WRITE=1. Use for brand-new files. Existing source files (.h/.cpp/.cs) are blocked by default; use replace_in_file instead.",
        inputSchema: makeJsonSchema({
          path: { type: "string", description: "Relative path inside workspace." },
          content: { type: "string", description: "Full file content to write." },
          createDirs: { type: "boolean", description: "Create parent directories if needed. Default false." }
        }, ["path", "content"])
      },
      {
        name: "replace_in_file",
        description: "Safely replace exact text in a file. Requires ALLOW_WRITE=1. Preferred patch tool for existing files; read the file first and set expectedOccurrences=1 when possible. Line endings (CRLF/LF) are normalized automatically — copy oldText exactly as shown by read_file or read_file_range. If oldText not found, a diagnostic hint and nearest partial match will be shown; do NOT retry with the same oldText — use read_file_range to re-read the exact lines and correct oldText before retrying.",
        inputSchema: makeJsonSchema({
          path: { type: "string", description: "Relative path inside workspace." },
          oldText: { type: "string", description: "Exact text to replace." },
          newText: { type: "string", description: "Replacement text." },
          expectedOccurrences: { type: "number", description: "If set, replacement only proceeds when occurrence count matches." }
        }, ["path", "oldText", "newText"])
      },
      {
        name: "propose_file_deletions",
        description: "Create a structured deletion plan after edits are complete. Deletes nothing. Required before delete_file: list file count, path, file name, reason, impact if kept, and impact if deleted, then wait for explicit user approval.",
        inputSchema: makeJsonSchema({
          completedEditsSummary: { type: "string", description: "What edits/checks are already complete before considering deletion." },
          files: {
            type: "array",
            description: "Deletion candidates. Each item must include path, reason, ifNotDeleted, and ifDeleted.",
            items: {
              type: "object",
              properties: {
                path: { type: "string" },
                reason: { type: "string" },
                ifNotDeleted: { type: "string" },
                ifDeleted: { type: "string" }
              }
            }
          }
        }, ["completedEditsSummary", "files"])
      },
      {
        name: "delete_file",
        description: "Delete one file under the active project's Source/ tree only after propose_file_deletions returned a per-file approvalToken and the user approved that plan. Requires ALLOW_WRITE=1 and ALLOW_SOURCE_DELETE=1. Extended mode only.",
        inputSchema: makeJsonSchema({
          path: { type: "string", description: "Relative path inside workspace." },
          completedEditsSummary: { type: "string", description: "Same completedEditsSummary used in propose_file_deletions." },
          reason: { type: "string", description: "Specific reason this file must be deleted." },
          ifNotDeleted: { type: "string", description: "What concretely happens if this file is not deleted." },
          ifDeleted: { type: "string", description: "What concretely happens if this file is deleted." },
          approvalToken: { type: "string", description: "Per-file approvalToken returned by propose_file_deletions after user approval." },
          expectedContent: { type: "string", description: "Optional exact file content guard before delete." }
        }, ["path", "completedEditsSummary", "reason", "ifNotDeleted", "ifDeleted", "approvalToken"])
      },
      {
        name: "static_validate_project",
        description: "Run static Unreal compile-readiness validation on the active project Source tree. Extended mode. Call before build_unreal_project when validation findings from writes need a full-project check.",
        inputSchema: makeJsonSchema({
          projectRoot: { type: "string", description: "Optional project root or .uproject path. Defaults to active project." }
        })
      },
      {
        name: "search_files",
        description: "Search text files by regex or plain text under WORKSPACE_ROOT. Scope path to the active project whenever possible; do not search repo infrastructure to explain Unreal build failures.",
        inputSchema: makeJsonSchema({
          query: { type: "string", description: "Regex or plain text to search." },
          path: { type: "string", description: "Relative directory/file to search. Default '.'." },
          regex: { type: "boolean", description: "Use query as regex. Default false." },
          maxResults: { type: "number", description: "Max matching lines. Default 100." }
        }, ["query"])
      },
      {
        name: "run_command",
        description: "Run a small allowlisted command in WORKSPACE_ROOT. Requires ALLOW_COMMANDS=1. Dangerous commands are blocked.",
        inputSchema: makeJsonSchema({
          command: { type: "string", description: "Command line. Allowlisted only." },
          cwd: { type: "string", description: "Relative cwd inside workspace. Default '.'." },
          timeoutMs: { type: "number", description: "Timeout. Default 10 minutes." }
        }, ["command"])
      },
      {
        name: "build_unreal_project",
        description: "Run Unreal Build.bat for the active .uproject after C++ or Build.cs edits. Requires ALLOW_UNREAL_BUILD=1. Use likelyErrors/build output as compile evidence; do not patch MCP tooling based on discovery or permission errors.",
        inputSchema: makeJsonSchema({
          hint: { type: "string", description: "Optional project folder or .uproject name fragment for auto-detection." },
          engineRoot: { type: "string", description: "Optional UE engine root. Auto-detected from EngineAssociation when omitted." },
          project: { type: "string", description: "Optional .uproject path relative to workspace or absolute inside workspace." },
          target: { type: "string", description: "Optional target name. Defaults to detected *Editor target." },
          platform: { type: "string", description: "Optional platform. Default Win64 on Windows." },
          configuration: { type: "string", description: "Optional configuration. Default Development." },
          allowAbsoluteProject: { type: "boolean", description: "Allow absolute .uproject path outside workspace. Default false." },
          timeoutMs: { type: "number", description: "Build timeout in ms. Default COMMAND_TIMEOUT_MS." }
        })
      }
  ];
}

server.setRequestHandler(ListToolsRequestSchema, async () => {
  const tools = allAgentTools();
  return {
    tools: filterAgentTools(tools)
  };
});

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  try {
    const name = request.params.name;
    const args = request.params.arguments || {};

    if (name === "get_workspace_info") {
      return text(JSON.stringify(await buildWorkspaceInfo(), null, 2));
    }

    if (name === "list_unreal_projects") {
      const payload = await listUnrealProjects(WORKSPACE_ROOT, CONFIG_PATH, {
        maxDepth: args.maxDepth
      });
      return text(JSON.stringify(payload, null, 2));
    }

    if (name === "get_active_project") {
      const activeProject = getActiveProject(CONFIG_PATH);
      let details = null;
      let projectContext = null;
      if (activeProject) {
        const selection = await resolveProjectSelection(WORKSPACE_ROOT, CONFIG_PATH, {
          hint: activeProject
        });
        details = selection.selected;
        projectContext = {
          ok: true,
          ...buildProjectBrowsePaths(activeProject, WORKSPACE_ROOT)
        };
      } else {
        projectContext = {
          ok: false,
          error: "activeProject is not set. Call set_active_project first.",
          browseAvailable: false,
          suggestedToolCalls: [{ tool: "set_active_project", args: {} }]
        };
      }
      return text(JSON.stringify({ activeProject, details, projectContext }, null, 2));
    }

    if (name === "set_active_project") {
      if (!ALLOW_WRITE) {
        return fail("set_active_project blocked. Set ALLOW_WRITE=1 to update config/agent-mcp.json.");
      }
      const result = await setActiveProject(WORKSPACE_ROOT, CONFIG_PATH, {
        projectPath: args.projectPath,
        hint: args.hint,
        clear: args.clear === true
      });
      invalidateWorkspaceInfoCache();
      return text(JSON.stringify(result, null, 2));
    }

    if (name === "open_active_project_picker") {
      return text(JSON.stringify(launchProjectPicker(args.explorer === true), null, 2));
    }

    if (name === "refactor_impact_scan") {
      const payload = await scanSymbolImpact(WORKSPACE_ROOT, CONFIG_PATH, {
        symbol: args.symbol,
        hint: args.hint,
        maxFiles: args.maxFiles
      });
      return text(JSON.stringify(payload, null, 2));
    }

    if (name === "refactor_plan_validate") {
      const payload = validateRefactorPlan(args.stage, args.planText);
      return text(JSON.stringify(payload, null, 2));
    }

    if (name === "detect_unreal_project") {
      const resolveBuildDefaults = args.resolveBuildDefaults !== false;
      const selection = await resolveProjectSelection(WORKSPACE_ROOT, CONFIG_PATH, {
        hint: args.hint
      });

      const payload = {
        selectionReason: selection.selectionReason,
        searchRoots: selection.roots,
        selected: selection.selected
          ? {
            projectFile: selection.selected.projectFile,
            projectPath: selection.selected.projectPath,
            projectDir: selection.selected.projectDir,
            projectName: selection.selected.projectName,
            preferredTarget: selection.selected.preferredTarget,
            allTargets: selection.selected.allTargets,
            engineAssociation: selection.selected.engineAssociation,
            modifiedAt: selection.selected.modifiedAt
          }
          : null,
        candidates: selection.projects.slice(0, 12).map((p) => ({
          projectFile: p.projectFile,
          projectPath: p.projectPath,
          preferredTarget: p.preferredTarget,
          score: p.score || 0,
          modifiedAt: p.modifiedAt
        })),
        error: selection.error || null,
        suggestions: selection.suggestions || null
      };

      if (resolveBuildDefaults) {
        const plan = await resolveBuildPlan(WORKSPACE_ROOT, CONFIG_PATH, { hint: args.hint });
        payload.buildDefaults = plan.ok ? plan.build : null;
        payload.buildError = plan.ok ? null : plan.error || null;
      }

      return text(JSON.stringify(payload, null, 2));
    }

    if (name === "list_directory") {
      const target = normalizeRelPath(args.path || ".");
      const maxEntries = Math.max(1, Math.min(Number(args.maxEntries || 200), 1000));
      const s = await statSafe(target);
      if (!s) return fail(`not found: ${args.path}`);
      if (!s.isDirectory()) return fail(`not a directory: ${args.path}`);

      const entries = await fsp.readdir(target, { withFileTypes: true });
      const rows = [];
      for (const e of entries.slice(0, maxEntries)) {
        const p = path.join(target, e.name);
        const st = await statSafe(p);
        rows.push({
          name: e.name,
          type: e.isDirectory() ? "dir" : e.isFile() ? "file" : "other",
          size: st ? st.size : null,
          modified: st ? st.mtime.toISOString() : null
        });
      }
      return text(JSON.stringify(rows, null, 2));
    }

    if (name === "read_unreal_logs") {
      const activeProject = getActiveProject(CONFIG_PATH);
      if (!activeProject) {
        return fail("activeProject is not set. Use set_active_project first.");
      }
      const projectDir = path.dirname(path.resolve(activeProject));
      const logsDir = path.join(projectDir, "Saved", "Logs");
      if (!(await exists(logsDir))) {
        return fail(`logs directory not found: ${logsDir}`);
      }
      const maxLines = Math.max(20, Math.min(Number(args.maxLines || 120), 500));
      const filterText = String(args.filter || "").toLowerCase();
      const entries = await fsp.readdir(logsDir, { withFileTypes: true });
      const logFiles = entries
        .filter((entry) => entry.isFile() && entry.name.toLowerCase().endsWith(".log"))
        .map((entry) => path.join(logsDir, entry.name));
      logFiles.sort((a, b) => {
        const sa = fs.statSync(a);
        const sb = fs.statSync(b);
        return sb.mtimeMs - sa.mtimeMs;
      });
      const picked = logFiles.slice(0, 3);
      const chunks = [];
      for (const logPath of picked) {
        const content = await fsp.readFile(logPath, "utf8");
        const lines = content.split(/\r?\n/);
        const tail = lines.slice(-maxLines);
        const filtered = filterText
          ? tail.filter((line) => line.toLowerCase().includes(filterText))
          : tail;
        chunks.push({
          file: path.basename(logPath),
          lineCount: filtered.length,
          lines: filtered
        });
      }
      return text(JSON.stringify({
        projectDir,
        logsDir,
        suggestedRagMode: filterText.includes("error") || filterText.includes("fatal")
          ? "compile_fix"
          : "runtime_debug",
        logs: chunks
      }, null, 2));
    }

    if (name === "read_file") {
      const target = normalizeRelPath(args.path);
      const s = await statSafe(target);
      if (!s) return fail(`not found: ${args.path}`);
      if (!s.isFile()) return fail(`not a file: ${args.path}`);

      const detail = resolveCodeDetail(args.detailLevel);
      const tierCap = CODE_DETAIL_READ_BYTES[detail];
      const maxBytes = Math.max(
        1,
        Math.min(Number(args.maxBytes || tierCap), tierCap, MAX_READ_BYTES)
      );
      const buffer = await readLeadingFileBuffer(target, s, maxBytes);
      if (!isTextLikely(buffer)) return fail(`file appears binary: ${args.path}`);
      const hasCRLF = buffer.includes(Buffer.from("\r\n"));
      // Normalize line endings so model's copy-paste into oldText matches replace_in_file
      let out = buffer.toString("utf8").replace(/\r\n/g, "\n");
      if (hasCRLF) {
          out = `[line-endings: CRLF — replace_in_file normalizes automatically]\n` + out;
        }
      if (s.size > buffer.length) {
        const nextDetail = detail === "compact" ? "medium" : detail === "medium" ? "large" : null;
        out += `\n\n[TRUNCATED: file size ${s.size} bytes, read ${buffer.length} bytes at detailLevel=${detail}.`;
        if (nextDetail) {
          out += ` Escalate once with detailLevel=${nextDetail} or use read_file_range.]`;
        } else {
          out += ` Use read_file_range for partial reads.]`;
      }
      }
      return text(out);
    }

    if (name === "read_file_range") {
      const target = normalizeRelPath(args.path);
      const s = await statSafe(target);
      if (!s) return fail(`not found: ${args.path}`);
      if (!s.isFile()) return fail(`not a file: ${args.path}`);

      const detail = resolveCodeDetail(args.detailLevel);
      const lineCap = CODE_DETAIL_LINE_CAP[detail];
      const startLine = Math.max(1, Number(args.startLine || 1));
      let endLine = Math.max(startLine, Number(args.endLine || startLine));
      if (!Number.isFinite(startLine) || !Number.isFinite(endLine)) {
        return fail("startLine and endLine must be numbers");
      }
      if (endLine - startLine + 1 > lineCap) {
        endLine = startLine + lineCap - 1;
      }
      if (endLine - startLine > 2000) {
        return fail("line range too large (max 2000 lines per request)");
      }

      let content = "";
      try {
        content = await readCachedTextFile(target, s);
      } catch (err) {
        if (err && err.code === "BINARY_FILE") return fail(`file appears binary: ${args.path}`);
        throw err;
      }
      const lines = content.split(/\r?\n/);
      const slice = lines.slice(startLine - 1, endLine);
      const numbered = slice.map((line, idx) => `${startLine + idx}|${line}`).join("\n");
      const rel = path.relative(WORKSPACE_ROOT, target);
      return text(
        `File: ${rel}\nLines: ${startLine}-${Math.min(endLine, lines.length)} of ${lines.length}\n\n${numbered}`
      );
    }

    if (name === "write_file") {
      if (!ALLOW_WRITE) return fail("write_file blocked. Set ALLOW_WRITE=1 to enable.");
      const target = normalizeRelPath(args.path);
      const parent = path.dirname(target);
      const activeProject = getActiveProject(CONFIG_PATH);
      const targetExists = await exists(target);
      const guard = await validateWriteTarget({
        targetAbsPath: target,
        workspaceRoot: WORKSPACE_ROOT,
        activeProjectPath: activeProject,
        createDirs: Boolean(args.createDirs),
        fileExists: async (p) => exists(p)
      });
      if (!guard.ok) {
        return fail(guard.message);
      }
      if (args.createDirs) await fsp.mkdir(parent, { recursive: true });
      if (!(await exists(parent))) return fail(`parent directory not found: ${path.relative(WORKSPACE_ROOT, parent)}`);
      if (targetExists && isPatchOnlyExistingFile(target) && !ALLOW_EXISTING_SOURCE_WRITE) {
        const rel = path.relative(WORKSPACE_ROOT, target);
        return fail(
          `write_file blocked for existing source file: ${rel}. `
          + "Use replace_in_file with exact oldText/newText. "
          + "If oldText does not match, re-read a smaller range and retry. "
          + "Set ALLOW_EXISTING_SOURCE_WRITE=1 only for a deliberate manual override."
        );
      }
      const priorContent = targetExists ? await fsp.readFile(target, "utf8") : null;
      await fsp.writeFile(target, String(args.content || ""), "utf8");
      invalidateFileCache(target);
      const validation = await validateAfterWrite(target, () => getActiveProject(CONFIG_PATH));
      const rel = path.relative(WORKSPACE_ROOT, target);
      if (validationFailed(validation)) {
        if (priorContent === null) {
          await fsp.unlink(target);
        } else {
          await fsp.writeFile(target, priorContent, "utf8");
        }
        invalidateFileCache(target);
        return {
          content: [{
            type: "text",
            text: `ERROR: wrote ${rel} but static validation failed.${formatValidationResult(validation)}`
          }],
          isError: true
        };
      }
      let message = `OK: wrote ${rel}`;
      if (validation && !validation.skipped) {
        message += formatValidationResult(validation);
      }
      return text(message);
    }

    if (name === "replace_in_file") {
      if (!ALLOW_WRITE) return fail("replace_in_file blocked. Set ALLOW_WRITE=1 to enable.");
      const target = normalizeRelPath(args.path);
      const s = await statSafe(target);
      if (!s || !s.isFile()) return fail(`not found or not file: ${args.path}`);
      const oldText = String(args.oldText ?? "");
      const newText = String(args.newText ?? "");
      if (!oldText) return fail("oldText must not be empty");

      const raw = await readCachedBufferFile(target, s);
      const hasCRLF = raw.includes(Buffer.from("\r\n"));
      // Normalize to LF for matching; preserve original line endings in output
      const content = raw.toString("utf8");
      const contentNorm = content.replace(/\r\n/g, "\n");
      const oldTextNorm = oldText.replace(/\r\n/g, "\n");

      const occurrences = contentNorm.split(oldTextNorm).length - 1;
      if (occurrences === 0) {
        // Provide actionable diagnostic: show up to 3 lines around nearest partial match
        const firstLine = oldTextNorm.split("\n")[0].trim().slice(0, 60);
        const nearIdx = firstLine ? contentNorm.indexOf(firstLine) : -1;
        let hint = "";
        if (nearIdx !== -1) {
          const before = contentNorm.lastIndexOf("\n", nearIdx - 1);
          const snippetStart = Math.max(0, before);
          const snippet = contentNorm.slice(snippetStart, nearIdx + 200).split("\n").slice(0, 5).join("\n");
          hint = `\n\nNearest partial match context:\n${snippet}\n\nHint: read the file with read_file_range to get the exact text, then retry with the exact content shown.`;
        } else {
          hint = "\n\nHint: the first line of oldText was not found anywhere in the file. Use read_file or search_files to verify the exact content before retrying.";
        }
        return fail(`oldText not found in ${args.path} (file uses ${hasCRLF ? "CRLF" : "LF"} line endings).${hint}`);
      }
      if (args.expectedOccurrences !== undefined && occurrences !== Number(args.expectedOccurrences)) {
        return fail(`occurrence mismatch: expected ${args.expectedOccurrences}, found ${occurrences}`);
      }

      // Apply replacement on normalized content, then restore original line endings if needed
      const priorContent = content;
      const updatedNorm = contentNorm.split(oldTextNorm).join(newText.replace(/\r\n/g, "\n"));
      const updated = hasCRLF ? updatedNorm.replace(/\n/g, "\r\n") : updatedNorm;
      await fsp.writeFile(target, updated, "utf8");
      invalidateFileCache(target);
      const validation = await validateAfterWrite(target, () => getActiveProject(CONFIG_PATH));
      const rel = path.relative(WORKSPACE_ROOT, target);
      if (validationFailed(validation)) {
        await fsp.writeFile(target, priorContent, "utf8");
        invalidateFileCache(target);
        return {
          content: [{
            type: "text",
            text: `ERROR: replaced text in ${rel} but static validation failed.${formatValidationResult(validation)}`
          }],
          isError: true
        };
      }
      let message = `OK: replaced ${occurrences} occurrence(s) in ${rel}`;
      if (validation && !validation.skipped) {
        message += formatValidationResult(validation);
      }
      return text(message);
    }

    if (name === "propose_file_deletions") {
      const activeProject = getActiveProject(CONFIG_PATH);
      const plan = await buildDeletionProposal(args.files, args.completedEditsSummary, activeProject);
      return text(JSON.stringify(plan, null, 2));
    }

    if (name === "delete_file") {
      if (!ALLOW_WRITE) return fail("delete_file blocked. Set ALLOW_WRITE=1 to enable.");
      if (!ALLOW_SOURCE_DELETE) {
        return fail("delete_file blocked. Set ALLOW_SOURCE_DELETE=1 to enable source deletions.");
      }
      const target = normalizeRelPath(args.path);
      const activeProject = getActiveProject(CONFIG_PATH);
      const guard = isDeleteAllowedPath(target, WORKSPACE_ROOT, activeProject);
      if (!guard.ok) {
        return fail(guard.message);
      }
      const rel = path.relative(WORKSPACE_ROOT, target).replace(/\\/g, "/");
      const completedEditsSummary = requireDeletionText(args.completedEditsSummary, "completedEditsSummary");
      const reason = requireDeletionText(args.reason, "reason");
      const ifNotDeleted = requireDeletionText(args.ifNotDeleted, "ifNotDeleted");
      const ifDeleted = requireDeletionText(args.ifDeleted, "ifDeleted");
      const expectedToken = deletionApprovalToken({
        relPath: rel,
        completedEditsSummary,
        reason,
        ifNotDeleted,
        ifDeleted,
      });
      if (String(args.approvalToken || "") !== expectedToken) {
        return fail(
          "delete_file blocked: approvalToken does not match this deletion explanation. "
          + "Call propose_file_deletions after edits are complete, show the plan to the user, "
          + "and pass the matching per-file approvalToken only after approval."
        );
      }
      const delStat = await statSafe(target);
      if (!delStat || !delStat.isFile()) return fail(`not found or not file: ${args.path}`);
      if (args.expectedContent !== undefined) {
        const content = await fsp.readFile(target, "utf8");
        if (content !== String(args.expectedContent)) {
          return fail("expectedContent mismatch; delete aborted.");
        }
      }
      await fsp.unlink(target);
      invalidateFileCache(target);
      return text(JSON.stringify({
        ok: true,
        deleted: rel,
        fileName: path.basename(target),
        completedEditsSummary,
        reason,
        ifNotDeleted,
        ifDeleted,
      }, null, 2));
    }

    if (name === "static_validate_project") {
      const activeProject = getActiveProject(CONFIG_PATH);
      let projectRoot = String(args.projectRoot || "").trim();
      if (!projectRoot && activeProject) {
        projectRoot = path.dirname(path.resolve(activeProject));
      }
      if (!projectRoot) {
        return fail("No active project and no projectRoot provided.");
      }
      const resolved = path.resolve(projectRoot);
      if (resolved.toLowerCase().endsWith(".uproject")) {
        projectRoot = path.dirname(resolved);
      } else {
        projectRoot = resolved;
      }
      const validation = await runStaticValidation(projectRoot);
      if (validationFailed(validation)) {
        return {
          content: [{
            type: "text",
            text: `Static validation failed.${formatValidationResult(validation)}`
          }],
          isError: true
        };
      }
      return text(`Static validation passed.${formatValidationResult(validation)}`);
    }

    if (name === "search_files") {
      const base = normalizeRelPath(args.path || ".");
      const maxResults = Math.max(1, Math.min(Number(args.maxResults || 100), 1000));
      const useRegex = !!args.regex;
      const query = String(args.query || "");
      if (!query) return fail("query must not be empty");

      const ignoreDirs = new Set([
        ".git", ".vs", ".idea", "Binaries", "DerivedDataCache", "Intermediate",
        "Saved", "node_modules", ".gradle", ".cache"
      ]);

      const matcher = useRegex
        ? new RegExp(query, "i")
        : null;

      const results = [];
      let filesSeen = 0;

      async function walk(p) {
        if (results.length >= maxResults || filesSeen >= SEARCH_MAX_FILES) return;
        const st = await statSafe(p);
        if (!st) return;

        if (st.isDirectory()) {
          const dirName = path.basename(p);
          if (ignoreDirs.has(dirName)) return;
          const entries = await fsp.readdir(p, { withFileTypes: true });
          for (const e of entries) {
            await walk(path.join(p, e.name));
            if (results.length >= maxResults || filesSeen >= SEARCH_MAX_FILES) break;
          }
          return;
        }

        if (!st.isFile()) return;
        filesSeen++;

        if (st.size > MAX_READ_BYTES) return;
        const buf = await fsp.readFile(p);
        if (!isTextLikely(buf)) return;

        const content = buf.toString("utf8");
        const lines = content.split(/\r?\n/);
        for (let i = 0; i < lines.length; i++) {
          const line = lines[i];
          const hit = useRegex ? matcher.test(line) : line.toLowerCase().includes(query.toLowerCase());
          if (hit) {
            results.push({
              file: path.relative(WORKSPACE_ROOT, p),
              line: i + 1,
              text: line.slice(0, 500)
            });
            if (results.length >= maxResults) break;
          }
        }
      }

      await walk(base);
      return text(JSON.stringify({ results, filesSeen }, null, 2));
    }

    if (name === "run_command") {
      if (!ALLOW_COMMANDS) return fail("run_command blocked. Set ALLOW_COMMANDS=1 to enable.");
      const command = String(args.command || "");
      if (!allowedCommandBase(command)) {
        return fail(`command not allowlisted or blocked: ${command}`);
      }
      const cwd = normalizeRelPath(args.cwd || ".");
      const s = await statSafe(cwd);
      if (!s || !s.isDirectory()) return fail(`cwd not found or not directory: ${args.cwd || "."}`);
      const result = await execCommand(command, cwd, Number(args.timeoutMs || COMMAND_TIMEOUT_MS));
      return text(JSON.stringify(result, null, 2));
    }

    if (name === "build_unreal_project") {
      if (!ALLOW_UNREAL_BUILD) return fail("build_unreal_project blocked. Set ALLOW_UNREAL_BUILD=1 to enable.");

      const planResult = await resolveBuildPlan(WORKSPACE_ROOT, CONFIG_PATH, args);
      if (!planResult.ok || !planResult.build) {
        return text(JSON.stringify({
          ok: false,
          error: planResult.error || "Could not resolve Unreal build plan.",
          selectionReason: planResult.selectionReason,
          suggestions: planResult.suggestions || null,
          searchRoots: planResult.roots || []
        }, null, 2));
      }

      const build = planResult.build;
      if (!(await exists(build.buildBat))) {
        return fail(`Build.bat not found: ${build.buildBat}`);
      }

      let projectPath = build.projectPath;
      const rawProject = String(args.project || "");
      if (path.isAbsolute(rawProject)) {
        if (!args.allowAbsoluteProject) {
          const rel = path.relative(WORKSPACE_ROOT, path.resolve(rawProject));
          if (rel.startsWith("..") || path.isAbsolute(rel)) {
            return fail("absolute project path outside WORKSPACE_ROOT blocked. Move project under WORKSPACE_ROOT or pass allowAbsoluteProject=true intentionally.");
          }
        }
        projectPath = path.resolve(rawProject);
      }

      if (!(await exists(projectPath))) return fail(`uproject not found: ${projectPath}`);
      if (!projectPath.toLowerCase().endsWith(".uproject")) return fail("project must be a .uproject file");

      const target = String(build.target || "").trim();
      if (!/^[A-Za-z0-9_]+$/.test(target)) return fail("target must be a simple target name, e.g. MyGameEditor");

      const platform = String(build.platform || "Win64").trim();
      const configuration = String(build.configuration || "Development").trim();

      if (!/^[A-Za-z0-9_]+$/.test(platform)) return fail("invalid platform");
      if (!/^[A-Za-z0-9_]+$/.test(configuration)) return fail("invalid configuration");

      const command = `"${build.buildBat}" ${target} ${platform} ${configuration} -Project="${projectPath}" -WaitMutex -NoHotReloadFromIDE`;
      const buildTimeout = Number(args.timeoutMs || COMMAND_TIMEOUT_MS);
      const result = await execCommand(command, path.dirname(projectPath), buildTimeout);
      const likelyErrors = extractLikelyCompileErrors(result.stdout, result.stderr);

      return text(JSON.stringify({
        autoDetected: {
          selectionReason: planResult.selectionReason,
          engineRoot: build.engineRoot,
          engineSource: build.engineSource,
          engineWarning: build.engineWarning || null,
          requestedEngineAssociation: build.requestedEngineAssociation || null,
          projectPath,
          projectFile: path.basename(projectPath),
          target,
          platform,
          configuration,
          allTargets: build.allTargets
        },
        command,
        ok: result.ok,
        exitCode: result.exitCode,
        likelyErrors,
        stdout: result.stdout,
        stderr: result.stderr,
        error: result.error
      }, null, 2));
    }

    return fail(`unknown tool: ${name}`);
  } catch (err) {
    return fail(err && err.stack ? err.stack : String(err));
  }
});

async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
