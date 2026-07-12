"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const test = require("node:test");
const {
  runUnrealBuildFromPlan,
  normalizeVersion,
  assertEngineContainment,
  resolveBuildExecutable,
  spawnBuildProcess,
  buildArgs,
} = require("../src/build-executor");

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

test("spawnBuildProcess uses cmd.exe for build bat", () => {
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

test("runUnrealBuildFromPlan reports timedOut", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "ue-timeout-"));
  const batDir = path.join(root, "Engine", "Build", "BatchFiles");
  fs.mkdirSync(batDir, { recursive: true });
  const bat = path.join(batDir, "Build.bat");
  fs.writeFileSync(bat, "@echo off\r\nping -n 30 127.0.0.1 >nul\r\n");
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
  assert.strictEqual(result.timedOut, true);
  assert.strictEqual(result.errorCode, "BUILD_TIMEOUT");
});
