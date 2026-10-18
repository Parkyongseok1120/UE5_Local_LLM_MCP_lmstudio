"use strict";

// Failure and partial results give one safe, observed next step: a missing
// path names an existing place to search, a denied root names the allowed
// roots and an observed Assets path, and a page cut by the budget keeps a
// cursor instead of failing. Nothing here widens the path policy.

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { createRuntime } = require("../src/server");

function fixture(t) {
  const root = fs.realpathSync.native(fs.mkdtempSync(path.join(os.tmpdir(), "unity-guidance-")));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  for (const folder of ["Assets/Scripts/Player", "Packages", "ProjectSettings", "Library/EvidenceFirst"])
    fs.mkdirSync(path.join(root, folder), { recursive: true });
  fs.writeFileSync(path.join(root, "Packages/manifest.json"), "{}");
  fs.writeFileSync(path.join(root, "ProjectSettings/ProjectVersion.txt"), "m_EditorVersion: 6000.3.14f1");
  fs.writeFileSync(path.join(root, "Assets/Scripts/Player/PlayerHealth.cs"), "class PlayerHealth {}\n");
  const runtime = createRuntime({ UNITY_PROJECT_ROOT: root, ALLOW_WRITE: "0" });
  t.after(() => runtime.close());
  return { root, ...runtime };
}

test("a missing file is not_found without a host path and names an existing directory to search", async t => {
  const f = fixture(t);
  const missing = await f.call("read_file", { path: "Assets/Scripts/Player/Missing/PlayerHealth.cs" });
  assert.equal(missing.errorCode, "not_found");
  assert.equal(missing.message, "Not found: Assets/Scripts/Player/Missing/PlayerHealth.cs");
  assert.ok(!missing.message.includes(f.root));
  assert.equal(missing.observationState, "unavailable");
  assert.deepEqual(missing.suggestion, { tool: "search_files", args: { path: "Assets/Scripts/Player", query: "PlayerHealth.cs", matchFileNames: true } });
  const found = await f.call(missing.suggestion.tool, missing.suggestion.args);
  assert.deepEqual(found.items.map(item => item.path), ["Assets/Scripts/Player/PlayerHealth.cs"]);
  const directory = await f.call("list_directory", { path: "Assets/Scripts/Enemy" });
  assert.equal(directory.errorCode, "not_found");
  assert.deepEqual(directory.suggestion, { tool: "search_files", args: { path: "Assets/Scripts", query: "Enemy", matchFileNames: true } });
});

test("a path outside the observable roots lists them and suggests the observed Assets path", async t => {
  const f = fixture(t);
  const denied = await f.call("read_file", { path: "Scripts/Player/PlayerHealth.cs", startLine: 1 });
  assert.equal(denied.errorCode, "path_denied");
  assert.deepEqual(denied.allowedRoots, ["Assets", "Packages", "ProjectSettings", "package://"]);
  assert.deepEqual(denied.suggestion, { tool: "read_file", args: { path: "Assets/Scripts/Player/PlayerHealth.cs", startLine: 1 } });
  const unknown = await f.call("read_file", { path: "Library/EvidenceFirst/bridge.json" });
  assert.equal(unknown.errorCode, "path_denied");
  assert.equal(unknown.suggestion, undefined, "no guessed path when Assets/<path> does not exist");
});

test("an absolute path inside the project names its project-relative form; a trailing slash is the same directory", async t => {
  const f = fixture(t);
  const absolute = await f.call("read_file", { path: path.join(f.root, "Assets", "Scripts", "Player", "PlayerHealth.cs") });
  assert.equal(absolute.errorCode, "invalid_path");
  assert.deepEqual(absolute.suggestion, { tool: "read_file", args: { path: "Assets/Scripts/Player/PlayerHealth.cs" } });
  const outside = await f.call("read_file", { path: path.join(os.tmpdir(), "elsewhere.cs") });
  assert.equal(outside.errorCode, "invalid_path");
  assert.equal(outside.suggestion, undefined);
  const slash = await f.call("list_directory", { path: "Assets/Scripts/" });
  assert.equal(slash.errorCode, undefined, JSON.stringify(slash));
  assert.deepEqual(slash.items.map(item => item.path), ["Assets/Scripts/Player"]);
  assert.equal((await f.call("list_directory", { path: "Assets//Scripts" })).errorCode, "invalid_path");
});

test("a page larger than the budget is cut to fit with a cursor that reaches every row", async t => {
  const f = fixture(t);
  for (let i = 0; i < 40; i++) {
    fs.writeFileSync(path.join(f.root, "Assets/Scripts", `Component${String(i).padStart(2, "0")}WithAVeryLongDescriptiveName.cs`), "//\n");
  }
  const args = { path: "Assets/Scripts", query: "Component", limit: 40, byteBudget: 2048 };
  const first = await f.call("search_files", args);
  assert.equal(first.errorCode, undefined, JSON.stringify(first));
  assert.ok(Buffer.byteLength(JSON.stringify(first)) <= 2048);
  assert.equal(first.omissionReason, "response_budget");
  assert.equal(first.requestedLimit, 40);
  assert.equal(first.total, 40);
  assert.ok(first.items.length > 0 && first.items.length < 40);
  assert.deepEqual(first.suggestion, { tool: "search_files", args: { ...args, cursor: first.nextCursor } });
  const rows = [...first.items];
  let page = first;
  while (page.nextCursor) {
    page = await f.call(page.suggestion.tool, page.suggestion.args);
    assert.equal(page.errorCode, undefined, JSON.stringify(page));
    rows.push(...page.items);
  }
  assert.equal(rows.length, 40);
  assert.equal(new Set(rows.map(row => row.path)).size, 40);
});

test("a read cut by its byte budget reports why and how to continue", async t => {
  const f = fixture(t);
  fs.writeFileSync(path.join(f.root, "Assets/Long.cs"), Array.from({ length: 60 }, (_, i) => `// line ${i} ${"x".repeat(40)}`).join("\n"));
  const cut = await f.call("read_file", { path: "Assets/Long.cs", limit: 60, byteBudget: 2048 });
  assert.equal(cut.truncated, true);
  assert.equal(cut.requestedRangeComplete, false);
  assert.equal(cut.omissionReason, "byte_budget");
  assert.equal(cut.effectiveByteBudget, 2048);
  assert.equal(cut.budgetScope, "whole_response");
  assert.deepEqual(cut.suggestion, { tool: "read_file", args: { path: "Assets/Long.cs", limit: 60, byteBudget: 2048, startLine: cut.nextStartLine } });
  const whole = await f.call("read_file", { path: "Assets/Long.cs", limit: 10 });
  assert.equal(whole.truncated, false);
  assert.equal(whole.moreLinesInFile, true);
  assert.equal(whole.omissionReason, "line_limit");
});

// The catalog is a fixed per-round prompt cost. This cap was set from the
// served size on 2026-10-18 (35,977 chars) plus room for the planned Unity
// work; raising it is a deliberate decision, not a side effect.
test("the served Unity tool catalog stays within its prompt budget and names continuation", async t => {
  const f = fixture(t);
  assert.ok(JSON.stringify(f.tools).length <= 37000, `catalog is ${JSON.stringify(f.tools).length} chars`);
  const read = f.tools.find(tool => tool.name === "read_file");
  assert.match(read.description, /moreLinesInFile/);
  assert.match(f.tools.find(tool => tool.name === "search_files").description, /No match is not absence/);
});
