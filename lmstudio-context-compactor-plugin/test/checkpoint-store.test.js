"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const store = require("../src/checkpoint-store.js");

test("checkpoint store keeps the newest 20 generations and active checkpoint", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-store-"));
  try {
    for (let generation = 1; generation <= 25; generation += 1) {
      await store.saveCheckpoint("session", {
        schemaVersion: 1,
        checkpointGeneration: generation,
        completedToolCallIds: [],
      }, root);
    }
    const dir = store.sessionDir("session", root);
    const generations = fs.readdirSync(dir).filter((name) => /^checkpoint-\d+\.json$/.test(name));
    assert.equal(generations.length, 20);
    assert.equal(generations.includes("checkpoint-000001.json"), false);
    assert.equal(generations.includes("checkpoint-000025.json"), true);
    const active = await store.loadCheckpoint("session", root);
    assert.equal(active.checkpointGeneration, 25);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("session cleanup applies bounded retention and preserves active or corrupt sessions", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-gc-"));
  const now = Date.parse("2026-08-02T00:00:00.000Z");
  try {
    const sessions = ["completed-old", "cancelled-old", "inactive-old", "active-old", "corrupt-old"];
    for (const session of sessions) {
      const dir = store.sessionDir(session, root);
      fs.mkdirSync(dir, { recursive: true });
      fs.writeFileSync(path.join(dir, "events.jsonl"), "{}\n", "utf8");
    }
    await store.markSessionStatus("completed-old", "completed", root);
    await store.markSessionStatus("cancelled-old", "cancelled", root);
    await store.markSessionStatus("active-old", "active", root);
    fs.writeFileSync(
      path.join(store.sessionDir("corrupt-old", root), "active-checkpoint.corrupt-1.json"),
      "{broken",
      "utf8",
    );
    const old = new Date(now - 120 * 24 * 60 * 60 * 1000);
    for (const session of sessions) {
      const dir = store.sessionDir(session, root);
      for (const name of fs.readdirSync(dir)) {
        fs.utimesSync(path.join(dir, name), old, old);
      }
      fs.utimesSync(dir, old, old);
    }

    const result = await store.cleanupSessions(root, { nowMs: now });

    assert.equal(result.deleted, 3);
    assert.equal(fs.existsSync(store.sessionDir("completed-old", root)), false);
    assert.equal(fs.existsSync(store.sessionDir("cancelled-old", root)), false);
    assert.equal(fs.existsSync(store.sessionDir("inactive-old", root)), false);
    assert.equal(fs.existsSync(store.sessionDir("active-old", root)), true);
    assert.equal(fs.existsSync(store.sessionDir("corrupt-old", root)), true);
    assert.equal(result.skippedCorrupt, 1);
    assert.equal(result.skippedActive, 1);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("corrupt active checkpoint is quarantined and does not block generation", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-corrupt-"));
  try {
    const dir = store.sessionDir("session", root);
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, "active-checkpoint.json"), "{broken", "utf8");
    const checkpoint = await store.loadCheckpoint("session", root);
    assert.equal(checkpoint, null);
    assert.ok(fs.readdirSync(dir).some((name) => name.startsWith("active-checkpoint.corrupt-")));
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("newest durable generation recovers a stale active checkpoint", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-recover-generation-"));
  try {
    await store.saveCheckpoint("session", {
      schemaVersion: 1,
      checkpointGeneration: 1,
      completedToolCallIds: [],
    }, root);
    const dir = store.sessionDir("session", root);
    fs.writeFileSync(
      path.join(dir, "checkpoint-000002.json"),
      JSON.stringify({
        schemaVersion: 1,
        checkpointGeneration: 2,
        completedToolCallIds: [],
      }),
      "utf8",
    );

    const checkpoint = await store.loadCheckpoint("session", root);
    assert.equal(checkpoint.checkpointGeneration, 2);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("corrupt active checkpoint falls back to the newest durable generation", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-recover-corrupt-"));
  try {
    await store.saveCheckpoint("session", {
      schemaVersion: 1,
      checkpointGeneration: 7,
      completedToolCallIds: [],
    }, root);
    const dir = store.sessionDir("session", root);
    fs.writeFileSync(path.join(dir, "active-checkpoint.json"), "{broken", "utf8");

    const checkpoint = await store.loadCheckpoint("session", root);
    assert.equal(checkpoint.checkpointGeneration, 7);
    assert.ok(fs.readdirSync(dir).some((name) => name.startsWith("active-checkpoint.corrupt-")));
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
