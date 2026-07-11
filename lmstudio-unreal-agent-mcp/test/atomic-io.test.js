"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const test = require("node:test");
const { atomicWriteText } = require("../src/atomic-io");

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
