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

test("diagnostic coverage is additive and shares the legacy parser and entry limit", () => {
  const stdout = Array.from({ length: 50 }, (_, i) => `Source/A.cpp(${i + 1}): error C2039: missing ${i}`).join("\n");
  const payload = buildDirectResponse(input({ ok: false, stdout }));
  assert.deepEqual(payload.diagnostics, extractBuildDiagnostics(stdout, ""));
  assert.equal(payload.diagnostics.length, 40);
  assert.equal(payload.diagnosticCoverage.truncated, true);
  assert.equal(payload.diagnosticCoverage.completeBuildLog, false);
  assert.equal(payload.diagnosticCoverage.returnedEntries, 40);
});

test("large static precheck and notes keep diagnostics and a readable run locator through MCP", () => {
  const { boundLongRunningPayload } = require("../src/direct-build-response");
  const { toMcpResult } = require("../src/direct-response");
  const stdout = Array.from({length:80}, (_,i) => "Source/A.cpp("+(i+1)+"): error C2065: "+"한글".repeat(400)).join("\n");
  const raw = buildDirectResponse(input({ok:false, stdout, exitCode:6, processExited:true,
    ubtLog:{fileName:"ubt-run.log",readableFileName:"ubt-run-readable.log",readableComplete:true}}));
  raw.staticPrecheck = {status:"advisory",scopeTargets:["x".repeat(9000)],findings:[{message:"z".repeat(6000)}]};
  const result = toMcpResult(boundLongRunningPayload(raw));
  const value = JSON.parse(result.content[0].text);
  assert.ok(result.content[0].text.length <= 3584);
  assert.ok(value.diagnostics.length > 1);
  assert.equal(value.diagnosticCoverage.truncated, true);
  assert.equal(value.diagnosticCoverage.returnedEntries, value.diagnostics.length);
  assert.equal(value.exitCode, 6);
  assert.equal(value.suggestion.args.fileName, "ubt-run-readable.log");
});
