"use strict";

const { spawn } = require("child_process");
const fs = require("fs");
const fsp = fs.promises;
const path = require("path");
const { absolutePathIsWithin } = require("./filesystem-path-identity");
const { runBoundedProcess } = require("./bounded-process-runner");
const {
  decodeProcessOutput: decodeBuildOutput,
  localeOutputEncoding,
  sanitizeBrokenCompilerLocalization,
} = require("./process-output-decoder");
const { killProcessTree } = require("./process-tree-termination");

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

async function detectEngineVersion(engineRoot) {
  const buildVersionPath = path.join(engineRoot, "Engine", "Build", "Build.version");
  try {
    const parsed = JSON.parse(await fsp.readFile(buildVersionPath, "utf8"));
    const major = Number(parsed.MajorVersion);
    const minor = Number(parsed.MinorVersion);
    if (Number.isInteger(major) && Number.isInteger(minor)) {
      return `${major}.${minor}`;
    }
  } catch {
    // Installed/source builds can omit Build.version. The root name is a useful fallback.
  }
  return parseEngineVersionFromRoot(engineRoot);
}

function resolveUbtPaths(engineRoot, hostPlatform = process.platform) {
  const directory = path.join(engineRoot, "Engine", "Binaries", "DotNET", "UnrealBuildTool");
  const executable = path.join(directory, "UnrealBuildTool.exe");
  const assembly = path.join(directory, "UnrealBuildTool.dll");
  return hostPlatform === "win32" ? [executable, assembly] : [assembly, executable];
}

function resolveUbtPath(engineRoot, hostPlatform = process.platform) {
  const candidates = resolveUbtPaths(engineRoot, hostPlatform);
  return candidates.find((candidate) => fs.existsSync(candidate)) || candidates[0];
}

function resolveBuildScriptPaths(engineRoot, hostPlatform = process.platform) {
  const batchRoot = path.join(engineRoot, "Engine", "Build", "BatchFiles");
  if (hostPlatform === "win32") {
    return [{ executable: path.join(batchRoot, "Build.bat"), kind: "build_bat" }];
  }
  const hostFolder = hostPlatform === "darwin" ? "Mac" : "Linux";
  return [
    { executable: path.join(batchRoot, hostFolder, "Build.sh"), kind: "build_sh" },
    { executable: path.join(batchRoot, "Build.sh"), kind: "build_sh" },
  ];
}

async function resolveBuildExecutable(engineRoot, hostPlatform = process.platform) {
  const scripts = resolveBuildScriptPaths(engineRoot, hostPlatform);
  const ubtCandidates = resolveUbtPaths(engineRoot, hostPlatform).map((executable) => ({
    executable,
    kind: executable.toLowerCase().endsWith(".dll") ? "ubt_dotnet" : "ubt",
  }));
  const candidates = hostPlatform === "win32"
    ? [...ubtCandidates, ...scripts]
    : [...scripts, ...ubtCandidates];
  const selected = candidates.find((candidate) => fs.existsSync(candidate.executable));
  if (selected) {
    return selected;
  }
  throw new Error(`No host build script or UnrealBuildTool assembly under engine root: ${engineRoot}`);
}

function assertEngineContainment(executable, engineRoot, hostPlatform = process.platform) {
  if (!absolutePathIsWithin(executable, engineRoot, hostPlatform)) {
    throw new Error(`Build executable outside engine root: ${executable}`);
  }
}

function buildArgs({ kind, target, platform, configuration, projectPath }) {
  if (kind === "build_bat" || kind === "build_sh") {
    return [target, platform, configuration, `-Project=${projectPath}`, "-WaitMutex", "-NoHotReloadFromIDE"];
  }
  return [target, platform, configuration, `-Project=${projectPath}`, "-NoUBA", "-MaxParallelActions=4"];
}

function buildProcessEnv(baseEnv = process.env) {
  const env = { ...baseEnv };
  // Prefer stable ASCII diagnostics from MSVC/UBT without changing the user's
  // global environment. The decoder below still handles localized output.
  if (!env.VSLANG) env.VSLANG = "1033";
  if (!env.DOTNET_CLI_UI_LANGUAGE) env.DOTNET_CLI_UI_LANGUAGE = "en-US";
  return env;
}

function buildWindowsBatchSpawnSpec(executable, args) {
  const values = [executable, ...args].map((value) => String(value));
  for (const value of values) {
    // Build.bat forwards %* through several CALL boundaries and enables delayed
    // expansion. These characters cannot be represented faithfully through that
    // contract, so fail closed instead of allowing expansion or command injection.
    if (/[\0\r\n"%!^]/.test(value)) {
      throw new Error("Build.bat path or argument contains an unsafe cmd.exe expansion character");
    }
  }

  const variableNames = values.map((_, index) => `MCP_UNREAL_BUILD_ARG_${index}`);
  const env = Object.fromEntries(variableNames.map((name, index) => [name, values[index]]));
  const quotedReferences = variableNames.map((name) => `"%${name}%"`).join(" ");

  // /S strips the outer quote pair. Every actual value is supplied via a private
  // environment variable and remains quoted, preserving whitespace, Unicode, and
  // quoted cmd metacharacters without concatenating untrusted text into the command.
  const commandLine = `"${quotedReferences}"`;
  return {
    command: "cmd.exe",
    args: ["/d", "/s", "/e:on", "/v:off", "/c", commandLine],
    env,
    windowsVerbatimArguments: true,
  };
}

function buildSpawnSpec({ executable, kind, args }) {
  if (kind === "build_bat") {
    return buildWindowsBatchSpawnSpec(executable, args);
  }
  if (kind === "build_sh") {
    // Unreal's Linux Build.sh is a Bash script (and UE shell helpers use
    // `source`/`[[ ... ]]`).  Invoking it through Ubuntu's /bin/sh (dash)
    // bypasses the shebang and fails before UnrealBuildTool starts.
    return { command: "/bin/bash", args: [executable, ...args] };
  }
  if (kind === "ubt_dotnet") {
    return { command: "dotnet", args: [executable, ...args] };
  }
  return { command: executable, args };
}

function defaultBuildPlatform(hostPlatform = process.platform) {
  if (hostPlatform === "win32") return "Win64";
  if (hostPlatform === "darwin") return "Mac";
  return "Linux";
}

function spawnBuildProcess({ executable, kind, args, workspaceRoot, hostPlatform = process.platform }) {
  const spec = buildSpawnSpec({ executable, kind, args });
  return spawn(spec.command, spec.args, {
    cwd: workspaceRoot,
    shell: false,
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"],
    env: buildProcessEnv({ ...process.env, ...(spec.env || {}) }),
    windowsVerbatimArguments: spec.windowsVerbatimArguments === true,
    detached: hostPlatform !== "win32",
  });
}

async function runUnrealBuildFromPlan(options = {}) {
  const {
    workspaceRoot,
    build,
    allowEngineFallback = false,
    expectedEngineVersion = "",
    timeoutMs = 45 * 60 * 1000,
    logPath = "",
    hostPlatform = process.platform,
  } = options;

  if (!build?.engineRoot || !build?.projectPath || !build?.target) {
    return { ok: false, error: "invalid build plan", commandSucceeded: false };
  }

  const resolvedEngineRoot = path.resolve(build.engineRoot);
  const resolvedVersion = normalizeVersion(await detectEngineVersion(resolvedEngineRoot));
  const projectVersion = normalizeVersion(
    build.requestedEngineAssociation || build.engineAssociation
  );
  const expectedVersion = normalizeVersion(expectedEngineVersion) || projectVersion;
  const engineMismatch = Boolean(expectedVersion && resolvedVersion && resolvedVersion !== expectedVersion);
  if (engineMismatch && !allowEngineFallback) {
    return {
      ok: false,
      commandSucceeded: false,
      engineMismatch: true,
      resolvedEngineVersion: resolvedVersion,
      expectedEngineVersion: expectedVersion,
      requestedEngineAssociation: build.requestedEngineAssociation || build.engineAssociation || null,
      resolvedEngineRoot,
      resolvedUbtPath: resolveUbtPath(resolvedEngineRoot, hostPlatform),
      error: `Engine version mismatch: project or policy expects ${expectedVersion}, resolved engine is ${resolvedVersion}`,
      errorCode: "ENGINE_VERSION_MISMATCH",
    };
  }

  const { executable, kind } = await resolveBuildExecutable(resolvedEngineRoot, hostPlatform);
  assertEngineContainment(executable, resolvedEngineRoot, hostPlatform);
  const args = buildArgs({
    kind,
    target: build.target,
    platform: build.platform || defaultBuildPlatform(hostPlatform),
    configuration: build.configuration || "Development",
    projectPath: build.projectPath,
  });

  const processResult = await runBoundedProcess({
    start: () => spawnBuildProcess({ executable, kind, args, workspaceRoot, hostPlatform }),
    timeoutMs,
    logPath,
    hostPlatform,
    maxOutputBytes: options.maxOutputBytes,
  });
  const failure = processResult.timedOut
    ? { errorCode: "BUILD_TIMEOUT", error: `Build timed out after ${timeoutMs}ms` }
    : processResult.spawnError
      ? { errorCode: "BUILD_PROCESS_FAILED", error: processResult.spawnError }
      : processResult.outputDecodeError
        ? { errorCode: "BUILD_OUTPUT_DECODE_FAILED", error: processResult.outputDecodeError }
        : processResult.logPersistenceError
          ? { errorCode: "BUILD_LOG_WRITE_FAILED", error: processResult.logPersistenceError }
          : null;
  const commandSucceeded = processResult.exitCode === 0 && !processResult.timedOut;
  return {
    ok: commandSucceeded && !failure,
    commandSucceeded,
    ...processResult,
    ...(failure || {}),
    resolvedEngineVersion: resolvedVersion,
    expectedEngineVersion: expectedVersion,
    requestedEngineAssociation: build.requestedEngineAssociation || build.engineAssociation || null,
    resolvedEngineRoot,
    resolvedUbtPath: resolveUbtPath(resolvedEngineRoot, hostPlatform),
    engineMismatch,
    allowEngineFallback: Boolean(allowEngineFallback),
    executable,
    args,
  };
}

module.exports = {
  runUnrealBuildFromPlan,
  normalizeVersion,
  parseEngineVersionFromRoot,
  detectEngineVersion,
  assertEngineContainment,
  resolveUbtPaths,
  resolveUbtPath,
  resolveBuildScriptPaths,
  resolveBuildExecutable,
  spawnBuildProcess,
  killProcessTree,
  buildSpawnSpec,
  buildProcessEnv,
  decodeBuildOutput,
  sanitizeBrokenCompilerLocalization,
  localeOutputEncoding,
  buildArgs,
  buildWindowsBatchSpawnSpec,
  defaultBuildPlatform,
};
