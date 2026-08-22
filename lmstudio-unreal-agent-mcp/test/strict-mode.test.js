"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const { createDirectRuntime } = require("../src/direct-server.js");
const {
  classifyToolDefinition,
  requiresStrictSession,
  toolDefinitions,
  toolEffect,
} = require("../src/direct-tool-catalog.js");
const { createStrictLifecycle } = require("../src/strict-lifecycle.js");
const { createStrictRuntime } = require("../src/strict-server.js");

function payloadOf(result) {
  assert.deepStrictEqual(JSON.parse(result.content[0].text), result.structuredContent);
  return result.structuredContent;
}

function fixture(t) {
  const workspaceRoot = fs.mkdtempSync(path.join(os.tmpdir(), "unreal-strict-mode-"));
  const projectRoot = path.join(workspaceRoot, "StrictFixture");
  const projectFile = path.join(projectRoot, "StrictFixture.uproject");
  const configFile = path.join(projectRoot, "Config", "Strict.ini");
  const stateRoot = path.join(workspaceRoot, "state");
  fs.mkdirSync(path.dirname(configFile), { recursive: true });
  fs.writeFileSync(projectFile, JSON.stringify({ FileVersion: 3, EngineAssociation: "5.5" }), "utf8");
  fs.writeFileSync(configFile, "[Strict]\nValue=one\n", "utf8");
  t.after(() => fs.rmSync(workspaceRoot, { recursive: true, force: true }));

  const directRuntime = createDirectRuntime({
    workspaceRoot,
    configPath: path.join(workspaceRoot, "agent-mcp.json"),
    stateRoot,
    runtimeOwner: "strict",
    env: {
      ALLOW_WRITE: "1",
      ALLOW_UNREAL_BUILD: "0",
      ALLOW_COMMANDS: "0",
      ALLOW_SOURCE_DELETE: "0",
      PROJECT_SEARCH_ROOTS: workspaceRoot,
    },
    getActiveProject: () => projectFile,
  });
  const lifecycle = createStrictLifecycle({ stateRoot });
  const runtime = createStrictRuntime({ directRuntime, lifecycle });
  return { runtime, lifecycle, workspaceRoot, projectRoot, projectFile, configFile, stateRoot };
}

test("Direct catalog classifies every tool and Strict derives all mutation/long-running gates", async (t) => {
  const definitions = toolDefinitions();
  assert.strictEqual(definitions.length, 20);
  assert.deepStrictEqual(
    Object.fromEntries(definitions.map((definition) => [definition.name, toolEffect(definition)])),
    {
      get_workspace_info: "read",
      list_unreal_projects: "read",
      get_active_project: "read",
      set_active_project: "mutation",
      detect_unreal_project: "read",
      list_directory: "read",
      search_files: "read",
      read_file: "read",
      read_file_range: "read",
      read_symbol: "read",
      read_unreal_logs: "read",
      write_file: "mutation",
      replace_in_file: "mutation",
      apply_edit_bundle: "mutation",
      propose_file_deletions: "ephemeral",
      delete_file: "mutation",
      static_validate_project: "long_running",
      build_unreal_project: "long_running",
      run_unreal_automation_tests: "long_running",
      run_command: "long_running",
    },
  );
  assert.strictEqual(JSON.stringify(definitions).includes("long_running"), false);

  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "strict-synthetic-effect-"));
  t.after(() => fs.rmSync(stateRoot, { recursive: true, force: true }));
  let calls = 0;
  const synthetic = classifyToolDefinition({
    name: "synthetic_mutation",
    description: "Synthetic mutation used to prove derived Strict authorization.",
    inputSchema: { type: "object", properties: {}, required: [], additionalProperties: false },
  }, "mutation");
  assert.strictEqual(requiresStrictSession(synthetic), true);
  const runtime = createStrictRuntime({
    directRuntime: {
      runtimeOwner: "strict",
      stateRoot,
      workspaceRoot: stateRoot,
      tools: [synthetic],
      async callTool() {
        calls += 1;
        return { structuredContent: { ok: true, synthetic: true } };
      },
    },
    lifecycle: createStrictLifecycle({ stateRoot }),
  });
  const blocked = payloadOf(await runtime.callTool("synthetic_mutation", {}));
  assert.strictEqual(blocked.errorCode, "STRICT_SESSION_INVALID");
  assert.strictEqual(calls, 0);
  assert.throws(
    () => createStrictRuntime({
      directRuntime: {
        runtimeOwner: "strict",
        stateRoot,
        workspaceRoot: stateRoot,
        tools: [{ name: "unclassified", inputSchema: { properties: {}, required: [] } }],
      },
      lifecycle: createStrictLifecycle({ stateRoot }),
    }),
    /no valid effect classification/i,
  );
});

test("Strict is a separate catalog and leaves every read/search capability task-free", async (t) => {
  const { runtime } = fixture(t);
  const readTool = runtime.tools.find((tool) => tool.name === "read_file");
  const writeTool = runtime.tools.find((tool) => tool.name === "replace_in_file");
  assert.strictEqual(readTool.inputSchema.properties.strictSessionId, undefined);
  assert.ok(writeTool.inputSchema.required.includes("strictSessionId"));
  assert.ok(writeTool.inputSchema.required.includes("conversationId"));
  assert.ok(runtime.tools.some((tool) => tool.name === "strict_begin"));

  const read = payloadOf(await runtime.callTool("read_file", { path: "project://Config/Strict.ini" }));
  assert.strictEqual(read.ok, true);
  assert.strictEqual(read.executionMode, "strict");
  assert.match(read.content, /Value=one/);

  const blocked = payloadOf(await runtime.callTool("replace_in_file", {
    path: "project://Config/Strict.ini",
    oldText: "Value=one",
    newText: "Value=two",
    expectedOccurrences: 1,
    expectedHash: read.sha256,
  }));
  assert.strictEqual(blocked.ok, false);
  assert.strictEqual(blocked.errorCode, "STRICT_SESSION_INVALID");
  assert.ok(JSON.stringify(blocked).length < 4096);
});

test("a new Strict conversation receives full read content without another chat's receipt", async (t) => {
  const { runtime } = fixture(t);
  await runtime.callTool("strict_begin", { conversationId: "chat-a", objective: "Inspect the configuration" });
  const first = payloadOf(await runtime.callTool("read_file", { path: "project://Config/Strict.ini" }));
  await runtime.callTool("strict_begin", { conversationId: "chat-b", objective: "Independently inspect the same file" });
  const second = payloadOf(await runtime.callTool("read_file", { path: "project://Config/Strict.ini" }));

  assert.match(first.content, /Value=one/);
  assert.match(second.content, /Value=one/);
  assert.strictEqual(second.duplicate, undefined);
  assert.notStrictEqual(second.repeatReceipt, first.repeatReceipt);
});

test("Strict mutation ownership is conversation-scoped and completion leaves no blocking task", async (t) => {
  const { runtime, configFile } = fixture(t);
  const begun = payloadOf(await runtime.callTool("strict_begin", {
    conversationId: "chat-a",
    objective: "Make one safe configuration edit",
  })).strictSession;
  const read = payloadOf(await runtime.callTool("read_file", { path: "project://Config/Strict.ini" }));

  const foreign = payloadOf(await runtime.callTool("replace_in_file", {
    strictSessionId: begun.id,
    conversationId: "chat-b",
    path: "project://Config/Strict.ini",
    oldText: "Value=one",
    newText: "Value=foreign",
    expectedOccurrences: 1,
    expectedHash: read.sha256,
  }));
  assert.strictEqual(foreign.ok, false);
  assert.match(foreign.message, /different conversation/i);

  const changed = payloadOf(await runtime.callTool("replace_in_file", {
    strictSessionId: begun.id,
    conversationId: "chat-a",
    path: "project://Config/Strict.ini",
    oldText: "Value=one",
    newText: "Value=two",
    expectedOccurrences: 1,
    expectedHash: read.sha256,
  }));
  assert.strictEqual(changed.ok, true);
  assert.strictEqual(changed.executionMode, "strict");
  assert.match(fs.readFileSync(configFile, "utf8"), /Value=two/);

  const completed = payloadOf(await runtime.callTool("strict_complete", {
    strictSessionId: begun.id,
    conversationId: "chat-a",
    summary: "Configuration edit completed",
  })).strictSession;
  assert.strictEqual(completed.status, "completed");

  const next = payloadOf(await runtime.callTool("strict_begin", {
    conversationId: "chat-b",
    objective: "Independent next work",
  })).strictSession;
  assert.strictEqual(next.status, "running");
});

test("Strict canonical project binding accepts the same exact name and workspace-relative path", async (t) => {
  const { runtime, workspaceRoot, projectRoot, projectFile } = fixture(t);
  const begun = payloadOf(await runtime.callTool("strict_begin", {
    conversationId: "chat-project-identity",
    objective: "Use equivalent exact project selectors",
    project: projectFile,
  })).strictSession;
  assert.strictEqual(path.resolve(begun.project), path.resolve(projectFile));

  const byName = payloadOf(await runtime.callTool("write_file", {
    strictSessionId: begun.id,
    conversationId: begun.conversationId,
    project: "StrictFixture",
    path: "project://Config/ByExactName.ini",
    content: "[Strict]\nSelector=name\n",
  }));
  assert.strictEqual(byName.ok, true);
  assert.strictEqual(fs.existsSync(path.join(projectRoot, "Config", "ByExactName.ini")), true);

  const byRelativePath = payloadOf(await runtime.callTool("write_file", {
    strictSessionId: begun.id,
    conversationId: begun.conversationId,
    project: path.relative(workspaceRoot, projectFile),
    path: "project://Config/ByRelativePath.ini",
    content: "[Strict]\nSelector=relative\n",
  }));
  assert.strictEqual(byRelativePath.ok, true);
  assert.strictEqual(fs.existsSync(path.join(projectRoot, "Config", "ByRelativePath.ini")), true);
});

test("Strict exact-name binding fails closed when same-name project clones are discoverable", async (t) => {
  const { runtime, workspaceRoot, projectRoot, projectFile } = fixture(t);
  const cloneProject = path.join(workspaceRoot, "CloneOwner", "StrictFixture.uproject");
  fs.mkdirSync(path.dirname(cloneProject), { recursive: true });
  fs.writeFileSync(cloneProject, JSON.stringify({ FileVersion: 3, EngineAssociation: "5.6" }), "utf8");
  const begun = payloadOf(await runtime.callTool("strict_begin", {
    conversationId: "chat-clone-ambiguity",
    objective: "Stay bound to one physical project",
    project: projectFile,
  })).strictSession;

  const result = payloadOf(await runtime.callTool("write_file", {
    strictSessionId: begun.id,
    conversationId: begun.conversationId,
    project: "StrictFixture",
    path: "project://Config/Ambiguous.ini",
    content: "must not be written\n",
  }));
  assert.strictEqual(result.errorCode, "STRICT_SESSION_INVALID");
  assert.match(result.message, /multiple projects exactly match/i);
  assert.strictEqual(fs.existsSync(path.join(projectRoot, "Config", "Ambiguous.ini")), false);
  assert.strictEqual(fs.existsSync(path.join(path.dirname(cloneProject), "Config", "Ambiguous.ini")), false);
});

test("Strict waiting state pauses mutations and a bound project cannot be silently changed", async (t) => {
  const { runtime, projectFile } = fixture(t);
  const begun = payloadOf(await runtime.callTool("strict_begin", {
    conversationId: "chat-bound",
    objective: "Bound project work",
    project: projectFile,
  })).strictSession;
  const read = payloadOf(await runtime.callTool("read_file", { project: projectFile, path: "project://Config/Strict.ini" }));
  await runtime.callTool("strict_wait", {
    strictSessionId: begun.id,
    conversationId: "chat-bound",
    status: "waiting_user",
    reason: "Need approval",
  });
  const waiting = payloadOf(await runtime.callTool("replace_in_file", {
    strictSessionId: begun.id,
    conversationId: "chat-bound",
    path: "project://Config/Strict.ini",
    oldText: "Value=one",
    newText: "Value=two",
    expectedOccurrences: 1,
    expectedHash: read.sha256,
  }));
  assert.strictEqual(waiting.ok, false);
  assert.match(waiting.message, /strict_heartbeat/);
  await runtime.callTool("strict_heartbeat", { strictSessionId: begun.id, conversationId: "chat-bound" });
  const otherProject = path.join(path.dirname(path.dirname(projectFile)), "Other", "Other.uproject");
  fs.mkdirSync(path.dirname(otherProject), { recursive: true });
  fs.writeFileSync(otherProject, "{}", "utf8");
  const switched = payloadOf(await runtime.callTool("set_active_project", {
    strictSessionId: begun.id,
    conversationId: "chat-bound",
    projectPath: otherProject,
  }));
  assert.strictEqual(switched.ok, false);
  assert.match(switched.message, /differs from the project bound/i);
  const mismatched = payloadOf(await runtime.callTool("replace_in_file", {
    strictSessionId: begun.id,
    conversationId: "chat-bound",
    project: otherProject,
    path: "project://Config/Strict.ini",
    oldText: "Value=one",
    newText: "Value=two",
    expectedOccurrences: 1,
    expectedHash: read.sha256,
  }));
  assert.strictEqual(mismatched.ok, false);
  assert.match(mismatched.message, /differs from the project bound/i);
  const mismatchedName = payloadOf(await runtime.callTool("replace_in_file", {
    strictSessionId: begun.id,
    conversationId: "chat-bound",
    project: "Other",
    path: "project://Config/Strict.ini",
    oldText: "Value=one",
    newText: "Value=two",
    expectedOccurrences: 1,
    expectedHash: read.sha256,
  }));
  assert.strictEqual(mismatchedName.ok, false);
  assert.match(mismatchedName.message, /differs from the project bound/i);
});

test("expired or disconnected Strict sessions become nonblocking orphans and resume is explicit", (t) => {
  const workspaceRoot = fs.mkdtempSync(path.join(os.tmpdir(), "strict-lifecycle-"));
  t.after(() => fs.rmSync(workspaceRoot, { recursive: true, force: true }));
  let now = Date.parse("2026-08-22T00:00:00Z");
  const lifecycle = createStrictLifecycle({ stateRoot: workspaceRoot, clock: () => now });
  const first = lifecycle.begin({ conversationId: "chat-one", objective: "Long autonomous task", ttlSeconds: 60 });
  now += 61_000;
  assert.strictEqual(lifecycle.status({ strictSessionId: first.id, conversationId: "chat-one" }).status, "orphaned");
  assert.throws(() => lifecycle.resume({ strictSessionId: first.id, conversationId: "chat-one", userApproved: false }), /userApproved/);
  assert.strictEqual(lifecycle.resume({ strictSessionId: first.id, conversationId: "chat-one", userApproved: true }).status, "running");

  lifecycle.orphanOwned("connection_closed");
  assert.strictEqual(lifecycle.status({ strictSessionId: first.id, conversationId: "chat-one" }).status, "orphaned");
  const independent = lifecycle.begin({ conversationId: "chat-two", objective: "New work is not blocked" });
  assert.strictEqual(independent.status, "running");
});

test("a restarted Strict runtime cannot adopt a persisted running session", (t) => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "strict-restart-"));
  t.after(() => fs.rmSync(stateRoot, { recursive: true, force: true }));
  const first = createStrictLifecycle({
    stateRoot,
    processId: 101,
    processStartedAtMs: 1_000,
    isProcessAlive: (pid) => pid === 101,
  });
  const begun = first.begin({ conversationId: "chat-restart", objective: "Persisted work" });

  const restarted = createStrictLifecycle({
    stateRoot,
    processId: 202,
    processStartedAtMs: 2_000,
    isProcessAlive: () => false,
  });
  assert.strictEqual(restarted.status({ strictSessionId: begun.id, conversationId: "chat-restart" }).status, "orphaned");
  assert.throws(
    () => restarted.requireRunning(begun.id, "chat-restart"),
    /orphaned/,
  );
  assert.strictEqual(restarted.resume({
    strictSessionId: begun.id,
    conversationId: "chat-restart",
    userApproved: true,
  }).status, "running");
  assert.doesNotThrow(() => restarted.requireRunning(begun.id, "chat-restart"));
});
