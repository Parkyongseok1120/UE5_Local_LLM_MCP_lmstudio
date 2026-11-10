"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), os = require("node:os");
const { execFileSync } = require("node:child_process");
const { createWorkspaceCapabilities } = require("../../shared-tool-core/workspace");
const { createRuntime } = require("../src/server");
function fixture(t, git = true) {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "harness-project-rules-")));
  t.after(() => fs.rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 50 }));
  const workspace = path.join(root, "Game");
  for (const dir of ["Assets/UI", "Assets/Other", "Packages", "ProjectSettings"]) fs.mkdirSync(path.join(workspace, dir), { recursive: true });
  fs.writeFileSync(path.join(workspace, "Packages/manifest.json"), "{}");
  fs.writeFileSync(path.join(workspace, "ProjectSettings/ProjectVersion.txt"), "test");
  if (git) execFileSync("git", ["init", "-q"], { cwd: root, windowsHide: true });
  const put = (file, value) => fs.writeFileSync(path.join(root, file), value);
  const call = createWorkspaceCapabilities({ resolveBinding: () => ({ root: workspace, engine: "unity", projectIdentity: "fixture" }) });
  return { root, workspace, put, call: args => call("project_rules", args), status: () => call("workspace_status", {}) };
}
test("rules index follows applicable ancestors, preserves scope and does not inject a 12KB body", async t => {
  const f = fixture(t);
  f.put("AGENTS.md", "# Root\nBODY-SECRET-FIXTURE\n" + "x".repeat(12000));
  f.put("Game/AGENTS.md", "# Game\nUse authored values.\n");
  f.put("Game/Assets/UI/AGENTS.md", "# UI\nPreserve bindings.\n");
  f.put("Game/Assets/Other/AGENTS.md", "# Sibling\nDo not include me.\n");
  const r = await f.call({ path: "Assets/UI/Panel.cs" });
  assert.equal(r.totalRules, 3);
  assert.deepEqual(r.rules.map(x => x.path), ["AGENTS.md", "Game/AGENTS.md", "Game/Assets/UI/AGENTS.md"]);
  assert.deepEqual(r.rules.map(x => x.appliesTo), [".", "Game", "Game/Assets/UI"]);
  assert.doesNotMatch(JSON.stringify(r), /BODY-SECRET-FIXTURE|Do not include me|x{100}/u);
  assert.ok(Buffer.byteLength(JSON.stringify(r)) <= 8192);
  const status = await f.status();
  assert.equal(status.rules.totalRules, 2, "workspace status indexes the workspace, not every descendant");
});
test("explicit rule reads use the observed version and section or source line ranges", async t => {
  const f = fixture(t); f.put("AGENTS.md", "# Root\nroot rule\n## Child\nchild rule\n# Other\nother rule\n");
  const index = await f.call({}); const rule = index.rules[0];
  const query = { action: "read", ruleId: rule.ruleId, version: rule.version };
  const section = await f.call({ ...query, section: "Root" });
  assert.match(section.content, /child rule/u); assert.doesNotMatch(section.content, /other rule/u);
  const lines = await f.call({ ...query, startLine: 4, endLine: 4 });
  assert.equal(lines.content, "4: child rule\n");
  f.put("AGENTS.md", "# Root\nchanged\n");
  await assert.rejects(f.call(query), e => e.code === "snapshot_changed");
  await assert.rejects(f.call({ action: "read", ruleId: rule.ruleId }), e => e.code === "invalid_arguments");
});
test("64KB cap reports a prefix hash and read pages obey the full JSON byte budget", async t => {
  const f = fixture(t); f.put("AGENTS.md", "# Rules\n" + "유니티 값을 보존한다.\n".repeat(6000));
  const r = await f.call({}); const rule = r.rules[0];
  assert.equal(rule.truncated, true); assert.equal(rule.observedBytes, 65536); assert.equal(rule.hashScope, "first_65536_bytes");
  const page = await f.call({ action: "read", ruleId: rule.ruleId, version: rule.version, byteBudget: 2048 });
  assert.ok(Buffer.byteLength(JSON.stringify(page)) <= 2048); assert.equal(page.hasMore, true);
  const next = await f.call({ action: "read", ruleId: rule.ruleId, version: rule.version, byteBudget: 2048, startLine: page.nextStartLine });
  assert.equal(next.startLine, page.endLine + 1); assert.doesNotMatch(next.content, /�/u);
});

for (const [name, line, budget] of [["JSON escaping", '"'.repeat(40000), 65536], ["Unicode", '🙂"'.repeat(9000), 8192]]) {
  test(`oversized source lines page losslessly within the JSON budget (${name})`, async t => {
    const f = fixture(t); f.put("AGENTS.md", "# Rules\n" + line);
    const index = await f.call({}); const rule = index.rules[0];
    assert.equal(rule.truncated, false);
    let startColumn, combined = "", pages = 0;
    do {
      const page = await f.call({ action: "read", ruleId: rule.ruleId, version: rule.version,
        startLine: 2, endLine: 2, byteBudget: budget, ...(startColumn === undefined ? {} : { startColumn }) });
      assert.equal(page.contentFormat, "line_fragment");
      assert.ok(Buffer.byteLength(JSON.stringify(page)) <= budget);
      assert.equal(page.lineFragment.startColumn, startColumn || 0);
      assert.doesNotMatch(page.content, /�/u);
      combined += page.content; pages++;
      if (!page.hasMore) { assert.equal(page.nextStartLine, null); break; }
      assert.equal(page.nextStartLine, 2); assert.ok(page.nextStartColumn > (startColumn || 0));
      startColumn = page.nextStartColumn;
      assert.ok(pages < 40, "every page must make progress");
    } while (true);
    assert.ok(pages > 1); assert.equal(combined, line);
    await assert.rejects(f.call({ action: "read", ruleId: rule.ruleId, version: rule.version,
      startLine: 2, startColumn: Array.from(line).length + 1 }), e => e.code === "invalid_arguments");
    await assert.rejects(f.call({ startColumn: 0 }), e => e.code === "invalid_arguments");
  });
}
test("non-Git lookup stays in the workspace and traversal or symlink scopes fail closed", async t => {
  const f = fixture(t, false); f.put("AGENTS.md", "# Outside\nnot applicable\n"); f.put("Game/AGENTS.md", "# Inside\nrule\n");
  const r = await f.call({}); assert.equal(r.scopeSource, "workspace_fallback"); assert.equal(r.totalRules, 1);
  for (const target of ["../AGENTS.md", "C:/private", "/etc", "Assets/../../outside"]) {
    await assert.rejects(f.call({ path: target }), e => e.code === "invalid_path");
  }
  fs.symlinkSync(f.root, path.join(f.workspace, "Assets/linked"), "junction");
  await assert.rejects(f.call({ path: "Assets/linked/AGENTS.md" }), e => e.code === "invalid_path");
});
test("index pagination pins the rule set and stale cursors never combine versions", async t => {
  const f = fixture(t); f.put("AGENTS.md", "# A\na\n"); f.put("Game/AGENTS.md", "# B\nb\n");
  const first = await f.call({ limit: 1 }); assert.equal(first.nextOffset, 1);
  const second = await f.call({ offset: first.nextOffset, setHash: first.setHash, limit: 1 });
  assert.equal(second.rules[0].path, "Game/AGENTS.md");
  f.put("Game/AGENTS.md", "# B\nupdated\n");
  await assert.rejects(f.call({ offset: 1, setHash: first.setHash }), e => e.code === "snapshot_changed");
});
test("Unity routes rules offline without widening normal file reads or another project", async t => {
  const f = fixture(t); f.put("AGENTS.md", "# Parent\nuse inspector\n");
  const runtime = createRuntime({ UNITY_PROJECT_ROOT: f.workspace });
  const rules = await runtime.call("project_rules", {});
  assert.equal(rules.kind, "project_rules"); assert.equal(rules.totalRules, 1);
  assert.notEqual((await runtime.call("read_file", { path: "../AGENTS.md" })).status, "observed");
  assert.equal((await runtime.call("project_rules", { project: f.root })).errorCode, "project_scope_mismatch");
  const legacy = createRuntime({ UNITY_PROJECT_ROOT: f.workspace, WORKSPACE_CAPABILITIES: "0" });
  assert.equal(legacy.tools.some(tool => tool.name === "project_rules"), false);
});
