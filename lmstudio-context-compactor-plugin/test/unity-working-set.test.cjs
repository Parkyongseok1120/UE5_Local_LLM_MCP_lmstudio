"use strict";

// The working-set pin against real Unity MCP results: Unity returns a read
// body in `text` (Unreal: `content`), reads a file in line pages, and reports
// an edit as {status:"applied", operation:"modified"} without `ok`. The first
// version of the review pin missed all three.

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { Chat, ChatMessage } = require("@lmstudio/sdk");
const { WorkingContext } = require("../src/working-context.js");
const { createRuntime } = require("../../lmstudio-unity-mcp/src/server");

function unityProject(t) {
  const root = fs.realpathSync.native(fs.mkdtempSync(path.join(os.tmpdir(), "unity-working-set-")));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  for (const folder of ["Assets/Scripts", "Packages", "ProjectSettings", "Library/EvidenceFirst"]) fs.mkdirSync(path.join(root, folder), { recursive: true });
  fs.writeFileSync(path.join(root, "Packages/manifest.json"), "{}");
  fs.writeFileSync(path.join(root, "ProjectSettings/ProjectVersion.txt"), "m_EditorVersion: 2022.3.0f1");
  const write = (file, lines) => fs.writeFileSync(path.join(root, file),
    Array.from({ length: lines }, (_, i) => `    int value${i} = Compute(${i}); // ${path.basename(file)} line ${i + 1}`).join("\n"));
  write("Assets/Scripts/Player.cs", 250);
  write("Assets/Scripts/Enemy.cs", 40);
  return createRuntime({ UNITY_PROJECT_ROOT: root, ALLOW_WRITE: "1" });
}

function exchange(history, id, name, args, result) {
  history.append(ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest",
    toolCallRequest: { type: "function", id, name, arguments: args } }] }));
  history.append(ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: id,
    content: JSON.stringify(result) }] }));
}

function projected(history, maxChars) {
  const context = new WorkingContext({ conversation: "unity-pin", lineage: `unity-${Math.random()}`, workspace: "w", repository: "r" });
  const toolProviders = { read_file: "mcp/unity-tools", patch_file: "mcp/unity-tools" };
  return context.project(history, () => true, { executionId: "exec", roundIndex: 3, pinnedWorkingSetMaxChars: maxChars, toolProviders }, 512)
    .history.getMessagesArray().filter(message => message.getToolCallResults().length)
    .map(message => JSON.parse(message.getToolCallResults()[0].content));
}

test("every page of a Unity file read in a review stays verbatim", async t => {
  const unity = unityProject(t);
  const history = Chat.from([{ role: "user", content: "Review Player.cs" }]);
  for (const [i, startLine] of [1, 101, 201].entries()) {
    const page = await unity.call("read_file", { path: "Assets/Scripts/Player.cs", startLine, limit: 100 });
    assert.equal(typeof page.text, "string", "Unity returns the body as text");
    assert.equal(page.content, undefined);
    exchange(history, `p${i}`, "read_file", { path: "Assets/Scripts/Player.cs", startLine, limit: 100 }, page);
  }
  const pages = projected(history, 60000);
  assert.deepEqual(pages.map(page => page.kind), ["workspace_file_observation", "workspace_file_observation", "workspace_file_observation"]);
  assert.deepEqual(pages.map(page => page.startLine), [1, 101, 201]);
});

test("a Unity patch makes its file the edit working set", async t => {
  const unity = unityProject(t);
  const history = Chat.from([{ role: "user", content: "Fix Player.cs" }]);
  const player = await unity.call("read_file", { path: "Assets/Scripts/Player.cs", limit: 40 });
  exchange(history, "r1", "read_file", { path: "Assets/Scripts/Player.cs", limit: 40 }, player);
  const enemy = await unity.call("read_file", { path: "Assets/Scripts/Enemy.cs", limit: 40 });
  exchange(history, "r2", "read_file", { path: "Assets/Scripts/Enemy.cs", limit: 40 }, enemy);
  const patch = await unity.call("patch_file", { path: "Assets/Scripts/Player.cs", receipt: player.receipt,
    edits: [{ oldText: "Compute(0)", newText: "Compute(1)" }] });
  assert.equal(patch.status, "applied", JSON.stringify(patch));
  assert.equal(patch.ok, undefined, "Unity edits carry no ok field");
  exchange(history, "w1", "patch_file", { path: "Assets/Scripts/Player.cs" }, patch);
  const [playerRead, enemyRead] = projected(history, 60000);
  assert.equal(playerRead.kind, "workspace_file_observation", "the edited file's read stays raw");
  assert.equal(enemyRead.kind, "archived_tool_result_projection", "an unrelated read is not pinned in an edit turn");
});

test("a path-only archive read picks the Unity page whose lines cover the request", async t => {
  const unity = unityProject(t);
  const history = Chat.from([{ role: "user", content: "Review Player.cs" }]);
  for (const [i, startLine] of [1, 101, 201].entries())
    exchange(history, `p${i}`, "read_file", { path: "Assets/Scripts/Player.cs", startLine, limit: 100 },
      await unity.call("read_file", { path: "Assets/Scripts/Player.cs", startLine, limit: 100 }));
  const context = new WorkingContext({ conversation: "unity-path", lineage: `unity-${Math.random()}`, workspace: "w", repository: "r" });
  context.project(history, () => true, { executionId: "exec", roundIndex: 3, pinnedWorkingSetMaxChars: 0 }, 512);
  // The newest record is lines 201-250; lines 150-152 are only in the 101-200 page,
  // whose range a file observation records as {unit, start, end, total}.
  const span = await context.tool().implementation({ action: "read", path: "Assets/Scripts/Player.cs", view: "body",
    startLine: 150, endLine: 152, maxChars: 4096 }, { signal: new AbortController().signal });
  assert.equal(span.ok, true, JSON.stringify(span));
  assert.match(span.content, /Player\.cs line 150/);
  context.close();
});

test("pages of an older version of a file are not kept next to the newer one", async t => {
  const unity = unityProject(t);
  const history = Chat.from([{ role: "user", content: "Review Player.cs" }]);
  const before = await unity.call("read_file", { path: "Assets/Scripts/Player.cs", startLine: 101, limit: 100 });
  exchange(history, "old", "read_file", { path: "Assets/Scripts/Player.cs", startLine: 101, limit: 100 }, before);
  const after = { ...await unity.call("read_file", { path: "Assets/Scripts/Player.cs", limit: 100 }), sha256: "f".repeat(64), hash: "f".repeat(64) };
  exchange(history, "new", "read_file", { path: "Assets/Scripts/Player.cs", limit: 100 }, after);
  const [oldPage, newPage] = projected(history, 60000);
  assert.equal(oldPage.kind, "archived_tool_result_projection");
  assert.equal(newPage.kind, "workspace_file_observation");
});
