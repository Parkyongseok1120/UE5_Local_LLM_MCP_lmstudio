"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const { createDirectRuntime } = require("../src/direct-server");
const {
  assertDirectMutationScope,
  classifyDirectMutationRelativePath,
} = require("../src/direct-mutation-scope");

function sha256(value) {
  return crypto.createHash("sha256").update(value).digest("hex");
}

function payloadOf(result) {
  assert.ok(result?.structuredContent);
  return result.structuredContent;
}

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "direct-mutation-scope-"));
  const projectRoot = path.join(root, "ScopeProject");
  const projectPath = path.join(projectRoot, "ScopeProject.uproject");
  const contents = new Map([
    ["ScopeProject.uproject", '{"FileVersion":3}\n'],
    ["Source/Allowed.cpp", "source=0\n"],
    ["Config/Allowed.ini", "config=0\n"],
    ["Plugins/P/Source/P/Allowed.cpp", "plugin=0\n"],
    ["Plugins/P/P.uplugin", '{"FileVersion":3,"Version":1}\n'],
    ["Plugins/P/Intermediate/Generated.txt", "intermediate=0\n"],
    ["Plugins/P/Binaries/Generated.txt", "binaries=0\n"],
    ["Content/Generated.txt", "content=0\n"],
    [".git/Generated.txt", "git=0\n"],
  ]);
  for (const [relativePath, content] of contents) {
    const target = path.join(projectRoot, ...relativePath.split("/"));
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, content, "utf8");
  }
  const stateRoot = path.join(root, "state");
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return {
    contents,
    projectPath,
    projectRoot,
    root,
    runtime: createDirectRuntime({
      workspaceRoot: root,
      stateRoot,
      configPath: path.join(root, "agent-mcp.json"),
      env: {
        AGENT_STATE_ROOT: stateRoot,
        ALLOW_SOURCE_DELETE: "1",
        ALLOW_WRITE: "1",
      },
      getActiveProject: () => projectPath,
      validateMutationSemanticText: () => ({ ok: true, hits: [] }),
    }),
  };
}

test("central mutation scope classifies only project/config/plugin source and exact descriptors", () => {
  const descriptor = "ScopeProject.uproject";
  const allowed = new Map([
    ["Source/Game/A.cpp", "project_source"],
    ["Config/DefaultGame.ini", "project_config"],
    ["Plugins/P/Source/P/Private/A.cpp", "plugin_source"],
    ["Plugins/P/P.uplugin", "plugin_descriptor"],
    [descriptor, "project_descriptor"],
  ]);
  for (const [relativePath, scope] of allowed) {
    assert.strictEqual(
      classifyDirectMutationRelativePath(relativePath, descriptor),
      scope,
      relativePath,
    );
  }
  for (const relativePath of [
    "Content/A.cpp",
    ".git/config",
    "Saved/Logs/Run.log",
    "Plugins/P/Intermediate/A.cpp",
    "Plugins/P/Binaries/A.dll",
    "Plugins/P/Source/Intermediate/A.cpp",
    "Plugins/P/Content/A.uasset",
    "Plugins/P/Other.uplugin",
    "Other.uproject",
  ]) {
    assert.strictEqual(
      classifyDirectMutationRelativePath(relativePath, descriptor),
      null,
      relativePath,
    );
  }
  assert.strictEqual(
    classifyDirectMutationRelativePath("NotAProject.txt", "NotAProject.txt"),
    null,
  );
  assert.throws(() => assertDirectMutationScope({
    absolutePath: "/project/Source/A.cpp",
    activeProject: "/project/ScopeProject.uproject",
    realPath: "/project/Content/A.cpp",
    relativePath: "Source/A.cpp",
    realRelativePath: "Content/A.cpp",
    hostPlatform: "linux",
  }), /not allowed/u);
});

test("replace and atomic bundle reject protected plugin, Content, and .git paths", async (t) => {
  const { contents, projectRoot, runtime } = fixture(t);
  const blocked = [
    "Plugins/P/Intermediate/Generated.txt",
    "Plugins/P/Binaries/Generated.txt",
    "Content/Generated.txt",
    ".git/Generated.txt",
  ];
  for (const relativePath of blocked) {
    const content = contents.get(relativePath);
    const replaced = payloadOf(await runtime.callTool("replace_in_file", {
      path: `project://${relativePath}`,
      oldText: "=0",
      newText: "=1",
      expectedOccurrences: 1,
      expectedHash: sha256(content),
    }));
    assert.strictEqual(replaced.ok, false, relativePath);
    assert.strictEqual(replaced.errorCode, "INVALID_ARGUMENT", relativePath);
    assert.strictEqual(fs.readFileSync(path.join(projectRoot, ...relativePath.split("/")), "utf8"), content);

    const sourceContent = fs.readFileSync(path.join(projectRoot, "Source", "Allowed.cpp"), "utf8");
    const bundled = payloadOf(await runtime.callTool("apply_edit_bundle", {
      patches: [
        {
          path: "Source/Allowed.cpp",
          oldText: "source=0",
          newText: "source=1",
          expectedOccurrences: 1,
          expectedHash: sha256(sourceContent),
        },
        {
          path: relativePath,
          oldText: "=0",
          newText: "=1",
          expectedOccurrences: 1,
          expectedHash: sha256(content),
        },
      ],
    }));
    assert.strictEqual(bundled.ok, false, relativePath);
    assert.strictEqual(bundled.errorCode, "BUNDLE_VALIDATION_FAILED", relativePath);
    assert.strictEqual(fs.readFileSync(path.join(projectRoot, "Source", "Allowed.cpp"), "utf8"), sourceContent);
    assert.strictEqual(fs.readFileSync(path.join(projectRoot, ...relativePath.split("/")), "utf8"), content);
  }

  const writeContent = payloadOf(await runtime.callTool("write_file", {
    path: "project://Content/New.cpp",
    content: "void MustNotExist() {}\n",
    createDirs: true,
  }));
  assert.strictEqual(writeContent.ok, false);
  assert.strictEqual(writeContent.errorCode, "INVALID_ARGUMENT");
  assert.strictEqual(fs.existsSync(path.join(projectRoot, "Content", "New.cpp")), false);

  const proposed = payloadOf(await runtime.callTool("propose_file_deletions", {
    completedEditsSummary: "No deletion should be proposed.",
    files: [{
      path: "project://Plugins/P/Intermediate/Generated.txt",
      reason: "blocked",
      ifNotDeleted: "unchanged",
      ifDeleted: "not applicable",
    }],
  }));
  assert.strictEqual(proposed.ok, false);
  assert.strictEqual(proposed.errorCode, "INVALID_ARGUMENT");
});

test("standalone write_file rejects an existing symlink or junction ancestor", async (t) => {
  const { projectRoot, root, runtime } = fixture(t);
  const outside = path.join(root, "OutsideWriteTarget");
  const linkedParent = path.join(projectRoot, "Source", "LinkedOutside");
  fs.mkdirSync(outside, { recursive: true });
  try {
    fs.symlinkSync(
      outside,
      linkedParent,
      process.platform === "win32" ? "junction" : "dir",
    );
  } catch (error) {
    if (["EACCES", "EPERM", "ENOTSUP"].includes(error.code)) {
      t.skip(`symlink/junction creation is unavailable: ${error.code}`);
      return;
    }
    throw error;
  }
  const result = payloadOf(await runtime.callTool("write_file", {
    path: "project://Source/LinkedOutside/Escape.cpp",
    content: "void MustStayInsideProject() {}\n",
    createDirs: true,
  }));
  assert.equal(result.ok, false);
  assert.equal(result.errorCode, "INVALID_ARGUMENT");
  assert.match(result.message, /symlink\/junction|outside the selected Unreal project/u);
  assert.equal(fs.existsSync(path.join(outside, "Escape.cpp")), false);
});

test("Source, Config, plugin Source/descriptors, and active descriptor remain writable", async (t) => {
  const { projectPath, projectRoot, runtime } = fixture(t);
  for (const [relativePath, oldText, newText] of [
    ["Source/Allowed.cpp", "source=0", "source=1"],
    ["Config/Allowed.ini", "config=0", "config=1"],
    ["Plugins/P/Source/P/Allowed.cpp", "plugin=0", "plugin=1"],
    ["Plugins/P/P.uplugin", '"Version":1', '"Version":2'],
    ["ScopeProject.uproject", '"FileVersion":3', '"FileVersion":4'],
  ]) {
    const target = path.join(projectRoot, ...relativePath.split("/"));
    const content = fs.readFileSync(target, "utf8");
    const replaced = payloadOf(await runtime.callTool("replace_in_file", {
      path: `project://${relativePath}`,
      oldText,
      newText,
      expectedOccurrences: 1,
      expectedHash: sha256(content),
    }));
    assert.strictEqual(replaced.ok, true, relativePath);
    assert.match(fs.readFileSync(target, "utf8"), new RegExp(newText.replace(/[.*+?^${}()|[\]\\]/gu, "\\$&"), "u"));
  }

  const created = payloadOf(await runtime.callTool("write_file", {
    path: "project://Plugins/P/Source/NewModule/Private/New.cpp",
    content: "void NewPluginSource() {}\n",
    createDirs: true,
  }));
  assert.strictEqual(created.ok, true);
  assert.strictEqual(fs.existsSync(path.join(projectRoot, "Plugins", "P", "Source", "NewModule", "Private", "New.cpp")), true);

  const pluginSource = path.join(projectRoot, "Plugins", "P", "Source", "P", "Allowed.cpp");
  const pluginDescriptor = path.join(projectRoot, "Plugins", "P", "P.uplugin");
  const sourceBeforeBundle = fs.readFileSync(pluginSource, "utf8");
  const descriptorBeforeBundle = fs.readFileSync(pluginDescriptor, "utf8");
  const bundle = payloadOf(await runtime.callTool("apply_edit_bundle", {
    patches: [
      {
        path: "Plugins/P/Source/P/Allowed.cpp",
        oldText: "plugin=1",
        newText: "plugin=2",
        expectedOccurrences: 1,
        expectedHash: sha256(sourceBeforeBundle),
      },
      {
        path: "Plugins/P/P.uplugin",
        oldText: '"Version":2',
        newText: '"Version":3',
        expectedOccurrences: 1,
        expectedHash: sha256(descriptorBeforeBundle),
      },
    ],
  }));
  assert.strictEqual(bundle.ok, true);
  assert.match(fs.readFileSync(pluginSource, "utf8"), /plugin=2/u);
  assert.match(fs.readFileSync(pluginDescriptor, "utf8"), /"Version":3/u);
  assert.match(fs.readFileSync(projectPath, "utf8"), /"FileVersion":4/u);
});

test("plugin Source deletion retains exact approval and hash gates", async (t) => {
  const { projectRoot, runtime } = fixture(t);
  const relativePath = "Plugins/P/Source/P/Allowed.cpp";
  const target = path.join(projectRoot, ...relativePath.split("/"));
  const content = fs.readFileSync(target, "utf8");
  const details = {
    completedEditsSummary: "Remove one obsolete plugin source file.",
    reason: "No longer compiled.",
    ifNotDeleted: "Duplicate implementation remains.",
    ifDeleted: "Plugin module uses the replacement implementation.",
  };
  const proposal = payloadOf(await runtime.callTool("propose_file_deletions", {
    completedEditsSummary: details.completedEditsSummary,
    files: [{
      path: `project://${relativePath}`,
      reason: details.reason,
      ifNotDeleted: details.ifNotDeleted,
      ifDeleted: details.ifDeleted,
    }],
  }));
  assert.strictEqual(proposal.ok, true);
  assert.strictEqual(fs.existsSync(target), true);

  const deleted = payloadOf(await runtime.callTool("delete_file", {
    path: `project://${relativePath}`,
    approvalToken: proposal.proposals[0].approvalToken,
    userApproved: true,
    expectedHash: sha256(content),
    ...details,
  }));
  assert.strictEqual(deleted.ok, true);
  assert.strictEqual(deleted.operation, "moved_to_trash");
  assert.strictEqual(fs.existsSync(target), false);
  assert.strictEqual(fs.readFileSync(deleted.restorePath, "utf8"), content);
});
