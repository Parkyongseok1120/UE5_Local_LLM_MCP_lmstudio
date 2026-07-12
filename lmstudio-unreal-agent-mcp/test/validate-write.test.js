"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");

const {
  resolveProjectRootForFile,
  validateReplaceOccurrences
} = require("../src/validate-write");

test("expectedOccurrences=1 rejects ambiguous replace", () => {
  const err = validateReplaceOccurrences("hello world hello", "hello", "hi", { expectedOccurrences: 1 });
  assert.ok(err);
  assert.match(String(err), /occurrence mismatch/i);
});

test("expectedOccurrences=1 accepts single match", () => {
  const err = validateReplaceOccurrences("hello world", "hello", "hi", { expectedOccurrences: 1 });
  assert.equal(err, null);
});

test("resolveProjectRootForFile finds game root from plugin source", async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "validate-write-"));
  const projectRoot = path.join(tmp, "MyGame");
  const pluginRoot = path.join(projectRoot, "Plugins", "MyPlugin");
  fs.mkdirSync(path.join(pluginRoot, "Source", "MyPlugin"), { recursive: true });
  const file = path.join(pluginRoot, "Source", "MyPlugin", "MyPluginModule.cpp");
  fs.writeFileSync(file, "// test\n");
  const uproject = path.join(projectRoot, "MyGame.uproject");
  fs.writeFileSync(uproject, "{}");
  const resolved = await resolveProjectRootForFile(file, () => uproject);
  assert.equal(path.normalize(resolved), path.normalize(projectRoot));
});
