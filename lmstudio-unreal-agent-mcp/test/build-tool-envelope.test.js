"use strict";

const assert = require("assert");
const test = require("node:test");
const {
  buildResponsePayload,
  extractLikelyCompileErrors,
  firstErrorCluster,
} = require("../src/context-ux");

test("build failure payload marks MCP isError when ok is false", () => {
  const payload = buildResponsePayload({
    result: { ok: false, exitCode: 1, stdout: "error C123", stderr: "", error: "" },
    build: { target: "GameEditor", platform: "Win64", configuration: "Development" },
    planResult: { ok: true },
    projectPath: "C:\\Game\\Game.uproject",
    command: "Build.bat GameEditor Win64 Development",
    logPath: "C:\\Game\\.agent\\logs\\latest-build.log",
    verbose: false,
  });
  assert.strictEqual(payload.ok, false);
  const shouldError = !payload.ok;
  assert.strictEqual(shouldError, true);
});

test("UHT warnings-as-errors are returned as actionable build errors", () => {
  const output = [
    'Running Internal UnrealHeaderTool "C:\\Game\\Game.uproject" -WarningsAsErrors',
    'C:\\Game\\Source\\Game\\Public\\Status.h(75): Warning: Property has a Category set but is not exposed to the editor or Blueprints.',
    "Unhandled 1 aggregate exceptions",
    "Result: Failed (OtherCompilationError)",
  ].join("\n");

  const errors = extractLikelyCompileErrors(output, "");

  assert.match(errors[0], /Status\.h\(75\): Warning:/);
  assert.ok(errors.some((line) => line.includes("OtherCompilationError")));
});

test("error clustering finds UHT failure before a long build tail", () => {
  const lines = [
    ...Array.from({ length: 80 }, (_, index) => `setup ${index}`),
    'C:\\Game\\Status.h(75): Warning: Property has a Category set but is not exposed.',
    "Unhandled 1 aggregate exceptions",
    "Result: Failed (OtherCompilationError)",
    ...Array.from({ length: 100 }, (_, index) => `timeline ${index}`),
  ];

  const cluster = firstErrorCluster(lines, 4, 30);

  assert.ok(cluster.some((line) => line.includes("Status.h(75): Warning:")));
  assert.ok(cluster.some((line) => line.includes("OtherCompilationError")));
  assert.ok(!cluster.some((line) => line === "timeline 99"));
});
