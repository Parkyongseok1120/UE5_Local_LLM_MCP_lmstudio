"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { configuredIntervalMs, createProgressHeartbeat } = require("../src/progress-heartbeat.js");

test("progress interval is clamped to the UX contract", () => {
  assert.equal(configuredIntervalMs({ MCP_PROGRESS_INTERVAL_SECONDS: "0.1" }), 2000);
  assert.equal(configuredIntervalMs({ MCP_PROGRESS_INTERVAL_SECONDS: "3" }), 3000);
  assert.equal(configuredIntervalMs({ MCP_PROGRESS_INTERVAL_SECONDS: "30" }), 5000);
  assert.equal(configuredIntervalMs({ MCP_PROGRESS_INTERVAL_SECONDS: "bad" }), 3000);
});

test("token progress emits start, phase, heartbeat, and one completion", async () => {
  const progress = [];
  let tick = null;
  let now = 1000;
  let cancelled = false;
  const heartbeat = createProgressHeartbeat({
    toolName: "build_unreal_project",
    progressToken: "token-1",
    sendProgress: (payload) => progress.push(payload),
    intervalMs: 3000,
    now: () => now,
    schedule: (callback) => {
      tick = callback;
      return 7;
    },
    cancel: (timer) => {
      cancelled = timer === 7;
    },
  });
  heartbeat.setPhase("Build executing");
  now = 4100;
  tick();
  heartbeat.finish();
  heartbeat.finish();
  await Promise.resolve();

  assert.equal(progress.length, 4);
  assert.deepEqual(progress.map((item) => item.progress), [1, 2, 3, 4]);
  assert.match(progress[2].message, /3s elapsed/);
  assert.match(progress[3].message, /completed/);
  assert.equal(cancelled, true);
});

test("token-less heartbeat uses logging messages and has no completion noise", async () => {
  const messages = [];
  let tick = null;
  const heartbeat = createProgressHeartbeat({
    toolName: "static_validate_project",
    sendMessage: (message) => messages.push(message),
    schedule: (callback) => {
      tick = callback;
      return 1;
    },
    cancel: () => undefined,
  });
  tick();
  heartbeat.finish();
  await Promise.resolve();
  assert.equal(messages.length, 1);
  assert.match(messages[0], /static_validate_project/);
});
