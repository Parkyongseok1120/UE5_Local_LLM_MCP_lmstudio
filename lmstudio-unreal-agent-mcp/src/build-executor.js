"use strict";

const { spawn } = require("child_process");
const fs = require("fs");
const fsp = fs.promises;
const path = require("path");

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
  const execResolved = path.resolve(executable);
  const rootResolved = path.resolve(engineRoot);
  const contained = hostPlatform === "win32"
    ? execResolved.toLowerCase().startsWith(rootResolved.toLowerCase() + path.sep)
    : execResolved.startsWith(rootResolved + path.sep);
  if (!contained) {
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

function normalizeOutputEncoding(value) {
  const label = String(value || "").trim().toLowerCase().replace(/_/g, "-");
  const aliases = {
    utf8: "utf-8",
    cp949: "euc-kr",
    "windows-949": "euc-kr",
    cp932: "shift_jis",
    "windows-31j": "shift_jis",
    cp936: "gb18030",
  };
  return aliases[label] || label;
}

function localeOutputEncoding(locale = "") {
  const normalized = String(locale || "").toLowerCase();
  if (normalized.startsWith("ko")) return "euc-kr";
  if (normalized.startsWith("ja")) return "shift_jis";
  if (normalized.startsWith("zh-tw") || normalized.startsWith("zh-hk")) return "big5";
  if (normalized.startsWith("zh")) return "gb18030";
  return "euc-kr";
}

function sanitizeBrokenCompilerLocalization(text) {
  return String(text || "").split(/\r?\n/).map((line) => {
    // Some MSVC/UBT combinations emit already-lossy CP949 compatibility-jamo
    // bytes. The localized prose cannot be reconstructed; retain the stable
    // path, error code, and C++ symbols instead of returning mojibake.
    const brokenAt = line.search(/[\u3130-\u318f\uff61-\uffdc\ufffd]/);
    if (brokenAt < 0) return line;
    return line.slice(0, brokenAt).replace(/\?+\s*$/, "").trimEnd();
  }).join("\n");
}

function decodeBuildOutput(chunks, options = {}) {
  const list = Array.isArray(chunks) ? chunks : [chunks];
  const buffer = Buffer.concat(list.filter(Boolean).map((chunk) => Buffer.from(chunk)));
  if (!buffer.length) return "";

  try {
    return sanitizeBrokenCompilerLocalization(
      new TextDecoder("utf-8", { fatal: true }).decode(buffer)
    );
  } catch {
    // Windows compiler output often follows the installed UI codepage.
  }

  let locale = options.locale;
  if (!locale) {
    try { locale = Intl.DateTimeFormat().resolvedOptions().locale; } catch { locale = ""; }
  }
  const requested = normalizeOutputEncoding(
    options.encoding || process.env.MCP_BUILD_OUTPUT_ENCODING || localeOutputEncoding(locale)
  );
  try {
    return sanitizeBrokenCompilerLocalization(
      new TextDecoder(requested || "euc-kr").decode(buffer)
    );
  } catch {
    return sanitizeBrokenCompilerLocalization(buffer.toString("utf8"));
  }
}

function buildSpawnSpec({ executable, kind, args }) {
  if (kind === "build_bat") {
    return { command: "cmd.exe", args: ["/d", "/s", "/c", executable, ...args] };
  }
  if (kind === "build_sh") {
    return { command: "/bin/sh", args: [executable, ...args] };
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

function spawnBuildProcess({ executable, kind, args, workspaceRoot }) {
  const spec = buildSpawnSpec({ executable, kind, args });
  return spawn(spec.command, spec.args, {
    cwd: workspaceRoot,
    shell: false,
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"],
    env: buildProcessEnv(),
    detached: process.platform !== "win32",
  });
}

function killProcessTree(pid) {
  return new Promise((resolve) => {
    if (process.platform === "win32") {
      const killer = spawn("taskkill", ["/PID", String(pid), "/T", "/F"], { stdio: "ignore" });
      killer.on("close", () => resolve());
      killer.on("error", () => resolve());
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
    resolve();
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

  return await new Promise((resolve) => {
    const child = spawnBuildProcess({ executable, kind, args, workspaceRoot });
    const stdoutChunks = [];
    const stderrChunks = [];
    let settled = false;
    child.stdout.on("data", (chunk) => { stdoutChunks.push(Buffer.from(chunk)); });
    child.stderr.on("data", (chunk) => { stderrChunks.push(Buffer.from(chunk)); });
    const timer = setTimeout(async () => {
      if (settled) {
        return;
      }
      settled = true;
      await killProcessTree(child.pid);
      const stdout = decodeBuildOutput(stdoutChunks);
      const stderr = decodeBuildOutput(stderrChunks);
      const fullLog = `${stdout}\n${stderr}`.trim();
      let savedLogPath = logPath;
      if (savedLogPath) {
        await fsp.mkdir(path.dirname(savedLogPath), { recursive: true });
        await fsp.writeFile(savedLogPath, fullLog, "utf8");
      }
      resolve({
        ok: false,
        commandSucceeded: false,
        timedOut: true,
        exitCode: 1,
        errorCode: "BUILD_TIMEOUT",
        error: `Build timed out after ${timeoutMs}ms`,
        resolvedEngineVersion: resolvedVersion,
        expectedEngineVersion: expectedVersion,
        requestedEngineAssociation: build.requestedEngineAssociation || build.engineAssociation || null,
        resolvedEngineRoot,
        resolvedUbtPath: resolveUbtPath(resolvedEngineRoot, hostPlatform),
        engineMismatch,
        allowEngineFallback: Boolean(allowEngineFallback),
        stdout,
        stderr,
        fullLogPath: savedLogPath || null,
        executable,
        args,
      });
    }, timeoutMs);
    child.on("close", async (code) => {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(timer);
      const stdout = decodeBuildOutput(stdoutChunks);
      const stderr = decodeBuildOutput(stderrChunks);
      const fullLog = `${stdout}\n${stderr}`.trim();
      let savedLogPath = logPath;
      if (savedLogPath) {
        await fsp.mkdir(path.dirname(savedLogPath), { recursive: true });
        await fsp.writeFile(savedLogPath, fullLog, "utf8");
      }
      resolve({
        ok: code === 0,
        commandSucceeded: code === 0,
        timedOut: false,
        exitCode: code ?? 1,
        resolvedEngineVersion: resolvedVersion,
        expectedEngineVersion: expectedVersion,
        requestedEngineAssociation: build.requestedEngineAssociation || build.engineAssociation || null,
        resolvedEngineRoot,
        resolvedUbtPath: resolveUbtPath(resolvedEngineRoot, hostPlatform),
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
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(timer);
      resolve({
        ok: false,
        commandSucceeded: false,
        timedOut: false,
        error: String(err.message || err),
        resolvedEngineVersion: resolvedVersion,
        expectedEngineVersion: expectedVersion,
        requestedEngineAssociation: build.requestedEngineAssociation || build.engineAssociation || null,
        resolvedEngineRoot,
        resolvedUbtPath: resolveUbtPath(resolvedEngineRoot, hostPlatform),
      });
    });
  });
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
  buildSpawnSpec,
  buildProcessEnv,
  decodeBuildOutput,
  sanitizeBrokenCompilerLocalization,
  localeOutputEncoding,
  buildArgs,
  defaultBuildPlatform,
};
