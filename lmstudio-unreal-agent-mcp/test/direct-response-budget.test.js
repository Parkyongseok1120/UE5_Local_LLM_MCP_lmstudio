"use strict";

// Response-size contract: compact model JSON, byteBudget on every bounded
// read, summary-shaped long-running results, and cancellable search.

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { EventEmitter } = require("node:events");
const { PassThrough } = require("node:stream");

const { createDirectRuntime, toolDefinitions } = require("../src/direct-server.js");
const { DEFAULT_MAX_RESPONSE_CHARS, renderModelJson, toMcpResult } = require("../src/direct-response.js");
const {
  LONG_RUNNING_FAILURE_CHARS,
  LONG_RUNNING_SUCCESS_CHARS,
  boundLongRunningPayload,
  buildDirectResponse,
} = require("../src/direct-build-response.js");
const { createProcessIdentityCache } = require("../src/write-locks.js");
const { gitLimits } = require("../../shared-tool-core/git.js");

function payloadOf(result) {
  assert.deepStrictEqual(JSON.parse(result.content[0].text), result.structuredContent);
  return result.structuredContent;
}

function fixture(t, env = {}, options = {}) {
  const workspaceRoot = fs.mkdtempSync(path.join(os.tmpdir(), "unreal-direct-budget-"));
  const projectRoot = path.join(workspaceRoot, "BudgetFixture");
  const projectFile = path.join(projectRoot, "BudgetFixture.uproject");
  const sourceDir = path.join(projectRoot, "Source", "BudgetFixture");
  fs.mkdirSync(sourceDir, { recursive: true });
  fs.writeFileSync(projectFile, JSON.stringify({ FileVersion: 3, EngineAssociation: "5.4" }), "utf8");
  t.after(() => fs.rmSync(workspaceRoot, { recursive: true, force: true }));
  const runtime = createDirectRuntime({
    workspaceRoot,
    configPath: path.join(workspaceRoot, "agent-mcp.json"),
    env: {
      AGENT_STATE_ROOT: path.join(workspaceRoot, "state"),
      ALLOW_WRITE: "0", ALLOW_COMMANDS: "0", ALLOW_UNREAL_BUILD: "0", ALLOW_SOURCE_DELETE: "0",
      ...env,
    },
    getActiveProject: () => projectFile,
    ...options,
  });
  return { runtime, workspaceRoot, projectRoot, sourceDir };
}

test("model text is compact JSON and the default response cap is context-sized", async t => {
  assert.equal(DEFAULT_MAX_RESPONSE_CHARS, 48_000);
  const result = toMcpResult({ ok: true, nested: { value: [1, 2] } });
  assert.equal(result.content[0].text, renderModelJson(result.structuredContent));
  assert.doesNotMatch(result.content[0].text, /\n/);
  assert.equal(fixture(t).runtime.limits.maxResponseChars, 48_000);
  assert.equal(fixture(t, { DIRECT_MAX_RESPONSE_CHARS: "64000" }).runtime.limits.maxResponseChars, 64_000);
});

test("byteBudget errors quote the published schema bounds", async t => {
  const { runtime, sourceDir } = fixture(t);
  const file = path.join(sourceDir, "A.cpp");
  fs.writeFileSync(file, "int A;\n");
  const schema = toolDefinitions().find(tool => tool.name === "read_file").inputSchema.properties.byteBudget;
  const bad = payloadOf(await runtime.callTool("read_file", { path: file, byteBudget: 12 }));
  assert.equal(bad.errorCode, "INVALID_ARGUMENT");
  assert.match(bad.message, new RegExp(`from ${schema.minimum} to ${schema.maximum}`));
  for (const name of ["list_directory", "read_unreal_logs"]) {
    const budget = toolDefinitions().find(tool => tool.name === name).inputSchema.properties.byteBudget;
    assert.equal(budget.$comment, "direct-json-text-utf8", name);
  }
});

test("list_directory honors byteBudget and pages losslessly with offset", async t => {
  const { runtime, sourceDir } = fixture(t);
  const names = Array.from({ length: 120 }, (_, i) => `File_${String(i).padStart(3, "0")}_${"x".repeat(30)}.h`);
  for (const name of names) fs.writeFileSync(path.join(sourceDir, name), "");
  const seen = [];
  let offset = 0;
  for (let page = 0; page < 200 && offset !== null; page += 1) {
    const response = await runtime.callTool("list_directory", { path: sourceDir, byteBudget: 2048, offset });
    assert.ok(Buffer.byteLength(response.content[0].text, "utf8") <= 2048);
    const payload = payloadOf(response);
    assert.equal(payload.ok, true, JSON.stringify(payload));
    assert.equal(payload.offset, offset);
    assert.ok(payload.entries.length > 0);
    if (payload.nextOffset !== null) assert.equal(payload.omissionReason, "response_budget");
    seen.push(...payload.entries.map(entry => entry.name));
    offset = payload.nextOffset;
  }
  assert.deepEqual(seen, [...names].sort((a, b) => a.localeCompare(b)));
});

test("read_unreal_logs fits byteBudget by shrinking the source window, keeping cursors exact", async t => {
  const { runtime, workspaceRoot } = fixture(t);
  const logDir = path.join(workspaceRoot, ".agent", "logs");
  fs.mkdirSync(logDir, { recursive: true });
  const text = Array.from({ length: 400 }, (_, i) => `[${i}] LogTemp: "quoted" 한글 ${"y".repeat(40)}`).join("\n") + "\n";
  fs.writeFileSync(path.join(logDir, "Big.log"), text, "utf8");

  const tail = await runtime.callTool("read_unreal_logs", { fileName: "Big.log", byteBudget: 4096 });
  assert.ok(Buffer.byteLength(tail.content[0].text, "utf8") <= 4096);
  assert.equal(payloadOf(tail).ok, true);

  let cursorByte = 0;
  let delivered = "";
  for (let page = 0; page < 400 && cursorByte < Buffer.byteLength(text); page += 1) {
    const response = await runtime.callTool("read_unreal_logs", {
      fileName: "Big.log", mode: "range", cursorByte, maxLines: 500, byteBudget: 4096,
    });
    assert.ok(Buffer.byteLength(response.content[0].text, "utf8") <= 4096);
    const [log] = payloadOf(response).logs;
    assert.equal(log.cursorByte, cursorByte);
    assert.ok(log.nextCursorByte > cursorByte);
    delivered += log.content;
    cursorByte = log.nextCursorByte;
  }
  assert.equal(delivered, text);
});

test("search_files rejects risky regexes before scanning", async t => {
  const { runtime, sourceDir } = fixture(t);
  fs.writeFileSync(path.join(sourceDir, "A.cpp"), "aaaa\n");
  for (const query of ["(a+)+$", "x".repeat(513)]) {
    const payload = payloadOf(await runtime.callTool("search_files", { path: sourceDir, query, regex: true }));
    assert.equal(payload.errorCode, "INVALID_REGEX", query.slice(0, 20));
    assert.equal(payload.retry.allowed, true);
  }
  const ok = payloadOf(await runtime.callTool("search_files", { path: sourceDir, query: "a{2}", regex: true }));
  assert.equal(ok.ok, true);
  assert.equal(ok.results.length, 1);
});

test("search_files keeps file order under concurrent reads and fits byteBudget", async t => {
  const { runtime, sourceDir } = fixture(t);
  for (let i = 0; i < 60; i += 1) fs.writeFileSync(path.join(sourceDir, `M${String(i).padStart(2, "0")}.cpp`), `// MATCH ${i}\n`);
  const expected = fs.readdirSync(sourceDir);
  const full = payloadOf(await runtime.callTool("search_files", { path: sourceDir, query: "MATCH" }));
  assert.equal(full.coverage, "complete");
  assert.deepEqual(full.results.map(result => result.path), expected);
  const budgeted = await runtime.callTool("search_files", { path: sourceDir, query: "MATCH", byteBudget: 2048 });
  assert.ok(Buffer.byteLength(budgeted.content[0].text, "utf8") <= 2048);
  const partial = payloadOf(budgeted);
  assert.equal(partial.omissionReason, "response_budget");
  assert.deepEqual(partial.results.map(result => result.path), expected.slice(0, partial.results.length));
});

test("search_files stops at its deadline with a partial result and reason", async t => {
  const { runtime, sourceDir } = fixture(t, { DIRECT_SEARCH_DEADLINE_MS: "1" });
  for (let i = 0; i < 400; i += 1) fs.writeFileSync(path.join(sourceDir, `D${i}.cpp`), "// NEEDLE\n");
  const payload = payloadOf(await runtime.callTool("search_files", { path: sourceDir, query: "NEEDLE" }));
  assert.equal(payload.ok, true);
  assert.equal(payload.stopReason, "deadline");
  assert.equal(payload.coverage, "partial");
  assert.equal(payload.deadlineMs, 1);
  assert.ok(payload.filesUnvisited >= 0);
});

test("search_files observes request cancellation while scanning", async t => {
  const { runtime, sourceDir } = fixture(t);
  for (let i = 0; i < 400; i += 1) fs.writeFileSync(path.join(sourceDir, `C${i}.cpp`), "// NEEDLE\n");
  const controller = new AbortController();
  const pending = runtime.callTool("search_files", { path: sourceDir, query: "NEEDLE" }, { signal: controller.signal });
  setImmediate(() => controller.abort());
  const payload = payloadOf(await pending);
  assert.equal(payload.errorCode, "REQUEST_CANCELLED");
});

test("long-running payloads keep summary, counts and fullLogPath within the transport caps", () => {
  const stdout = Array.from({ length: 50 }, (_, i) => `Source/A.cpp(${i + 1}): error C2039: ${"missing member ".repeat(25)}${i}`).join("\n");
  const raw = buildDirectResponse({
    result: { ok: false, exitCode: 6, stdout, stderr: "x".repeat(50_000) },
    build: { target: "DemoEditor", platform: "Win64", configuration: "Development" },
    projectPath: "C:/Projects/Demo/Demo.uproject",
    command: "UnrealBuildTool",
    logPath: "C:/logs/build.log",
    verbose: true,
  });
  assert.ok(renderModelJson(raw).length > LONG_RUNNING_FAILURE_CHARS);
  const bounded = boundLongRunningPayload(raw);
  assert.ok(renderModelJson(bounded).length <= LONG_RUNNING_FAILURE_CHARS);
  assert.equal(bounded.fullLogPath, "C:/logs/build.log");
  assert.equal(bounded.summary, raw.summary);
  assert.ok(bounded.diagnostics.length >= 1);
  assert.deepEqual(bounded.diagnostics, raw.diagnostics.slice(0, bounded.diagnostics.length));
  assert.equal(bounded.diagnosticCoverage.returnedEntries, bounded.diagnostics.length);
  assert.ok(bounded.responseTrimmed.stderr);
  // The bounded failure survives the 4,096-character error transport intact.
  const transported = JSON.parse(toMcpResult(bounded).content[0].text);
  assert.equal(transported.fullLogPath, "C:/logs/build.log");
  assert.equal(transported.errorCode, "BUILD_FAILED");

  const findings = Array.from({ length: 100 }, (_, i) => ({ code: `F${i}`, message: "m".repeat(400) }));
  const success = boundLongRunningPayload({ ok: true, findingCount: 100, findings });
  assert.ok(renderModelJson(success).length <= LONG_RUNNING_SUCCESS_CHARS);
  assert.equal(success.findingCount, 100);
  assert.deepEqual(success.findings, findings.slice(0, success.findings.length));
});

test("run_command returns bounded tails and persists the captured output", async t => {
  const output = Array.from({ length: 2000 }, (_, i) => `line ${i}`).join("\n");
  function spawnCommand() {
    const child = new EventEmitter();
    child.pid = 515151;
    child.stdout = new PassThrough();
    child.stderr = new PassThrough();
    process.nextTick(() => {
      child.stdout.end(output);
      setImmediate(() => child.emit("close", 0));
    });
    return child;
  }
  const { runtime } = fixture(t, { ALLOW_COMMANDS: "1" }, { spawnCommand, killProcessTree: async () => {} });
  const payload = payloadOf(await runtime.callTool("run_command", { command: "node --version" }));
  assert.equal(payload.ok, true, JSON.stringify(payload));
  assert.ok(payload.stdout.length <= 6000);
  assert.ok(payload.stdout.endsWith("line 1999"));
  assert.ok(fs.readFileSync(payload.fullLogPath, "utf8").includes("line 0\n"));
  assert.ok(renderModelJson(payload).length <= LONG_RUNNING_SUCCESS_CHARS);
});

test("other-PID identity probes are cached briefly and failures are retried", () => {
  let clock = 0;
  const calls = [];
  const results = { 10: "filetime:1", 11: "" };
  const cached = createProcessIdentityCache({ probe: pid => { calls.push(pid); return results[pid]; }, ttlMs: 100, now: () => clock });
  assert.equal(cached(10), "filetime:1");
  assert.equal(cached(10), "filetime:1");
  assert.equal(cached(11), "");
  assert.equal(cached(11), "");
  assert.deepEqual(calls, [10, 11, 11]);
  clock = 101;
  assert.equal(cached(10), "filetime:1");
  assert.deepEqual(calls, [10, 11, 11, 10]);
});

test("Git subprocess limits are configurable and clamped", t => {
  const saved = { timeout: process.env.WORKSPACE_GIT_TIMEOUT_MS, buffer: process.env.WORKSPACE_GIT_MAX_BUFFER_BYTES };
  t.after(() => {
    for (const [key, value] of [["WORKSPACE_GIT_TIMEOUT_MS", saved.timeout], ["WORKSPACE_GIT_MAX_BUFFER_BYTES", saved.buffer]]) {
      if (value === undefined) delete process.env[key]; else process.env[key] = value;
    }
  });
  delete process.env.WORKSPACE_GIT_TIMEOUT_MS;
  delete process.env.WORKSPACE_GIT_MAX_BUFFER_BYTES;
  assert.deepEqual(gitLimits(), { timeoutMs: 10_000, maxBuffer: 4 * 1024 * 1024 });
  process.env.WORKSPACE_GIT_TIMEOUT_MS = "30000";
  process.env.WORKSPACE_GIT_MAX_BUFFER_BYTES = String(1024 ** 4);
  assert.deepEqual(gitLimits(), { timeoutMs: 30_000, maxBuffer: 64 * 1024 * 1024 });
});

// 16,384 since edits[], search contextLines/include and read_symbol view: each
// removes whole rounds or file reads, costing about 100 schema tokens per round.
// 17,024 since the line-range patch form and build staticPrecheck: the first
// replaces stale-oldText OCCURRENCE_MISMATCH rounds, the second failed builds.
test("published catalog stays inside the per-round schema budget", () => {
  assert.ok(JSON.stringify(toolDefinitions()).length <= 17_024);
});
