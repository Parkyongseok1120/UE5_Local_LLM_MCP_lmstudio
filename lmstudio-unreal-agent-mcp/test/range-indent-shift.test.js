"use strict";

// Conversation 1794590083146: reads number their lines as "<n>\t<text>", and
// the model kept the tab after the number as part of each line. oldText sent
// that way failed twice with a whitespace diagnosis; the three range edits
// that followed had no text to compare and wrote their lines one tab deeper
// (turn 1 call 44, turn 2 calls 31 and 33). Replayed against the real runtime.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const { createDirectRuntime } = require("../src/direct-server.js");

const T = "\t";
const SOURCE = [
  "float AEnemy::TakeDamage(float Damage)",
  "{",
  `${T}UE_LOG(LogTemp, Warning, TEXT("hit"));`,
  `${T}if (HealthComponent)`,
  `${T}{`,
  `${T}${T}UE_LOG(LogTemp, Warning, TEXT("health"), `,
  `${T}${T}${T}   OldHealth, NewHealth);`,
  `${T}}`,
  `${T}return Damage;`,
  "}",
  "",
].join("\r\n");

function fixture(t, source = SOURCE) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "indent-shift-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const projectRoot = path.join(root, "Game");
  fs.mkdirSync(path.join(projectRoot, "Source", "Game"), { recursive: true });
  const projectFile = path.join(projectRoot, "Game.uproject");
  fs.writeFileSync(projectFile, JSON.stringify({ FileVersion: 3 }), "utf8");
  const file = path.join(projectRoot, "Source", "Game", "Enemy.cpp");
  fs.writeFileSync(file, source, "utf8");
  const stateRoot = path.join(root, "state");
  const runtime = createDirectRuntime({ workspaceRoot: root, configPath: path.join(root, "agent.json"), stateRoot,
    env: { AGENT_STATE_ROOT: stateRoot, ALLOW_WRITE: "1" }, getActiveProject: () => projectFile });
  const call = async (tool, args) => (await runtime.callTool(tool, args, { sessionId: "indent-shift" })).structuredContent;
  const range = async (startLine, endLine, newText) => {
    const read = await call("read_file_range", { path: "project://Source/Game/Enemy.cpp", startLine, endLine });
    return call("replace_in_file", { path: "project://Source/Game/Enemy.cpp", startLine, endLine,
      expectedRangeSha256: read.rangeSha256, fileVersionReceipt: read.fileVersionReceipt, newText });
  };
  return { call, range, file, path: "project://Source/Game/Enemy.cpp" };
}

test("a range edit that repeats the replaced lines one tab deeper is refused with the lines as they are", async (t) => {
  const f = fixture(t);
  // Lines 2-3 as the model saw them ("2\t{" and "3\t\tUE_LOG"), with the guard it wanted to add.
  const shifted = [`${T}{`, `${T}${T}if (!HasAuthority()) return 0.f;`, `${T}${T}UE_LOG(LogTemp, Warning, TEXT("hit"));`].join("\n");
  const refused = await f.range(2, 3, shifted);
  assert.equal(refused.errorCode, "SEPARATOR_TAB_IN_NEW_TEXT", JSON.stringify(refused));
  assert.equal(refused.currentRange.text, `{\n${T}UE_LOG(LogTemp, Warning, TEXT("hit"));`);
  assert.match(refused.message, /Remove that tab from every line of newText/u);
  assert.equal(refused.retry.mode, "different_arguments");
  assert.equal(fs.readFileSync(f.file, "utf8"), SOURCE);
  const applied = await f.range(2, 3, shifted.split("\n").map(line => line.slice(1)).join("\n"));
  assert.equal(applied.ok, true, JSON.stringify(applied));
  assert.equal(applied.changeEvidence.hunks[0].indentAdvisory, undefined);
  const bundle = await f.call("apply_edit_bundle", { patches: [{ path: f.path, fileVersionReceipt: applied.fileVersionReceipt, startLine: 10, endLine: 10,
    expectedRangeSha256: (await f.call("read_file_range", { path: f.path, startLine: 10, endLine: 10 })).rangeSha256,
    newText: `${T}${T}return Damage;` }] });
  assert.equal(bundle.errorCode, "SEPARATOR_TAB_IN_NEW_TEXT", JSON.stringify(bundle));
});

test("new lines that are all one tab deeper apply with a note, since nothing proves the tab was copied", async (t) => {
  const f = fixture(t);
  const deeper = [`${T}${T}${T}// UE_LOG(LogTemp, Warning, TEXT("health"), `, `${T}${T}${T}${T}   // OldHealth, NewHealth);`].join("\n");
  const applied = await f.range(6, 7, deeper);
  assert.equal(applied.ok, true, JSON.stringify(applied));
  assert.match(applied.changeEvidence.hunks[0].indentAdvisory, /one tab deeper than the lines it replaced/u);
});

test("edits that legitimately change depth are neither refused nor noted", async (t) => {
  const f = fixture(t);
  // Wrapping a statement in one edit: the wrapper stays at the old depth.
  const wrapped = await f.range(3, 3, [`${T}if (bVerbose)`, `${T}{`, `${T}${T}UE_LOG(LogTemp, Warning, TEXT("hit"));`, `${T}}`].join("\n"));
  assert.equal(wrapped.ok, true, JSON.stringify(wrapped));
  assert.equal(wrapped.changeEvidence.hunks[0].indentAdvisory, undefined);
  // Re-indenting the body of a block that an earlier edit opened just above it.
  const opened = fixture(t, [`${T}if (bVerbose)`, `${T}{`, `${T}Foo();`, `${T}Bar();`, `${T}}`, ""].join("\n"));
  const body = await opened.range(3, 4, [`${T}${T}Foo();`, `${T}${T}Bar();`].join("\n"));
  assert.equal(body.ok, true, JSON.stringify(body));
  assert.equal(body.changeEvidence.hunks[0].indentAdvisory, undefined);
  // Deleting lines and same-depth replacements are untouched.
  const same = await f.range(1, 1, "float AEnemy::TakeDamage(float Damage, AActor* Causer)");
  assert.equal(same.ok, true);
  assert.equal(same.changeEvidence.hunks[0].indentAdvisory, undefined);
});

test("the copied tab is recognized in a space-indented file too", async (t) => {
  const f = fixture(t, ["void F()", "{", "    Foo();", "    Bar();", "}", ""].join("\n"));
  const refused = await f.range(3, 4, [`${T}    Foo();`, `${T}    Baz();`].join("\n"));
  assert.equal(refused.errorCode, "SEPARATOR_TAB_IN_NEW_TEXT", JSON.stringify(refused));
});
