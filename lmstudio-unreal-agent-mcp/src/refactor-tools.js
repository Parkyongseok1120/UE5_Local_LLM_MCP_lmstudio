"use strict";

const fs = require("fs");
const path = require("path");
const os = require("os");
const cp = require("child_process");
const { promisify } = require("util");
const { getActiveProject, resolveProjectSelection } = require("./unreal-detect.js");

const execFile = promisify(cp.execFile);

const UNREAL58_ROOT = path.resolve(
  process.env.UNREAL58_ROOT || path.join(os.homedir(), ".lmstudio", "Unreal58-RAG")
);

function resolvePythonExe() {
  const bundled = path.join(
    os.homedir(),
    ".cache",
    "codex-runtimes",
    "codex-primary-runtime",
    "dependencies",
    "python",
    "python.exe"
  );
  if (fs.existsSync(bundled)) {
    return bundled;
  }
  return "python";
}

function resolveRefactorPlanScript() {
  const repoScript = path.join(UNREAL58_ROOT, "scripts", "refactor_plan.py");
  if (fs.existsSync(repoScript)) {
    return repoScript;
  }
  return path.resolve(__dirname, "..", "..", "scripts", "refactor_plan.py");
}

async function callRefactorCli(subcommand, cliArgs) {
  const python = resolvePythonExe();
  const script = resolveRefactorPlanScript();
  const { stdout } = await execFile(python, [script, subcommand, ...cliArgs], {
    cwd: path.dirname(script),
    timeout: 120000,
    maxBuffer: 4 * 1024 * 1024,
  });
  return JSON.parse(stdout);
}

async function resolveProjectRoot(workspaceRoot, configPath, hint) {
  const active = getActiveProject(configPath);
  const selection = await resolveProjectSelection(workspaceRoot, configPath, {
    hint: hint || active || undefined,
  });
  if (!selection.selected) {
    return { ok: false, error: selection.error || "No project resolved.", selection };
  }
  return {
    ok: true,
    projectPath: selection.selected.projectPath,
    projectDir: selection.selected.projectDir,
    projectName: selection.selected.projectName,
  };
}

async function scanSymbolImpact(workspaceRoot, configPath, options = {}) {
  const resolved = await resolveProjectRoot(workspaceRoot, configPath, options.hint);
  if (!resolved.ok) {
    return resolved;
  }
  const symbol = String(options.symbol || "").trim();
  if (symbol.length < 2) {
    return { ok: false, error: "symbol must be at least 2 characters." };
  }
  const root = path.resolve(options.projectDir || resolved.projectDir);
  try {
    return await callRefactorCli("scan", [
      "--project-root",
      root,
      "--symbol",
      symbol,
      "--max-files",
      String(Number(options.maxFiles || 40)),
    ]);
  } catch (error) {
    return { ok: false, error: String(error.message || error), symbol, projectRoot: root };
  }
}

async function validateRefactorPlan(stage, planText) {
  try {
    return await callRefactorCli("validate", [
      "--stage",
      String(stage || "R0"),
      "--plan-text",
      String(planText || ""),
    ]);
  } catch (error) {
    return {
      ok: false,
      stage: String(stage || "R0").trim().toUpperCase(),
      issues: [String(error.message || error)],
      warnings: [],
      passed: [],
    };
  }
}

module.exports = {
  scanSymbolImpact,
  validateRefactorPlan,
};
