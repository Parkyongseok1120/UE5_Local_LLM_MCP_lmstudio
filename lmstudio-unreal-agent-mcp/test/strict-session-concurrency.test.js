"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawn } = require("node:child_process");

const { createStrictLifecycle } = require("../src/strict-lifecycle.js");

function tempState(t, prefix) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), prefix));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return root;
}

function spawnResumeWorker(stateRoot, sessionId, conversationId) {
  const lifecyclePath = path.resolve(__dirname, "../src/strict-lifecycle.js");
  const source = `
    const { createStrictLifecycle } = require(process.env.STRICT_LIFECYCLE_PATH);
    const lifecycle = createStrictLifecycle({ stateRoot: process.env.STRICT_STATE_ROOT });
    process.on("message", (message) => {
      if (message === "exit") process.exit(0);
    });
    process.on("disconnect", () => process.exit(2));
    const watchdog = setTimeout(() => process.exit(2), 30_000);
    watchdog.unref();
    try {
      lifecycle.resume({
        strictSessionId: process.env.STRICT_SESSION_ID,
        conversationId: process.env.STRICT_CONVERSATION_ID,
        userApproved: true,
      });
      process.send({ type: "result", payload: { ok: true, pid: process.pid } });
    } catch (error) {
      process.send({
        type: "result",
        payload: { ok: false, code: error.code || "", message: error.message },
      });
    }
  `;
  const child = spawn(process.execPath, ["-e", source], {
    env: {
      ...process.env,
      STRICT_LIFECYCLE_PATH: lifecyclePath,
      STRICT_STATE_ROOT: stateRoot,
      STRICT_SESSION_ID: sessionId,
      STRICT_CONVERSATION_ID: conversationId,
    },
    stdio: ["ignore", "ignore", "pipe", "ipc"],
  });
  let stderr = "";
  let resultSettled = false;
  let resolveResult;
  let rejectResult;
  const result = new Promise((resolve, reject) => {
    resolveResult = resolve;
    rejectResult = reject;
  });
  let resolveExit;
  let rejectExit;
  const exit = new Promise((resolve, reject) => {
    resolveExit = resolve;
    rejectExit = reject;
  });
  child.stderr.on("data", (chunk) => { stderr += chunk; });
  child.on("message", (message) => {
    if (resultSettled || message?.type !== "result") return;
    resultSettled = true;
    resolveResult(message.payload);
  });
  child.on("error", (error) => {
    if (!resultSettled) {
      resultSettled = true;
      rejectResult(error);
    }
    rejectExit(error);
  });
  child.on("close", (code) => {
    if (!resultSettled) {
      resultSettled = true;
      rejectResult(new Error(`Strict worker exited before reporting a result: ${stderr}`));
    }
    resolveExit({ code, stderr });
  });
  return { child, result, exit };
}

test("only one process can resume the same orphaned Strict session", async (t) => {
  const stateRoot = tempState(t, "strict-concurrent-resume-");
  const owner = createStrictLifecycle({ stateRoot });
  const session = owner.begin({ conversationId: "chat-race", objective: "Exclusive work" });
  owner.orphanOwned("connection_closed");

  const workers = [
    spawnResumeWorker(stateRoot, session.id, "chat-race"),
    spawnResumeWorker(stateRoot, session.id, "chat-race"),
  ];
  let results;
  try {
    results = await Promise.all(workers.map((worker) => worker.result));
  } finally {
    for (const worker of workers) {
      if (worker.child.connected) worker.child.send("exit");
    }
  }
  const exits = await Promise.all(workers.map((worker) => worker.exit));

  assert.strictEqual(results.filter((payload) => payload.ok).length, 1);
  assert.strictEqual(results.filter((payload) => !payload.ok).length, 1);
  assert.ok(exits.every(({ code }) => code === 0));
});

test("an in-flight operation defers disconnect orphaning and blocks resume", async (t) => {
  const stateRoot = tempState(t, "strict-operation-close-");
  let now = Date.parse("2026-08-22T00:00:00Z");
  const lifecycle = createStrictLifecycle({ stateRoot, clock: () => now });
  const session = lifecycle.begin({
    conversationId: "chat-operation",
    objective: "Run one bounded command",
    ttlSeconds: 60,
  });
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const operation = lifecycle.runOperation(
    session.id,
    "chat-operation",
    "run_command",
    async () => gate,
  );
  await new Promise((resolve) => setImmediate(resolve));

  now += 120_000;
  assert.throws(
    () => lifecycle.status({ strictSessionId: session.id, conversationId: "chat-operation" }),
    (error) => error?.code === "STRICT_SESSION_BUSY",
  );
  lifecycle.orphanOwned("connection_closed");
  const contender = createStrictLifecycle({ stateRoot, clock: () => now });
  assert.throws(
    () => contender.resume({
      strictSessionId: session.id,
      conversationId: "chat-operation",
      userApproved: true,
    }),
    (error) => error?.code === "STRICT_SESSION_BUSY",
  );

  release({ ok: true });
  const completed = await operation;
  assert.deepStrictEqual(completed.result, { ok: true });
  assert.strictEqual(completed.session.status, "orphaned");
  assert.strictEqual(contender.resume({
    strictSessionId: session.id,
    conversationId: "chat-operation",
    userApproved: true,
  }).status, "running");
});

test("a gated operation longer than the TTL renews its lease on completion", async (t) => {
  const stateRoot = tempState(t, "strict-operation-ttl-");
  let now = Date.parse("2026-08-22T00:00:00Z");
  const lifecycle = createStrictLifecycle({ stateRoot, clock: () => now });
  const session = lifecycle.begin({
    conversationId: "chat-long",
    objective: "Long build",
    ttlSeconds: 60,
  });

  const completed = await lifecycle.runOperation(
    session.id,
    "chat-long",
    "build_unreal_project",
    async () => {
      now += 180_000;
      return "built";
    },
  );

  assert.strictEqual(completed.result, "built");
  assert.strictEqual(completed.session.status, "running");
  assert.strictEqual(lifecycle.status({
    strictSessionId: session.id,
    conversationId: "chat-long",
  }).status, "running");
});
