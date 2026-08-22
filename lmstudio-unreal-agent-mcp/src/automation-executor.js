"use strict";

const path = require("path");
const {
  MAX_AUTOMATION_FILTERS,
  automationArgs,
  automationFilterMatches,
  resolveEditorCmd,
  resolveEditorCmdPaths,
  validateAutomationFilter,
} = require("./automation-command-contract");
const { parseAutomationOutput } = require("./automation-output-parser");
const { runAutomationProcess } = require("./automation-process-runner");
const {
  discoverAutomationTests,
  moduleRootForTarget,
  resolveAutomationScopeRoots,
} = require("./automation-source-discovery");

function processFailure(processResult, timeoutMs) {
  if (processResult.timedOut) {
    return {
      parsed: {
        ok: false,
        errorCode: "AUTOMATION_TIMEOUT",
        succeededCount: 0,
        failedCount: 0,
        queueEmpty: false,
      },
      error: `Automation timed out after ${timeoutMs}ms`,
    };
  }
  const failures = [
    [processResult.spawnError, "AUTOMATION_PROCESS_FAILED"],
    [processResult.outputDecodeError, "AUTOMATION_OUTPUT_DECODE_FAILED"],
    [processResult.logPersistenceError, "AUTOMATION_LOG_WRITE_FAILED"],
  ];
  const failure = failures.find(([message]) => Boolean(message));
  if (!failure) return null;
  return {
    parsed: {
      ok: false,
      errorCode: failure[1],
      succeededCount: 0,
      failedCount: 0,
      queueEmpty: false,
    },
    error: failure[0],
  };
}

async function runAutomationTests(options = {}) {
  const {
    engineRoot,
    projectPath,
    testFilter,
    timeoutMs = 30 * 60 * 1000,
    logPath = "",
    hostPlatform = process.platform,
    scopeTargets,
  } = options;
  if (!engineRoot || !projectPath || !testFilter) {
    return {
      ok: false,
      errorCode: "INVALID_AUTOMATION_PLAN",
      error: "engineRoot, projectPath, and testFilter are required",
    };
  }
  const discovery = discoverAutomationTests(path.dirname(path.resolve(projectPath)), { scopeTargets });
  if (discovery.truncated) {
    const unmapped = discovery.unmappedScopeTargets.length > 0;
    return {
      ok: false,
      errorCode: unmapped ? "AUTOMATION_SCOPE_UNMAPPED" : "AUTOMATION_DISCOVERY_TRUNCATED",
      error: unmapped
        ? "One or more Automation scope targets could not be mapped to an existing source module"
        : "Automation declaration discovery did not complete",
      testFilter: String(testFilter || "").trim(),
      expectedTests: [],
      automationCoverage: discovery,
    };
  }
  const filterValidation = validateAutomationFilter(testFilter, discovery.names);
  if (!filterValidation.ok) {
    return {
      ok: false,
      errorCode: filterValidation.errorCode,
      error: filterValidation.error,
      testFilter: filterValidation.filter,
      expectedTests: [],
      automationCoverage: discovery,
    };
  }
  const expectedTests = filterValidation.expectedTests;
  let executable;
  try {
    executable = resolveEditorCmd(path.resolve(engineRoot), hostPlatform);
  } catch (error) {
    return {
      ok: false,
      errorCode: error.errorCode || "UNREAL_EDITOR_CMD_NOT_FOUND",
      error: String(error.message || error),
      testFilter: filterValidation.filter,
      expectedTests,
      automationCoverage: discovery,
    };
  }
  const args = automationArgs(projectPath, filterValidation.filter, {
    discoveredNames: discovery.names,
  });
  const processResult = await runAutomationProcess({
    executable,
    args,
    projectPath,
    timeoutMs,
    logPath,
    hostPlatform,
  });
  const failure = processFailure(processResult, timeoutMs);
  const fullOutput = `${processResult.stdout}\n${processResult.stderr}`.trim();
  const parsed = failure?.parsed
    || parseAutomationOutput(fullOutput, processResult.exitCode, { expectedTests });
  return {
    ...parsed,
    exitCode: processResult.exitCode,
    timedOut: processResult.timedOut,
    error: failure?.error || "",
    outputDecodeError: processResult.outputDecodeError,
    logPersistenceError: processResult.logPersistenceError,
    stdout: processResult.stdout,
    stderr: processResult.stderr,
    executable: processResult.executable,
    args: processResult.args,
    fullLogPath: processResult.fullLogPath,
    testFilter: filterValidation.filter,
    expectedTests,
    automationCoverage: discovery,
  };
}

module.exports = {
  MAX_AUTOMATION_FILTERS,
  resolveEditorCmdPaths,
  resolveEditorCmd,
  resolveAutomationScopeRoots,
  moduleRootForTarget,
  discoverAutomationTests,
  automationFilterMatches,
  validateAutomationFilter,
  automationArgs,
  parseAutomationOutput,
  runAutomationTests,
};
