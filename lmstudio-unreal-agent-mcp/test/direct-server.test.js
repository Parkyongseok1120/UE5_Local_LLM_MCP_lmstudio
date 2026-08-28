"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const crypto = require("node:crypto");
const { spawnSync } = require("node:child_process");
const { EventEmitter } = require("node:events");
const { PassThrough } = require("node:stream");

const {
  createDirectRuntime,
  toolDefinitions,
} = require("../src/direct-server.js");

const CONTROL_FIELD_PATTERN = /taskAuthorization|ownerCapability|routeHash|toolRoute|serverControl|synthesisReadiness|claimLedger/;

function payloadOf(result) {
  assert.ok(result && typeof result === "object");
  assert.ok(result.structuredContent && typeof result.structuredContent === "object");
  assert.deepStrictEqual(JSON.parse(result.content[0].text), result.structuredContent);
  return result.structuredContent;
}

function fixture(t, envOverrides = {}) {
  const workspaceRoot = fs.mkdtempSync(path.join(os.tmpdir(), "unreal-direct-server-"));
  const projectRoot = path.join(workspaceRoot, "DirectFixture");
  const projectFile = path.join(projectRoot, "DirectFixture.uproject");
  const configFile = path.join(projectRoot, "Config", "DirectTest.ini");
  const sourceFile = path.join(projectRoot, "Source", "DirectFixture", "DirectFixture.cpp");
  const stateRoot = path.join(workspaceRoot, "foreign-agent-state");

  fs.mkdirSync(path.dirname(configFile), { recursive: true });
  fs.mkdirSync(path.dirname(sourceFile), { recursive: true });
  fs.writeFileSync(projectFile, JSON.stringify({
    FileVersion: 3,
    EngineAssociation: "5.4",
    Modules: [{ Name: "DirectFixture", Type: "Runtime" }],
  }), "utf8");
  fs.writeFileSync(configFile, "[Direct]\nValue=alpha\n", "utf8");
  fs.writeFileSync(sourceFile, "void DirectFixture() {}\n", "utf8");

  const foreignTaskDir = path.join(stateRoot, "tasks", "foreign-task-from-another-chat");
  fs.mkdirSync(foreignTaskDir, { recursive: true });
  fs.writeFileSync(path.join(foreignTaskDir, "state.json"), JSON.stringify({
    taskSessionId: "foreign-task-from-another-chat",
    status: "running",
    mode: "read_only",
    ownerCapability: "foreign-owner-secret",
    mcpConnectionId: "different-connection",
    toolRoute: {
      phase: "synthesis",
      activeTools: [],
      allowedTools: [],
      routeHash: "foreign-route",
    },
  }), "utf8");

  const previousStateRoot = process.env.AGENT_STATE_ROOT;
  process.env.AGENT_STATE_ROOT = stateRoot;
  t.after(() => {
    if (previousStateRoot === undefined) delete process.env.AGENT_STATE_ROOT;
    else process.env.AGENT_STATE_ROOT = previousStateRoot;
    fs.rmSync(workspaceRoot, { recursive: true, force: true });
  });

  const env = {
    AGENT_STATE_ROOT: stateRoot,
    ALLOW_WRITE: "0",
    ALLOW_COMMANDS: "0",
    ALLOW_UNREAL_BUILD: "0",
    ALLOW_SOURCE_DELETE: "0",
    ...envOverrides,
  };
  const runtime = createDirectRuntime({
    workspaceRoot,
    configPath: path.join(workspaceRoot, "agent-mcp.json"),
    env,
    getActiveProject: () => projectFile,
  });
  return { runtime, workspaceRoot, projectRoot, projectFile, configFile, sourceFile, stateRoot };
}

test("Direct and Strict use separate executable entries", () => {
  const pkg = require("../package.json");
  assert.strictEqual(pkg.main, "src/direct-server.js");
  assert.strictEqual(pkg.scripts.start, "node src/direct-server.js");
  assert.strictEqual(pkg.scripts["start:strict"], "node src/strict-server.js");
  const directSource = fs.readFileSync(path.resolve(__dirname, "../src/direct-server.js"), "utf8");
  assert.doesNotMatch(directSource, /MCP_EXECUTION_MODE|strict-server|createStrictRuntime/);
});

test("Direct catalog is static capability surface without task/control schemas", () => {
  const tools = toolDefinitions();
  const names = tools.map((tool) => tool.name);
  for (const expected of [
    "get_active_project",
    "list_directory",
    "search_files",
    "read_file",
    "read_file_range",
    "read_symbol",
    "read_unreal_logs",
    "replace_in_file",
    "build_unreal_project",
  ]) {
    assert.ok(names.includes(expected), `${expected} should be visible in Direct mode`);
  }
  assert.deepStrictEqual(
    names.filter((name) => /(?:task|route|plan|checkpoint|synthesis|intent|evidence)/i.test(name)),
    [],
  );
  assert.doesNotMatch(JSON.stringify(tools.map((tool) => tool.inputSchema)), CONTROL_FIELD_PATTERN);
  for (const tool of tools) {
    assert.strictEqual(tool.inputSchema.additionalProperties, false);
  }
  const workspaceInfo = tools.find((tool) => tool.name === "get_workspace_info");
  const search = tools.find((tool) => tool.name === "search_files");
  assert.match(workspaceInfo.description, /useful early authority check when the current flags are unknown/u);
  assert.match(workspaceInfo.description, /does not own tool order/u);
  assert.match(search.description, /directly reusable uri/u);
  assert.match(search.description, /exact activeProject/u);
});

test("mutation schemas expose focused per-round edit bounds", () => {
  const tools = toolDefinitions();
  const write = tools.find((tool) => tool.name === "write_file");
  const replace = tools.find((tool) => tool.name === "replace_in_file");
  const bundle = tools.find((tool) => tool.name === "apply_edit_bundle");
  const proposeDelete = tools.find((tool) => tool.name === "propose_file_deletions");
  const deleteFile = tools.find((tool) => tool.name === "delete_file");
  assert.ok(write);
  assert.ok(replace);
  assert.ok(bundle);
  assert.ok(proposeDelete);
  assert.ok(deleteFile);

  assert.strictEqual(write.inputSchema.properties.path.minLength, 1);
  assert.strictEqual(write.inputSchema.properties.content.maxLength, 12_000);
  assert.match(write.description, /apply_edit_bundle never creates files/u);

  const replaceProperties = replace.inputSchema.properties;
  assert.deepStrictEqual(
    {
      minLength: replaceProperties.oldText.minLength,
      maxLength: replaceProperties.oldText.maxLength,
    },
    { minLength: 1, maxLength: 1_200 },
  );
  assert.strictEqual(replaceProperties.newText.maxLength, 2_800);
  assert.deepStrictEqual(
    {
      type: replaceProperties.expectedOccurrences.type,
      const: replaceProperties.expectedOccurrences.const,
    },
    { type: "integer", const: 1 },
  );
  assert.strictEqual(replaceProperties.expectedHash.pattern, "^[A-Fa-f0-9]{64}$");
  assert.strictEqual(replaceProperties.fileVersionReceipt.minLength, 1);
  assert.deepStrictEqual(replace.inputSchema.anyOf, [
    { required: ["fileVersionReceipt"] },
    { required: ["expectedHash"] },
  ]);
  assert.match(replace.description, /Emit this tool call immediately/u);
  assert.match(replace.description, /4000 combined characters/u);
  assert.match(replace.description, /next prediction round/u);
  assert.match(replace.description, /no same-session evidence is selected automatically/u);
  assert.match(replace.description, /Requires ALLOW_WRITE=1/u);

  const patches = bundle.inputSchema.properties.patches;
  assert.deepStrictEqual(bundle.inputSchema.required, ["patches"]);
  assert.strictEqual(Object.hasOwn(bundle.inputSchema.properties, "files"), false);
  assert.strictEqual(patches.minItems, 1);
  assert.strictEqual(patches.maxItems, 2);
  assert.strictEqual(patches.items.properties.oldText.maxLength, 1_200);
  assert.strictEqual(patches.items.properties.newText.maxLength, 2_800);
  assert.strictEqual(patches.items.properties.expectedOccurrences.const, 1);
  assert.deepStrictEqual(patches.items.anyOf, [
    { required: ["fileVersionReceipt"] },
    { required: ["expectedHash"] },
  ]);
  assert.match(patches.description, /server-enforced because JSON Schema cannot express them/u);
  assert.match(bundle.description, /at most 2 distinct existing files/u);
  assert.match(bundle.description, /server additionally enforces unique normalized paths/u);
  assert.match(bundle.description, /at most 64 aggregate changed lines/u);
  assert.match(bundle.description, /next prediction round/u);
  assert.match(bundle.description, /New files are created only with standalone write_file/u);
  assert.match(bundle.description, /Requires ALLOW_WRITE=1/u);

  const deletionFiles = proposeDelete.inputSchema.properties.files;
  assert.strictEqual(deletionFiles.minItems, 1);
  assert.strictEqual(deletionFiles.maxItems, 32);
  assert.strictEqual(deletionFiles.items.properties.reason.minLength, 1);
  assert.deepStrictEqual(deleteFile.inputSchema.anyOf, [
    { required: ["fileVersionReceipt"] },
    { required: ["expectedHash"] },
  ]);
});

test("foreign persistent task files cannot block Direct reads", async (t) => {
  const { runtime } = fixture(t);
  const result = await runtime.callTool("read_file", {
    path: "project://Config/DirectTest.ini",
  });
  const payload = payloadOf(result);

  assert.strictEqual(result.isError, false);
  assert.strictEqual(payload.ok, true);
  assert.match(payload.content, /Value=alpha/);
  assert.match(payload.sha256, /^[a-f0-9]{64}$/);
  assert.doesNotMatch(JSON.stringify(payload), CONTROL_FIELD_PATTERN);
});

test("successful Direct read is concise only when the caller echoes its receipt", async (t) => {
  const { runtime } = fixture(t);
  const args = { path: "project://Config/DirectTest.ini" };
  const first = payloadOf(await runtime.callTool("read_file", args));
  const secondResult = await runtime.callTool("read_file", args);
  const second = payloadOf(secondResult);
  const acknowledgedResult = await runtime.callTool("read_file", { ...args, repeatReceipt: second.repeatReceipt });
  const acknowledged = payloadOf(acknowledgedResult);

  assert.match(first.content, /Value=alpha/);
  assert.match(first.repeatReceipt, /^[A-Za-z0-9_-]+$/);
  assert.match(second.content, /Value=alpha/);
  assert.notStrictEqual(second.repeatReceipt, first.repeatReceipt);
  assert.strictEqual(acknowledged.ok, true);
  assert.strictEqual(acknowledged.duplicate, true);
  assert.strictEqual(acknowledged.status, "no_new_information");
  assert.strictEqual(acknowledged.content, undefined);
  assert.ok(acknowledgedResult.content[0].text.length < 1024);
  assert.doesNotMatch(acknowledgedResult.content[0].text, CONTROL_FIELD_PATTERN);
});

test("one Direct process can address multiple Unreal projects per call without changing the active project", async (t) => {
  const { runtime, workspaceRoot, projectFile } = fixture(t, { ALLOW_WRITE: "1" });
  const secondRoot = path.join(workspaceRoot, "SecondFixture");
  const secondProject = path.join(secondRoot, "SecondFixture.uproject");
  const secondConfig = path.join(secondRoot, "Config", "Second.ini");
  fs.mkdirSync(path.dirname(secondConfig), { recursive: true });
  fs.writeFileSync(secondProject, JSON.stringify({
    FileVersion: 3,
    EngineAssociation: "5.6",
    Modules: [{ Name: "SecondFixture", Type: "Runtime" }],
  }), "utf8");
  fs.writeFileSync(secondConfig, "[Second]\nValue=two\n", "utf8");

  const read = payloadOf(await runtime.callTool("read_file", {
    project: secondProject,
    path: "project://Config/Second.ini",
  }));
  assert.strictEqual(read.ok, true);
  assert.strictEqual(read.activeProject, secondProject);
  assert.match(read.content, /Value=two/);

  const search = payloadOf(await runtime.callTool("search_files", {
    project: secondProject,
    path: "project://Config",
    query: "Second.ini",
    matchFileNames: true,
    maxResults: 5,
  }));
  assert.strictEqual(search.path, "project://Config");
  assert.strictEqual(search.displayPath, "project://Config");
  assert.strictEqual(search.projectRelativePath, "Config");
  assert.strictEqual(search.activeProject, secondProject);
  assert.strictEqual(search.results[0].path, "Second.ini");
  assert.strictEqual(search.results[0].uri, "project://Config/Second.ini");
  const searchRead = payloadOf(await runtime.callTool("read_file", {
    project: search.activeProject,
    path: search.results[0].uri,
  }));
  assert.match(searchRead.content, /Value=two/);

  const patched = payloadOf(await runtime.callTool("replace_in_file", {
    project: secondProject,
    path: "project://Config/Second.ini",
    oldText: "Value=two",
    newText: "Value=updated",
    expectedOccurrences: 1,
    expectedHash: read.sha256,
  }));
  assert.strictEqual(patched.ok, true);
  assert.match(fs.readFileSync(secondConfig, "utf8"), /Value=updated/);

  const detected = payloadOf(await runtime.callTool("detect_unreal_project", {
    project: "SecondFixture",
    resolveBuildDefaults: false,
  }));
  assert.strictEqual(path.resolve(detected.selected.projectPath), path.resolve(secondProject));

  let capturedBuildOptions;
  const buildRuntime = createDirectRuntime({
    workspaceRoot,
    configPath: path.join(workspaceRoot, "agent-mcp.json"),
    env: { ALLOW_WRITE: "0", ALLOW_UNREAL_BUILD: "1", ALLOW_COMMANDS: "0" },
    getActiveProject: () => projectFile,
    resolveBuildPlan: async (_workspace, _config, options) => {
      capturedBuildOptions = options;
      return { ok: false, errorCode: "TEST_STOP", error: "stop after selection" };
    },
  });
  await buildRuntime.callTool("build_unreal_project", { project: "SecondFixture" });
  assert.strictEqual(path.resolve(capturedBuildOptions.project), path.resolve(secondProject));
  assert.strictEqual(capturedBuildOptions.hint, undefined);

  const active = payloadOf(await runtime.callTool("get_active_project", {}));
  assert.strictEqual(active.activeProject, projectFile);
  for (const name of ["list_directory", "search_files", "read_file", "read_file_range", "read_symbol", "write_file", "replace_in_file", "apply_edit_bundle"] ) {
    assert.ok(runtime.tools.find((tool) => tool.name === name).inputSchema.properties.project, `${name} must support a per-call project selector`);
  }
});

test("search_files URIs retain root identity and exact-project pairing", async (t) => {
  const { runtime, workspaceRoot, projectFile } = fixture(t);
  const cloneRoot = path.join(workspaceRoot, "SameNameClone", "DirectFixture");
  const cloneProject = path.join(cloneRoot, "DirectFixture.uproject");
  const cloneConfig = path.join(cloneRoot, "Config", "DirectTest.ini");
  fs.mkdirSync(path.dirname(cloneConfig), { recursive: true });
  fs.writeFileSync(cloneProject, JSON.stringify({ FileVersion: 3, Modules: [{ Name: "DirectFixture", Type: "Runtime" }] }), "utf8");
  fs.writeFileSync(cloneConfig, "[Direct]\nValue=clone\n", "utf8");

  const original = payloadOf(await runtime.callTool("search_files", {
    project: projectFile,
    path: "project://Config",
    query: "Value=",
    maxResults: 5,
  }));
  const clone = payloadOf(await runtime.callTool("search_files", {
    project: cloneProject,
    path: "project://Config",
    query: "Value=",
    maxResults: 5,
  }));
  assert.strictEqual(original.results[0].uri, "project://Config/DirectTest.ini");
  assert.strictEqual(clone.results[0].uri, original.results[0].uri);
  assert.notStrictEqual(clone.activeProject, original.activeProject);
  const cloneRead = payloadOf(await runtime.callTool("read_file", {
    project: clone.activeProject,
    path: clone.results[0].uri,
  }));
  assert.match(cloneRead.content, /Value=clone/);

  const workspace = payloadOf(await runtime.callTool("search_files", {
    path: "workspace://DirectFixture/Config",
    query: "Value=",
    maxResults: 5,
  }));
  assert.strictEqual(workspace.results[0].uri, "workspace://DirectFixture/Config/DirectTest.ini");
  const singleFile = payloadOf(await runtime.callTool("search_files", {
    path: "project://Config/DirectTest.ini",
    query: "Value=",
    maxResults: 5,
  }));
  assert.strictEqual(singleFile.results[0].path, ".");
  assert.strictEqual(singleFile.results[0].uri, "project://Config/DirectTest.ini");
});

test("Direct exact patch succeeds without plan/task and stale hash is rejected", async (t) => {
  const { runtime, configFile } = fixture(t, { ALLOW_WRITE: "1" });
  const read = payloadOf(await runtime.callTool("read_file", {
    path: "project://Config/DirectTest.ini",
  }));

  const patchedResult = await runtime.callTool("replace_in_file", {
    path: "project://Config/DirectTest.ini",
    oldText: "Value=alpha",
    newText: "Value=beta",
    expectedOccurrences: 1,
    expectedHash: read.sha256,
  });
  const patched = payloadOf(patchedResult);
  assert.strictEqual(patchedResult.isError, false);
  assert.strictEqual(patched.ok, true);
  assert.strictEqual(patched.operation, "replaced");
  assert.match(fs.readFileSync(configFile, "utf8"), /Value=beta/);
  assert.doesNotMatch(JSON.stringify(patched), CONTROL_FIELD_PATTERN);

  const conflictResult = await runtime.callTool("replace_in_file", {
    path: "project://Config/DirectTest.ini",
    oldText: "Value=beta",
    newText: "Value=gamma",
    expectedOccurrences: 1,
    expectedHash: read.sha256,
  });
  const conflict = payloadOf(conflictResult);
  assert.strictEqual(conflictResult.isError, true);
  assert.strictEqual(conflict.errorCode, "FILE_VERSION_CONFLICT");
  assert.deepStrictEqual(conflict.retry, {
    allowed: true,
    mode: "after_state_change",
  });
  assert.strictEqual(conflict.suggestion.tool, "read_file_range");
  assert.match(fs.readFileSync(configFile, "utf8"), /Value=beta/);
});

test("static validation is advisory and build execution is independently permissioned", async (t) => {
  const { runtime } = fixture(t);
  const staticTool = runtime.tools.find((tool) => tool.name === "static_validate_project");
  const buildTool = runtime.tools.find((tool) => tool.name === "build_unreal_project");
  assert.match(staticTool.description, /advisory/i);
  assert.match(buildTool.description, /Immediately resolve and run UBT\/UHT/i);
  for (const field of ["taskAuthorization", "planId", "validationProof", "requiredGate"] ) {
    assert.strictEqual(buildTool.inputSchema.properties[field], undefined);
  }

  const workspace = payloadOf(await runtime.callTool("get_workspace_info", {}));
  assert.equal(Object.hasOwn(workspace, "workflow"), false);

  const buildResult = await runtime.callTool("build_unreal_project", {});
  const build = payloadOf(buildResult);
  assert.strictEqual(buildResult.isError, true);
  assert.strictEqual(build.errorCode, "BUILD_DISABLED");
  assert.deepStrictEqual(build.retry, { allowed: false, mode: "none" });
  assert.doesNotMatch(JSON.stringify(build), /VALIDATION_REQUIRED|TASK_|requiredNextTool|control/);
});

test("default Direct dependency graph excludes legacy task/route/synthesis owners", () => {
  const target = path.resolve(__dirname, "../src/direct-server.js");
  const script = `
    const Module = require("module");
    const loaded = [];
    const original = Module._load;
    Module._load = function(request, parent, isMain) {
      try {
        const resolved = Module._resolveFilename(request, parent, isMain);
        if (typeof resolved === "string") loaded.push(resolved.replace(/\\\\/g, "/"));
      } catch {}
      return original.apply(this, arguments);
    };
    require(${JSON.stringify(target)});
    process.stdout.write(JSON.stringify(loaded));
  `;
  const child = spawnSync(process.execPath, ["-e", script], {
    cwd: path.dirname(target),
    encoding: "utf8",
    // A stale legacy mode variable must not alter the dedicated Direct entry.
    env: { ...process.env, MCP_EXECUTION_MODE: "strict" },
  });
  assert.strictEqual(child.status, 0, child.stderr);
  const loaded = JSON.parse(child.stdout);
  const banned = [
    "/src/server.js",
    "/src/task-auth.js",
    "/src/task-control-transition.js",
    "/src/post-read-route-control.js",
    "/src/route-watcher.js",
    "/src/route-authorization-failure-options.js",
    "/src/synthesis-readiness.js",
    "/src/control-envelope.js",
    "/src/control-protocol-spec.js",
  ];
  const violations = loaded.filter((file) => banned.some((suffix) => file.endsWith(suffix)));
  assert.deepStrictEqual(violations, []);
});

test("Direct composition root stays small and delegates bounded capability modules", () => {
  const sourceRoot = path.resolve(__dirname, "../src");
  const serverSource = fs.readFileSync(path.join(sourceRoot, "direct-server.js"), "utf8");
  assert.ok(serverSource.split(/\r?\n/).length <= 180, "direct-server.js must remain a composition root");
  for (const factory of [
    "createProjectCapabilities",
    "createReadCapabilities",
    "createLogCapabilities",
    "createMutationCapabilities",
    "createDiagnosticCapabilities",
  ]) {
    assert.match(serverSource, new RegExp(`\\b${factory}\\b`));
  }
  assert.doesNotMatch(serverSource, /readFile\(|replaceWithCAS|readUtf8Window|spawnCommand|runStaticValidation/);

  for (const fileName of [
    "direct-project-capabilities.js",
    "direct-read-capabilities.js",
    "direct-log-capabilities.js",
    "direct-mutation-capabilities.js",
    "direct-diagnostic-capabilities.js",
  ]) {
    const source = fs.readFileSync(path.join(sourceRoot, fileName), "utf8");
    assert.ok(source.split(/\r?\n/).length <= 360, `${fileName} exceeded the capability-module size boundary`);
    assert.doesNotMatch(source, CONTROL_FIELD_PATTERN);
  }
});

test("read_file advances by exactly the UTF-8 bytes delivered under a small response limit", async (t) => {
  const { runtime, projectRoot } = fixture(t, {
    DIRECT_MAX_RESPONSE_CHARS: "16000",
    MAX_READ_BYTES: String(2 * 1024 * 1024),
  });
  const target = path.join(projectRoot, "Config", "LargeUtf8.ini");
  const source = Array.from(
    { length: 3600 },
    (_, index) => `키-${String(index).padStart(5, "0")}-🙂-${"x".repeat(72)}`,
  ).join("\n");
  assert.ok(Buffer.byteLength(source, "utf8") > 300 * 1024);
  fs.writeFileSync(target, source, "utf8");
  const fullSnapshotHash = crypto.createHash("sha256").update(Buffer.from(source, "utf8")).digest("hex");

  let offsetBytes = 0;
  let reconstructed = "";
  let expectedHash = "";
  for (let call = 0; call < 200; call += 1) {
    const result = await runtime.callTool("read_file", {
      path: "project://Config/LargeUtf8.ini",
      offsetBytes,
      maxBytes: 2 * 1024 * 1024,
    });
    const payload = payloadOf(result);
    assert.strictEqual(result.isError, false);
    const deliveredBytes = Buffer.byteLength(payload.content, "utf8");
    assert.ok(deliveredBytes > 0 || payload.hasMore === false);
    assert.strictEqual(payload.offsetBytes, offsetBytes);
    assert.strictEqual(payload.nextOffsetBytes, offsetBytes + deliveredBytes);
    assert.ok(result.content[0].text.length <= 16000);
    if (!expectedHash) expectedHash = payload.sha256;
    assert.strictEqual(payload.sha256, expectedHash);
    assert.strictEqual(payload.sha256, fullSnapshotHash);
    reconstructed += payload.content;
    offsetBytes = payload.nextOffsetBytes;
    if (!payload.hasMore) break;
  }
  assert.strictEqual(offsetBytes, Buffer.byteLength(source, "utf8"));
  assert.strictEqual(reconstructed, source);
});

test("read_file_range reports the last line actually delivered and a lossless continuation", async (t) => {
  const { runtime, projectRoot } = fixture(t, { DIRECT_MAX_RESPONSE_CHARS: "16000" });
  const target = path.join(projectRoot, "Config", "ManyLines.ini");
  const sourceLines = Array.from(
    { length: 6000 },
    (_, index) => `Line-${String(index + 1).padStart(5, "0")}-${"v".repeat(32)}`,
  );
  fs.writeFileSync(target, sourceLines.join("\n"), "utf8");

  const received = [];
  let startLine = 1;
  for (let call = 0; call < 100; call += 1) {
    const result = await runtime.callTool("read_file_range", {
      path: "project://Config/ManyLines.ini",
      startLine,
      endLine: sourceLines.length,
    });
    const payload = payloadOf(result);
    assert.strictEqual(result.isError, false);
    const delivered = payload.content.split("\n");
    assert.strictEqual(payload.startLine, startLine);
    assert.strictEqual(payload.endLine, startLine + delivered.length - 1);
    assert.ok(result.content[0].text.length <= 16000);
    received.push(...delivered);
    if (payload.nextStartLine === null) break;
    assert.strictEqual(payload.nextStartLine, payload.endLine + 1);
    assert.ok(payload.nextStartLine > startLine);
    startLine = payload.nextStartLine;
  }
  assert.deepStrictEqual(received, sourceLines);
});

test("read_unreal_logs range keeps source-byte cursors across CRLF filters and large lines", async (t) => {
  const { runtime, workspaceRoot } = fixture(t, { DIRECT_MAX_RESPONSE_CHARS: "16000" });
  const logRoot = path.join(workspaceRoot, ".agent", "logs");
  fs.mkdirSync(logRoot, { recursive: true });
  const filteredPath = path.join(logRoot, "Cursor.log");
  const filteredText = Array.from(
    { length: 180 },
    (_, index) => `${index % 9 === 0 ? "MATCH" : "skip"}-${String(index).padStart(4, "0")}-${"a".repeat(24)}`,
  ).join("\r\n");
  fs.writeFileSync(filteredPath, filteredText, "utf8");

  const matched = payloadOf(await runtime.callTool("read_unreal_logs", {
    mode: "range",
    fileName: "Cursor.log",
    cursorByte: 0,
    maxBytes: 1024,
    maxLines: 500,
    filter: "MATCH",
  })).logs[0];
  assert.ok(matched.nextCursorByte > 0);
  assert.ok(matched.hasMore);
  assert.match(matched.content, /MATCH/);
  assert.strictEqual(matched.size, Buffer.byteLength(filteredText, "utf8"));

  let cursorByte = 0;
  for (let call = 0; call < 100; call += 1) {
    const payload = payloadOf(await runtime.callTool("read_unreal_logs", {
      mode: "range",
      fileName: "Cursor.log",
      cursorByte,
      maxBytes: 1024,
      maxLines: 500,
      filter: "NEVER_PRESENT",
    }));
    const log = payload.logs[0];
    assert.strictEqual(log.content, "");
    assert.ok(log.nextCursorByte > cursorByte || log.hasMore === false);
    cursorByte = log.nextCursorByte;
    if (!log.hasMore) break;
  }
  assert.strictEqual(cursorByte, Buffer.byteLength(filteredText, "utf8"));

  const singleLinePath = path.join(logRoot, "SingleLine.log");
  const singleLine = `MATCH-EVIDENCE-${"z".repeat(5000)}`;
  fs.writeFileSync(singleLinePath, singleLine, "utf8");
  const large = payloadOf(await runtime.callTool("read_unreal_logs", {
    mode: "range",
    fileName: "SingleLine.log",
    cursorByte: 0,
    maxBytes: 1024,
    maxLines: 500,
    filter: "MATCH-EVIDENCE",
  })).logs[0];
  assert.strictEqual(large.fileName, "SingleLine.log");
  assert.strictEqual(path.resolve(large.fullPath), path.resolve(singleLinePath));
  assert.strictEqual(large.cursorByte, 0);
  assert.ok(large.nextCursorByte > 0);
  assert.ok(large.hasMore);
  assert.match(large.content, /^MATCH-EVIDENCE/);
});

test("per-call project remains bound in static validation and recovery suggestions", async (t) => {
  const { runtime, workspaceRoot, projectRoot } = fixture(t);
  const secondRoot = path.join(workspaceRoot, "BoundProject");
  const secondProject = path.join(secondRoot, "BoundProject.uproject");
  fs.mkdirSync(path.join(secondRoot, "Config"), { recursive: true });
  fs.writeFileSync(secondProject, JSON.stringify({ FileVersion: 3, EngineAssociation: "5.5" }), "utf8");

  const mismatchResult = await runtime.callTool("static_validate_project", {
    project: secondProject,
    projectRoot,
  });
  const mismatch = payloadOf(mismatchResult);
  assert.strictEqual(mismatchResult.isError, true);
  assert.strictEqual(mismatch.errorCode, "PROJECT_ROOT_MISMATCH");

  const missing = payloadOf(await runtime.callTool("read_file", {
    project: secondProject,
    path: "project://Config/Missing.ini",
  }));
  assert.strictEqual(missing.errorCode, "NOT_FOUND");
  assert.strictEqual(missing.suggestion.tool, "search_files");
  assert.strictEqual(missing.suggestion.args.project, secondProject);
});

test("run_command bounds captured output and invokes process-tree termination", async (t) => {
  const killed = [];
  function spawnCommand() {
    const child = new EventEmitter();
    child.pid = 424242;
    child.stdout = new PassThrough();
    child.stderr = new PassThrough();
    process.nextTick(() => {
      child.stdout.write(Buffer.alloc(20 * 1024, 0x78));
      setImmediate(() => child.emit("close", 0));
    });
    return child;
  }
  const { workspaceRoot, projectFile } = fixture(t);
  const runtime = createDirectRuntime({
    workspaceRoot,
    configPath: path.join(workspaceRoot, "agent-mcp.json"),
    env: {
      ALLOW_COMMANDS: "1",
      ALLOW_WRITE: "0",
      ALLOW_UNREAL_BUILD: "0",
      DIRECT_MAX_COMMAND_OUTPUT_BYTES: String(16 * 1024),
      DIRECT_MAX_RESPONSE_CHARS: "16000",
    },
    getActiveProject: () => projectFile,
    spawnCommand,
    killProcessTree: async (pid, platform) => killed.push({ pid, platform }),
  });
  const result = await runtime.callTool("run_command", { command: "node --version" });
  const payload = payloadOf(result);
  assert.strictEqual(result.isError, true);
  assert.strictEqual(payload.errorCode, "COMMAND_OUTPUT_LIMIT");
  assert.strictEqual(payload.outputLimited, true);
  assert.ok(payload.stdout.length <= 768);
  assert.deepStrictEqual(killed, [{ pid: 424242, platform: process.platform }]);
});
