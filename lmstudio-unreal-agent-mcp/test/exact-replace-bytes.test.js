"use strict";

// Conversation 1794402335258 review: exact-text edits returned ok:true while
// saving "$&"/"$$" expanded and every LF of a mixed file turned into CRLF; a
// duplicated oldText passed the pre-check and failed under the lock as
// PATCH_FAILED; a blank line copied as " " was a bare OLD_TEXT_NOT_FOUND.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const { createDirectRuntime } = require("../src/direct-server.js");
const { calculateReplacement, calculateReplacements } = require("../src/safe-write.js");

function fixture(t) {
  const workspaceRoot = fs.mkdtempSync(path.join(os.tmpdir(), "exact-replace-"));
  const projectRoot = path.join(workspaceRoot, "Bytes");
  const projectFile = path.join(projectRoot, "Bytes.uproject");
  fs.mkdirSync(path.join(projectRoot, "Source", "Bytes"), { recursive: true });
  fs.writeFileSync(projectFile, JSON.stringify({ FileVersion: 3 }), "utf8");
  t.after(() => fs.rmSync(workspaceRoot, { recursive: true, force: true }));
  const runtime = createDirectRuntime({
    workspaceRoot,
    configPath: path.join(workspaceRoot, "agent-mcp.json"),
    env: { AGENT_STATE_ROOT: path.join(workspaceRoot, "state"), ALLOW_WRITE: "1" },
    getActiveProject: () => projectFile,
  });
  const absolute = (relative) => path.join(projectRoot, relative);
  return {
    call: async (name, args) => (await runtime.callTool(name, args)).structuredContent,
    seed: async (relative, body) => {
      fs.writeFileSync(absolute(relative), body, "utf8");
      return (await runtime.callTool("read_file", { path: `project://${relative}` })).structuredContent;
    },
    disk: (relative) => fs.readFileSync(absolute(relative), "utf8"),
  };
}

test("newText is inserted literally: no $& / $$ / $` / $' patterns", async (t) => {
  const f = fixture(t);
  const rel = "Source/Bytes/Bytes.Build.cs";
  const read = await f.seed(rel, "var x = OLD;\n");
  const literal = 'Q($$"""{A}""") [$&] [$`] [$\']';
  const result = await f.call("replace_in_file", { path: `project://${rel}`, oldText: "OLD", newText: literal,
    fileVersionReceipt: read.fileVersionReceipt });
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.equal(f.disk(rel), `var x = ${literal};\n`);
  assert.equal(result.sha256, require("node:crypto").createHash("sha256").update(f.disk(rel), "utf8").digest("hex"));

  const edits = calculateReplacements({ priorContent: "a OLD b", edits: [{ oldText: "OLD", newText: "[$&]" }] });
  assert.equal(edits.updated, "a [$&] b");
});

test("lines outside the matched text keep their own line endings", async (t) => {
  const f = fixture(t);
  const rel = "Source/Bytes/Mixed.h";
  const read = await f.seed(rel, "a\r\nb\nc\nOLD\r\n");
  const single = await f.call("replace_in_file", { path: `project://${rel}`, oldText: "OLD", newText: "NEW",
    fileVersionReceipt: read.fileVersionReceipt });
  assert.equal(single.ok, true, JSON.stringify(single));
  assert.equal(f.disk(rel), "a\r\nb\nc\nNEW\r\n", "the LF lines stay LF");

  const multi = await f.call("replace_in_file", { path: `project://${rel}`,
    edits: [{ oldText: "a", newText: "A" }, { oldText: "c", newText: "C" }], fileVersionReceipt: single.fileVersionReceipt });
  assert.equal(multi.ok, true, JSON.stringify(multi));
  assert.equal(f.disk(rel), "A\r\nb\nC\nNEW\r\n");

  // A match that spans a CRLF consumes the CR with it; new breaks use the file's CRLF style.
  assert.equal(calculateReplacement({ priorContent: "x\r\ny\nz", oldText: "x\ny", newText: "X\nY", expectedOccurrences: 1 }).updated,
    "X\r\nY\nz");
  assert.equal(calculateReplacement({ priorContent: "a\r\nb\r\n", oldText: "a\n", newText: "", expectedOccurrences: 1 }).updated,
    "b\r\n", "no orphan CR is left behind");
  assert.equal(calculateReplacement({ priorContent: "p\nq\n", oldText: "p", newText: "P1\r\nP2", expectedOccurrences: 1 }).updated,
    "P1\nP2\nq\n", "an LF-only file stays LF");
});

test("a duplicated oldText is an occurrence mismatch before the write, not PATCH_FAILED", async (t) => {
  const f = fixture(t);
  const rel = "Source/Bytes/Twice.h";
  const read = await f.seed(rel, "int A;\nint A;\n");
  const result = await f.call("replace_in_file", { path: `project://${rel}`, oldText: "int A;", newText: "int B;",
    fileVersionReceipt: read.fileVersionReceipt });
  assert.equal(result.errorCode, "OCCURRENCE_MISMATCH");
  assert.deepEqual(result.retry, { allowed: true, mode: "different_arguments" });
  assert.equal(f.disk(rel), "int A;\nint A;\n");

  const missing = await f.call("replace_in_file", { path: `project://${rel}`, oldText: "int Z;", newText: "int B;",
    fileVersionReceipt: read.fileVersionReceipt });
  assert.equal(missing.errorCode, "OLD_TEXT_NOT_FOUND", "absent text is still not found, not a count mismatch");
});

test("a whitespace-only mismatch names the differing line and offers the exact range", async (t) => {
  const f = fixture(t);
  const rel = "Source/Bytes/Blank.h";
  const read = await f.seed(rel, "struct S {\r\n};\r\n\r\n// next\r\nint\tCount = 0;\r\n");
  const blank = await f.call("replace_in_file", { path: `project://${rel}`, oldText: "};\n \n// next", newText: "};\n// next",
    fileVersionReceipt: read.fileVersionReceipt });
  assert.equal(blank.errorCode, "OLD_TEXT_NOT_FOUND");
  assert.match(blank.message, /matches lines 2-4 only when whitespace is ignored \(oldText line 2 is " "; file line 3 is ""\)/u);
  assert.equal(blank.whitespaceCandidate.currentRange.text, "};\n\n// next");
  assert.equal(f.disk(rel), "struct S {\r\n};\r\n\r\n// next\r\nint\tCount = 0;\r\n", "a candidate is never edited");

  const range = await f.call("replace_in_file", { path: `project://${rel}`, startLine: 2, endLine: 4,
    expectedRangeSha256: blank.whitespaceCandidate.currentRange.rangeSha256, newText: "};\n// next",
    fileVersionReceipt: read.fileVersionReceipt });
  assert.equal(range.ok, true, JSON.stringify(range));
  assert.equal(f.disk(rel), "struct S {\r\n};\r\n// next\r\nint\tCount = 0;\r\n");

  const tab = await f.call("replace_in_file", { path: `project://${rel}`, oldText: "int    Count = 0;", newText: "int Count = 1;",
    fileVersionReceipt: range.fileVersionReceipt });
  assert.match(tab.message, /matches lines 4-4 only when whitespace is ignored/u, "a tab copied as spaces is named too");

  const twice = calculateReplacement({ priorContent: "\tint32 Value;\n  int32 Value;\n", oldText: "int32  Value;",
    newText: "x", expectedOccurrences: 1 });
  assert.equal(twice.whitespaceCandidate.candidates, "multiple");
  assert.equal(twice.whitespaceCandidate.currentRange, undefined, "an ambiguous candidate offers no range");
});

test("a missing receipt names the latest read or write as the proof to pass", async (t) => {
  const f = fixture(t);
  const rel = "Source/Bytes/New.h";
  const created = await f.call("write_file", { path: `project://${rel}`, content: "int A = 0;\n" });
  assert.equal(typeof created.fileVersionReceipt, "string");
  const missing = await f.call("replace_in_file", { path: `project://${rel}`, oldText: "A = 0", newText: "A = 1" });
  assert.equal(missing.errorCode, "FILE_SNAPSHOT_REQUIRED");
  assert.match(missing.message, /most recent read or successful write of this file/u);
  const chained = await f.call("replace_in_file", { path: `project://${rel}`, oldText: "A = 0", newText: "A = 1",
    fileVersionReceipt: created.fileVersionReceipt });
  assert.equal(chained.ok, true, "the write_file receipt proves the version without a read");
});
