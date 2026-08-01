"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const { inspect, parseArgs } = require("../scripts/status.cjs");

function writeEvents(root, events) {
  const session = path.join(root, "session-a");
  fs.mkdirSync(session, { recursive: true });
  fs.writeFileSync(
    path.join(session, "events.jsonl"),
    `${events.map((event) => JSON.stringify(event)).join("\n")}\n{partially-written`,
    "utf8",
  );
}

function measurement(at, overrides = {}) {
  return {
    type: "context_measurement",
    at,
    proxyActive: true,
    targetModel: "qwen",
    inputTokens: 123,
    contextLength: 55_040,
    decision: { action: "normal" },
    ...overrides,
  };
}

test("fresh proxy measurement proves activation on every platform", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "compactor-status-"));
  const now = Date.parse("2026-08-02T01:00:00.000Z");
  try {
    writeEvents(root, [measurement("2026-08-02T00:55:00.000Z")]);
    const result = inspect({ stateRoot: root, maxAgeMinutes: 30, requireCompaction: false }, now);
    assert.equal(result.active, true);
    assert.equal(result.measurementAgeMinutes, 5);
    assert.equal(result.targetModel, "qwen");
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("stale historical measurement cannot prove current proxy activation", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "compactor-status-stale-"));
  const now = Date.parse("2026-08-02T02:00:00.000Z");
  try {
    writeEvents(root, [measurement("2026-08-02T00:00:00.000Z")]);
    const result = inspect({ stateRoot: root, maxAgeMinutes: 30, requireCompaction: false }, now);
    assert.equal(result.active, false);
    assert.equal(result.reason, "stale_proxy_measurement");
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("future-dated measurement cannot prove current proxy activation", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "compactor-status-future-"));
  const now = Date.parse("2026-08-02T01:00:00.000Z");
  try {
    writeEvents(root, [measurement("2026-08-02T02:00:00.000Z")]);
    const result = inspect({ stateRoot: root, maxAgeMinutes: 30, requireCompaction: false }, now);
    assert.equal(result.active, false);
    assert.equal(result.reason, "future_proxy_measurement");
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("large event files are inspected from a bounded recent tail", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "compactor-status-large-"));
  const now = Date.parse("2026-08-02T01:00:00.000Z");
  try {
    writeEvents(root, [
      ...Array.from({ length: 70_000 }, () => ({ type: "noise" })),
      measurement("2026-08-02T00:59:00.000Z"),
    ]);
    const result = inspect({ stateRoot: root, maxAgeMinutes: 30, requireCompaction: false }, now);
    assert.equal(result.active, true);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("require-compaction demands a fresh applied compaction", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "compactor-status-required-"));
  const now = Date.parse("2026-08-02T02:00:00.000Z");
  try {
    writeEvents(root, [
      { type: "compaction_decision", at: "2026-08-02T00:00:00.000Z", applied: true },
      measurement("2026-08-02T01:59:00.000Z"),
    ]);
    const result = inspect({ stateRoot: root, maxAgeMinutes: 30, requireCompaction: true }, now);
    assert.equal(result.active, false);
    assert.equal(result.reason, "no_fresh_applied_compaction");
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("incomplete telemetry cannot prove proxy activation", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "compactor-status-incomplete-"));
  const now = Date.parse("2026-08-02T01:00:00.000Z");
  try {
    writeEvents(root, [{
      type: "context_measurement",
      at: "2026-08-02T00:59:00.000Z",
      proxyActive: true,
    }]);
    const result = inspect({ stateRoot: root, maxAgeMinutes: 30, requireCompaction: false }, now);
    assert.equal(result.active, false);
    assert.equal(result.reason, "no_proxy_measurement");
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("status CLI rejects a missing state-root argument", () => {
  assert.throws(() => parseArgs(["--state-root"]), /requires a path/u);
});
