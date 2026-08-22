"use strict";

const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");
const { decodeBuildOutput, killProcessTree } = require("./build-executor");

async function persistAutomationLog(logPath, output) {
  if (!logPath) return "";
  try {
    await fs.promises.mkdir(path.dirname(logPath), { recursive: true });
    await fs.promises.writeFile(logPath, output, "utf8");
    return "";
  } catch (error) {
    return String(error?.message || error);
  }
}

function runAutomationProcess(options) {
  const {
    executable,
    args,
    projectPath,
    timeoutMs,
    logPath,
    hostPlatform,
  } = options;
  return new Promise((resolve) => {
    const child = spawn(executable, args, {
      cwd: path.dirname(path.resolve(projectPath)),
      shell: false,
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
      detached: hostPlatform !== "win32",
    });
    const stdoutChunks = [];
    const stderrChunks = [];
    let settled = false;
    let timer;
    const finish = async (exitCode, timedOut = false, spawnError = "") => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      let stdout = "";
      let stderr = "";
      let outputDecodeError = "";
      try {
        stdout = decodeBuildOutput(stdoutChunks, { hostPlatform });
        stderr = decodeBuildOutput(stderrChunks, { hostPlatform });
      } catch (error) {
        outputDecodeError = String(error?.message || error);
      }
      const fullOutput = `${stdout}\n${stderr}`.trim();
      const logPersistenceError = await persistAutomationLog(logPath, fullOutput);
      resolve({
        exitCode: exitCode ?? 1,
        timedOut,
        spawnError,
        outputDecodeError,
        logPersistenceError,
        stdout,
        stderr,
        executable,
        args,
        fullLogPath: logPath || null,
      });
    };
    child.stdout.on("data", (chunk) => stdoutChunks.push(Buffer.from(chunk)));
    child.stderr.on("data", (chunk) => stderrChunks.push(Buffer.from(chunk)));
    timer = setTimeout(() => {
      Promise.resolve(killProcessTree(child.pid, hostPlatform))
        .catch(() => undefined)
        .then(() => finish(1, true));
    }, timeoutMs);
    child.on("close", (code) => { void finish(code ?? 1); });
    child.on("error", (error) => { void finish(1, false, String(error.message || error)); });
  });
}

module.exports = { persistAutomationLog, runAutomationProcess };
