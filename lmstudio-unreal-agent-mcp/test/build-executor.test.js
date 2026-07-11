"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const test = require("node:test");
const { runUnrealBuildFromPlan, normalizeVersion, assertEngineContainment } = require("../src/build-executor");

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
