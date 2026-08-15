"use strict";

const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const {
  locateUtf8LineStart,
  readUtf8LineWindow,
  readUtf8Range,
  readUtf8Tail,
  readUtf8Window,
  sha256File,
  streamFileSha256,
} = require("../src/bounded-read");

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

test("streamFileSha256 hashes a portable Unicode path with bounded stream chunks", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "bounded hash path "));
  const nested = path.join(root, "프로젝트 with spaces", "Source");
  const sourcePath = path.join(nested, "Portable.cpp");
  try {
    fs.mkdirSync(nested, { recursive: true });
    const source = Buffer.from("line-one\r\n🙂 Unreal 경로\n".repeat(20_000), "utf8");
    fs.writeFileSync(sourcePath, source);
    const expected = crypto.createHash("sha256").update(source).digest("hex");

    const result = await streamFileSha256(sourcePath, { highWaterMark: 4_096 });
    assert.equal(result.algorithm, "sha256");
    assert.equal(result.digest, expected);
    assert.equal(result.bytesHashed, source.length);
    assert.equal(result.highWaterMark, 4_096);
    assert.equal(await sha256File(sourcePath, { highWaterMark: 8_192 }), expected);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("readUtf8Window bounds a huge single line and returns a lossless continuation", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "bounded-window-single-line-"));
  const sourcePath = path.join(root, "single-line.txt");
  try {
    fs.writeFileSync(sourcePath, `${"🙂".repeat(300_000)}\n`, "utf8");
    const first = await readUtf8Window(sourcePath, { maxBytes: 4_097 });
    const second = await readUtf8Window(sourcePath, {
      startByte: first.continuation.cursorByte,
      maxBytes: 4_097,
    });

    assert.ok(first.bytesReturned <= 4_097);
    assert.ok(second.bytesReturned <= 4_097);
    assert.equal(Buffer.byteLength(first.content, "utf8"), first.bytesReturned);
    assert.equal(first.hasMore, true);
    assert.equal(first.contentEndsMidLine, true);
    assert.equal(first.continuation.startsMidLine, true);
    assert.ok(second.contentStartByte >= first.contentEndByte);
    assert.doesNotMatch(first.content, /�/u);
    assert.doesNotMatch(second.content, /�/u);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("readUtf8Window realigns an arbitrary cursor inside a UTF-8 code point", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "bounded-window-realign-"));
  const sourcePath = path.join(root, "utf8.txt");
  try {
    fs.writeFileSync(sourcePath, "A🙂B\n", "utf8");
    const result = await readUtf8Window(sourcePath, {
      startByte: 2,
      maxBytes: 4,
    });

    assert.equal(result.leadingUtf8BytesSkipped, 3);
    assert.equal(result.content, "B");
    assert.equal(result.contentStartByte, 5);
    assert.equal(result.nextCursorByte, 6);
    assert.doesNotMatch(result.content, /�/u);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("readUtf8Window never exceeds a caller's sub-codepoint byte cap", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "bounded-window-tiny-cap-"));
  const sourcePath = path.join(root, "emoji.txt");
  try {
    fs.writeFileSync(sourcePath, "🙂next", "utf8");
    const blocked = await readUtf8Window(sourcePath, { maxBytes: 1 });
    const resumed = await readUtf8Window(sourcePath, {
      startByte: blocked.continuation.cursorByte,
      maxBytes: blocked.minimumBytesRequired,
    });

    assert.equal(blocked.maxBytes, 1);
    assert.equal(blocked.bytesReturned, 0);
    assert.equal(blocked.progressBlocked, true);
    assert.equal(blocked.minimumBytesRequired, 4);
    assert.equal(resumed.content, "🙂");
    assert.equal(resumed.bytesReturned, 4);
    assert.equal(resumed.progressBlocked, false);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("readUtf8Window reports the minimum CRLF window and does not hold a bare CR", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "bounded-window-crlf-cap-"));
  const crlfPath = path.join(root, "crlf.txt");
  const bareCrPath = path.join(root, "bare-cr.txt");
  try {
    fs.writeFileSync(crlfPath, "\r\nnext", "utf8");
    fs.writeFileSync(bareCrPath, "\rX", "utf8");
    const blocked = await readUtf8Window(crlfPath, { maxBytes: 1 });
    const resumed = await readUtf8Window(crlfPath, {
      startByte: blocked.nextCursorByte,
      maxBytes: blocked.minimumBytesRequired,
    });
    const bare = await readUtf8Window(bareCrPath, { maxBytes: 1 });

    assert.equal(blocked.bytesReturned, 0);
    assert.equal(blocked.crlfBoundaryHeldBytes, 1);
    assert.equal(blocked.minimumBytesRequired, 2);
    assert.equal(blocked.progressBlocked, true);
    assert.equal(resumed.content, "\r\n");
    assert.equal(bare.content, "\r");
    assert.equal(bare.progressBlocked, false);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("readUtf8LineWindow traverses one oversized CRLF line without loss", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "bounded-line-window-"));
  const sourcePath = path.join(root, "oversized.cpp");
  const oversizedLine = `const TCHAR* Data = TEXT(\"${"한글🙂".repeat(8_000)}\");`;
  try {
    fs.writeFileSync(sourcePath, `header\r\n${oversizedLine}\r\ntail\r\n`, "utf8");
    let result = await readUtf8LineWindow(sourcePath, {
      startLine: 2,
      endLine: 2,
      maxBytes: 1_025,
    });
    const chunks = [];
    let iterations = 0;
    while (true) {
      chunks.push(result.content);
      assert.ok(result.bytesReturned <= 1_025);
      assert.doesNotMatch(result.content, /�/u);
      if (result.rangeComplete) break;
      assert.equal(result.nextStartLine, 2);
      assert.equal(result.continuation.line, 2);
      result = await readUtf8LineWindow(sourcePath, {
        startLine: 2,
        endLine: 2,
        cursorByte: result.continuation.cursorByte,
        cursorLine: result.continuation.line,
        maxBytes: 1_025,
      });
      iterations += 1;
      assert.ok(iterations < 200, "line continuation must make bounded progress");
    }

    assert.equal(chunks.join(""), `${oversizedLine}\r\n`);
    assert.equal(result.contentEndLine, 2);
    assert.equal(result.nextStartLine, 3);
    assert.equal(result.continuation, null);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("line lookup and bounded line windows use platform-native paths only", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "bounded-portable-path-"));
  const nested = path.join(root, "Project Name", "Plugins", "테스트", "Source");
  const sourcePath = path.join(nested, "Portable.inl");
  const lines = Array.from({ length: 300 }, (_, index) => `portable-${index + 1}`);
  try {
    fs.mkdirSync(nested, { recursive: true });
    fs.writeFileSync(sourcePath, `${lines.join("\n")}\n`, "utf8");
    const location = await locateUtf8LineStart(sourcePath, 200, { scanBytes: 127 });
    const result = await readUtf8LineWindow(sourcePath, {
      startLine: 200,
      endLine: 202,
      maxBytes: 128,
      scanBytes: 127,
    });

    assert.equal(location.lineFound, true);
    assert.equal(location.actualLine, 200);
    assert.equal(location.byteOffset, Buffer.byteLength(`${lines.slice(0, 199).join("\n")}\n`));
    assert.equal(result.content, `${lines.slice(199, 202).join("\n")}\n`);
    assert.equal(result.rangeComplete, true);
    assert.equal(result.nextStartLine, 203);
    assert.ok(result.bytesReturned <= 128);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
