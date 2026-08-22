"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const test = require("node:test");
const { DIRECT_SOURCE_FILES, inspect } = require("../scripts/status.cjs");

test("status verifies every direct prediction-loop source and its index wiring", () => {
  const root = path.resolve(__dirname, "..");
  const result = inspect(root);
  assert.equal(result.ok, true, result.issues.join("; "));
  assert.equal(result.sourceLayoutVerified, true);
  assert.equal(result.executionMode, "transparent_context_only");
  assert.equal(result.modelOwner, "lmstudio_selected_model");
  assert.equal(result.toolsOwner, "lmstudio_selected_model");
  assert.equal(result.runtimeActivationProven, false);
  assert.deepEqual(DIRECT_SOURCE_FILES, [
    "src/index.ts",
    "src/prediction-loop.ts",
    "src/direct-compaction-core.js",
    "src/compaction-tool-memory.js",
    "src/continuity-file-observations.js",
    "src/continuity-memory.js",
    "src/continuity-objectives.js",
    "src/continuity-text.js",
    "src/durable-memory-sanitizer.js",
    "src/direct-config.ts",
  ]);
});

test("status fails closed when a direct runtime source is absent", () => {
  const source = path.resolve(__dirname, "..");
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "compactor-status-"));
  try {
    for (const relative of ["manifest.json", "package.json", ".lmstudio/entry.ts", ...DIRECT_SOURCE_FILES]) {
      const from = path.join(source, relative);
      const to = path.join(root, relative);
      fs.mkdirSync(path.dirname(to), { recursive: true });
      fs.copyFileSync(from, to);
    }
    fs.rmSync(path.join(root, "src", "prediction-loop.ts"));
    const result = inspect(root);
    assert.equal(result.ok, false);
    assert.deepEqual(result.missing, ["src/prediction-loop.ts"]);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("runtime-required status never turns source presence into activation proof", () => {
  const result = spawnSync(process.execPath, [path.resolve(__dirname, "../scripts/status.cjs"), "--require-runtime", "--json"], {
    encoding: "utf8",
  });
  assert.equal(result.status, 3, result.stderr || result.stdout);
  const payload = JSON.parse(result.stdout);
  assert.equal(payload.sourceLayoutVerified, true);
  assert.equal(payload.runtimeActivationProven, false);
});
