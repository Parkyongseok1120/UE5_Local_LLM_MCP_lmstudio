"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const test = require("node:test");
const { atomicWriteText, atomicCreateText, uniqueTempPath } = require("../src/atomic-io");

test("atomicWriteText updates file content", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "atomic-io-"));
  const target = path.join(dir, "sample.txt");
  atomicWriteText(target, "first\n");
  assert.strictEqual(fs.readFileSync(target, "utf8"), "first\n");
  atomicWriteText(target, "second\n");
  assert.strictEqual(fs.readFileSync(target, "utf8"), "second\n");
});

test("atomicWriteText uses unique temp files per write", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "atomic-io-"));
  const target = path.join(dir, "sample.txt");
  atomicWriteText(target, "a\n");
  atomicWriteText(target, "b\n");
  const temps = fs.readdirSync(dir).filter((name) => name.endsWith(".tmp"));
  assert.strictEqual(temps.length, 0);
});

test("uniqueTempPath includes timestamp segment", () => {
  const target = path.join(os.tmpdir(), "sample.txt");
  const temp = uniqueTempPath(target);
  const middle = path.basename(temp).split(".");
  assert.ok(Number(middle[2]) > 0);
});

test("atomicCreateText rejects existing file", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "atomic-io-"));
  const target = path.join(dir, "new.txt");
  atomicCreateText(target, "one\n");
  assert.throws(() => atomicCreateText(target, "two\n"), /EEXIST|exists/i);
});

test("atomicCreateText allows exactly one concurrent create", async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "atomic-io-"));
  const target = path.join(dir, "race.txt");
  const attempts = await Promise.allSettled(
    Array.from({ length: 20 }, () =>
      Promise.resolve().then(() => atomicCreateText(target, "winner\n"))
    )
  );
  const fulfilled = attempts.filter((entry) => entry.status === "fulfilled");
  const rejected = attempts.filter((entry) => entry.status === "rejected");
  assert.strictEqual(fulfilled.length, 1);
  assert.strictEqual(rejected.length, 19);
  assert.strictEqual(fs.readFileSync(target, "utf8"), "winner\n");
});
