"use strict";

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const cp = require("node:child_process");
const { promisify } = require("node:util");
const { resolvePythonExe } = require("./python-executable");

const execFile = promisify(cp.execFile);

function resolveValidationRoot(options = {}) {
  const env = options.env || process.env;
  const configured = Object.prototype.hasOwnProperty.call(options, "envRoot")
    ? String(options.envRoot || "").trim()
    : String(env.UNREAL58_ROOT || "").trim();
  if (configured) return path.resolve(configured);

  const repositoryRoot = path.resolve(options.repositoryRoot || path.join(__dirname, "..", ".."));
  if (fs.existsSync(path.join(repositoryRoot, "scripts", "validate_project_sources.py"))) {
    return repositoryRoot;
  }
  const homeDir = Object.prototype.hasOwnProperty.call(options, "homeDir")
    ? String(options.homeDir || "")
    : os.homedir();
  return path.resolve(homeDir, ".lmstudio", "Unreal58-RAG");
}

function resolveStaticValidationTimeoutMs(env = process.env) {
  const raw = Number(env.STATIC_VALIDATION_TIMEOUT_MS);
  return Number.isFinite(raw) && raw > 0
    ? Math.max(25, Math.min(Math.trunc(raw), 10 * 60 * 1000))
    : 120000;
}

function blockingErrorsOf(payload) {
  if (payload && Object.prototype.hasOwnProperty.call(payload, "hasBlockingErrors")) {
    return Boolean(payload.hasBlockingErrors);
  }
  return Boolean(payload && payload.hasErrors);
}

function validationPayload(payload, projectRoot, writeTarget, scopeTargets) {
  return {
    ok: !blockingErrorsOf(payload),
    skipped: false,
    projectRoot,
    writeTarget,
    scopeTargets: payload.scopeTargets || scopeTargets,
    scanMode: payload.scanMode || "full",
    scopeKind: payload.scopeKind || (scopeTargets.length ? "scoped" : "full_audit"),
    scopedFileCount: payload.scopedFileCount || 0,
    elapsedMs: payload.elapsedMs || 0,
    findingCount: payload.findingCount,
    deferredCount: payload.deferredCount || 0,
    preExistingCount: payload.preExistingCount || 0,
    findings: payload.findings || [],
  };
}

async function runStaticValidation(projectRoot, options = {}) {
  const env = options.env || process.env;
  const validationRoot = resolveValidationRoot({ ...options, env });
  const timeoutMs = Number.isFinite(options.timeoutMs) && options.timeoutMs > 0
    ? options.timeoutMs
    : resolveStaticValidationTimeoutMs(env);
  const writeTarget = options.writeTarget || null;
  const scopeTargets = [...new Set(
    (Array.isArray(options.scopeTargets) ? options.scopeTargets : [])
      .map((item) => String(item || "").trim().replace(/\\/g, "/"))
      .filter(Boolean),
  )];
  const script = path.join(validationRoot, "scripts", "validate_project_sources.py");
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

  const args = [script, "--project-root", projectRoot, "--json"];
  if (writeTarget) args.push("--write-target", writeTarget);
  for (const scopeTarget of scopeTargets) args.push("--scope-target", scopeTarget);
  try {
    const { stdout } = await execFile(resolvePythonExe(env), args, {
      cwd: validationRoot,
      timeout: timeoutMs,
      maxBuffer: 4 * 1024 * 1024,
    });
    return validationPayload(JSON.parse(stdout), projectRoot, writeTarget, scopeTargets);
  } catch (error) {
    const stderr = error.stderr ? String(error.stderr) : "";
    const stdout = error.stdout ? String(error.stdout) : "";
    try {
      if (stdout) {
        return validationPayload(JSON.parse(stdout), projectRoot, writeTarget, scopeTargets);
      }
    } catch {
      // Fall through to a bounded infrastructure result.
    }
    if (error.killed === true) {
      const reason = `validation exceeded time budget (${timeoutMs}ms)`;
      return {
        ok: false,
        skipped: false,
        timedOut: true,
        projectRoot,
        reason,
        findingCount: 1,
        findings: [{
          severity: "warning",
          code: "VALIDATOR_TIMEOUT",
          path: projectRoot,
          line: 0,
          message: reason,
        }],
      };
    }
    const reason = `${error.message}${stderr ? `\n${stderr}` : ""}`;
    return {
      ok: false,
      skipped: false,
      reason,
      findingCount: 1,
      findings: [{
        severity: "error",
        code: "VALIDATOR_EXEC_FAILED",
        path: projectRoot,
        line: 0,
        message: reason,
      }],
    };
  }
}

module.exports = {
  blockingErrorsOf,
  resolveStaticValidationTimeoutMs,
  resolveValidationRoot,
  runStaticValidation,
};
