"use strict";

// Conversation 1794419304890: edits refused for size were 92-98% unchanged
// lines. A size refusal now names the file lines that actually change and
// their range hash, only after the same version proof an edit needs.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const { createDirectRuntime } = require("../src/direct-server.js");
const { changedLineRange } = require("../src/safe-write.js");

const REL = "Source/Hint/Big.cpp";
const numbered = (from, to, eol = "\n") => Array.from({ length: to - from + 1 }, (_, i) => `line${from + i}`).join(eol);

function fixture(t, body) {
  const workspaceRoot = fs.mkdtempSync(path.join(os.tmpdir(), "oversize-hint-"));
  const projectRoot = path.join(workspaceRoot, "Hint");
  const projectFile = path.join(projectRoot, "Hint.uproject");
  fs.mkdirSync(path.join(projectRoot, "Source", "Hint"), { recursive: true });
  fs.writeFileSync(projectFile, JSON.stringify({ FileVersion: 3 }), "utf8");
  fs.writeFileSync(path.join(projectRoot, REL), body, "utf8");
  t.after(() => fs.rmSync(workspaceRoot, { recursive: true, force: true }));
  const runtime = createDirectRuntime({ workspaceRoot, configPath: path.join(workspaceRoot, "agent-mcp.json"),
    env: { AGENT_STATE_ROOT: path.join(workspaceRoot, "state"), ALLOW_WRITE: "1" }, getActiveProject: () => projectFile });
  const call = async (name, args) => (await runtime.callTool(name, args)).structuredContent;
  return { call, disk: () => fs.readFileSync(path.join(projectRoot, REL), "utf8") };
}

test("a size refusal names the changed file lines, and their range hash replaces just them", async (t) => {
  const body = `${numbered(1, 80, "\r\n")}\r\n`;
  const f = fixture(t, body);
  const read = await f.call("read_file", { path: `project://${REL}` });
  const oldText = numbered(10, 79);
  const newText = oldText.replace("line40", "LINE40");
  const refused = await f.call("replace_in_file", { path: `project://${REL}`, oldText, newText, fileVersionReceipt: read.fileVersionReceipt });
  assert.equal(refused.errorCode, "PATCH_TOO_LARGE");
  assert.match(refused.message, /^newText lines 70 > 64\. Only file line 40 changes \(to newText line 31\): replace just it by range/u);
  assert.equal(refused.changedRange.startLine, 40);
  assert.equal(refused.changedRange.endLine, 40);
  assert.equal(refused.changedRange.text, "line40");
  assert.equal(f.disk(), body, "a hint never edits");

  const range = await f.call("replace_in_file", { path: `project://${REL}`, startLine: 40, endLine: 40,
    expectedRangeSha256: refused.changedRange.rangeSha256, newText: "LINE40", fileVersionReceipt: read.fileVersionReceipt });
  assert.equal(range.ok, true, JSON.stringify(range));
  assert.equal(f.disk(), body.replace("line40\r\n", "LINE40\r\n"));
});

test("an insertion names the neighbouring line to replace together with the added lines", async (t) => {
  const f = fixture(t, `${numbered(1, 80)}\n`);
  const read = await f.call("read_file", { path: `project://${REL}` });
  const oldText = numbered(10, 79);
  const refused = await f.call("replace_in_file", { path: `project://${REL}`, oldText,
    newText: oldText.replace("line40\n", "line40\nADDED\n"), fileVersionReceipt: read.fileVersionReceipt });
  assert.deepEqual([refused.changedRange.startLine, refused.changedRange.endLine, refused.changedRange.insertion], [40, 40, "after"]);
  assert.match(refused.message, /newText only adds newText line 32 after file line 40/u);
});

test("no changed range without version proof, for a non-unique oldText, or for identical text", async (t) => {
  const f = fixture(t, `${numbered(1, 80)}\n${numbered(10, 79)}\n`);
  const read = await f.call("read_file", { path: `project://${REL}` });
  const oldText = numbered(10, 79), newText = oldText.replace("line40", "LINE40");
  const unproven = await f.call("replace_in_file", { path: `project://${REL}`, oldText, newText });
  assert.equal(unproven.errorCode, "PATCH_TOO_LARGE");
  assert.equal(unproven.changedRange, undefined, "no file detail before the version is proven");
  const twice = await f.call("replace_in_file", { path: `project://${REL}`, oldText, newText, fileVersionReceipt: read.fileVersionReceipt });
  assert.equal(twice.changedRange, undefined, "oldText occurs twice");
  const stale = await f.call("replace_in_file", { path: `project://${REL}`, oldText, newText,
    expectedHash: "0".repeat(64) });
  assert.equal(stale.changedRange, undefined, "a stale version gets no file detail");

  assert.deepEqual(changedLineRange("a\nb\n", "a\nb", "a\nb"), { identical: true });
  assert.equal(changedLineRange("abc", "zzz", "y"), null);
});

test("changed lines larger than one region are named without a hash", () => {
  const content = `${numbered(1, 200)}\n`;
  const oldText = numbered(1, 200);
  const newText = oldText.split("\n").map(line => line.toUpperCase()).join("\n");
  const range = changedLineRange(content, oldText, newText, { maxRangeTextChars: 2400, maxPatchLines: 64 });
  assert.deepEqual([range.startLine, range.endLine, range.fits, range.rangeSha256], [1, 200, false, undefined]);
});
