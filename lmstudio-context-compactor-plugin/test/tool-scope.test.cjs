"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const {
  bindProjectArguments,
  detectMentionedProject,
  detectWorkspaceProject,
  filterToolsForScope,
  resolveToolScope,
  toolEngine,
} = require("../dist/tool-scope.js");
const { Chat } = require("@lmstudio/sdk");

const unityTool = { name: "read_file", description: "Unity read", pluginIdentifier: "mcp/unity-tools",
  parametersJsonSchema: { type: "object", properties: { path: { type: "string" } } } };
const unrealTool = { name: "read_file", description: "Unreal read", pluginIdentifier: "mcp/unreal-agent",
  parametersJsonSchema: { type: "object", properties: { path: { type: "string" }, project: { type: "string" } } } };
const ragTool = { name: "search_guidance", description: "Unreal RAG", pluginIdentifier: "mcp/unreal-rag",
  parametersJsonSchema: { type: "object", properties: { query: { type: "string" }, project: { type: "string" } } } };
const commonTool = { name: "common", description: "Common", pluginIdentifier: "mcp/other",
  parametersJsonSchema: { type: "object" } };

test("workspace markers select the nearest exact Unity project", (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "tool-scope-unity-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.mkdirSync(path.join(root, "Assets"));
  fs.mkdirSync(path.join(root, "Packages"));
  fs.mkdirSync(path.join(root, "ProjectSettings"));
  fs.writeFileSync(path.join(root, "Packages", "manifest.json"), "{}");
  fs.writeFileSync(path.join(root, "ProjectSettings", "ProjectVersion.txt"), "m_EditorVersion: 6");
  const nested = path.join(root, "Assets", "Scripts");
  fs.mkdirSync(nested, { recursive: true });

  const detected = detectWorkspaceProject(nested);
  const scope = resolveToolScope("auto", "", nested, [unityTool, unrealTool, commonTool]);

  assert.equal(detected.engine, "unity");
  assert.equal(detected.projectIdentity, path.resolve(root));
  assert.equal(scope.engine, "unity");
  assert.equal(scope.source, "workspace");
  assert.deepEqual(filterToolsForScope([unityTool, unrealTool, commonTool], scope), [unityTool, commonTool]);
});

test("ambiguous auto scope withholds both engine families", () => {
  const scope = resolveToolScope("auto", "", "", [unityTool, unrealTool, ragTool, commonTool]);
  assert.equal(scope.engine, "unknown");
  assert.equal(scope.source, "ambiguous");
  assert.deepEqual(filterToolsForScope([unityTool, unrealTool, ragTool, commonTool], scope), [commonTool]);
});

test("a verified Unity path in the latest user messages selects Unity deterministically", (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "tool-scope-mentioned-unity-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.mkdirSync(path.join(root, "Assets"));
  fs.mkdirSync(path.join(root, "Packages"));
  fs.mkdirSync(path.join(root, "ProjectSettings"));
  fs.writeFileSync(path.join(root, "Packages", "manifest.json"), "{}");
  fs.writeFileSync(path.join(root, "ProjectSettings", "ProjectVersion.txt"), "m_EditorVersion: 6");
  const history = Chat.from([{ role: "user", content: `${root} 이거로` }]);

  const mentioned = detectMentionedProject(history.getMessagesArray());
  const scope = resolveToolScope("auto", "", "", [unityTool, unrealTool], mentioned);

  assert.equal(mentioned.engine, "unity");
  assert.equal(mentioned.projectIdentity, path.resolve(root));
  assert.equal(scope.engine, "unity");
  assert.equal(scope.source, "message");
  assert.deepEqual(filterToolsForScope([unityTool, unrealTool], scope), [unityTool]);
});

test("a verified Unreal directory in the latest user messages resolves its sole descriptor", (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "tool-scope-mentioned-unreal-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const descriptor = path.join(root, "JRPG.uproject");
  fs.writeFileSync(descriptor, "{}");
  const history = Chat.from([
    { role: "user", content: "Use the previous project." },
    { role: "user", content: `\`${root}\`` },
  ]);

  const mentioned = detectMentionedProject(history.getMessagesArray());
  const scope = resolveToolScope("auto", "", "", [unityTool, unrealTool], mentioned);

  assert.equal(mentioned.engine, "unreal");
  assert.equal(mentioned.projectIdentity, path.resolve(descriptor));
  assert.equal(scope.engine, "unreal");
  assert.equal(scope.source, "message");
  assert.deepEqual(filterToolsForScope([unityTool, unrealTool], scope), [unrealTool]);
});

test("an exact configured project path is enough for Auto scope", (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "tool-scope-configured-unreal-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const descriptor = path.join(root, "Configured.uproject");
  fs.writeFileSync(descriptor, "{}");

  const scope = resolveToolScope("auto", descriptor, "", [unityTool, unrealTool]);

  assert.equal(scope.engine, "unreal");
  assert.equal(scope.source, "config");
  assert.equal(scope.projectIdentity, path.resolve(descriptor));
});

test("explicit mixed scope is the only mode that exposes both engine families", () => {
  const scope = resolveToolScope("mixed", "", "", [unityTool, unrealTool, ragTool, commonTool]);
  assert.deepEqual(filterToolsForScope([unityTool, unrealTool, ragTool, commonTool], scope),
    [unityTool, unrealTool, ragTool, commonTool]);
});

test("Unreal project-capable calls are bound while Unity and common calls remain untouched", () => {
  const project = "C:\\Projects\\Game\\Game.uproject";
  const request = { id: "1", type: "function", name: "read_file",
    arguments: { path: "project://Source/A.cpp", project: "Other" } };
  assert.equal(toolEngine(unityTool), "unity");
  assert.equal(toolEngine(unrealTool), "unreal");
  assert.equal(toolEngine(commonTool), "common");
  assert.deepEqual(bindProjectArguments(unrealTool, request, project), {
    path: "project://Source/A.cpp",
    project,
  });
  assert.equal(bindProjectArguments(unityTool, request, project), null);
  assert.equal(bindProjectArguments(commonTool, request, project), null);
});
