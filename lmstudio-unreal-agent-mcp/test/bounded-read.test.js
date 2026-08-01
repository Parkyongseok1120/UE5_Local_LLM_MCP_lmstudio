"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const { readUtf8Tail } = require("../src/bounded-read");

test("readUtf8Tail bounds large logs and drops the partial first line", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "bounded-log-read-"));
  const log = path.join(root, "large.log");
  try {
    fs.writeFileSync(
      log,
      `old-marker ${"x".repeat(4_000)}\ncomplete recent line\nerror C2065: recent-marker\n`,
      "utf8",
    );
    const result = await readUtf8Tail(log, 1_024);
    assert.equal(result.sourceTruncated, true);
    assert.ok(result.bytesRead <= 1_024);
    assert.doesNotMatch(result.content, /old-marker/);
    assert.match(result.content, /complete recent line/);
    assert.match(result.content, /recent-marker/);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("readUtf8Tail returns a complete small file", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "bounded-log-read-small-"));
  const log = path.join(root, "small.log");
  try {
    fs.writeFileSync(log, "alpha\nbeta\n", "utf8");
    const result = await readUtf8Tail(log, 1_024);
    assert.equal(result.sourceTruncated, false);
    assert.equal(result.content, "alpha\nbeta\n");
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
