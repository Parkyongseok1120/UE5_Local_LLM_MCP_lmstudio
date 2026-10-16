"use strict";

const assert = require("assert");
const { EventEmitter } = require("events");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { PassThrough } = require("stream");
const test = require("node:test");

const {
  BoundedProcessOutput,
  runBoundedProcess,
} = require("../src/bounded-process-runner");

function fakeChild(pid = 1234) {
  const child = new EventEmitter();
  child.pid = pid;
  child.stdout = new PassThrough();
  child.stderr = new PassThrough();
  return child;
}

test("bounded output preserves the beginning and end without retaining the middle", () => {
  const owner = new BoundedProcessOutput(1024);
  owner.push(Buffer.alloc(700, "A"));
  owner.push(Buffer.alloc(8192, "M"));
  owner.push(Buffer.alloc(700, "Z"));

  const rendered = Buffer.concat(owner.chunks()).toString("utf8");
  const summary = owner.summary();
  assert.strictEqual(summary.truncated, true);
  assert.strictEqual(summary.capturedBytes, 1024);
  assert.strictEqual(summary.totalBytes, 9592);
  assert.ok(summary.omittedBytes > 0);
  assert.ok(rendered.startsWith("A".repeat(512)));
  assert.ok(rendered.endsWith("Z".repeat(512)));
  assert.match(rendered, /process-output bytes omitted/);
});

test("runner bounds huge output and persists only the bounded projection", async () => {
  const child = fakeChild();
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "bounded-process-"));
  const logPath = path.join(root, "process.log");
  try {
    const pending = runBoundedProcess({
      start: () => child,
      timeoutMs: 5000,
      logPath,
      maxOutputBytes: 1024,
    });
    child.stdout.write(Buffer.alloc(2 * 1024 * 1024, "X"));
    child.stdout.end(Buffer.alloc(1024, "Y"));
    child.emit("close", 0);
    const result = await pending;

    assert.strictEqual(result.exitCode, 0);
    assert.strictEqual(result.stdoutCapture.truncated, true);
    assert.strictEqual(result.stdoutCapture.capturedBytes, 1024);
    assert.ok(result.stdout.length < 1400);
    assert.strictEqual(fs.readFileSync(logPath, "utf8"), result.stdout);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("timeout owns a close race and settles exactly once", async () => {
  const child = fakeChild(4321);
  let terminateCount = 0;
  const result = await runBoundedProcess({
    start: () => child,
    timeoutMs: 5,
    maxOutputBytes: 1024,
    terminate: async () => {
      terminateCount += 1;
      child.emit("close", 0);
    },
  });
  assert.strictEqual(terminateCount, 1);
  assert.strictEqual(result.timedOut, true);
  assert.strictEqual(result.exitCode, 1);
});

test("log write failure is data, not an unhandled rejection", async () => {
  const child = fakeChild();
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "bounded-log-failure-"));
  const blocked = path.join(root, "blocked");
  fs.writeFileSync(blocked, "file", "utf8");
  try {
    const pending = runBoundedProcess({
      start: () => child,
      timeoutMs: 5000,
      logPath: path.join(blocked, "process.log"),
      maxOutputBytes: 1024,
    });
    child.emit("close", 0);
    const result = await pending;
    assert.ok(result.logPersistenceError);
    assert.strictEqual(result.timedOut, false);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("pre-aborted runner does not start or persist a log", async () => {
  const controller = new AbortController(); controller.abort();
  const result = await runBoundedProcess({ signal: controller.signal, timeoutMs: 5,
    start: () => { throw Error("must not start"); }, logPath: "not-written", persistLog: () => { throw Error("must not write"); } });
  assert.strictEqual(result.cancelled, true); assert.strictEqual(result.processStarted, false);
  assert.strictEqual(result.terminationStatus, "not_started"); assert.strictEqual(result.spawnError, "");
});

for (const close of [false, true]) test(`hanging terminator cannot retain timeout response (close=${close})`, async () => {
  const child = fakeChild();
  const result = await runBoundedProcess({ start: () => child, timeoutMs: 5, shutdownTimeoutMs: 15,
    terminate: () => { if (close) child.emit("close", 0); return new Promise(() => {}); } });
  assert.strictEqual(result.timedOut, true); assert.strictEqual(result.exitCode, 1);
  assert.strictEqual(result.processExited, close);
  assert.strictEqual(result.terminationStatus, close ? "process_exited" : "unconfirmed");
  child.stdout.write("late output"); child.emit("close", 0);
});

test("abort, close and rejecting termination settle once without claiming successful execution", async () => {
  const child = fakeChild(); const controller = new AbortController(); let kills = 0;
  const pending = runBoundedProcess({ start: () => child, timeoutMs: 5000, shutdownTimeoutMs: 10,
    signal: controller.signal, terminate: () => { kills++; throw Error("cleanup failure"); } });
  controller.abort();
  const result = await pending;
  assert.strictEqual(result.cancelled, true); assert.strictEqual(kills, 1); assert.strictEqual(result.processExited, false);
  assert.match(result.terminationError, /cleanup failure/);
  assert.strictEqual(require("node:events").getEventListeners(controller.signal, "abort").length, 0);
});

test("log persistence itself has a bounded response deadline", async () => {
  const child = fakeChild();
  const pending = runBoundedProcess({ start: () => child, timeoutMs: 5000, logPath: "fake", logTimeoutMs: 10,
    persistLog: () => new Promise(() => {}) });
  child.emit("close", 0);
  const result = await pending;
  assert.strictEqual(result.processExited, true); assert.match(result.logPersistenceError, /deadline/);
});

for (const close of [false, true]) test(`post-spawn error retains cleanup ownership (close=${close})`, async () => {
  const child = fakeChild(); let kills = 0;
  const pending = runBoundedProcess({ start: () => child, timeoutMs: 5000, shutdownTimeoutMs: 10,
    terminate: () => { kills++; if (close) child.emit("close", 0); return new Promise(() => {}); } });
  child.emit("error", Error("running child error"));
  const result = await pending;
  assert.strictEqual(kills, 1); assert.strictEqual(result.exitCode, 1);
  assert.strictEqual(result.processStarted, true); assert.strictEqual(result.processExited, close);
  assert.strictEqual(result.terminationStatus, close ? "process_exited" : "unconfirmed");
  assert.match(result.spawnError, /running child error/);
  child.emit("error", Error("late child error"));
});

test("spawn failure without a pid does not request process termination", async () => {
  const child = fakeChild(); delete child.pid; let kills = 0;
  const pending = runBoundedProcess({ start: () => child, timeoutMs: 5000, terminate: () => { kills++; } });
  child.emit("error", Error("could not spawn"));
  const result = await pending;
  assert.strictEqual(kills, 0); assert.strictEqual(result.processStarted, false);
  assert.strictEqual(result.terminationStatus, "not_started"); assert.match(result.spawnError, /could not spawn/);
});
