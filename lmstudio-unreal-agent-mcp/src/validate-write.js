"use strict";

const fs = require("fs");
const fsp = fs.promises;
const path = require("path");
const os = require("os");
const cp = require("child_process");
const { promisify } = require("util");

const execFile = promisify(cp.execFile);

const { markUnvalidated, clearValidated } = require("./validation-dirty");

function resolveValidationRoot(options = {}) {
  const configured = Object.prototype.hasOwnProperty.call(options, "envRoot")
    ? String(options.envRoot || "").trim()
    : String(process.env.UNREAL58_ROOT || "").trim();
  if (configured) return path.resolve(configured);

  // Portable/repo layout: lmstudio-unreal-agent-mcp/src -> ../../scripts.
  // Prefer the scripts shipped beside this MCP over a historical home clone.
  const repositoryRoot = path.resolve(options.repositoryRoot || path.join(__dirname, "..", ".."));
  if (fs.existsSync(path.join(repositoryRoot, "scripts", "validate_project_sources.py"))) {
    return repositoryRoot;
  }

  const homeDir = Object.prototype.hasOwnProperty.call(options, "homeDir")
    ? String(options.homeDir || "")
    : os.homedir();
  return path.resolve(homeDir, ".lmstudio", "Unreal58-RAG");
}

const VALIDATION_ROOT = resolveValidationRoot();
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
// Keep the default generous for large projects, while allowing installers and
// hermetic recovery tests to prove the timeout path without waiting two minutes.
function resolveStaticValidationTimeoutMs() {
  const raw = Number(process.env.STATIC_VALIDATION_TIMEOUT_MS);
  if (Number.isFinite(raw) && raw > 0) {
    return Math.max(25, Math.min(Math.trunc(raw), 10 * 60 * 1000));
  }
  return 120000;
}

const STATIC_VALIDATION_TIMEOUT_MS = resolveStaticValidationTimeoutMs();

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
  const explicit = String(process.env.PYTHON_EXE || process.env.PYTHON || "").trim();
  if (explicit) {
    return explicit;
  }
  const bundledRoot = path.join(
    os.homedir(),
    ".cache",
    "codex-runtimes",
    "codex-primary-runtime",
    "dependencies",
    "python"
  );
  const bundledCandidates = process.platform === "win32"
    ? [path.join(bundledRoot, "python.exe")]
    : [
      path.join(bundledRoot, "bin", "python3.12"),
      path.join(bundledRoot, "bin", "python3"),
      path.join(bundledRoot, "bin", "python"),
    ];
  for (const bundled of bundledCandidates) {
    if (fs.existsSync(bundled)) {
      return bundled;
    }
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
  return process.platform === "win32" ? "python" : "python3";
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
  const script = path.join(VALIDATION_ROOT, "scripts", "runtime_config_checklist.py");
  if (!fs.existsSync(script)) {
    return { ok: true, skipped: true, reason: "runtime_config_checklist.py missing" };
  }
  const python = resolvePythonExe();
  try {
    const { stdout } = await execFile(
      python,
      [script, "--project-root", projectRoot],
      { cwd: VALIDATION_ROOT, timeout: 60000, maxBuffer: 1024 * 1024 }
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
  const resolved = path.resolve(absPath);
  const activeProject = getActiveProject();
  if (activeProject) {
    const activeRoot = path.dirname(path.resolve(activeProject));
    const rel = path.relative(activeRoot, resolved);
    if (rel && !rel.startsWith("..") && !path.isAbsolute(rel)) {
      return activeRoot;
    }
  }

  let dir = path.dirname(resolved);
  for (let depth = 0; depth < 20; depth += 1) {
    try {
      const entries = await fsp.readdir(dir);
      const uproject = entries.find((entry) => entry.toLowerCase().endsWith(".uproject"));
      if (uproject) {
        return dir;
      }
    } catch {
      break;
    }
    const base = path.basename(dir).toLowerCase();
    if (base === "source") {
      let candidate = path.dirname(dir);
      for (let up = 0; up < 15; up += 1) {
        try {
          const entries = await fsp.readdir(candidate);
          if (entries.some((entry) => entry.toLowerCase().endsWith(".uproject"))) {
            return candidate;
          }
        } catch {
          break;
        }
        const parent = path.dirname(candidate);
        if (parent === candidate) {
          break;
        }
        candidate = parent;
      }
      return path.dirname(dir);
    }
    const parent = path.dirname(dir);
    if (parent === dir) {
      break;
    }
    dir = parent;
  }

  dir = path.dirname(resolved);
  for (let depth = 0; depth < 20; depth += 1) {
    try {
      const entries = await fsp.readdir(dir);
      if (entries.some((entry) => entry.toLowerCase().endsWith(".uplugin"))) {
        let candidate = path.dirname(dir);
        for (let up = 0; up < 15; up += 1) {
          try {
            const pluginEntries = await fsp.readdir(candidate);
            if (pluginEntries.some((entry) => entry.toLowerCase().endsWith(".uproject"))) {
              return candidate;
            }
          } catch {
            break;
          }
          const parent = path.dirname(candidate);
          if (parent === candidate) {
            break;
          }
          candidate = parent;
        }
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

  if (activeProject) {
    return path.dirname(path.resolve(activeProject));
  }
  return null;
}

function blockingErrorsOf(payload) {
  if (payload && Object.prototype.hasOwnProperty.call(payload, "hasBlockingErrors")) {
    return Boolean(payload.hasBlockingErrors);
  }
  return Boolean(payload && payload.hasErrors);
}

async function runStaticValidation(projectRoot, options = {}) {
  const timeoutMs = Number.isFinite(options.timeoutMs) && options.timeoutMs > 0
    ? options.timeoutMs
    : STATIC_VALIDATION_TIMEOUT_MS;
  const writeTarget = options.writeTarget || null;
  const scopeTargets = [...new Set(
    (Array.isArray(options.scopeTargets) ? options.scopeTargets : [])
      .map((item) => String(item || "").trim().replace(/\\/g, "/"))
      .filter(Boolean)
  )];
  const script = path.join(VALIDATION_ROOT, "scripts", "validate_project_sources.py");
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
  const args = [script, "--project-root", projectRoot, "--json"];
  if (writeTarget) {
    args.push("--write-target", writeTarget);
  }
  for (const scopeTarget of scopeTargets) {
    args.push("--scope-target", scopeTarget);
  }
  try {
    const { stdout } = await execFile(
      python,
      args,
      {
        cwd: VALIDATION_ROOT,
        timeout: timeoutMs,
        maxBuffer: 4 * 1024 * 1024,
      }
    );
    const payload = JSON.parse(stdout);
    return {
      ok: !blockingErrorsOf(payload),
      skipped: false,
      projectRoot,
      writeTarget,
      scopeTargets: payload.scopeTargets || scopeTargets,
      scanMode: payload.scanMode || "full",
      scopeKind: payload.scopeKind || (scopeTargets.length ? "task_slice" : "full_audit"),
      scopedFileCount: payload.scopedFileCount || 0,
      elapsedMs: payload.elapsedMs || 0,
      findingCount: payload.findingCount,
      deferredCount: payload.deferredCount || 0,
      preExistingCount: payload.preExistingCount || 0,
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
    // The script exits non-zero whenever ANY error exists project-wide, even if it is
    // pre-existing in another file or a deferred counterpart finding. Use the scoped
    // hasBlockingErrors field (not the exit code) to decide whether this write is ok.
    if (parsed) {
      return {
        ok: !blockingErrorsOf(parsed),
        skipped: false,
        projectRoot,
        writeTarget,
        scopeTargets: parsed.scopeTargets || scopeTargets,
        scanMode: parsed.scanMode || "full",
        scopeKind: parsed.scopeKind || (scopeTargets.length ? "task_slice" : "full_audit"),
        scopedFileCount: parsed.scopedFileCount || 0,
        elapsedMs: parsed.elapsedMs || 0,
        findingCount: parsed.findingCount,
        deferredCount: parsed.deferredCount || 0,
        preExistingCount: parsed.preExistingCount || 0,
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
const VALIDATION_INFRASTRUCTURE_CODES = new Set(["VALIDATOR_MISSING", "VALIDATOR_EXEC_FAILED"]);

function isValidationInfrastructureFailure(result) {
  const blockingCodes = (result?.findings || [])
    .filter((finding) => finding?.severity === "error")
    .map((finding) => String(finding.code || ""));
  return blockingCodes.length > 0 && blockingCodes.every((code) => VALIDATION_INFRASTRUCTURE_CODES.has(code));
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
      const issues = runtime.payload?.issues || runtime.findings || [];
      const findings = issues.length
        ? issues.map((msg, index) => ({
          severity: "error",
          code: "RUNTIME_CONFIG",
          path: absPath,
          line: index + 1,
          message: typeof msg === "string" ? msg : String(msg.message || msg),
        }))
        : [{
          severity: "error",
          code: "RUNTIME_CONFIG",
          path: absPath,
          line: 0,
          message: runtime.reason || "runtime config check failed",
        }];
      return {
        ok: false,
        skipped: false,
        projectRoot,
        reason: runtime.reason || "runtime config check failed",
        findingCount: findings.length,
        findings,
      };
    }
    return { ok: true, skipped: false, projectRoot, findingCount: 0, findings: [] };
  }
  if (!isSourceLike(absPath)) {
    return null;
  }
  const writeTarget = path.relative(projectRoot, absPath).split(path.sep).join("/");
  const result = await runStaticValidation(projectRoot, {
    timeoutMs: VALIDATE_ON_WRITE_TIMEOUT_MS,
    writeTarget,
  });
  // Validator availability is not evidence that the edited source is invalid. Keep the
  // write, mark validation dirty, and require an explicit full scan before the build gate.
  // Actual source findings still fail closed exactly as before.
  const infrastructureError = isValidationInfrastructureFailure(result);
  if (result && (result.timedOut || infrastructureError)) {
    const skipReason = result.timedOut
      ? "validation skipped (time budget)"
      : "validation unavailable (validator infrastructure failure)";
    markUnvalidated(projectRoot, writeTarget, skipReason);
    return {
      ok: true,
      skipped: true,
      timedOut: Boolean(result.timedOut),
      infrastructureError,
      projectRoot,
      findingCount: 0,
      findings: [],
      advisoryFindings: result.findings || [],
      note: skipReason + "; run static_validate_project before build",
    };
  }
  if (result && result.ok) {
    // Scoped validation success does not clear global dirty state; full static_validate_project does.
  }
  return result;
}

async function validateAfterDelete(absPath, getActiveProject) {
  if (!VALIDATE_ON_WRITE || !isSourceLike(absPath)) {
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
        message: "could not resolve project root for post-delete validation",
      }],
    };
  }
  const deletedTarget = path.relative(projectRoot, absPath).split(path.sep).join("/");
  // A deleted path cannot be the validator's write target: limiting blockers to
  // a file that no longer exists would falsely pass. Run the project audit so
  // missing counterparts and references in surviving files remain observable.
  const result = await runStaticValidation(projectRoot, {
    timeoutMs: VALIDATE_ON_WRITE_TIMEOUT_MS,
  });
  const infrastructureError = isValidationInfrastructureFailure(result);
  if (result && (result.timedOut || infrastructureError)) {
    const skipReason = result.timedOut
      ? "post-delete validation skipped (time budget)"
      : "post-delete validation unavailable (validator infrastructure failure)";
    markUnvalidated(projectRoot, deletedTarget, skipReason);
    return {
      ok: true,
      skipped: true,
      timedOut: Boolean(result.timedOut),
      infrastructureError,
      projectRoot,
      deletedTarget,
      findingCount: 0,
      findings: [],
      advisoryFindings: result.findings || [],
      note: skipReason + "; run static_validate_project before build",
    };
  }
  return result ? { ...result, deletedTarget } : result;
}

function formatValidationResult(result) {
  if (!result) {
    return "";
  }
  const displayFindings = result.skipped
    ? (result.advisoryFindings || result.findings || [])
    : (result.findings || []);
  const lines = [
    "",
    "Static validation:",
    `projectRoot=${result.projectRoot}`,
    `scanMode=${result.scanMode || "full"}`,
    `elapsedMs=${result.elapsedMs || 0}`,
    `findingCount=${result.findingCount}`,
  ];
  if (result.skipped) {
    lines.push(
      `Validation SKIPPED: ${result.note || "validation infrastructure was unavailable or exceeded its time budget."}`,
      "Run static_validate_project before build readiness is claimed."
    );
  }
  for (const finding of displayFindings.slice(0, 20)) {
    lines.push(
      `- [${finding.severity}] ${finding.code} ${finding.path}:${finding.line} ${finding.message}`
    );
  }
  if (displayFindings.length > 20) {
    lines.push(`... ${displayFindings.length - 20} more finding(s)`);
  }
  if (result.skipped) {
    lines.push("Validation status is advisory-only until a full static validation succeeds.");
  } else if (!result.ok) {
    lines.push("Validation FAILED. Fix findings before claiming compile readiness.");
  } else if (result.writeTarget && (result.deferredCount || result.preExistingCount)) {
    lines.push(`Validation passed for this write (no blocking errors on ${result.writeTarget}).`);
    if (result.deferredCount) {
      lines.push(
        `Advisory: ${result.deferredCount} deferred counterpart finding(s) (e.g. add the matching .cpp / _Implementation next).`
      );
    }
    if (result.preExistingCount) {
      lines.push(
        `Note: ${result.preExistingCount} pre-existing error(s) in other files — run static_validate_project before claiming compile readiness.`
      );
    }
  } else {
    lines.push("Validation passed (no static errors).");
  }
  return lines.join("\n");
}

function countOccurrences(haystack, needle) {
  if (!needle) {
    return 0;
  }
  let count = 0;
  let index = 0;
  while ((index = haystack.indexOf(needle, index)) !== -1) {
    count += 1;
    index += needle.length || 1;
  }
  return count;
}

function validateReplaceOccurrences(content, oldText, _newText, options = {}) {
  const expected = options.expectedOccurrences !== undefined
    ? Number(options.expectedOccurrences)
    : undefined;
  const occurrences = countOccurrences(String(content || ""), String(oldText || ""));
  if (expected !== undefined && occurrences !== expected) {
    return `occurrence mismatch: expected ${expected}, found ${occurrences}`;
  }
  return null;
}

module.exports = {
  VALIDATE_ON_WRITE,
  VALIDATE_ON_WRITE_TIMEOUT_MS,
  resolveValidateOnWrite,
  resolveValidateOnWriteTimeoutMs,
  resolveValidationRoot,
  resolvePythonExe,
  validateAfterWrite,
  validateAfterDelete,
  formatValidationResult,
  runStaticValidation,
  isValidationInfrastructureFailure,
  resolveProjectRootForFile,
  blockingErrorsOf,
  clearValidated,
  markUnvalidated,
  countOccurrences,
  validateReplaceOccurrences,
};
