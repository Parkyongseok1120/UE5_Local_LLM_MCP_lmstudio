"use strict";

const path = require("path");
const { spawn } = require("child_process");
const {
  persistProcessLog: persistAutomationLog,
  runBoundedProcess,
} = require("./bounded-process-runner");

async function runAutomationProcess(options) {
  const {
    executable,
    args,
    projectPath,
    timeoutMs,
    logPath,
    hostPlatform,
  } = options;
  const result = await runBoundedProcess({
    start: () => spawn(executable, args, {
      cwd: path.dirname(path.resolve(projectPath)),
      shell: false,
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
      detached: hostPlatform !== "win32",
    }),
    timeoutMs,
    logPath,
    hostPlatform,
    maxOutputBytes: options.maxOutputBytes,
  });
  return { ...result, executable, args };
}

module.exports = { persistAutomationLog, runAutomationProcess };
