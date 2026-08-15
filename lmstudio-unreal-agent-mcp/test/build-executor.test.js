"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const test = require("node:test");
const {
  runUnrealBuildFromPlan,
  normalizeVersion,
  detectEngineVersion,
  assertEngineContainment,
  resolveBuildExecutable,
  spawnBuildProcess,
  buildSpawnSpec,
  buildProcessEnv,
  decodeBuildOutput,
  localeOutputEncoding,
  buildArgs,
  buildWindowsBatchSpawnSpec,
  defaultBuildPlatform,
} = require("../src/build-executor");

function createFakeEngine(version, folderPrefix = "UE-portable-") {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), folderPrefix));
  const buildDir = path.join(root, "Engine", "Build");
  const batchDir = path.join(buildDir, "BatchFiles");
  fs.mkdirSync(batchDir, { recursive: true });
  const [major, minor] = version.split(".").map(Number);
  fs.writeFileSync(
    path.join(buildDir, "Build.version"),
    JSON.stringify({ MajorVersion: major, MinorVersion: minor }),
    "utf8"
  );
  fs.writeFileSync(path.join(batchDir, "Build.bat"), "@echo off\r\nexit /b 0\r\n", "utf8");
  if (process.platform !== "win32") {
    const hostFolder = process.platform === "darwin" ? "Mac" : "Linux";
    const hostDir = path.join(batchDir, hostFolder);
    fs.mkdirSync(hostDir, { recursive: true });
    fs.writeFileSync(path.join(hostDir, "Build.sh"), "#!/bin/sh\nexit 0\n", "utf8");
  }
  return root;
}

test("engine mismatch fails closed for 5.4 when 5.8 expected", async () => {
  const fakeEngineRoot = path.join(os.tmpdir(), "FakeUE_5.4");
  const result = await runUnrealBuildFromPlan({
    workspaceRoot: os.tmpdir(),
    build: {
      engineRoot: fakeEngineRoot,
      engineAssociation: "5.4",
      projectPath: path.join(os.tmpdir(), "Game", "Game.uproject"),
      target: "GameEditor",
      platform: "Win64",
      configuration: "Development",
    },
    allowEngineFallback: false,
    expectedEngineVersion: "5.8",
    timeoutMs: 1000,
  });
  assert.strictEqual(result.ok, false);
  assert.strictEqual(result.errorCode, "ENGINE_VERSION_MISMATCH");
});

test("assertEngineContainment rejects outside engine root", () => {
  const engineRoot = path.join(os.tmpdir(), "FakeUE_5.8");
  const outsideExe = path.join(os.tmpdir(), "outside-bin", "cmd.exe");
  assert.throws(
    () => assertEngineContainment(outsideExe, engineRoot),
    /outside engine root/i
  );
});

test("normalizeVersion extracts semver", () => {
  assert.strictEqual(normalizeVersion("5.8"), "5.8");
  assert.strictEqual(normalizeVersion("UE_5.8"), "5.8");
});

test("detectEngineVersion reads Build.version independently of install folder name", async () => {
  const root = createFakeEngine("5.7", "CustomStudioEngine-");
  assert.strictEqual(await detectEngineVersion(root), "5.7");
});

test("project EngineAssociation selects the expected version instead of globally forcing 5.8", async () => {
  const root = createFakeEngine("5.7");
  const result = await runUnrealBuildFromPlan({
    workspaceRoot: os.tmpdir(),
    build: {
      engineRoot: root,
      engineAssociation: "5.7",
      projectPath: path.join(os.tmpdir(), "PortableGame", "PortableGame.uproject"),
      target: "PortableGameEditor",
    },
    timeoutMs: 5000,
  });
  assert.strictEqual(result.ok, true, JSON.stringify(result));
  assert.strictEqual(result.expectedEngineVersion, "5.7");
  assert.strictEqual(result.resolvedEngineVersion, "5.7");
});

test("project engine mismatch compares EngineAssociation with the actual Build.version", async () => {
  const root = createFakeEngine("5.8");
  const result = await runUnrealBuildFromPlan({
    workspaceRoot: os.tmpdir(),
    build: {
      engineRoot: root,
      engineAssociation: "5.7",
      projectPath: path.join(os.tmpdir(), "OtherGame", "OtherGame.uproject"),
      target: "OtherGameEditor",
    },
  });
  assert.strictEqual(result.ok, false);
  assert.strictEqual(result.errorCode, "ENGINE_VERSION_MISMATCH");
  assert.strictEqual(result.expectedEngineVersion, "5.7");
  assert.strictEqual(result.resolvedEngineVersion, "5.8");
});

test("custom GUID EngineAssociation does not impose a false numeric version policy", async () => {
  const root = createFakeEngine("5.6", "SourceBuild-");
  const result = await runUnrealBuildFromPlan({
    workspaceRoot: os.tmpdir(),
    build: {
      engineRoot: root,
      engineAssociation: "{01234567-89AB-CDEF-0123-456789ABCDEF}",
      projectPath: path.join(os.tmpdir(), "SourceGame", "SourceGame.uproject"),
      target: "SourceGameEditor",
    },
    timeoutMs: 5000,
  });
  assert.strictEqual(result.ok, true, JSON.stringify(result));
  assert.strictEqual(result.expectedEngineVersion, "");
  assert.strictEqual(result.resolvedEngineVersion, "5.6");
});

test("resolveBuildExecutable prefers ubt over build bat", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "ue-root-"));
  const ubtDir = path.join(root, "Engine", "Binaries", "DotNET", "UnrealBuildTool");
  fs.mkdirSync(ubtDir, { recursive: true });
  const ubt = path.join(ubtDir, "UnrealBuildTool.exe");
  fs.writeFileSync(ubt, "");
  const batDir = path.join(root, "Engine", "Build", "BatchFiles");
  fs.mkdirSync(batDir, { recursive: true });
  fs.writeFileSync(path.join(batDir, "Build.bat"), "@echo off\r\n");
  const resolved = await resolveBuildExecutable(root);
  assert.strictEqual(resolved.kind, "ubt");
  assert.strictEqual(resolved.executable, ubt);
});

test("resolveBuildExecutable selects host Build.sh on macOS and Linux", async () => {
  for (const [hostPlatform, hostFolder] of [["darwin", "Mac"], ["linux", "Linux"]]) {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), `ue-${hostPlatform}-`));
    const scriptDir = path.join(root, "Engine", "Build", "BatchFiles", hostFolder);
    fs.mkdirSync(scriptDir, { recursive: true });
    const script = path.join(scriptDir, "Build.sh");
    fs.writeFileSync(script, "#!/usr/bin/env sh\nexit 0\n", "utf8");
    const resolved = await resolveBuildExecutable(root, hostPlatform);
    assert.deepStrictEqual(resolved, { executable: script, kind: "build_sh" });
  }
});

test("Unix UBT DLL is launched through dotnet and Build.sh through Bash", () => {
  const args = ["GameEditor", "Mac", "Development"];
  assert.deepStrictEqual(
    buildSpawnSpec({ executable: "/Engine/Build.sh", kind: "build_sh", args }),
    { command: "/bin/bash", args: ["/Engine/Build.sh", ...args] }
  );
  const portable = buildSpawnSpec({
    executable: "/opt/Epic Engines/UE 5.8/Engine/Build/BatchFiles/Linux/Build.sh",
    kind: "build_sh",
    args: ["게임Editor", "Linux", "Development", "-Project=/srv/프로젝트 공간/Game.uproject"],
  });
  assert.strictEqual(portable.command, "/bin/bash");
  assert.deepStrictEqual(portable.args, [
    "/opt/Epic Engines/UE 5.8/Engine/Build/BatchFiles/Linux/Build.sh",
    "게임Editor",
    "Linux",
    "Development",
    "-Project=/srv/프로젝트 공간/Game.uproject",
  ]);
  assert.deepStrictEqual(
    buildSpawnSpec({ executable: "/Engine/UnrealBuildTool.dll", kind: "ubt_dotnet", args }),
    { command: "dotnet", args: ["/Engine/UnrealBuildTool.dll", ...args] }
  );
  assert.strictEqual(defaultBuildPlatform("darwin"), "Mac");
  assert.strictEqual(defaultBuildPlatform("linux"), "Linux");
});

test("spawnBuildProcess uses cmd.exe for build bat", { skip: process.platform !== "win32" }, () => {
  const bat = path.join(os.tmpdir(), "Build.bat");
  const args = buildArgs({
    kind: "build_bat",
    target: "GameEditor",
    platform: "Win64",
    configuration: "Development",
    projectPath: "C:\\Game\\Game.uproject",
  });
  const child = spawnBuildProcess({
    executable: bat,
    kind: "build_bat",
    args,
    workspaceRoot: os.tmpdir(),
  });
  assert.ok(Array.isArray(child.spawnargs));
  assert.strictEqual(child.spawnargs[0], "cmd.exe");
  child.kill();
});

test("Build.bat spawn spec keeps untrusted values out of the cmd command string", () => {
  const spec = buildWindowsBatchSpawnSpec(
    "C:\\Program Files\\Epic Games\\UE_5.8\\Engine\\Build\\BatchFiles\\Build.bat",
    ["GameEditor", "Win64", "Development", "-Project=C:\\프로젝트 폴더 & 샘플\\Game.uproject"]
  );
  assert.strictEqual(spec.command, "cmd.exe");
  assert.deepStrictEqual(spec.args.slice(0, 5), ["/d", "/s", "/e:on", "/v:off", "/c"]);
  assert.ok(!spec.args[5].includes("프로젝트"));
  assert.ok(!spec.args[5].includes("&"));
  assert.strictEqual(spec.env.MCP_UNREAL_BUILD_ARG_0.endsWith("Build.bat"), true);
  assert.strictEqual(spec.env.MCP_UNREAL_BUILD_ARG_4.includes("프로젝트 폴더 & 샘플"), true);
});

test("Build.bat fallback rejects values that Epic's CALL and delayed expansion cannot preserve", () => {
  for (const unsafe of ["C:\\Game%PATH%\\Game.uproject", "C:\\Game!TEMP!\\Game.uproject", "C:\\Game^Name\\Game.uproject", "bad\"target"] ) {
    assert.throws(
      () => buildWindowsBatchSpawnSpec("C:\\Engine\\Build.bat", [unsafe]),
      /unsafe cmd\.exe expansion character/i
    );
  }
});

test("Build.bat fallback preserves spaced Unicode paths and quoted metacharacters", { skip: process.platform !== "win32" }, async () => {
  const fixtureRoot = fs.mkdtempSync(path.join(os.tmpdir(), "UE 공백 & 엔진-"));
  const script = path.join(fixtureRoot, "빌드 도구 & Batch", "Build.bat");
  const capture = path.join(fixtureRoot, "captured args.txt");
  const projectPath = path.join(fixtureRoot, "프로젝트 & 샘플 (5.8)", "Game Test.uproject");
  fs.mkdirSync(path.dirname(script), { recursive: true });
  fs.writeFileSync(
    script,
    [
      "@echo off",
      "setlocal EnableDelayedExpansion",
      "call :Capture %*",
      "exit /b !ERRORLEVEL!",
      ":Capture",
      "\"%MCP_BUILD_TEST_NODE%\" -e \"require('fs').writeFileSync(process.env.MCP_BUILD_TEST_CAPTURE, JSON.stringify(process.argv.slice(1)), 'utf8')\" %*",
      "exit /b 0",
      "",
    ].join("\r\n"),
    "utf8"
  );
  const previousCapture = process.env.MCP_BUILD_TEST_CAPTURE;
  const previousNode = process.env.MCP_BUILD_TEST_NODE;
  process.env.MCP_BUILD_TEST_CAPTURE = capture;
  process.env.MCP_BUILD_TEST_NODE = process.execPath;
  try {
    const args = buildArgs({
      kind: "build_bat",
      target: "GameEditor",
      platform: "Win64",
      configuration: "Development",
      projectPath,
    });
    const child = spawnBuildProcess({
      executable: script,
      kind: "build_bat",
      args,
      workspaceRoot: fixtureRoot,
    });
    let diagnostic = `${JSON.stringify(child.spawnargs)}\n`;
    child.stdout.on("data", (chunk) => { diagnostic += chunk.toString(); });
    child.stderr.on("data", (chunk) => { diagnostic += chunk.toString(); });
    const exitCode = await new Promise((resolve, reject) => {
      child.once("error", reject);
      child.once("close", resolve);
    });
    assert.strictEqual(exitCode, 0, diagnostic);
    assert.deepStrictEqual(JSON.parse(fs.readFileSync(capture, "utf8")), args);
  } finally {
    if (previousCapture === undefined) delete process.env.MCP_BUILD_TEST_CAPTURE;
    else process.env.MCP_BUILD_TEST_CAPTURE = previousCapture;
    if (previousNode === undefined) delete process.env.MCP_BUILD_TEST_NODE;
    else process.env.MCP_BUILD_TEST_NODE = previousNode;
    fs.rmSync(fixtureRoot, { recursive: true, force: true });
  }
});

test("build process environment requests stable English diagnostics without overriding policy", () => {
  const defaults = buildProcessEnv({ PATH: "x" });
  assert.strictEqual(defaults.VSLANG, "1033");
  assert.strictEqual(defaults.DOTNET_CLI_UI_LANGUAGE, "en-US");

  const explicit = buildProcessEnv({ VSLANG: "1042", DOTNET_CLI_UI_LANGUAGE: "ko-KR" });
  assert.strictEqual(explicit.VSLANG, "1042");
  assert.strictEqual(explicit.DOTNET_CLI_UI_LANGUAGE, "ko-KR");
});

test("build output decoder preserves UTF-8 and decodes Korean Windows output", () => {
  assert.strictEqual(decodeBuildOutput(Buffer.from("빌드 오류", "utf8")), "빌드 오류");
  // CP949 bytes for "오류". WHATWG's euc-kr decoder covers Windows-949.
  assert.strictEqual(decodeBuildOutput(Buffer.from([0xbf, 0xc0, 0xb7, 0xf9]), { encoding: "cp949" }), "오류");
  const lossyCp949 = Buffer.concat([
    Buffer.from("error C2039: 'Empty'?", "ascii"),
    Buffer.from([0xa4, 0xc4, 0x20, 0xa4, 0xb8]),
  ]);
  assert.strictEqual(
    decodeBuildOutput(lossyCp949, { encoding: "cp949" }),
    "error C2039: 'Empty'"
  );
  assert.strictEqual(localeOutputEncoding("ko-KR", "win32"), "euc-kr");
  assert.strictEqual(localeOutputEncoding("ja-JP", "win32"), "shift_jis");
  assert.strictEqual(localeOutputEncoding("en-US", "win32"), "windows-1252");
  assert.strictEqual(localeOutputEncoding("ko-KR", "linux"), "utf-8");
  assert.strictEqual(localeOutputEncoding("ja-JP", "darwin"), "utf-8");
});

test("Windows engine containment folds ASCII case without merging I-dot lookalikes", () => {
  const base = path.join(os.tmpdir(), "engine-containment-identity");
  const asciiRoot = path.join(base, "ASCIIEngine");
  const asciiChild = path.join(base, "asciiengine", "Engine", "Build.bat");
  assert.doesNotThrow(() => assertEngineContainment(asciiChild, asciiRoot, "win32"));

  const idotRoot = path.join(base, "\u0130Engine");
  const lookalikeChild = path.join(base, "i\u0307engine", "Engine", "Build.bat");
  assert.throws(
    () => assertEngineContainment(lookalikeChild, idotRoot, "win32"),
    /outside engine root/i,
  );
});

test("engine containment resolves an existing directory alias before authorizing", (t) => {
  const fixtureRoot = fs.mkdtempSync(path.join(os.tmpdir(), "engine-containment-alias-"));
  const engineRoot = path.join(fixtureRoot, "EngineRoot");
  const outsideRoot = path.join(fixtureRoot, "Outside");
  const alias = path.join(engineRoot, "Engine", "Binaries");
  const executable = path.join(alias, "UnrealBuildTool.exe");
  fs.mkdirSync(path.dirname(alias), { recursive: true });
  fs.mkdirSync(outsideRoot, { recursive: true });
  fs.writeFileSync(path.join(outsideRoot, "UnrealBuildTool.exe"), "outside");
  try {
    try {
      fs.symlinkSync(
        outsideRoot,
        alias,
        process.platform === "win32" ? "junction" : "dir",
      );
    } catch (error) {
      if (!["EPERM", "EACCES", "ENOTSUP", "UNKNOWN"].includes(String(error.code || ""))) {
        throw error;
      }
      t.skip(`directory aliases are unavailable on this host: ${error.code || error.message}`);
      return;
    }
    assert.throws(
      () => assertEngineContainment(executable, engineRoot),
      /outside engine root/i,
    );
  } finally {
    fs.rmSync(fixtureRoot, { recursive: true, force: true });
  }
});

test("runUnrealBuildFromPlan reports timedOut", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "ue-timeout-"));
  const batDir = path.join(root, "Engine", "Build", "BatchFiles");
  fs.mkdirSync(batDir, { recursive: true });
  const bat = path.join(batDir, "Build.bat");
  fs.writeFileSync(bat, "@echo off\r\nping -n 30 127.0.0.1 >nul\r\n");
  if (process.platform !== "win32") {
    const hostFolder = process.platform === "darwin" ? "Mac" : "Linux";
    const hostDir = path.join(batDir, hostFolder);
    fs.mkdirSync(hostDir, { recursive: true });
    fs.writeFileSync(path.join(hostDir, "Build.sh"), "#!/bin/sh\nsleep 30\n", "utf8");
  }
  const result = await runUnrealBuildFromPlan({
    workspaceRoot: os.tmpdir(),
    build: {
      engineRoot: root,
      engineAssociation: "5.8",
      projectPath: path.join(os.tmpdir(), "Game", "Game.uproject"),
      target: "GameEditor",
      platform: "Win64",
      configuration: "Development",
    },
    allowEngineFallback: true,
    expectedEngineVersion: "5.8",
    timeoutMs: 500,
  });
  assert.strictEqual(result.timedOut, true, JSON.stringify(result));
  assert.strictEqual(result.errorCode, "BUILD_TIMEOUT");
});
