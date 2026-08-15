"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const test = require("node:test");
const {
  exactRecoveryLogObligation,
  recoveryLogSource,
} = require("../src/recovery-log-contract");

const exactArgs = {
  mode: "first_error",
  fileName: "latest-build.log",
  summaryOnly: true,
  maxFiles: 1,
  maxLines: 200,
};

function taskState(overrides = {}) {
  return {
    mutationGeneration: 4,
    recoveryObligation: {
      status: "evidence_required",
      source: "build",
      requiredTool: { name: "read_unreal_logs", args: exactArgs },
      ...overrides,
    },
  };
}

test("recovery log blocker applies only to the exact active evidence obligation", () => {
  assert.strictEqual(exactRecoveryLogObligation(taskState(), exactArgs).matched, true);
  assert.strictEqual(
    exactRecoveryLogObligation(taskState({ status: "external_blocker" }), exactArgs).matched,
    false
  );
  assert.strictEqual(
    exactRecoveryLogObligation(taskState({
      requiredTool: { name: "read_file", args: exactArgs },
    }), exactArgs).matched,
    false
  );
  assert.strictEqual(
    exactRecoveryLogObligation(taskState(), { ...exactArgs, maxLines: 60 }).matched,
    false
  );
});

test("Automation recovery log inference is case-insensitive", () => {
  assert.strictEqual(recoveryLogSource({}, "Latest-AUTOMATION.Log"), "automation");
  assert.strictEqual(recoveryLogSource({}, "latest-build.log"), "build");
  assert.strictEqual(recoveryLogSource({ source: "custom" }, "AUTOMATION.log"), "custom");
});

test("server recovery log success fails closed when evidence commit fails", () => {
  const source = fs.readFileSync(path.join(__dirname, "..", "src", "server.js"), "utf8");
  const start = source.indexOf('if (name === "read_unreal_logs")');
  const end = source.indexOf('if (name === "write_session_handoff")', start);
  assert.ok(start >= 0 && end > start);
  const handler = source.slice(start, end);
  const mark = handler.indexOf("markRecoveryEvidenceViaPython(");
  const failedCommit = handler.indexOf("if (recoveryEvidence?.ok === false)", mark);
  const failClosed = handler.indexOf(
    'return fail("Recovery log evidence was read but could not be committed."',
    failedCommit
  );
  assert.ok(mark >= 0 && failedCommit > mark && failClosed > failedCommit);
  assert.ok(handler.includes("recoveryLogObligation.matched"));
  assert.ok(handler.includes('doNotRetry = ["read_unreal_logs"]'));
  assert.ok(handler.includes("Required recovery log directories are unavailable"));
});

test("server classifies truncated empty Automation discovery before no declarations", () => {
  const source = fs.readFileSync(path.join(__dirname, "..", "src", "server.js"), "utf8");
  const start = source.indexOf('if (name === "run_unreal_automation_tests")');
  const end = source.indexOf('if (name === "build_unreal_project")', start);
  assert.ok(start >= 0 && end > start);
  const handler = source.slice(start, end);
  const truncated = handler.indexOf("if (discovery.truncated)");
  const empty = handler.indexOf("if (!discovery.count)");
  assert.ok(truncated >= 0 && empty > truncated);
  assert.ok(handler.slice(truncated, empty).includes("AUTOMATION_DISCOVERY_TRUNCATED"));
});

test("build-loop log obligation remains executable and engine mismatch comment is not retry advice", () => {
  const source = fs.readFileSync(path.join(__dirname, "..", "src", "server.js"), "utf8");
  const buildLoop = source.indexOf('"build_recovery_exhausted"');
  const nextHandler = source.indexOf("const buildTimeout", buildLoop);
  const block = source.slice(buildLoop, nextHandler);
  assert.ok(block.includes("stopCurrentWorkflow: false"));
  assert.ok(block.includes('requiredNextTool: "read_unreal_logs"'));
  assert.doesNotMatch(source, /Permit one corrected retry without forcing a fake edit/);
});

test("range recovery binds cursor, byte window, and filter exactly", () => {
  const rangeArgs = {
    mode: "range",
    fileName: "latest-build.log",
    cursorByte: 4_194_304,
    maxBytes: 4_194_304,
    maxFiles: 1,
    maxLines: 200,
    summaryOnly: false,
    filter: "fatal error",
  };
  const rangeState = taskState({
    requiredTool: { name: "read_unreal_logs", args: rangeArgs },
  });
  assert.strictEqual(exactRecoveryLogObligation(rangeState, rangeArgs).matched, true);
  assert.strictEqual(
    exactRecoveryLogObligation(rangeState, { ...rangeArgs, cursorByte: 0 }).matched,
    false
  );
  assert.strictEqual(
    exactRecoveryLogObligation(rangeState, { ...rangeArgs, maxBytes: 65_536 }).matched,
    false
  );
});
