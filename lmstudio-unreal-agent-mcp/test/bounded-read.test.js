"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const { readUtf8Range, readUtf8Tail } = require("../src/bounded-read");

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

test("readUtf8Range exposes a stable continuation cursor and drops partial lines", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "bounded-log-range-"));
  const log = path.join(root, "range.log");
  try {
    fs.writeFileSync(log, `${"a".repeat(2_000)}\nfirst error C1000\nlast\n`, "utf8");
    const first = await readUtf8Range(log, 0, 1_024);
    assert.equal(first.requestedStartByte, 0);
    assert.equal(first.nextCursorByte, 1_024);
    assert.equal(first.hasMore, true);
    const second = await readUtf8Range(log, first.nextCursorByte, 1_024);
    assert.doesNotMatch(second.content, /^a+/u);
    assert.match(second.content, /first error C1000/u);
    assert.ok(second.nextCursorByte > first.nextCursorByte);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("readUtf8Range preserves a complete line when the cursor is already aligned", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "bounded-log-aligned-"));
  const log = path.join(root, "aligned.log");
  try {
    fs.writeFileSync(log, "alpha\nerror C2000\nomega\n", "utf8");
    const result = await readUtf8Range(log, Buffer.byteLength("alpha\n"), 1_024);
    assert.equal(result.startsAtLineBoundary, true);
    assert.match(result.content, /^error C2000/u);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("readUtf8Range can preserve a partial leading line for streaming scanners", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "bounded-log-stream-"));
  const log = path.join(root, "stream.log");
  try {
    fs.writeFileSync(log, `${"x".repeat(1_020)}error C3000\n`, "utf8");
    const result = await readUtf8Range(log, 1_024, 1_024, {
      preservePartialLeading: true,
    });
    assert.equal(result.startsAtLineBoundary, false);
    assert.match(result.content, /^r C3000/u);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("readUtf8Range line limit advances only through returned lines", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "bounded-log-lines-"));
  const log = path.join(root, "lines.log");
  try {
    const sourceLines = Array.from(
      { length: 200 },
      (_, index) => `line-${String(index).padStart(3, "0")}`,
    );
    fs.writeFileSync(log, `${sourceLines.join("\n")}\n`, "utf8");

    const first = await readUtf8Range(log, 0, 65_536, {
      preservePartialLeading: true,
      maxLines: 60,
    });
    const second = await readUtf8Range(log, first.nextCursorByte, 65_536, {
      preservePartialLeading: true,
      maxLines: 60,
    });

    assert.deepEqual(first.content.trimEnd().split("\n"), sourceLines.slice(0, 60));
    assert.deepEqual(second.content.trimEnd().split("\n"), sourceLines.slice(60, 120));
    assert.equal(
      first.nextCursorByte,
      Buffer.byteLength(`${sourceLines.slice(0, 60).join("\n")}\n`),
    );
    assert.equal(first.hasMore, true);
    assert.equal(first.lineLimited, true);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("readUtf8Range keeps multibyte UTF-8 intact across byte cursors", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "bounded-log-utf8-"));
  const log = path.join(root, "utf8.log");
  try {
    const source = `${"🙂".repeat(400)}\n끝\n`;
    fs.writeFileSync(log, source, "utf8");
    const first = await readUtf8Range(log, 0, 1_025, { preservePartialLeading: true });
    const second = await readUtf8Range(log, first.nextCursorByte, 1_025, {
      preservePartialLeading: true,
    });

    assert.doesNotMatch(first.content, /�/u);
    assert.doesNotMatch(second.content, /�/u);
    assert.equal(first.content + second.content, source);
    assert.equal(first.nextCursorByte % 4, 0);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
