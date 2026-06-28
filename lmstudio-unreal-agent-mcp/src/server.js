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
  listUnrealProjects
} = require("./unreal-detect.js");
const {
  scanSymbolImpact,
  validateRefactorPlan
} = require("./refactor-tools.js");
const {
  validateAfterWrite,
  formatValidationResult
} = require("./validate-write.js");

const WORKSPACE_ROOT = path.resolve(process.env.WORKSPACE_ROOT || process.cwd());
const CONFIG_PATH = path.resolve(
  process.env.AGENT_MCP_CONFIG
  || path.join(__dirname, "..", "config", "agent-mcp.json")
);
const ALLOW_WRITE = process.env.ALLOW_WRITE === "1" || process.env.ALLOW_WRITE === "true";
const ALLOW_COMMANDS = process.env.ALLOW_COMMANDS === "1" || process.env.ALLOW_COMMANDS === "true";
const ALLOW_UNREAL_BUILD = process.env.ALLOW_UNREAL_BUILD === "1" || process.env.ALLOW_UNREAL_BUILD === "true";
const MAX_READ_BYTES = Number(process.env.MAX_READ_BYTES || 64 * 1024);
const MAX_OUTPUT_BYTES = Number(process.env.MAX_OUTPUT_BYTES || 1024 * 256);
const COMMAND_TIMEOUT_MS = Number(process.env.COMMAND_TIMEOUT_MS || 1000 * 60 * 10);
const SEARCH_MAX_FILES = Number(process.env.SEARCH_MAX_FILES || 5000);

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

function normalizeRelPath(p) {
  if (!p || typeof p !== "string") {
    throw new Error("path must be a non-empty string");
  }
  const resolved = path.resolve(WORKSPACE_ROOT, p);
  const relative = path.relative(WORKSPACE_ROOT, resolved);

  if (relative.startsWith("..") || path.isAbsolute(relative)) {
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

async function buildWorkspaceInfo() {
  const engines = await findEngineInstalls();
  const discovery = await discoverProjects(WORKSPACE_ROOT, CONFIG_PATH);
  return {
    workspaceRoot: WORKSPACE_ROOT,
    configPath: CONFIG_PATH,
    activeProject: getActiveProject(CONFIG_PATH),
    allowWrite: ALLOW_WRITE,
    allowCommands: ALLOW_COMMANDS,
    allowUnrealBuild: ALLOW_UNREAL_BUILD,
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
}

server.setRequestHandler(ListToolsRequestSchema, async () => {
  return {
    tools: [
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
        description: "Return the currently selected active Unreal project from config/agent-mcp.json.",
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
        description: "List files/directories under WORKSPACE_ROOT. Path must stay inside workspace.",
        inputSchema: makeJsonSchema({
          path: { type: "string", description: "Relative path inside workspace, e.g. '.', 'Source'." },
          maxEntries: { type: "number", description: "Max entries to show. Default 200." }
        }, ["path"])
      },
      {
        name: "read_file",
        description: "Read a UTF-8 text file inside WORKSPACE_ROOT. Default cap 64 KiB; use read_file_range for partial reads.",
        inputSchema: makeJsonSchema({
          path: { type: "string", description: "Relative path inside workspace." },
          maxBytes: { type: "number", description: "Optional max bytes. Default 64 KiB." }
        }, ["path"])
      },
      {
        name: "read_file_range",
        description: "Read a line range from a UTF-8 text file inside WORKSPACE_ROOT. Prefer this over read_file for large sources.",
        inputSchema: makeJsonSchema({
          path: { type: "string", description: "Relative path inside workspace." },
          startLine: { type: "number", description: "1-based start line (inclusive)." },
          endLine: { type: "number", description: "1-based end line (inclusive)." }
        }, ["path", "startLine", "endLine"])
      },
      {
        name: "write_file",
        description: "Write a UTF-8 file inside WORKSPACE_ROOT. Requires ALLOW_WRITE=1. Does not delete files.",
        inputSchema: makeJsonSchema({
          path: { type: "string", description: "Relative path inside workspace." },
          content: { type: "string", description: "Full file content to write." },
          createDirs: { type: "boolean", description: "Create parent directories if needed. Default false." }
        }, ["path", "content"])
      },
      {
        name: "replace_in_file",
        description: "Safely replace exact text in a file. Requires ALLOW_WRITE=1. Good for minimal patches.",
        inputSchema: makeJsonSchema({
          path: { type: "string", description: "Relative path inside workspace." },
          oldText: { type: "string", description: "Exact text to replace." },
          newText: { type: "string", description: "Replacement text." },
          expectedOccurrences: { type: "number", description: "If set, replacement only proceeds when occurrence count matches." }
        }, ["path", "oldText", "newText"])
      },
      {
        name: "search_files",
        description: "Search text files by regex or plain text under WORKSPACE_ROOT. Returns matching lines; skips binary/build dirs. Files larger than read cap are skipped.",
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
        description: "Run Unreal Build.bat for a .uproject. Requires ALLOW_UNREAL_BUILD=1. If project/engineRoot/target are omitted, auto-detects from workspace search roots and Target.cs files.",
        inputSchema: makeJsonSchema({
          hint: { type: "string", description: "Optional project folder or .uproject name fragment for auto-detection." },
          engineRoot: { type: "string", description: "Optional UE engine root. Auto-detected from EngineAssociation when omitted." },
          project: { type: "string", description: "Optional .uproject path relative to workspace or absolute inside workspace." },
          target: { type: "string", description: "Optional target name. Defaults to detected *Editor target." },
          platform: { type: "string", description: "Optional platform. Default Win64 on Windows." },
          configuration: { type: "string", description: "Optional configuration. Default Development." },
          allowAbsoluteProject: { type: "boolean", description: "Allow absolute .uproject path outside workspace. Default false." }
        })
      }
    ]
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
      if (activeProject) {
        const selection = await resolveProjectSelection(WORKSPACE_ROOT, CONFIG_PATH, {
          hint: activeProject
        });
        details = selection.selected;
      }
      return text(JSON.stringify({ activeProject, details }, null, 2));
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

      const maxBytes = Math.max(1, Math.min(Number(args.maxBytes || MAX_READ_BYTES), MAX_READ_BYTES));
      const fd = await fsp.open(target, "r");
      try {
        const buffer = Buffer.alloc(Math.min(maxBytes, s.size));
        await fd.read(buffer, 0, buffer.length, 0);
        if (!isTextLikely(buffer)) return fail(`file appears binary: ${args.path}`);
        let out = buffer.toString("utf8");
        if (s.size > buffer.length) {
          out += `\n\n[TRUNCATED: file size ${s.size} bytes, read ${buffer.length} bytes. Use read_file_range for partial reads.]`;
        }
        return text(out);
      } finally {
        await fd.close();
      }
    }

    if (name === "read_file_range") {
      const target = normalizeRelPath(args.path);
      const s = await statSafe(target);
      if (!s) return fail(`not found: ${args.path}`);
      if (!s.isFile()) return fail(`not a file: ${args.path}`);

      const startLine = Math.max(1, Number(args.startLine || 1));
      const endLine = Math.max(startLine, Number(args.endLine || startLine));
      if (!Number.isFinite(startLine) || !Number.isFinite(endLine)) {
        return fail("startLine and endLine must be numbers");
      }
      if (endLine - startLine > 2000) {
        return fail("line range too large (max 2000 lines per request)");
      }

      const content = await fsp.readFile(target, "utf8");
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
      if (args.createDirs) await fsp.mkdir(parent, { recursive: true });
      if (!(await exists(parent))) return fail(`parent directory not found: ${path.relative(WORKSPACE_ROOT, parent)}`);
      await fsp.writeFile(target, String(args.content || ""), "utf8");
      const validation = await validateAfterWrite(target, () => getActiveProject(CONFIG_PATH));
      const rel = path.relative(WORKSPACE_ROOT, target);
      if (validation && !validation.skipped && !validation.ok) {
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

      const content = await fsp.readFile(target, "utf8");
      const occurrences = content.split(oldText).length - 1;
      if (occurrences === 0) return fail("oldText not found");
      if (args.expectedOccurrences !== undefined && occurrences !== Number(args.expectedOccurrences)) {
        return fail(`occurrence mismatch: expected ${args.expectedOccurrences}, found ${occurrences}`);
      }

      const updated = content.split(oldText).join(newText);
      await fsp.writeFile(target, updated, "utf8");
      const validation = await validateAfterWrite(target, () => getActiveProject(CONFIG_PATH));
      const rel = path.relative(WORKSPACE_ROOT, target);
      if (validation && !validation.skipped && !validation.ok) {
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
      const result = await execCommand(command, path.dirname(projectPath), COMMAND_TIMEOUT_MS);
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
