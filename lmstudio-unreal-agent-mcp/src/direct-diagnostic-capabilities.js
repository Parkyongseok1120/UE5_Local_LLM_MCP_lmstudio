"use strict";

const path = require("node:path");
const cp = require("node:child_process");

const { resolveBuildPlan, resolveProjectSelection } = require("./unreal-detect.js");
const { runStaticValidation } = require("./direct-static-validation.js");
const { runUnrealBuildFromPlan, killProcessTree } = require("./build-executor.js");
const { runAutomationTests } = require("./automation-executor.js");
const { buildDirectResponse } = require("./direct-build-response.js");
const { parseAllowedCommand } = require("./command-policy.js");
const { success, failure } = require("./direct-response.js");
const {
  clamp,
  envFlag,
  normalizeProjectRoot,
  nowStamp,
  statOrNull,
} = require("./direct-runtime-shared.js");

function createDiagnosticCapabilities(context) {
  const {
    configPath,
    env,
    getActive,
    limits,
    options,
    resolveCallProject,
    resolveToolPath,
    runtimeOwner,
    workspaceRoot,
  } = context;
  const resolveBuild = options.resolveBuildPlan || resolveBuildPlan;
  const spawnCommand = options.spawnCommand || cp.spawn;
  const terminateProcessTree = options.killProcessTree || killProcessTree;

  async function resolvePlan(args) {
    const requestedProject = String(args.project || "").trim();
    const exactProject = requestedProject ? await resolveCallProject(requestedProject) : getActive();
    return resolveBuild(workspaceRoot, configPath, {
      hint: requestedProject ? undefined : args.hint,
      project: exactProject,
      engineRoot: args.engineRoot,
      target: args.target,
      platform: args.platform,
      configuration: args.configuration,
    });
  }

  async function detectProject(args) {
    if (args.resolveBuildDefaults === false) {
      const requestedProject = String(args.project || "").trim();
      const exactProject = requestedProject ? await resolveCallProject(requestedProject) : "";
      const selection = await resolveProjectSelection(workspaceRoot, configPath, {
        hint: requestedProject ? undefined : args.hint,
        project: exactProject,
      });
      return selection.selected
        ? success({ selected: selection.selected, selectionReason: selection.selectionReason, projects: selection.projects })
        : failure(selection.errorCode || "PROJECT_NOT_FOUND", selection.error || "No unambiguous project was found", {
          details: { suggestions: selection.suggestions || [] },
          retryAllowed: true,
        });
    }
    const plan = await resolvePlan(args);
    return plan.ok
      ? success({ selected: plan.selected, selectionReason: plan.selectionReason, build: plan.build })
      : failure(plan.errorCode || "DETECTION_FAILED", plan.error || "Could not resolve Unreal project/engine", {
        details: { suggestions: plan.suggestions || [] },
        retryAllowed: true,
      });
  }

  async function staticValidate(args) {
    const active = await resolveCallProject(args.project);
    if (!active) return failure("ACTIVE_PROJECT_REQUIRED", "Select an active Unreal project or pass an exact project selector.");
    const selectedRoot = path.dirname(path.resolve(active));
    const projectRoot = normalizeProjectRoot(active, args.projectRoot);
    if (path.relative(selectedRoot, path.resolve(projectRoot)) !== "") {
      return failure("PROJECT_ROOT_MISMATCH", "projectRoot must exactly match the directory of the selected project.", {
        retryAllowed: true,
        retryMode: "different_arguments",
      });
    }
    const result = await runStaticValidation(projectRoot, {
      env,
      timeoutMs: clamp(args.timeoutMs, 120000, 1000, 30 * 60 * 1000),
      scopeTargets: Array.isArray(args.scopeTargets) ? args.scopeTargets.slice(0, 64) : [],
    });
    return success({
      advisory: true,
      blocksBuild: false,
      validationOk: result.ok === true,
      projectRoot,
      scanMode: result.scanMode,
      scopeKind: result.scopeKind,
      findingCount: result.findingCount ?? result.findings?.length ?? 0,
      findings: Array.isArray(result.findings) ? result.findings.slice(0, 100) : [],
      skipped: result.skipped === true,
      reason: result.reason || result.note || "",
    });
  }

  async function buildProject(args) {
    if (!envFlag(env, "ALLOW_UNREAL_BUILD", false)) return failure("BUILD_DISABLED", "Unreal build execution is disabled. Start the MCP with ALLOW_UNREAL_BUILD=1.");
    const plan = await resolvePlan(args);
    if (!plan.ok) {
      return failure(plan.errorCode || "BUILD_PLAN_FAILED", plan.error || "Could not resolve an Unreal build plan", {
        details: { suggestions: plan.suggestions || [] },
        retryAllowed: true,
        retryMode: "different_arguments",
      });
    }
    const logPath = path.join(workspaceRoot, ".agent", "logs", `${nowStamp()}-${plan.build.projectName}-build.log`);
    const result = await runUnrealBuildFromPlan({
      workspaceRoot,
      build: plan.build,
      allowEngineFallback: args.allowEngineFallback === true,
      expectedEngineVersion: plan.build.requestedEngineAssociation || plan.build.engineAssociation,
      timeoutMs: clamp(args.timeoutMs, 45 * 60 * 1000, 1000, 4 * 60 * 60 * 1000),
      logPath,
    });
    const command = [result.executable || plan.build.buildTool || "UnrealBuildTool", ...(result.args || [])].join(" ");
    const payload = buildDirectResponse({
      result,
      build: plan.build,
      planResult: plan,
      projectPath: plan.build.projectPath,
      command,
      logPath,
      verbose: args.verboseOutput === true,
      executionMode: runtimeOwner,
    });
    payload.executionMode = runtimeOwner;
    return payload.ok ? success(payload) : { ...payload, ok: false, retry: { allowed: false, mode: "none" } };
  }

  async function runAutomation(args) {
    if (!envFlag(env, "ALLOW_UNREAL_BUILD", false)) return failure("AUTOMATION_DISABLED", "Unreal automation execution is disabled. Start the MCP with ALLOW_UNREAL_BUILD=1.");
    const plan = await resolvePlan(args);
    if (!plan.ok) return failure(plan.errorCode || "AUTOMATION_PLAN_FAILED", plan.error || "Could not resolve project/engine", { retryAllowed: true });
    const logPath = path.join(workspaceRoot, ".agent", "logs", `${nowStamp()}-${plan.build.projectName}-automation.log`);
    const result = await runAutomationTests({
      engineRoot: plan.build.engineRoot,
      projectPath: plan.build.projectPath,
      testFilter: String(args.testFilter || ""),
      timeoutMs: clamp(args.timeoutMs, 30 * 60 * 1000, 1000, 4 * 60 * 60 * 1000),
      logPath,
      scopeTargets: Array.isArray(args.scopeTargets) ? args.scopeTargets : undefined,
    });
    const payload = {
      ...result,
      stdout: args.verboseOutput === true ? String(result.stdout || "").slice(-100000) : undefined,
      stderr: args.verboseOutput === true ? String(result.stderr || "").slice(-100000) : undefined,
      fullLogPath: logPath,
    };
    return result.ok ? success(payload) : failure(result.errorCode || "AUTOMATION_FAILED", result.error || "Automation tests failed", {
      details: {
        exitCode: result.exitCode,
        failedCount: result.failedCount,
        succeededCount: result.succeededCount,
        missingTests: result.missingTests,
        fullLogPath: logPath,
      },
    });
  }

  async function runCommand(args) {
    if (!envFlag(env, "ALLOW_COMMANDS", false)) return failure("COMMANDS_DISABLED", "Command execution is disabled. Start the MCP with ALLOW_COMMANDS=1.");
    const parsed = parseAllowedCommand(String(args.command || ""));
    if (!parsed) return failure("COMMAND_NOT_ALLOWED", "Command is not in the read/build diagnostic allowlist.");
    const cwdResolution = await resolveToolPath(args.cwd || "workspace://", args.project);
    const cwdStat = await statOrNull(cwdResolution.absolutePath);
    if (!cwdStat?.isDirectory()) return failure("INVALID_CWD", "cwd must resolve to an existing contained directory", { retryAllowed: true });
    const timeoutMs = clamp(args.timeoutMs, limits.commandTimeoutMs, 1000, 60 * 60 * 1000);
    const result = await new Promise((resolve) => {
      const child = spawnCommand(parsed.file, parsed.args, {
        cwd: cwdResolution.absolutePath,
        shell: false,
        windowsHide: true,
        stdio: ["ignore", "pipe", "pipe"],
        detached: process.platform !== "win32",
      });
      const stdout = [];
      const stderr = [];
      let capturedBytes = 0;
      let settled = false;
      let termination = null;
      let terminationPromise = null;
      let killFallback = null;
      let timer = null;
      const finish = (exitCode, error = "", timedOut = false, outputLimited = false) => {
        if (settled) return;
        settled = true;
        if (timer) clearTimeout(timer);
        if (killFallback) clearTimeout(killFallback);
        resolve({
          exitCode,
          error,
          timedOut,
          outputLimited,
          stdout: Buffer.concat(stdout).toString("utf8"),
          stderr: Buffer.concat(stderr).toString("utf8"),
        });
      };
      const terminate = async (kind, message) => {
        if (termination || settled) return;
        termination = { kind, message };
        terminationPromise = Promise.resolve().then(() => terminateProcessTree(child.pid, process.platform));
        try {
          await terminationPromise;
        } catch {
          // The output/timeout failure must still settle even if OS tree cleanup
          // itself reports an error. The bounded fallback prevents a hung call.
          terminationPromise = Promise.resolve();
        }
        killFallback = setTimeout(() => finish(1, message, kind === "timeout", kind === "output"), 5000);
      };
      const capture = (collection, chunk) => {
        const buffer = Buffer.from(chunk);
        const remaining = Math.max(0, limits.maxCommandOutputBytes - capturedBytes);
        if (remaining > 0) collection.push(buffer.subarray(0, remaining));
        capturedBytes += Math.min(buffer.length, remaining);
        if (buffer.length > remaining) void terminate("output", `Command output exceeded ${limits.maxCommandOutputBytes} bytes`);
      };
      child.stdout.on("data", (chunk) => capture(stdout, chunk));
      child.stderr.on("data", (chunk) => capture(stderr, chunk));
      child.on("close", (code) => {
        if (termination) {
          void Promise.resolve(terminationPromise)
            .catch(() => undefined)
            .then(() => finish(
              1,
              termination.message,
              termination.kind === "timeout",
              termination.kind === "output",
            ));
        } else {
          finish(code ?? 1);
        }
      });
      child.on("error", (error) => finish(1, String(error.message || error)));
      timer = setTimeout(() => {
        void terminate("timeout", `Command timed out after ${timeoutMs}ms`);
      }, timeoutMs);
    });
    const commandOk = result.exitCode === 0 && !result.error;
    const outputChars = commandOk
      ? Math.max(1024, Math.floor((limits.maxResponseChars - 4096) / 16))
      : 768;
    const payload = {
      ok: commandOk,
      exitCode: result.exitCode,
      timedOut: result.timedOut,
      outputLimited: result.outputLimited,
      stdout: result.stdout.slice(-outputChars),
      stderr: result.stderr.slice(-outputChars),
      error: result.error,
    };
    return payload.ok ? success(payload) : failure(
      result.timedOut ? "COMMAND_TIMEOUT" : result.outputLimited ? "COMMAND_OUTPUT_LIMIT" : "COMMAND_FAILED",
      result.error || `Command exited ${result.exitCode}`,
      { details: payload },
    );
  }

  return {
    build_unreal_project: buildProject,
    detect_unreal_project: detectProject,
    run_command: runCommand,
    run_unreal_automation_tests: runAutomation,
    static_validate_project: staticValidate,
  };
}

module.exports = { createDiagnosticCapabilities };
