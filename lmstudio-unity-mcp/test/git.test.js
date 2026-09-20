"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const fs = require("node:fs"), os = require("node:os"), path = require("node:path");
const { execFileSync } = require("node:child_process");
const { createRuntime } = require("../src/server");
const { VersionControl } = require("../../shared-tool-core/git");
function fixture(t) {
  const root = fs.realpathSync.native(fs.mkdtempSync(path.join(os.tmpdir(), "workspace-git-long-name-")));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  for (const dir of ["Assets", "Packages", "ProjectSettings"]) fs.mkdirSync(path.join(root, dir));
  fs.writeFileSync(path.join(root, "Packages/manifest.json"), "{}");
  fs.writeFileSync(path.join(root, "ProjectSettings/ProjectVersion.txt"), "test");
  const git = (...args) => execFileSync("git", args, { cwd: root, encoding: "utf8", windowsHide: true });
  git("init", "-q"); git("config", "user.email", "test@example.invalid"); git("config", "user.name", "Test"); git("config", "core.autocrlf", "false");
  const put = (file, text) => fs.writeFileSync(path.join(root, file), text);
  const commit = message => { git("add", "."); git("commit", "-qm", message); return git("rev-parse", "HEAD").trim(); };
  const runtime = createRuntime({ UNITY_PROJECT_ROOT: root });
  return { root, git, put, commit, runtime, call: a => runtime.call("unity_git", a) };
}
test("comparison never silently ignores base/head; no implicit diff", async t => {
  const f = fixture(t); f.commit("initial");
  for (const args of [{}, { comparison: "worktree", base: "HEAD" }, { comparison: "staged", head: "HEAD" }, { comparison: "range", base: "HEAD" }]) {
    const r = await f.call({ action: "diff", ...args }); assert.equal(r.errorCode, "invalid_arguments", JSON.stringify(r));
  }
});

test("Workspace rollback and project mismatch are enforced before bridge or Git access", async t => {
  const f = fixture(t); f.commit("base");
  const status = await f.runtime.call("workspace_status", { project: f.root });
  assert.equal(status.engine, "unity"); assert.equal(status.independentWriter, false);
  assert(!f.runtime.tools.some(t => t.name === "unity_git"));
  for (const name of ["unity_status", "workspace_status", "read_file"]) {
    const result = await f.runtime.call(name, { project: path.dirname(f.root) });
    assert.equal(result.errorCode, "project_scope_mismatch");
  }
  const legacy = createRuntime({ UNITY_PROJECT_ROOT: f.root, WORKSPACE_CAPABILITIES: "0" });
  assert(!legacy.tools.some(t => t.name === "git_status"));
  assert(legacy.tools.some(t => t.name === "unity_git"));
  assert.equal((await legacy.call("git_status", {})).errorCode, "invalid_arguments");
  assert.equal((await legacy.call("unity_git", { action: "status" })).status, "observed");
});

test("Workspace observations share the existing Unity receipt owner; rollback cannot replay receipts", async t => {
  const f = fixture(t); f.put("Assets/A.cs", "old\n"); f.commit("base");
  const runtime = createRuntime({ UNITY_PROJECT_ROOT: f.root, ALLOW_WRITE: "1" });
  const read = await runtime.call("read_file", { path: "Assets/A.cs" });
  await runtime.call("git_status", {});
  const args = { path: "Assets/A.cs", receipt: read.receipt, edits: [{ oldText: "old", newText: "new" }] };
  const edited = await runtime.call("patch_file", args);
  assert.equal(edited.status, "applied", JSON.stringify(edited));
  assert.equal(edited.kind, "workspace_mutation_observation");
  assert.equal((await runtime.call("patch_file", args)).errorCode, "receipt_conflict");
  const rollback = createRuntime({ UNITY_PROJECT_ROOT: f.root, ALLOW_WRITE: "1", WORKSPACE_CAPABILITIES: "0" });
  assert.equal((await rollback.call("patch_file", args)).errorCode, "invalid_receipt");
  assert.equal(fs.readFileSync(path.join(f.root, "Assets/A.cs"), "utf8"), "new\n");
});

test("Git read operations reject ignored arguments and directories, preserve raw BOM hashes", async t => {
  const f = fixture(t); f.put("Assets/Bom.cs", "\ufeffclass Bom {}\r\n"); f.commit("base");
  assert.equal((await f.call({ action: "read_file", revision: "HEAD", path: "Assets" })).errorCode, "not_file");
  assert.equal((await f.call({ action: "diff_file", comparison: "staged", path: "missing.cs" })).errorCode, "not_file");
  assert.equal((await f.call({ action: "status", revision: "HEAD" })).errorCode, "invalid_arguments");
  assert.equal((await f.call({ action: "read_file", revision: "HEAD", path: "Assets/Bom.cs", startLine: 1, cursor: "x" })).errorCode, "invalid_arguments");
  const blob = await f.call({ action: "read_file", revision: "HEAD", path: "Assets/Bom.cs" });
  assert.equal(blob.text.charCodeAt(0), 0xfeff);
  const raw = execFileSync("git", ["cat-file", "blob", "HEAD:Assets/Bom.cs"], { cwd: f.root });
  assert.equal(blob.sha256, require("node:crypto").createHash("sha256").update(raw).digest("hex"));
});

test("read-only Git suppresses fsmonitor and rejects executable clean filters without running them", async t => {
  const f = fixture(t); f.put("Assets/A.cs", "old"); f.commit("base");
  f.git("config", "core.fsmonitor", "echo invoked > fsmonitor-sentinel");
  f.put("Assets/A.cs", "new");
  assert.equal((await f.call({ action: "status" })).status, "observed");
  assert(!fs.existsSync(path.join(f.root, "fsmonitor-sentinel")));
  f.git("config", "core.fsmonitor", "false");
  f.put(".gitattributes", "*.cs filter=hostile\n");
  f.git("config", "filter.hostile.clean", "echo invoked > filter-sentinel");
  for (const query of [{ action: "status" }, { action: "diff", comparison: "worktree" }])
    assert.equal((await f.call(query)).errorCode, "external_filter_unsupported");
  assert(!fs.existsSync(path.join(f.root, "filter-sentinel")));
  assert.equal((await f.call({ action: "changed_files", comparison: "range", base: "HEAD", head: "HEAD" })).status, "observed");
  const previous = process.env.GIT_DIR;
  try { process.env.GIT_DIR = path.join(f.root, "missing-git-dir");
    assert.equal((await f.call({ action: "read_file", revision: "HEAD", path: "Assets/A.cs" })).text, "old");
  } finally { if (previous === undefined) delete process.env.GIT_DIR; else process.env.GIT_DIR = previous; }
});
test("changed files pin commits, preserve rename and page one immutable snapshot", async t => {
  const f = fixture(t); f.put("Assets/Old.cs", "class Example {}\n"); const base = f.commit("base");
  f.git("mv", "Assets/Old.cs", "Assets/New.cs"); f.put("Assets/Z.cs", "class Z {}\n"); const head = f.commit("head");
  const a = { action: "changed_files", comparison: "range", base, head, limit: 1 };
  const first = await f.call(a); assert.equal(first.status, "observed", JSON.stringify(first));
  assert.equal(first.base, base); assert.equal(first.head, head); assert(first.nextCursor);
  const rest = await f.call({ ...a, cursor: first.nextCursor });
  const rows = [...first.items, ...rest.items];
  assert(rows.some(x => x.status === "renamed" && x.path === "Assets/New.cs" && x.oldPath === "Assets/Old.cs" && x.similarity === 100));
  assert.equal(rest.snapshotId, first.snapshotId);
  assert.equal((await f.call({ ...a, paths: ["Assets/Z.cs"], cursor: first.nextCursor })).errorCode, "invalid_cursor");
  assert.equal((await f.call({ ...a, cursor: first.nextCursor + "tamper" })).errorCode, "invalid_cursor");
  f.put("Assets/New.cs", "uncommitted text");
  const old = await f.call({ action: "read_file", revision: head, path: "Assets/New.cs" });
  assert.match(old.text, /class Example/); assert.equal(old.head, head);
});
test("unborn status, root commit, literal paths and result budgets", async t => {
  const f = fixture(t);
  const unborn = await f.call({ action: "status" }); assert.equal(unborn.head, null, JSON.stringify(unborn));
  f.put("Assets/file with spaces.cs", "one\ntwo\n"); f.commit("initial");
  const root = await f.call({ action: "changed_files", comparison: "last_commit" });
  assert(root.items.some(x => x.path === "Assets/file with spaces.cs"));
  for (const p of ["../outside", ":(top)*", "/outside"]) assert.equal((await f.call({ action: "status", paths: [p] })).errorCode, "invalid_path");
  f.put("Assets/file with spaces.cs", "changed\n");
  const r = await f.call({ action: "status", paths: ["Assets/file with spaces.cs"], byteBudget: 2048 });
  assert.equal(r.items[0].path, "Assets/file with spaces.cs"); assert(Buffer.byteLength(JSON.stringify(r)) <= 2048);
});
test("status NUL parsing retains newline and trailing spaces on supporting filesystems", { skip: process.platform === "win32" }, async t => {
  const f = fixture(t); const file = "Assets/line\nname.cs "; f.put(file, "x");
  const r = await f.call({ action: "status" }); assert(r.items.some(x => x.path === file));
});
test("Windows short root aliases identify the same Git project", { skip: process.platform !== "win32" }, async t => {
  const f = fixture(t); f.commit("initial");
  const short = execFileSync("cmd.exe", ["/d", "/c", `for %I in ("${f.root}") do @echo %~sI`], { encoding: "utf8", windowsHide: true }).trim();
  if (!short.includes("~")) return t.skip("Volume does not provide DOS short aliases");
  const runtime = createRuntime({ UNITY_PROJECT_ROOT: short });
  const r = await runtime.call("unity_git", { action: "status" }); assert.equal(r.status, "observed", JSON.stringify(r));
});

test("nested workspaces report exact requested and repository query scopes without guessing prefixes", t => {
  const repositoryRoot = fs.realpathSync.native(fs.mkdtempSync(path.join(os.tmpdir(), "nested-workspace-git-")));
  const workspaceRoot = path.join(repositoryRoot, "SyntheticGame");
  t.after(() => fs.rmSync(repositoryRoot, { recursive: true, force: true }));
  fs.mkdirSync(path.join(workspaceRoot, "Assets"), { recursive: true });
  fs.mkdirSync(path.join(workspaceRoot, "SyntheticGame", "Assets"), { recursive: true });
  const git = (...args) => execFileSync("git", args, { cwd: repositoryRoot, encoding: "utf8", windowsHide: true }).trim();
  git("init", "-q"); git("config", "user.email", "test@example.invalid"); git("config", "user.name", "Test");
  fs.writeFileSync(path.join(workspaceRoot, "Assets", "A.cs"), "base\n");
  fs.writeFileSync(path.join(workspaceRoot, "Assets", "Deleted.cs"), "deleted later\n");
  fs.writeFileSync(path.join(workspaceRoot, "SyntheticGame", "Assets", "Nested.cs"), "nested base\n");
  git("add", "."); git("commit", "-qm", "base"); const base = git("rev-parse", "HEAD");
  fs.writeFileSync(path.join(workspaceRoot, "Assets", "A.cs"), "head\n");
  fs.rmSync(path.join(workspaceRoot, "Assets", "Deleted.cs"));
  fs.writeFileSync(path.join(workspaceRoot, "SyntheticGame", "Assets", "Nested.cs"), "nested head\n");
  git("add", "-A"); git("commit", "-qm", "head"); const head = git("rev-parse", "HEAD");
  const vc = new VersionControl({ root: workspaceRoot });
  const changed = paths => vc.call({ action: "changed_files", comparison: "range", base, head, paths });

  const ordinary = changed(["Assets/A.cs"]);
  assert.deepEqual(ordinary.requestedPaths, ["Assets/A.cs"]);
  assert.deepEqual(ordinary.resolvedRepositoryPaths, ["SyntheticGame/Assets/A.cs"]);
  assert.equal(ordinary.queryPathBase, "workspace_root");
  assert.deepEqual(ordinary.items.map(item => item.path), ["Assets/A.cs"]);

  const actualNestedDirectory = changed(["SyntheticGame/Assets/Nested.cs"]);
  assert.deepEqual(actualNestedDirectory.resolvedRepositoryPaths,
    ["SyntheticGame/SyntheticGame/Assets/Nested.cs"]);
  assert.deepEqual(actualNestedDirectory.items.map(item => item.path), ["SyntheticGame/Assets/Nested.cs"]);

  const repeatedPrefixDoesNotMatchAnotherFile = changed(["SyntheticGame/Assets/A.cs"]);
  assert.deepEqual(repeatedPrefixDoesNotMatchAnotherFile.items, []);
  assert.equal(repeatedPrefixDoesNotMatchAnotherFile.complete, true);
  assert.deepEqual(repeatedPrefixDoesNotMatchAnotherFile.resolvedRepositoryPaths,
    ["SyntheticGame/SyntheticGame/Assets/A.cs"]);

  const historicalDeletion = changed(["Assets/Deleted.cs"]);
  assert.deepEqual(historicalDeletion.items, [{ status: "deleted", path: "Assets/Deleted.cs" }]);
  const empty = changed(["Assets/DoesNotExist.cs"]);
  assert.deepEqual(empty.items, []);
  assert.equal(empty.complete, true);
  assert.deepEqual(empty.requestedPaths, ["Assets/DoesNotExist.cs"]);
});
