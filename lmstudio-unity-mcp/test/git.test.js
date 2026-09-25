"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const fs = require("node:fs"), os = require("node:os"), path = require("node:path");
const { execFileSync } = require("node:child_process");
const { createRuntime } = require("../src/server");
const { VersionControl } = require("../../shared-tool-core/git");
function fixture(t) {
  const root = fs.realpathSync.native(fs.mkdtempSync(path.join(os.tmpdir(), "workspace-git-long-name-")));
  // Git may finish writing a temporary object just as the fixture is removed
  // on a busy CI runner. Retry transient ENOTEMPTY during cleanup.
  t.after(() => fs.rmSync(root, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 }));
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
  assert.equal(first.pageStart, 1);
  assert.equal(first.pageEnd, 1);
  assert.equal(first.pageHasMore, true);
  assert.equal(first.sourceResultComplete, true);
  const rest = await f.call({ ...a, cursor: first.nextCursor });
  const rows = [...first.items, ...rest.items];
  assert(rows.some(x => x.status === "renamed" && x.path === "Assets/New.cs" && x.oldPath === "Assets/Old.cs" && x.similarity === 100));
  assert.equal(rest.snapshotId, first.snapshotId);
  assert.equal(rest.pageStart, 2);
  assert.equal(rest.pageEnd, 2);
  assert.equal(rest.pageHasMore, false);
  assert.equal(rest.sourceResultComplete, true);
  assert.equal(rest.complete, true);
  assert.equal((await f.call({ ...a, paths: ["Assets/Z.cs"], cursor: first.nextCursor })).errorCode, "invalid_cursor");
  assert.equal((await f.call({ ...a, cursor: first.nextCursor + "tamper" })).errorCode, "invalid_cursor");
  f.put("Assets/New.cs", "uncommitted text");
  const old = await f.call({ action: "read_file", revision: head, path: "Assets/New.cs" });
  assert.match(old.text, /class Example/); assert.equal(old.head, head);
});

test("Git observations expose query, phase, cache and serialization timing without changing paging", async t => {
  const f = fixture(t); f.put("Assets/Timed.cs", "base\n"); const base = f.commit("timing base");
  f.put("Assets/Timed.cs", "head\n"); const head = f.commit("timing head");
  const query = { action: "diff_file", comparison: "range", base, head, path: "Assets/Timed.cs",
    byteBudget: 4096 };
  const first = await f.call(query);
  assert.equal(first.status, "observed", JSON.stringify(first));
  assert.match(first.gitTiming.queryHash, /^[a-f0-9]{64}$/u);
  assert.equal(first.gitTiming.cacheHit, false);
  assert.ok(first.gitTiming.phaseMs.scope >= 0);
  assert.ok(first.gitTiming.phaseMs.resolve >= 0);
  assert.ok(first.gitTiming.phaseMs.ls_tree >= 0);
  assert.ok(first.gitTiming.phaseMs.diff >= 0);
  assert.ok(first.gitTiming.phaseMs.serialization >= 0);
  assert.ok(first.gitTiming.payloadBytes > 0);
  assert.ok(first.gitTiming.envelopeBytes > first.gitTiming.payloadBytes);
  assert.equal(first.gitTiming.returnedRows, first.text.split("\n").length);
  assert.equal(first.gitTiming.subprocessMs >= 0, true);
  assert.equal(first.gitTiming.serializationBytes >= first.gitTiming.payloadBytes, true);

  const invalidPaging = await f.call({ ...query, cursor: "not-a-cursor", startLine: 1 });
  assert.equal(invalidPaging.errorCode, "invalid_arguments");
  const firstPage = await f.call({ ...query, limit: 1 });
  if (firstPage.nextCursor) {
    const secondPage = await f.call({ ...query, cursor: firstPage.nextCursor });
    assert.equal(secondPage.gitTiming.cacheHit, true);
    assert.equal(secondPage.snapshotId, firstPage.snapshotId);
    assert.equal(secondPage.gitTiming.queryHash, firstPage.gitTiming.queryHash);
  }
});

test("immutable changed-file fixtures deliver all 232 rows after the first 200", async t => {
  const f = fixture(t);
  for (let index = 1; index <= 232; index += 1) f.put(`Assets/Fixture-${index}.cs`, `base-${index}\n`);
  const base = f.commit("232 base files");
  for (let index = 1; index <= 232; index += 1) f.put(`Assets/Fixture-${index}.cs`, `head-${index}\n`);
  const head = f.commit("232 changed files");
  const query = { action: "changed_files", comparison: "range", base, head, limit: 200, byteBudget: 32768 };
  const first = await f.call(query);
  const second = await f.call({ ...query, cursor: first.nextCursor });

  assert.equal(first.items.length, 200);
  assert.equal(first.pageStart, 1);
  assert.equal(first.pageEnd, 200);
  assert.equal(first.pageHasMore, true);
  assert.equal(second.items.length, 32);
  assert.equal(second.pageStart, 201);
  assert.equal(second.pageEnd, 232);
  assert.equal(second.pageHasMore, false);
  assert.equal(second.sourceResultComplete, true);
  assert.equal(new Set([...first.items, ...second.items].map(item => item.path)).size, 232);
});

test("650-row Git paging advances by returned pages under the changed-files default budget", async t => {
  const f = fixture(t);
  for (let index = 1; index <= 650; index += 1) f.put(`Assets/Pressure-${index}.cs`, `base-${index}\n`);
  const base = f.commit("650 base files");
  for (let index = 1; index <= 650; index += 1) f.put(`Assets/Pressure-${index}.cs`, `head-${index}\n`);
  const head = f.commit("650 changed files");
  const query = { action: "changed_files", comparison: "range", base, head, limit: 200 };
  const rows = [];
  const pageStarts = [];
  let cursor;
  do {
    const page = await f.call(cursor ? { ...query, cursor } : query);
    assert.equal(page.errorCode, undefined, JSON.stringify(page));
    if (pageStarts.length === 0) assert(page.items.length < 200, JSON.stringify(page));
    pageStarts.push(page.pageStart);
    rows.push(...page.items);
    cursor = page.nextCursor;
    if (!cursor) assert.equal(page.pageHasMore, false);
  } while (cursor);

  assert.equal(rows.length, 650);
  assert.equal(new Set(rows.map(item => item.path)).size, 650);
  assert.equal(pageStarts[0], 1);
  assert.deepEqual(pageStarts, [...pageStarts].sort((left, right) => left - right));
});

test("Git cursors distinguish query mismatch, TTL, cache eviction, and server restart", async t => {
  const f = fixture(t);
  const paths = [];
  for (let index = 1; index <= 10; index += 1) {
    const relative = `Assets/Cursor-${index}.cs`;
    paths.push(relative);
    f.put(relative, `base-${index}\n`);
  }
  const base = f.commit("cursor base");
  for (const relative of paths) f.put(relative, `head-${relative}\n`);
  const head = f.commit("cursor head");
  const query = { action: "changed_files", comparison: "range", base, head, paths: [...paths, "Assets/Not-present.cs"], limit: 1 };
  const first = await f.call(query);
  assert(first.nextCursor);
  assert.equal((await f.call({ ...query, paths, cursor: first.nextCursor })).errorCode, "invalid_cursor");

  const originalNow = Date.now;
  try {
    Date.now = () => originalNow() + 300001;
    assert.equal((await f.call({ ...query, cursor: first.nextCursor })).errorCode, "snapshot_expired");
  } finally {
    Date.now = originalNow;
  }

  const restarted = new VersionControl({ root: f.root });
  assert.throws(
    () => restarted.call({ ...query, cursor: first.nextCursor }),
    error => error?.code === "invalid_cursor",
  );

  for (let index = 1; index <= 8; index += 1) {
    const page = await f.call({
      ...query,
      paths: [...paths, `Assets/Unique-${index}.cs`],
    });
    assert(page.nextCursor);
  }
  assert.equal((await f.call({ ...query, cursor: first.nextCursor })).errorCode, "snapshot_expired");
});

test("git log returns author and committer evidence with bounded date and literal author filters", async t => {
  const f = fixture(t);
  const datedCommit = (file, subject, author, email, authoredAt, committedAt) => {
    f.put(file, `${subject}\n`);
    f.git("add", file);
    execFileSync("git", ["commit", "-qm", subject, `--author=${author} <${email}>`], {
      cwd: f.root,
      windowsHide: true,
      env: {
        ...process.env,
        GIT_AUTHOR_DATE: authoredAt,
        GIT_COMMITTER_DATE: committedAt,
      },
    });
  };
  datedCommit("Assets/A.cs", "yongseok change", "Yongseok Park", "yongseok@example.invalid",
    "2026-09-14T09:00:00+09:00", "2026-09-14T10:00:00+09:00");
  datedCommit("Assets/B.cs", "other change", "Other Author", "other@example.invalid",
    "2026-09-15T09:00:00+09:00", "2026-09-15T10:00:00+09:00");

  const selected = await f.call({
    action: "log",
    since: "2026-09-14T00:00:00+09:00",
    until: "2026-09-14T23:59:59+09:00",
    authorQuery: "Yongseok Park",
  });
  assert.equal(selected.items.length, 1, JSON.stringify(selected));
  assert.equal(selected.items[0].authorName, "Yongseok Park");
  assert.equal(selected.items[0].authorEmail, "yongseok@example.invalid");
  assert.equal(selected.items[0].committerName, "Test");
  assert.equal(selected.items[0].committerEmail, "test@example.invalid");
  assert.equal(selected.identitySemantics,
    "author_and_committer_metadata_only_not_code_ownership_or_work_responsibility");
  assert.equal(selected.authorQuerySemantics, "literal_fixed_string_name_or_email_fragment");

  const first = await f.call({ action: "log", limit: 1 });
  assert(first.nextCursor);
  assert.equal((await f.call({
    action: "log", limit: 1, authorQuery: "Yongseok", cursor: first.nextCursor,
  })).errorCode, "invalid_cursor");
  assert.equal((await f.call({ action: "log", since: "2026/09/14" })).errorCode,
    "invalid_arguments");
});

test("git log authorQuery is a literal fixed string for regex metacharacters and non-ASCII", async t => {
  const f = fixture(t);
  const commitAs = (file, author, email) => {
    f.put(file, `${author}\n`);
    f.git("add", file);
    execFileSync("git", ["commit", "-qm", author, `--author=${author} <${email}>`], {
      cwd: f.root,
      windowsHide: true,
      env: { ...process.env, GIT_AUTHOR_DATE: "2026-09-14T09:00:00+09:00",
        GIT_COMMITTER_DATE: "2026-09-14T10:00:00+09:00" },
    });
  };
  commitAs("Assets/Plus.cs", "dev+qa", "plus@example.invalid");
  commitAs("Assets/Regex.cs", "devvqa", "regex@example.invalid");
  commitAs("Assets/Korean.cs", "박용석", "korean@example.invalid");

  const plus = await f.call({ action: "log", authorQuery: "dev+qa" });
  assert.deepEqual(plus.items.map(item => item.authorName), ["dev+qa"]);
  const dottedEmail = await f.call({ action: "log", authorQuery: "plus@example.invalid" });
  assert.deepEqual(dottedEmail.items.map(item => item.authorEmail), ["plus@example.invalid"]);
  const korean = await f.call({ action: "log", authorQuery: "박용석" });
  assert.deepEqual(korean.items.map(item => item.authorName), ["박용석"]);
  assert.equal(plus.authorQuerySemantics, "literal_fixed_string_name_or_email_fragment");
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
