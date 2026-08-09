"use strict";

const fs = require("fs");
const fsp = fs.promises;
const path = require("path");
const { spawn } = require("child_process");
const {
  assertEngineContainment,
  decodeBuildOutput,
  killProcessTree,
} = require("./build-executor");

// Unreal projects commonly wrap the test name in TEXT("..."). Accept both
// forms and arbitrary whitespace/newlines so discovery cannot silently skip
// the project's real Automation suite.
const AUTOMATION_NAME_RE = /IMPLEMENT_(?:SIMPLE|COMPLEX)_AUTOMATION_TEST\s*\([^,]+,\s*(?:TEXT\s*\(\s*)?"([^"]+)"\s*\)?/g;
const SKIP_DIRS = new Set([".git", "Binaries", "DerivedDataCache", "Intermediate", "Saved"]);

function resolveEditorCmdPaths(engineRoot, hostPlatform = process.platform) {
  const folder = hostPlatform === "win32" ? "Win64" : hostPlatform === "darwin" ? "Mac" : "Linux";
  const binary = hostPlatform === "win32" ? "UnrealEditor-Cmd.exe" : "UnrealEditor-Cmd";
  return [path.join(engineRoot, "Engine", "Binaries", folder, binary)];
}

function resolveEditorCmd(engineRoot, hostPlatform = process.platform) {
  const candidates = resolveEditorCmdPaths(engineRoot, hostPlatform);
  const selected = candidates.find((candidate) => fs.existsSync(candidate));
  if (!selected) {
    const error = new Error(`UnrealEditor-Cmd was not found under engine root: ${engineRoot}`);
    error.errorCode = "UNREAL_EDITOR_CMD_NOT_FOUND";
    throw error;
  }
  assertEngineContainment(selected, engineRoot, hostPlatform);
  return selected;
}

function discoverAutomationTests(projectRoot, options = {}) {
  const maxFiles = Math.max(1, Math.min(5000, Number(options.maxFiles || 2000)));
  const roots = [path.join(projectRoot, "Source"), path.join(projectRoot, "Plugins")]
    .filter((candidate) => fs.existsSync(candidate));
  const names = [];
  let inspectedFileCount = 0;
  const pending = [...roots];
  while (pending.length && inspectedFileCount < maxFiles) {
    const current = pending.pop();
    let entries = [];
    try { entries = fs.readdirSync(current, { withFileTypes: true }); } catch { continue; }
    for (const entry of entries) {
      if (entry.isDirectory()) {
        if (!SKIP_DIRS.has(entry.name)) pending.push(path.join(current, entry.name));
        continue;
      }
      if (!entry.isFile() || !/\.(?:cpp|cc|cxx|h|hpp)$/i.test(entry.name)) continue;
      inspectedFileCount += 1;
      let text = "";
      try { text = fs.readFileSync(path.join(current, entry.name), "utf8"); } catch { continue; }
      AUTOMATION_NAME_RE.lastIndex = 0;
      for (const match of text.matchAll(AUTOMATION_NAME_RE)) {
        if (match[1] && !names.includes(match[1])) names.push(match[1]);
      }
      if (inspectedFileCount >= maxFiles) break;
    }
  }
  const rootsFound = [...new Set(names.map((name) => name.split(".")[0]).filter(Boolean))];
  return {
    names,
    count: names.length,
    inspectedFileCount,
    truncated: Boolean(pending.length),
    suggestedFilter: rootsFound.length === 1 ? rootsFound[0] : "",
  };
}

function automationArgs(projectPath, testFilter) {
  const filter = String(testFilter || "").trim();
  if (!filter) throw new Error("testFilter is required when Automation tests are declared");
  return [
    path.resolve(projectPath),
    "-unattended",
    "-nop4",
    "-NullRHI",
    "-nosplash",
    "-stdout",
    "-FullStdOutLogOutput",
    `-ExecCmds=Automation RunTests ${filter};Quit`,
    "-TestExit=Automation Test Queue Empty",
  ];
}

function parseAutomationOutput(output, exitCode = 0) {
  const text = String(output || "");
  // Current Unreal versions report each result through AutomationController as
  // `Test Completed. Result={Success}`. Keep the older command-line wording as
  // a fallback for engines that still emit `Automation Test Succeeded`.
  const completedResults = [
    ...text.matchAll(/Test Completed\.\s*Result=\{([^}]+)\}/gi),
  ].map((match) => String(match[1] || "").trim().toLowerCase());
  const succeeded = completedResults.length > 0
    ? completedResults.filter((result) => result === "success" || result === "succeeded").length
    : (text.match(/Automation Test Succeeded/gi) || []).length;
  const failed = completedResults.length > 0
    ? completedResults.length - succeeded
    : (text.match(/Automation Test Failed/gi) || []).length;
  const terminalExitMatch = text.match(/TEST COMPLETE\.\s*EXIT CODE:\s*(-?\d+)/i);
  const terminalExitCode = terminalExitMatch ? Number(terminalExitMatch[1]) : 0;
  const queueEmpty = /Automation Test Queue Empty/i.test(text) || Boolean(terminalExitMatch);
  if (failed > 0 || Number(exitCode) !== 0 || terminalExitCode !== 0) {
    return {
      ok: false,
      errorCode: failed > 0 ? "AUTOMATION_TEST_FAILED" : "AUTOMATION_PROCESS_FAILED",
      succeededCount: succeeded,
      failedCount: failed,
      queueEmpty,
    };
  }
  if (succeeded === 0) {
    return {
      ok: false,
      errorCode: "NO_AUTOMATION_TESTS_EXECUTED",
      succeededCount: 0,
      failedCount: 0,
      queueEmpty,
    };
  }
  return {
    ok: true,
    errorCode: "",
    succeededCount: succeeded,
    failedCount: 0,
    queueEmpty,
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
  } = options;
  if (!engineRoot || !projectPath || !testFilter) {
    return { ok: false, errorCode: "INVALID_AUTOMATION_PLAN", error: "engineRoot, projectPath, and testFilter are required" };
  }
  let executable;
  try {
    executable = resolveEditorCmd(path.resolve(engineRoot), hostPlatform);
  } catch (error) {
    return { ok: false, errorCode: error.errorCode || "UNREAL_EDITOR_CMD_NOT_FOUND", error: String(error.message || error) };
  }
  const args = automationArgs(projectPath, testFilter);
  return await new Promise((resolve) => {
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
    const finish = async (exitCode, timedOut = false, spawnError = "") => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      const stdout = decodeBuildOutput(stdoutChunks, { hostPlatform });
      const stderr = decodeBuildOutput(stderrChunks, { hostPlatform });
      const fullOutput = `${stdout}\n${stderr}`.trim();
      if (logPath) {
        await fsp.mkdir(path.dirname(logPath), { recursive: true });
        await fsp.writeFile(logPath, fullOutput, "utf8");
      }
      const parsed = timedOut
        ? { ok: false, errorCode: "AUTOMATION_TIMEOUT", succeededCount: 0, failedCount: 0, queueEmpty: false }
        : spawnError
          ? { ok: false, errorCode: "AUTOMATION_PROCESS_FAILED", succeededCount: 0, failedCount: 0, queueEmpty: false }
          : parseAutomationOutput(fullOutput, exitCode);
      resolve({
        ...parsed,
        exitCode: exitCode ?? 1,
        timedOut,
        error: spawnError || (timedOut ? `Automation timed out after ${timeoutMs}ms` : ""),
        stdout,
        stderr,
        executable,
        args,
        fullLogPath: logPath || null,
        testFilter,
      });
    };
    child.stdout.on("data", (chunk) => stdoutChunks.push(Buffer.from(chunk)));
    child.stderr.on("data", (chunk) => stderrChunks.push(Buffer.from(chunk)));
    const timer = setTimeout(async () => {
      await killProcessTree(child.pid, hostPlatform);
      await finish(1, true);
    }, timeoutMs);
    child.on("close", (code) => { finish(code ?? 1); });
    child.on("error", (error) => { finish(1, false, String(error.message || error)); });
  });
}

module.exports = {
  resolveEditorCmdPaths,
  resolveEditorCmd,
  discoverAutomationTests,
  automationArgs,
  parseAutomationOutput,
  runAutomationTests,
};
