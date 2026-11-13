"use strict";

// Conversation 1794506466121 (16 replace_in_file calls, 11 failed): a range
// edit succeeded with Category=\"PF|LaunchReaction\" in its newText, the
// quote escapes of the JSON read result copied as text, and the header no
// longer compiled. Repairing it, the model sent the same escapes in oldText
// (OLD_TEXT_NOT_FOUND, no hint) and the same call without a receipt four
// times (FILE_SNAPSHOT_REQUIRED). Replayed here against the real runtime.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const { createDirectRuntime } = require("../src/direct-server.js");
const { escapedQuoteIssue } = require("../src/source-escape-guard");

const HEADER = [
  "#pragma once",
  "class AEnemy",
  "{",
  "    void ForceRestartAI();",
  "",
  "    // 커맨드 공격으로 띄워질 때 랜덤 애니메이션 출력",
  "    UFUNCTION(BlueprintCallable, Category=\"PF|LaunchReaction\")",
  "    void PlayRandomLaunchReaction();",
  "};",
  "",
].join("\n");
const ESCAPED = "    UFUNCTION(BlueprintCallable, Category=\\\"PF|LaunchReaction\\\")";

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "escaped-edit-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const projectRoot = path.join(root, "Game");
  fs.mkdirSync(path.join(projectRoot, "Source", "Game"), { recursive: true });
  const projectFile = path.join(projectRoot, "Game.uproject");
  fs.writeFileSync(projectFile, JSON.stringify({ FileVersion: 3 }), "utf8");
  const header = path.join(projectRoot, "Source", "Game", "Enemy.h");
  fs.writeFileSync(header, HEADER, "utf8");
  const stateRoot = path.join(root, "state");
  const runtime = createDirectRuntime({ workspaceRoot: root, configPath: path.join(root, "agent.json"), stateRoot,
    env: { AGENT_STATE_ROOT: stateRoot, ALLOW_WRITE: "1" }, getActiveProject: () => projectFile });
  const call = async (tool, args) => (await runtime.callTool(tool, args, { sessionId: "escaped-edit" })).structuredContent;
  return { call, header, path: "project://Source/Game/Enemy.h" };
}

test("an edit that would write JSON-escaped quotes into C++ is refused, the file untouched", async (t) => {
  const f = fixture(t);
  const read = await f.call("read_file_range", { path: f.path, startLine: 4, endLine: 8 });
  const range = await f.call("replace_in_file", { path: f.path, startLine: 4, endLine: 8, expectedRangeSha256: read.rangeSha256,
    fileVersionReceipt: read.fileVersionReceipt,
    newText: ["    void ForceRestartAI();", "", "    // 커맨드 공격으로 띄워질 때 랜덤 애니메이션 출력", ESCAPED, "    void PlayRandomLaunchReaction();"].join("\n") });
  assert.equal(range.errorCode, "ESCAPED_QUOTE_IN_CODE", JSON.stringify(range));
  assert.equal(range.line, 4);
  assert.equal(range.suggestedText, "    UFUNCTION(BlueprintCallable, Category=\"PF|LaunchReaction\")");
  assert.equal(fs.readFileSync(f.header, "utf8"), HEADER);
  const created = await f.call("write_file", { path: "project://Source/Game/New.h", content: `#pragma once\n#include \\"Enemy.h\\"\n` });
  assert.equal(created.errorCode, "ESCAPED_QUOTE_IN_CODE");
  // A real escaped string, a .ini file and an escape the file already has are fine.
  const string = await f.call("replace_in_file", { path: f.path, fileVersionReceipt: read.fileVersionReceipt,
    oldText: "    void ForceRestartAI();", newText: "    void ForceRestartAI(); // TEXT(\"say \\\"hi\\\"\")" });
  assert.equal(string.ok, true, JSON.stringify(string));
  assert.equal(escapedQuoteIssue("Config/Game.ini", ESCAPED), null);
  assert.equal(escapedQuoteIssue("Enemy.h", ESCAPED, `x\n${ESCAPED}\n`), null);
});

test("oldText that matches only without one level of escaping names those lines", async (t) => {
  const f = fixture(t);
  const read = await f.call("read_file_range", { path: f.path, startLine: 1, endLine: 10 });
  const sent = await f.call("replace_in_file", { path: f.path, fileVersionReceipt: read.fileVersionReceipt,
    oldText: `    // 커맨드 공격으로 띄워질 때 랜덤 애니메이션 출력\n${ESCAPED}\n    void PlayRandomLaunchReaction();`,
    newText: "    void PlayRandomLaunchReaction();" });
  assert.equal(sent.errorCode, "OLD_TEXT_NOT_FOUND");
  assert.match(sent.message, /matches lines 6-8 only with one level of backslash escaping removed/u);
  assert.equal(sent.escapedCandidate.currentRange.startLine, 6);
  assert.match(sent.escapedCandidate.currentRange.text, /Category="PF\|LaunchReaction"/u);
  assert.equal(fs.readFileSync(f.header, "utf8"), HEADER, "diagnosis only, never edited");
  // The candidate's range hash completes the edit.
  const range = await f.call("replace_in_file", { path: f.path, fileVersionReceipt: read.fileVersionReceipt, startLine: 6, endLine: 8,
    expectedRangeSha256: sent.escapedCandidate.currentRange.rangeSha256, newText: "    void PlayRandomLaunchReaction();" });
  assert.equal(range.ok, true, JSON.stringify(range));
});

test("a call that lacks only its version proof is told the exact field to add", async (t) => {
  const f = fixture(t);
  const read = await f.call("read_file_range", { path: f.path, startLine: 6, endLine: 8 });
  const args = { path: f.path, startLine: 6, endLine: 8, expectedRangeSha256: read.rangeSha256,
    newText: "    // 랜덤 애니메이션\n    UFUNCTION(BlueprintCallable)\n    void PlayRandomLaunchReaction();" };
  const missing = await f.call("replace_in_file", args);
  assert.equal(missing.errorCode, "FILE_SNAPSHOT_REQUIRED");
  assert.equal(missing.resendWith.expectedHash, missing.currentSha256);
  assert.match(missing.message, /same call with expectedHash set to currentSha256/u);
  assert.equal(missing.suggestion.tool, "read_file_range", "the read suggestion stays");
  const resent = await f.call("replace_in_file", { ...args, ...missing.resendWith });
  assert.equal(resent.ok, true, JSON.stringify(resent));
  // Not offered when the target is not proven: a stale range hash, or an escaped newText.
  const stale = await f.call("replace_in_file", { ...args, newText: "x" });
  assert.equal(stale.resendWith, undefined);
  const after = await f.call("read_file_range", { path: f.path, startLine: 4, endLine: 4 });
  const escaped = await f.call("replace_in_file", { path: f.path, startLine: 4, endLine: 4, expectedRangeSha256: after.rangeSha256,
    newText: ESCAPED });
  assert.equal(escaped.errorCode, "FILE_SNAPSHOT_REQUIRED");
  assert.equal(escaped.resendWith, undefined, "a resend that would corrupt the file is not offered");
});
