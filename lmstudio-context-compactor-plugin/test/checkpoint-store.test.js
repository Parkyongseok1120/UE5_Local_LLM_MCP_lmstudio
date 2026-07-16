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
