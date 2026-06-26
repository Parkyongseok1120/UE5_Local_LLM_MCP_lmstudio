"use strict";

const fs = require("fs");
const fsp = fs.promises;
const path = require("path");
const os = require("os");
const cp = require("child_process");
const { promisify } = require("util");

const execFile = promisify(cp.execFile);

const UNREAL58_ROOT = path.resolve(
  process.env.UNREAL58_ROOT || path.join(os.homedir(), ".lmstudio", "Unreal58-RAG")
);
const VALIDATE_ON_WRITE = process.env.VALIDATE_ON_WRITE === "1" || process.env.VALIDATE_ON_WRITE === "true";

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
  const localRoot = path.join(process.env.LOCALAPPDATA || "", "Programs", "Python");
  if (fs.existsSync(localRoot)) {
    const versions = fs.readdirSync(localRoot)
      .filter((name) => name.toLowerCase().startsWith("python"))
      .sort()
      .reverse();
    for (const version of versions) {
      const candidate = path.join(localRoot, version, "python.exe");
      if (fs.existsSync(candidate)) {
        return candidate;
      }
    }
  }
  return "python";
}

function isSourceLike(filePath) {
  const ext = path.extname(filePath).toLowerCase();
  return [".h", ".hpp", ".cpp", ".c", ".cc", ".cs"].includes(ext);
}

function isConfigLike(filePath) {
  const ext = path.extname(filePath).toLowerCase();
  const base = path.basename(filePath).toLowerCase();
  return ext === ".ini" && base.startsWith("default");
}

async function runRuntimeConfigCheck(projectRoot) {
  const script = path.join(UNREAL58_ROOT, "scripts", "runtime_config_checklist.py");
  if (!fs.existsSync(script)) {
    return { ok: true, skipped: true, reason: "runtime_config_checklist.py missing" };
  }
  const python = resolvePythonExe();
  try {
    const { stdout } = await execFile(
      python,
      [script, "--project-root", projectRoot],
      { cwd: UNREAL58_ROOT, timeout: 60000, maxBuffer: 1024 * 1024 }
    );
    const payload = JSON.parse(stdout);
    return { ok: payload.ok, skipped: false, payload };
  } catch (error) {
    return { ok: true, skipped: true, reason: String(error.message || error) };
  }
}

async function resolveProjectRootForFile(absPath, getActiveProject) {
  let dir = path.dirname(absPath);
  for (let depth = 0; depth < 10; depth += 1) {
    const base = path.basename(dir).toLowerCase();
    if (base === "source") {
      return path.dirname(dir);
    }
    try {
      const entries = await fsp.readdir(dir);
      const uproject = entries.find((entry) => entry.toLowerCase().endsWith(".uproject"));
      if (uproject) {
        return dir;
      }
    } catch {
      break;
    }
    const parent = path.dirname(dir);
    if (parent === dir) {
      break;
    }
    dir = parent;
  }

  const activeProject = getActiveProject();
  if (activeProject) {
    return path.dirname(path.resolve(activeProject));
  }
  return null;
}

async function runStaticValidation(projectRoot) {
  const script = path.join(UNREAL58_ROOT, "scripts", "validate_project_sources.py");
  if (!fs.existsSync(script)) {
    return {
      ok: false,
      skipped: true,
      reason: `validator script missing: ${script}`,
      findings: [],
    };
  }

  const python = resolvePythonExe();
  try {
    const { stdout } = await execFile(
      python,
      [script, "--project-root", projectRoot, "--json"],
      {
        cwd: UNREAL58_ROOT,
        timeout: 120000,
        maxBuffer: 4 * 1024 * 1024,
      }
    );
    const payload = JSON.parse(stdout);
    return {
      ok: !payload.hasErrors,
      skipped: false,
      projectRoot,
      findingCount: payload.findingCount,
      findings: payload.findings || [],
    };
  } catch (error) {
    const stderr = error.stderr ? String(error.stderr) : "";
    const stdout = error.stdout ? String(error.stdout) : "";
    let parsed = null;
    try {
      parsed = stdout ? JSON.parse(stdout) : null;
    } catch {
      parsed = null;
    }
    if (parsed && parsed.hasErrors) {
      return {
        ok: false,
        skipped: false,
        projectRoot,
        findingCount: parsed.findingCount,
        findings: parsed.findings || [],
      };
    }
    return {
      ok: false,
      skipped: true,
      reason: `${error.message}${stderr ? `\n${stderr}` : ""}`,
      findings: [],
    };
  }
}

async function validateAfterWrite(absPath, getActiveProject) {
  if (!VALIDATE_ON_WRITE) {
    return null;
  }
  const projectRoot = await resolveProjectRootForFile(absPath, getActiveProject);
  if (!projectRoot) {
    return {
      ok: true,
      skipped: true,
      reason: "could not resolve project root for validation",
      findings: [],
    };
  }
  if (isConfigLike(absPath)) {
    const runtime = await runRuntimeConfigCheck(projectRoot);
    if (!runtime.skipped && !runtime.ok) {
      return {
        ok: false,
        skipped: false,
        projectRoot,
        findingCount: (runtime.payload?.issues || []).length,
        findings: (runtime.payload?.issues || []).map((msg, index) => ({
          severity: "error",
          code: "RUNTIME_CONFIG",
          path: absPath,
          line: index + 1,
          message: msg,
        })),
      };
    }
    return { ok: true, skipped: false, projectRoot, findingCount: 0, findings: [] };
  }
  if (!isSourceLike(absPath)) {
    return null;
  }
  return runStaticValidation(projectRoot);
}

function formatValidationResult(result) {
  if (!result || result.skipped) {
    return "";
  }
  const lines = [
    "",
    "Static validation:",
    `projectRoot=${result.projectRoot}`,
    `findingCount=${result.findingCount}`,
  ];
  for (const finding of (result.findings || []).slice(0, 20)) {
    lines.push(
      `- [${finding.severity}] ${finding.code} ${finding.path}:${finding.line} ${finding.message}`
    );
  }
  if ((result.findings || []).length > 20) {
    lines.push(`... ${result.findings.length - 20} more finding(s)`);
  }
  if (!result.ok) {
    lines.push("Validation FAILED. Fix findings before claiming compile readiness.");
  } else {
    lines.push("Validation passed (no static errors).");
  }
  return lines.join("\n");
}

module.exports = {
  VALIDATE_ON_WRITE,
  validateAfterWrite,
  formatValidationResult,
  runStaticValidation,
  resolveProjectRootForFile,
};
