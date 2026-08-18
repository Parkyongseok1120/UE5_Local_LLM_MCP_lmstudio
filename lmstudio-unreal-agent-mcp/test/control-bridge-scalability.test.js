"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { execFile } = require("node:child_process");
const { promisify } = require("node:util");
const { commitControlTransition } = require("../src/task-control-transition");

const execFileAsync = promisify(execFile);

function largeState(megabytes, id = "large-state") {
  return {
    taskSessionId: id,
    status: "running",
    mode: "read_only",
    planRevision: "1",
    mutationGeneration: 0,
    toolRoute: { phase: "planner", activeTools: ["search_files"] },
    transportPadding: "x".repeat(megabytes * 1024 * 1024),
  };
}

test("canonical bridge round-trips 1MB through 17MB state without stdout overflow", () => {
  for (const megabytes of [1, 8, 15, 16, 17]) {
    const value = largeState(megabytes, `large-${megabytes}`);
    commitControlTransition(value);
    assert.equal(value.taskSessionId, `large-${megabytes}`);
    assert.equal(value.transportPadding.length, megabytes * 1024 * 1024);
    assert.equal(value.controlState?.authoritative, true);
  }
});

test("concurrent canonical bridge file transports do not collide", async () => {
  const modulePath = path.resolve(__dirname, "../src/task-control-transition.js");
  const source = `
    const { commitControlTransition } = require(${JSON.stringify(modulePath)});
    const id = process.argv[1];
    const state = {
      taskSessionId: id,
      status: 'running',
      mode: 'read_only',
      planRevision: '1',
      mutationGeneration: 0,
      toolRoute: { phase: 'planner', activeTools: ['search_files'] },
      transportPadding: 'x'.repeat(1024 * 1024),
    };
    commitControlTransition(state);
    process.stdout.write(JSON.stringify({ id: state.taskSessionId, length: state.transportPadding.length }));
  `;
  const results = await Promise.all(
    ["concurrent-a", "concurrent-b", "concurrent-c", "concurrent-d"].map((id) => (
      execFileAsync(process.execPath, ["-e", source, id], {
        encoding: "utf8",
        timeout: 120_000,
        maxBuffer: 1024 * 1024,
      })
    )),
  );
  assert.deepEqual(
    results.map(({ stdout }) => JSON.parse(stdout)).map(({ id }) => id).sort(),
    ["concurrent-a", "concurrent-b", "concurrent-c", "concurrent-d"],
  );
  assert.ok(results.every(({ stdout }) => JSON.parse(stdout).length === 1024 * 1024));
});
