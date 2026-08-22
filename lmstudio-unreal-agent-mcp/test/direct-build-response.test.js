"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const { buildDirectResponse, extractBuildDiagnostics } = require("../src/direct-build-response.js");

function input(result) {
  return {
    result,
    build: {
      target: "DemoEditor",
      platform: "Win64",
      configuration: "Development",
      engineRoot: "C:/Epic/UE_5.6",
      engineAssociation: "5.6",
    },
    planResult: { selectionReason: "explicit.project" },
    projectPath: "C:/Projects/Demo/Demo.uproject",
    command: "UnrealBuildTool DemoEditor Win64 Development",
    logPath: "C:/logs/build.log",
  };
}

test("Direct build failure returns diagnostics without owning the repair workflow", () => {
  const payload = buildDirectResponse(input({
    ok: false,
    exitCode: 6,
    stdout: "[1/2] Compile Demo.cpp\nC:/Projects/Demo/Source/Demo/Demo.cpp(42): error C2039: 'Run': is not a member of 'UDemo'\n",
    stderr: "Build failed",
  }));
  assert.strictEqual(payload.ok, false);
  assert.strictEqual(payload.errorCode, "BUILD_FAILED");
  assert.match(payload.firstError, /Source\/Demo\/Demo\.cpp\(42\).*C2039/);
  assert.deepStrictEqual(payload.retry, { allowed: false, mode: "none" });
  assert.doesNotMatch(JSON.stringify(payload), /requiredNextTool|requiredSequence|forbiddenUntilMutation|taskAuthorization|synthesis|ownerCapability/);
});

test("Direct build success reports compile proof as evidence only", () => {
  const payload = buildDirectResponse(input({
    ok: true,
    exitCode: 0,
    stdout: "[1/2] Compile Demo.cpp\n[2/2] Link DemoEditor.exe\nTotal time in Parallel executor: 1.2 seconds\n",
    stderr: "",
  }));
  assert.strictEqual(payload.ok, true);
  assert.ok(payload.proof.observedCompileLines >= 1);
  assert.ok(payload.proof.observedLinkLines >= 1);
  assert.strictEqual(payload.project.engineAssociation, "5.6");
  assert.strictEqual(payload.diagnostics.length, 0);
});

test("UHT warnings-as-errors and clang undefined symbols remain visible", () => {
  const diagnostics = extractBuildDiagnostics(
    "Running Internal UnrealHeaderTool -WarningsAsErrors\nDemo.h(17): Warning: Missing generated body\nUndefined symbols for architecture arm64:\n  \"Demo::Run()\", referenced from:\nclang++: error: linker command failed",
    "",
  );
  assert.ok(diagnostics.some((line) => /Warning: Missing generated body/.test(line)));
  assert.ok(diagnostics.some((line) => /Demo::Run/.test(line)));
  assert.ok(diagnostics.some((line) => /clang\+\+: error/.test(line)));
});
