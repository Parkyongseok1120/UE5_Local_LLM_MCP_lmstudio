"use strict";

// Conversation 1794590083146 (turn 2, call 28): after four range edits that
// lacked newText, the model sent one whose lines began with the two characters
// backslash and "t". The edit succeeded, CEnemyCharacterBase.cpp failed to
// compile (C2017), and ten more rounds went into the repair. Replayed here
// against the real runtime on a tab-indented CRLF file.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const { createDirectRuntime } = require("../src/direct-server.js");
const { escapedWhitespaceIssue } = require("../src/source-escape-guard");

const T = "\t";
const SOURCE = [
  "#include \"Enemy.h\"",
  "",
  "AEnemy::AEnemy()",
  "{",
  `${T}if (UCapsuleComponent* Cap = GetCapsuleComponent())`,
  `${T}{`,
  `${T}${T}// radius`,
  `${T}${T}Cap->SetCapsuleRadius(RVOAvoidanceRadius);`,
  `${T}}`,
  "}",
  "",
].join("\r\n");
// What the model sent: every tab as backslash + t.
const LITERAL = ["\\tif (UCapsuleComponent* Cap = GetCapsuleComponent())", "\\t{", "\\t\\t// radius",
  "\\t\\t// Cap->SetCapsuleRadius(RVOAvoidanceRadius);", "\\t}"].join("\n");
const REAL = LITERAL.replace(/\\t/gu, T);

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "escaped-whitespace-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const projectRoot = path.join(root, "Game");
  fs.mkdirSync(path.join(projectRoot, "Source", "Game"), { recursive: true });
  const projectFile = path.join(projectRoot, "Game.uproject");
  fs.writeFileSync(projectFile, JSON.stringify({ FileVersion: 3 }), "utf8");
  const source = path.join(projectRoot, "Source", "Game", "Enemy.cpp");
  fs.writeFileSync(source, SOURCE, "utf8");
  const stateRoot = path.join(root, "state");
  const runtime = createDirectRuntime({ workspaceRoot: root, configPath: path.join(root, "agent.json"), stateRoot,
    env: { AGENT_STATE_ROOT: stateRoot, ALLOW_WRITE: "1" }, getActiveProject: () => projectFile });
  const call = async (tool, args) => (await runtime.callTool(tool, args, { sessionId: "escaped-whitespace" })).structuredContent;
  return { call, source, path: "project://Source/Game/Enemy.cpp" };
}

test("a range edit that would write backslash-t into C++ is refused, the file untouched", async (t) => {
  const f = fixture(t);
  const read = await f.call("read_file_range", { path: f.path, startLine: 5, endLine: 9 });
  const args = { path: f.path, startLine: 5, endLine: 9, expectedRangeSha256: read.rangeSha256, fileVersionReceipt: read.fileVersionReceipt };
  const refused = await f.call("replace_in_file", { ...args, newText: LITERAL });
  assert.equal(refused.errorCode, "ESCAPED_WHITESPACE_IN_CODE", JSON.stringify(refused));
  assert.equal(refused.line, 5, "the file line after the edit");
  assert.deepEqual(refused.escapes, ["\\t"]);
  assert.equal(refused.suggestedText, `${T}if (UCapsuleComponent* Cap = GetCapsuleComponent())`);
  assert.match(refused.message, /a backslash followed by "t"/u);
  assert.equal(refused.retry.mode, "different_arguments");
  assert.equal(fs.readFileSync(f.source, "utf8"), SOURCE);
  // One line of newText that carries the line breaks as backslash-n too.
  const oneLine = await f.call("replace_in_file", { ...args, newText: LITERAL.replace(/\n/gu, "\\n") });
  assert.equal(oneLine.errorCode, "ESCAPED_WHITESPACE_IN_CODE");
  assert.deepEqual(oneLine.escapes.sort(), ["\\n", "\\t"]);
  // The same edit with real tabs applies and keeps the file's CRLF.
  const applied = await f.call("replace_in_file", { ...args, newText: REAL });
  assert.equal(applied.ok, true, JSON.stringify(applied));
  assert.equal(fs.readFileSync(f.source, "utf8"), SOURCE.replace(`${T}${T}Cap->`, `${T}${T}// Cap->`));
});

test("oldText edits, bundles, new files and the receipt resend all refuse the same text", async (t) => {
  const f = fixture(t);
  const read = await f.call("read_file_range", { path: f.path, startLine: 1, endLine: 10 });
  const exact = await f.call("replace_in_file", { path: f.path, fileVersionReceipt: read.fileVersionReceipt,
    oldText: `${T}${T}Cap->SetCapsuleRadius(RVOAvoidanceRadius);`, newText: "\\t\\t// Cap->SetCapsuleRadius(RVOAvoidanceRadius);" });
  assert.equal(exact.errorCode, "ESCAPED_WHITESPACE_IN_CODE", JSON.stringify(exact));
  const bundle = await f.call("apply_edit_bundle", { patches: [{ path: f.path, fileVersionReceipt: read.fileVersionReceipt,
    startLine: 5, endLine: 9, expectedRangeSha256: (await f.call("read_file_range", { path: f.path, startLine: 5, endLine: 9 })).rangeSha256, newText: LITERAL }] });
  assert.equal(bundle.errorCode, "ESCAPED_WHITESPACE_IN_CODE", JSON.stringify(bundle));
  const created = await f.call("write_file", { path: "project://Source/Game/New.cpp", content: "void F()\n{\n\\tG();\n}\n" });
  assert.equal(created.errorCode, "ESCAPED_WHITESPACE_IN_CODE");
  const range = await f.call("read_file_range", { path: f.path, startLine: 5, endLine: 9 });
  const noProof = await f.call("replace_in_file", { path: f.path, startLine: 5, endLine: 9, expectedRangeSha256: range.rangeSha256, newText: LITERAL });
  assert.equal(noProof.errorCode, "FILE_SNAPSHOT_REQUIRED");
  assert.equal(noProof.resendWith, undefined, "a resend that would corrupt the file is not offered");
  assert.equal(fs.readFileSync(f.source, "utf8"), SOURCE);
  // oldText sent with the tab as backslash-t names the lines it meant.
  const escapedOld = await f.call("replace_in_file", { path: f.path, fileVersionReceipt: read.fileVersionReceipt,
    oldText: "\\t\\tCap->SetCapsuleRadius(RVOAvoidanceRadius);", newText: `${T}${T}// removed` });
  assert.equal(escapedOld.errorCode, "OLD_TEXT_NOT_FOUND", JSON.stringify(escapedOld));
  assert.equal(escapedOld.escapedCandidate.currentRange.startLine, 8);
});

test("backslashes that C and C++ allow are not reported", () => {
  const ok = (name, text, previous = "") => assert.equal(escapedWhitespaceIssue(name, text, previous), null, text);
  ok("A.cpp", "FString S = TEXT(\"a\\tb\\n\");\nTCHAR C = '\\t';\nchar N = '\\n';");
  ok("A.cpp", "// see C:\\temp\\new and \\todo\n/* \\return\n   \\note x */\nint a;");
  ok("A.h", "#define LOG(x) \\\n\tprintf(#x \"\\n\"); \\\n\tFlush()");
  ok("A.cpp", "const char* Shader = \"float4 Main()\\n\\\n\t/*Normal*/ %s,\\n\\\n\treturn 0;\\n\";");
  ok("A.cpp", "auto R = R\"(\\t literal \\n text)\";\nauto M = R\"x(\nline \\t one\n)x\";\nint b;");
  ok("A.cpp", "#include <sys\\types.h>\n#pragma message(\"a\\tb\")\n#error bad \\tool");
  ok("Config/Game.ini", "Path=C:\\temp\\new");
  ok("Tool.cs", "var s = @\"C:\\temp\\new\";\nvar t = $\"{a}\\n\";");
  // A line the file already had is not this edit's text.
  ok("A.cpp", "\\told();\nint c;", "int x;\n\\told();");
  const hit = escapedWhitespaceIssue("A.cpp", "int a;\n\\tint b;\nint c;", "int a;\nint c;");
  assert.deepEqual([hit.line, hit.text, hit.suggestedText], [2, "\\tint b;", "\tint b;"]);
});

test("an edit inside a string opened above the edited lines is judged with the whole file", async (t) => {
  const f = fixture(t);
  const shader = ["const char* Shader = \"float4 Main()\\n\\", `${T}/*Normal*/ %s,\\n\\`, `${T}return 0;\\n\";`, ""].join("\r\n");
  fs.writeFileSync(f.source, shader, "utf8");
  const read = await f.call("read_file_range", { path: f.path, startLine: 2, endLine: 2 });
  const applied = await f.call("replace_in_file", { path: f.path, startLine: 2, endLine: 2, expectedRangeSha256: read.rangeSha256,
    fileVersionReceipt: read.fileVersionReceipt, newText: `${T}/*Normal*/ %s, %s,\\n\\` });
  assert.equal(applied.ok, true, JSON.stringify(applied));
});
