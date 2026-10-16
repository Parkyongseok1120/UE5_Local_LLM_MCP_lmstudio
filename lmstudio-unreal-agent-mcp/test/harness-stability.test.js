"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { createExclusive, replaceWithCAS, sha256Text } = require("../src/safe-write");
const { applyDirectEditBundle } = require("../src/direct-edit-bundle");
const { canonicalAbsolutePathIdentity } = require("../src/filesystem-path-identity");
const identity = value => canonicalAbsolutePathIdentity(value, process.platform, { realpath: false });
const { lexicalSymbolWindow } = require("../src/direct-symbol-extraction");
function root(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "harness-stability-"));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  return dir;
}

test("canceled create/CAS cannot publish or overwrite a file", async t => {
  const dir = root(t), target = path.join(dir, "old.txt"), ctrl = new AbortController();
  fs.writeFileSync(target, "before"); ctrl.abort();
  await assert.rejects(createExclusive(path.join(dir, "new.txt"), "new", ctrl.signal), { code: "REQUEST_CANCELLED" });
  await assert.rejects(replaceWithCAS({ targetPath: target, priorContent: "before", oldText: "before", newText: "after",
    readHash: sha256Text("before"), signal: ctrl.signal }), { code: "REQUEST_CANCELLED" });
  assert.equal(fs.existsSync(path.join(dir, "new.txt")), false);
  assert.equal(fs.readFileSync(target, "utf8"), "before");
});

for (const phase of ["afterWriteAhead", "afterDiskWrite"])
test(`bundle cancellation at ${phase} preserves its publication boundary`, async t => {
  const dir = root(t), ctrl = new AbortController();
  for (const name of ["a.txt", "b.txt"]) fs.writeFileSync(path.join(dir, name), "before");
  const result = await applyDirectEditBundle({ patches: ["a.txt", "b.txt"].map(name => ({ path: name,
    oldText: "before", newText: "after", expectedHash: sha256Text("before"), expectedOccurrences: 1 })) },
    async name => ({ ok: true, absolutePath: path.join(dir, name), expectedIdentity: {
      absolutePath: identity(path.join(dir, name)), realPath: canonicalAbsolutePathIdentity(path.join(dir, name)),
      lexicalRoot: identity(dir), allowedRealRoot: canonicalAbsolutePathIdentity(dir) } }),
    { projectRoot: dir, projectPath: path.join(dir, "Demo.uproject"), runtimeOwner: "direct", stateRoot: path.join(dir, "state"),
      signal: ctrl.signal, transactionHooks: { [phase]: async () => ctrl.abort() } });
  const committed = phase === "afterDiskWrite";
  assert.equal(result.ok, committed, JSON.stringify(result));
  if (!committed) assert.equal(result.errorCode, "REQUEST_CANCELLED");
  for (const name of ["a.txt", "b.txt"]) assert.equal(fs.readFileSync(path.join(dir, name), "utf8"), committed ? "after" : "before");
});

test("symbol body selection ignores comments, literals and earlier invocations", () => {
  const source = [
    '// void Foo() { misleading(); }',
    'const char* s = R"tag(Foo() { fake(); })tag";',
    'void Other() { Foo(); }',
    'void Foo() {',
    '  const char* brace = "}"; /* } */',
    '  if (true) { Work(); }',
    '}',
    'void Foo(int value) { Work(); }',
  ].join('\n');
  const result = lexicalSymbolWindow(source, "Foo", 0);
  assert.equal(result.ok, true);
  assert.equal(lexicalSymbolWindow("void A::Foo() { work(); }", "B::Foo", 0).ok, false);
  assert.equal(result.start, 4);
  assert.equal(result.end, 7);
  assert.deepEqual(result.candidateLines, [4, 8]);
  assert.match(result.selection, /unverified/);
  assert.equal(lexicalSymbolWindow("void Other() { Foo(); }\nvoid Later() {}", "Foo", 0).ok, false);
});
