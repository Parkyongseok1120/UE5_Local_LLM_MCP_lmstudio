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
function resolveValidateOnWrite() {
  const explicit = String(process.env.VALIDATE_ON_WRITE || "").trim().toLowerCase();
  if (["0", "false", "no", "off"].includes(explicit)) {
    return false;
  }
  if (["1", "true", "yes", "on"].includes(explicit)) {
    return true;
  }
  const allowWrite = String(process.env.ALLOW_WRITE || "").trim().toLowerCase();
  return ["1", "true", "yes", "on"].includes(allowWrite);
}

const VALIDATE_ON_WRITE = resolveValidateOnWrite();

// Full static validation timeout for explicit static_validate_project calls.
const STATIC_VALIDATION_TIMEOUT_MS = 120000;

// Tighter time budget for validation that runs synchronously inside write_file /
// replace_in_file. Kept well under any client tool timeout so the write tool always
// responds before the client gives up (which caused -32001 + the timeout-retry spiral).
function resolveValidateOnWriteTimeoutMs() {
  const raw = Number(process.env.VALIDATE_ON_WRITE_TIMEOUT_MS);
  if (Number.isFinite(raw) && raw > 0) {
    return raw;
  }
  return 45000;
}

const VALIDATE_ON_WRITE_TIMEOUT_MS = resolveValidateOnWriteTimeoutMs();

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
    return {
      ok: false,
      skipped: false,
      reason: String(error.message || error),
      findingCount: 1,
      findings: [{
        severity: "error",
        code: "RUNTIME_CONFIG_CHECK_FAILED",
        path: projectRoot,
        line: 0,
        message: String(error.message || error),
      }],
    };
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

async function runStaticValidation(projectRoot, options = {}) {
  const timeoutMs = Number.isFinite(options.timeoutMs) && options.timeoutMs > 0
    ? options.timeoutMs
    : STATIC_VALIDATION_TIMEOUT_MS;
  const script = path.join(UNREAL58_ROOT, "scripts", "validate_project_sources.py");
  if (!fs.existsSync(script)) {
    return {
      ok: false,
      skipped: false,
      reason: `validator script missing: ${script}`,
      findingCount: 1,
      findings: [{
        severity: "error",
        code: "VALIDATOR_MISSING",
        path: projectRoot,
        line: 0,
        message: `validator script missing: ${script}`,
      }],
    };
  }

  const python = resolvePythonExe();
  try {
    const { stdout } = await execFile(
      python,
      [script, "--project-root", projectRoot, "--json"],
      {
        cwd: UNREAL58_ROOT,
        timeout: timeoutMs,
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
    // execFile sets killed=true when the timeout budget is exceeded. Surface this
    // distinctly so validateAfterWrite can fail OPEN instead of blocking the write.
    if (error.killed === true) {
      return {
        ok: false,
        skipped: false,
        timedOut: true,
        projectRoot,
        reason: `validation exceeded time budget (${timeoutMs}ms)`,
        findingCount: 1,
        findings: [{
          severity: "warning",
          code: "VALIDATOR_TIMEOUT",
          path: projectRoot,
          line: 0,
          message: `validation exceeded time budget (${timeoutMs}ms)`,
        }],
      };
    }
    return {
      ok: false,
      skipped: false,
      reason: `${error.message}${stderr ? `\n${stderr}` : ""}`,
      findingCount: 1,
      findings: [{
        severity: "error",
        code: "VALIDATOR_EXEC_FAILED",
        path: projectRoot,
        line: 0,
        message: `${error.message}${stderr ? `\n${stderr}` : ""}`,
      }],
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
      ok: false,
      skipped: false,
      projectRoot: null,
      findingCount: 1,
      findings: [{
        severity: "error",
        code: "VALIDATION_PROJECT_ROOT",
        path: absPath,
        line: 0,
        message: "could not resolve project root for validation",
      }],
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
  const result = await runStaticValidation(projectRoot, {
    timeoutMs: VALIDATE_ON_WRITE_TIMEOUT_MS,
  });
  // Fail OPEN only when validation timed out: the write persists and we flag that
  // validation was skipped so the model runs static_validate_project before building.
  // Real findings still fail CLOSED exactly as before.
  if (result && result.timedOut) {
    return {
      ok: true,
      skipped: true,
      timedOut: true,
      projectRoot,
      findingCount: 0,
      findings: [],
      note: "validation skipped (time budget); run static_validate_project before build",
    };
  }
  return result;
}

function formatValidationResult(result) {
  if (!result) {
    return "";
  }
  if (result.skipped && result.ok !== false) {
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
  VALIDATE_ON_WRITE_TIMEOUT_MS,
  resolveValidateOnWrite,
  resolveValidateOnWriteTimeoutMs,
  validateAfterWrite,
  formatValidationResult,
  runStaticValidation,
  resolveProjectRootForFile,
};
