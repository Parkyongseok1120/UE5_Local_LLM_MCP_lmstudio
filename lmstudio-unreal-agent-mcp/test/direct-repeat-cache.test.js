"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const {
  DirectRepeatCache,
  stableValue,
} = require("../src/direct-repeat-cache.js");

test("Direct repeat signatures are order-stable and exclude only the repeat receipt", () => {
  const cache = new DirectRepeatCache();
  const left = cache.key("read_file", {
    path: "project://Source/Demo.cpp",
    options: { end: 20, start: 1 },
    repeatReceipt: "receipt-a",
  }, "file-hash-a");
  const right = cache.key("read_file", {
    options: { start: 1, end: 20 },
    path: "project://Source/Demo.cpp",
    repeatReceipt: "receipt-b",
  }, "file-hash-a");

  assert.strictEqual(left, right);
  assert.deepStrictEqual(stableValue({ z: 1, a: 2 }), { a: 2, z: 1 });
});

test("successful observations require their opaque receipt before concise suppression", () => {
  const cache = new DirectRepeatCache();
  const args = { path: "project://Source/Demo.cpp", maxBytes: 4096 };
  const remembered = cache.remember("read_file", args, "sha256-a", {
    ok: true,
    content: "large source body".repeat(1000),
  });

  assert.strictEqual(cache.lookup("read_file", args, "sha256-a"), null);
  assert.strictEqual(cache.lookup("read_file", { ...args, repeatReceipt: "forged" }, "sha256-a"), null);
  const duplicate = cache.lookup("read_file", { ...args, repeatReceipt: remembered.repeatReceipt }, "sha256-a");
  assert.deepStrictEqual(
    {
      ok: duplicate.ok,
      duplicate: duplicate.duplicate,
      status: duplicate.status,
      originalOutcome: duplicate.originalOutcome,
    },
    {
      ok: true,
      duplicate: true,
      status: "no_new_information",
      originalOutcome: "success",
    },
  );
  assert.ok(JSON.stringify(duplicate).length < 1024);
  assert.strictEqual(duplicate.content, undefined);
});

test("changed observable state is not treated as a duplicate", () => {
  const cache = new DirectRepeatCache();
  const args = { path: "project://Source/Demo.cpp" };
  const remembered = cache.remember("read_file", args, "sha256-before", { ok: true, content: "before" });

  assert.strictEqual(cache.lookup("read_file", args, "sha256-after"), null);
  assert.strictEqual(cache.lookup("read_file", { ...args, repeatReceipt: remembered.repeatReceipt }, "sha256-after"), null);
  assert.ok(cache.lookup("read_file", { ...args, repeatReceipt: remembered.repeatReceipt }, "sha256-before"));
});

test("expired successful receipt returns the full-result path", () => {
  let now = 10_000;
  const cache = new DirectRepeatCache({ ttlMs: 1000, now: () => now });
  const args = { path: "project://Source/Demo.cpp" };
  const remembered = cache.remember("read_file", args, "sha256-a", { ok: true, content: "source" });
  now += 1001;
  assert.strictEqual(cache.lookup("read_file", { ...args, repeatReceipt: remembered.repeatReceipt }, "sha256-a"), null);
});

test("repeated failures stay failures and become concise non-retryable observations", () => {
  const cache = new DirectRepeatCache();
  const args = { path: "project://Source/Missing.cpp" };
  cache.remember("read_file", args, "missing", {
    ok: false,
    errorCode: "NOT_FOUND",
    control: "large legacy control".repeat(1000),
  });

  const duplicate = cache.lookup("read_file", args, "missing");
  assert.strictEqual(duplicate.ok, false);
  assert.strictEqual(duplicate.status, "no_new_information");
  assert.strictEqual(duplicate.originalOutcome, "failure");
  assert.strictEqual(duplicate.originalErrorCode, "NOT_FOUND");
  assert.deepStrictEqual(duplicate.retry, { allowed: false, mode: "none" });
  assert.ok(JSON.stringify(duplicate).length < 1024);
});
