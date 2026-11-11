"use strict";

// Conversations 2026-11-11/12: every path-less search_files failed with "read
// path escapes workspace through symlink/junction". It searched WORKSPACE_ROOT
// (another project) and a Saved/.../node_modules junction pointing outside it
// failed the whole walk. Links are skipped now, and a path-less search with a
// project looks in that project.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const { createDirectRuntime } = require("../src/direct-server.js");

function fixture(t, { withProject = true } = {}) {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), "link-search-"));
  t.after(() => fs.rmSync(base, { recursive: true, force: true }));
  const workspaceRoot = path.join(base, "Workspace");
  const outside = path.join(base, "Outside");
  fs.mkdirSync(path.join(outside, "pkg"), { recursive: true });
  fs.writeFileSync(path.join(outside, "pkg", "secret.txt"), "MARKER outside\n", "utf8");
  // Another project in the workspace, with its own matching file and an outward junction.
  const other = path.join(workspaceRoot, "OtherProject");
  fs.mkdirSync(path.join(other, "Saved", "build"), { recursive: true });
  fs.writeFileSync(path.join(other, "Other.txt"), "MARKER other project\n", "utf8");
  fs.symlinkSync(outside, path.join(other, "Saved", "build", "node_modules"), "junction");
  // The selected project, outside the workspace like PoisonMainGame, also with links.
  const projectRoot = path.join(base, "Game");
  const projectFile = path.join(projectRoot, "Game.uproject");
  fs.mkdirSync(path.join(projectRoot, "Source", "Game"), { recursive: true });
  fs.writeFileSync(projectFile, JSON.stringify({ FileVersion: 3 }), "utf8");
  fs.writeFileSync(path.join(projectRoot, "Source", "Game", "Game.cpp"), "// MARKER game\n", "utf8");
  fs.symlinkSync(outside, path.join(projectRoot, "Source", "linked_out"), "junction");
  fs.symlinkSync(path.join(projectRoot, "Source", "Game"), path.join(projectRoot, "Source", "linked_in"), "junction");
  const runtime = createDirectRuntime({ workspaceRoot, configPath: path.join(base, "agent.json"),
    env: { AGENT_STATE_ROOT: path.join(base, "state") }, getActiveProject: () => (withProject ? projectFile : null) });
  return { call: async (name, args) => (await runtime.callTool(name, args)).structuredContent, projectFile };
}

test("a path-less search with a project searches that project and skips links", async (t) => {
  const f = fixture(t);
  const found = await f.call("search_files", { query: "MARKER" });
  assert.equal(found.ok, true, JSON.stringify(found));
  assert.equal(found.resolvedRootType, "active_project");
  assert.deepEqual(found.results.map(result => result.uri), ["project://Source/Game/Game.cpp"],
    "neither the other workspace project nor a linked copy or outside target is searched");
  assert.ok(found.excludedEntries >= 2, "both links are counted as excluded");

  const perCall = await f.call("search_files", { query: "MARKER", project: f.projectFile });
  assert.equal(perCall.resolvedRootType, "active_project");
  const workspace = await f.call("search_files", { query: "MARKER", path: "workspace://" });
  assert.equal(workspace.ok, true, "an explicit workspace search still works past the outward junction");
  assert.deepEqual(workspace.results.map(result => result.uri), ["workspace://OtherProject/Other.txt"]);
});

test("without a project, a path-less search stays on the workspace and survives an outward junction", async (t) => {
  const f = fixture(t, { withProject: false });
  const found = await f.call("search_files", { query: "MARKER" });
  assert.equal(found.ok, true, JSON.stringify(found));
  assert.equal(found.resolvedRootType, "workspace");
  assert.deepEqual(found.results.map(result => result.uri), ["workspace://OtherProject/Other.txt"]);
});

test("a cursor from a path-less search is tied to the root it resolved to", async (t) => {
  const f = fixture(t);
  const first = await f.call("search_files", { query: "MARKER", maxResults: 1 });
  assert.equal(first.ok, true);
  if (first.nextCursor) {
    const elsewhere = await f.call("search_files", { query: "MARKER", path: "workspace://", cursor: first.nextCursor });
    assert.equal(elsewhere.errorCode, "INVALID_ARGUMENT");
  }
});

test("list_directory lists an outward link by name instead of failing", async (t) => {
  const f = fixture(t);
  const listed = await f.call("list_directory", { path: "project://Source" });
  assert.equal(listed.ok, true, JSON.stringify(listed));
  const out = listed.entries.find(entry => entry.name === "linked_out");
  assert.deepEqual(JSON.parse(JSON.stringify(out)), { name: "linked_out", type: "other", outsideRoot: true },
    "as the model receives it: never followed or stat'ed");
  const inside = listed.entries.find(entry => entry.name === "linked_in");
  assert.equal(inside.outsideRoot, undefined, "a link inside the root keeps its existing listing");
  assert.equal(listed.entries.find(entry => entry.name === "Game").type, "directory");

  const throughLink = await f.call("read_file", { path: "project://Source/linked_out/pkg/secret.txt" });
  assert.equal(throughLink.ok, false, "reading through the outward link is still refused");
});

test("range hints ask to keep the same version proof", () => {
  const { calculateReplacement, changedLineRangeMessage } = require("../src/safe-write.js");
  const whitespace = calculateReplacement({ priorContent: "struct S {\n};\n\n// next\n", oldText: "};\n \n// next",
    newText: "};", expectedOccurrences: 1 });
  assert.match(whitespace.error, /Send the same fileVersionReceipt or expectedHash with it/u);
  assert.match(changedLineRangeMessage({ startLine: 4, endLine: 4, newTextLines: [2, 2], fits: true }),
    /changedRange\.rangeSha256; newText holds whole lines\. Send the same fileVersionReceipt or expectedHash/u);
});
