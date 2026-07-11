"use strict";

const { spawn } = require("child_process");
const fs = require("fs");
const fsp = fs.promises;
const path = require("path");

const DEFAULT_EXPECTED_ENGINE = "5.8";

function normalizeVersion(value) {
  const match = String(value || "").match(/(\d+\.\d+)/);
  return match ? match[1] : "";
}

function parseEngineVersionFromRoot(engineRoot) {
  const folder = path.basename(String(engineRoot || ""));
  const fromFolder = folder.match(/^UE_(\d+(?:\.\d+)?)$/i);
  if (fromFolder) return normalizeVersion(fromFolder[1]);
  return normalizeVersion(String(engineRoot || ""));
}

function resolveUbtPath(engineRoot) {
  return path.join(engineRoot, "Engine", "Binaries", "DotNET", "UnrealBuildTool", "UnrealBuildTool.exe");
}

async function resolveBuildExecutable(engineRoot) {
  const buildBat = path.join(engineRoot, "Engine", "Build", "BatchFiles", "Build.bat");
  if (fs.existsSync(buildBat)) {
    return { executable: buildBat, kind: "build_bat" };
  }
  const ubt = resolveUbtPath(engineRoot);
  if (fs.existsSync(ubt)) {
    return { executable: ubt, kind: "ubt" };
  }
  throw new Error(`No Build.bat or UnrealBuildTool.exe under engine root: ${engineRoot}`);
}

function assertEngineContainment(executable, engineRoot) {
  const execResolved = path.resolve(executable);
  const rootResolved = path.resolve(engineRoot);
  if (!execResolved.toLowerCase().startsWith(rootResolved.toLowerCase() + path.sep)) {
    throw new Error(`Build executable outside engine root: ${executable}`);
  }
}

function buildArgs({ kind, target, platform, configuration, projectPath }) {
  if (kind === "build_bat") {
    return [target, platform, configuration, `-Project=${projectPath}`, "-WaitMutex", "-NoHotReloadFromIDE"];
  }
  return [target, platform, configuration, `-Project=${projectPath}`, "-NoUBA", "-MaxParallelActions=4"];
}

function killProcessTree(pid) {
  if (process.platform === "win32") {
    spawn("taskkill", ["/PID", String(pid), "/T", "/F"], { stdio: "ignore" });
    return;
  }
  try {
    process.kill(-pid, "SIGKILL");
  } catch {
    try {
      process.kill(pid, "SIGKILL");
    } catch {
      // ignore
    }
  }
}

async function runUnrealBuildFromPlan(options = {}) {
  const {
    workspaceRoot,
    build,
    allowEngineFallback = false,
    expectedEngineVersion = DEFAULT_EXPECTED_ENGINE,
    timeoutMs = 45 * 60 * 1000,
    logPath = "",
  } = options;

  if (!build?.engineRoot || !build?.projectPath || !build?.target) {
    return { ok: false, error: "invalid build plan", commandSucceeded: false };
  }

  const resolvedEngineRoot = path.resolve(build.engineRoot);
  const resolvedVersion = normalizeVersion(
    build.engineAssociation || parseEngineVersionFromRoot(resolvedEngineRoot)
  );
  const expectedVersion = normalizeVersion(expectedEngineVersion);
  const engineMismatch = Boolean(expectedVersion && resolvedVersion && resolvedVersion !== expectedVersion);
  if (engineMismatch && !allowEngineFallback) {
    return {
      ok: false,
      commandSucceeded: false,
      engineMismatch: true,
      resolvedEngineVersion: resolvedVersion,
      resolvedEngineRoot,
      resolvedUbtPath: resolveUbtPath(resolvedEngineRoot),
      error: `Engine version mismatch: expected ${expectedVersion}, resolved ${resolvedVersion}`,
      errorCode: "ENGINE_VERSION_MISMATCH",
    };
  }

  const { executable, kind } = await resolveBuildExecutable(resolvedEngineRoot);
  assertEngineContainment(executable, resolvedEngineRoot);
  const args = buildArgs({
    kind,
    target: build.target,
    platform: build.platform || "Win64",
    configuration: build.configuration || "Development",
    projectPath: build.projectPath,
  });

  return await new Promise((resolve) => {
    const child = spawn(executable, args, {
      cwd: workspaceRoot,
      shell: false,
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk.toString("utf8"); });
    child.stderr.on("data", (chunk) => { stderr += chunk.toString("utf8"); });
    const timer = setTimeout(() => killProcessTree(child.pid), timeoutMs);
    child.on("close", async (code) => {
      clearTimeout(timer);
      const fullLog = `${stdout}\n${stderr}`.trim();
      let savedLogPath = logPath;
      if (savedLogPath) {
        await fsp.mkdir(path.dirname(savedLogPath), { recursive: true });
        await fsp.writeFile(savedLogPath, fullLog, "utf8");
      }
      resolve({
        ok: code === 0,
        commandSucceeded: code === 0,
        exitCode: code ?? 1,
        resolvedEngineVersion: resolvedVersion,
        resolvedEngineRoot,
        resolvedUbtPath: resolveUbtPath(resolvedEngineRoot),
        engineMismatch,
        allowEngineFallback: Boolean(allowEngineFallback),
        stdout,
        stderr,
        fullLogPath: savedLogPath || null,
        executable,
        args,
      });
    });
    child.on("error", (err) => {
      clearTimeout(timer);
      resolve({
        ok: false,
        commandSucceeded: false,
        error: String(err.message || err),
        resolvedEngineVersion: resolvedVersion,
        resolvedEngineRoot,
        resolvedUbtPath: resolveUbtPath(resolvedEngineRoot),
      });
    });
  });
}

module.exports = {
  runUnrealBuildFromPlan,
  DEFAULT_EXPECTED_ENGINE,
  normalizeVersion,
  parseEngineVersionFromRoot,
  assertEngineContainment,
  resolveUbtPath,
};
