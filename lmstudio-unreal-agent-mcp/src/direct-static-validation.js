"use strict";

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const cp = require("node:child_process");
const { runBoundedProcess } = require("./bounded-process-runner");
const { resolvePythonExe } = require("./python-executable");


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
  const result = await runBoundedProcess({
    start: () => cp.spawn(resolvePythonExe(env), args, {
      cwd: validationRoot, windowsHide: true, shell: false,
      stdio: ["ignore", "pipe", "pipe"], detached: process.platform !== "win32",
    }),
    timeoutMs, signal: options.signal, shutdownTimeoutMs: options.shutdownTimeoutMs,
    ...(options.terminate ? { terminate: options.terminate } : {}),
    maxOutputBytes: 4 * 1024 * 1024, outputLimitBytes: 4 * 1024 * 1024,
    decode: chunks => Buffer.concat(chunks).toString("utf8"),
  });
  const lifecycle = { cancelled: result.cancelled, processStarted: result.processStarted,
    processExited: result.processExited, terminationStatus: result.terminationStatus };
  if (!result.cancelled && !result.timedOut && !result.outputLimited && !result.spawnError) {
    try { return { ...validationPayload(JSON.parse(result.stdout), projectRoot, writeTarget, scopeTargets), ...lifecycle }; }
    catch { /* Invalid output remains an infrastructure failure, never clean validation. */ }
  }
  const code = result.cancelled ? "VALIDATOR_CANCELLED" : result.timedOut ? "VALIDATOR_TIMEOUT"
    : result.outputLimited ? "VALIDATOR_OUTPUT_LIMIT" : "VALIDATOR_EXEC_FAILED";
  const reason = result.cancelled ? "Validation cancellation requested."
    : result.timedOut ? `validation exceeded time budget (${timeoutMs}ms)`
      : result.spawnError || result.stderr || "Validator output was incomplete or invalid.";
  return { ok: false, skipped: false, ...lifecycle, timedOut: result.timedOut,
    projectRoot, reason, findingCount: 1,
    findings: [{ severity: result.timedOut || result.cancelled ? "warning" : "error", code,
      path: projectRoot, line: 0, message: reason }] };

}

module.exports = {
  blockingErrorsOf,
  resolveStaticValidationTimeoutMs,
  resolveValidationRoot,
  runStaticValidation,
};
