"use strict";

const assert = require("node:assert/strict");
const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const CLI = path.resolve(__dirname, "..", "src", "resolve-project-name-cli.js");

function createProject(parent, directoryName, projectName = directoryName) {
  const projectDir = path.join(parent, directoryName);
  fs.mkdirSync(path.join(projectDir, "Source"), { recursive: true });
  const projectPath = path.join(projectDir, `${projectName}.uproject`);
  fs.writeFileSync(
    projectPath,
    JSON.stringify({
      FileVersion: 3,
      EngineAssociation: "5.8",
      Modules: [{ Name: projectName, Type: "Runtime" }],
    }),
    "utf8",
  );
  return projectPath;
}

function createHarness() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "unreal cli exact "));
  const workspaceRoot = path.join(root, "workspace with spaces");
  const projectRoot = path.join(root, "프로젝트 roots");
  const configPath = path.join(root, "agent config.json");
  const sharedConfig = path.join(root, "shared config.json");
  const unrelatedCwd = path.join(root, "unrelated cwd", "nested");
  for (const directory of [workspaceRoot, projectRoot, unrelatedCwd]) {
    fs.mkdirSync(directory, { recursive: true });
  }
  fs.writeFileSync(
    configPath,
    JSON.stringify({ projectSearchRoots: [projectRoot] }),
    "utf8",
  );
  fs.writeFileSync(sharedConfig, "{}", "utf8");
  return {
    root,
    workspaceRoot,
    projectRoot,
    configPath,
    sharedConfig,
    unrelatedCwd,
    cleanup() {
      fs.rmSync(root, { recursive: true, force: true });
    },
  };
}

function invokeCli(harness, payload, inputOverride) {
  return spawnSync(process.execPath, [CLI], {
    cwd: harness.unrelatedCwd,
    env: {
      ...process.env,
      SHARED_UNREAL_CONFIG: harness.sharedConfig,
    },
    input: inputOverride === undefined ? JSON.stringify(payload) : inputOverride,
    encoding: "utf8",
    windowsHide: true,
  });
}

function parseSingleStdoutJson(result) {
  const lines = String(result.stdout || "").split(/\r?\n/).filter(Boolean);
  assert.equal(lines.length, 1, `expected one stdout JSON line, got: ${result.stdout}`);
  return JSON.parse(lines[0]);
}

test("resolver CLI reads stdin JSON and is independent of cwd and shell quoting", () => {
  const harness = createHarness();
  const projectPath = createProject(harness.projectRoot, "Quoted_Project");
  try {
    const result = invokeCli(harness, {
      workspaceRoot: harness.workspaceRoot,
      configPath: harness.configPath,
      target: "  QUOTED project.UPROJECT  ",
    });
    const payload = parseSingleStdoutJson(result);
    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stderr, "");
    assert.equal(payload.ok, true);
    assert.equal(payload.selected.projectPath, path.resolve(projectPath));
    assert.equal(payload.normalizedName, "quotedproject");
  } finally {
    harness.cleanup();
  }
});

test("resolver CLI anchors relative configured search roots to workspaceRoot", () => {
  const harness = createHarness();
  const projectPath = createProject(harness.projectRoot, "상대 Root Game");
  fs.writeFileSync(
    harness.configPath,
    JSON.stringify({
      projectSearchRoots: [path.relative(harness.workspaceRoot, harness.projectRoot)],
    }),
    "utf8",
  );
  try {
    const result = invokeCli(harness, {
      workspaceRoot: harness.workspaceRoot,
      configPath: harness.configPath,
      target: "상대-root-game",
    });
    const payload = parseSingleStdoutJson(result);
    assert.equal(result.status, 0, result.stderr);
    assert.equal(payload.selected.projectPath, path.resolve(projectPath));
    assert.ok(payload.searchRoots.includes(path.resolve(harness.projectRoot)));
    assert.ok(!payload.searchRoots.includes(path.resolve(
      harness.unrelatedCwd,
      path.relative(harness.workspaceRoot, harness.projectRoot),
    )));
  } finally {
    harness.cleanup();
  }
});

test("resolver CLI supports omitted configPath through its module-relative default", () => {
  const harness = createHarness();
  const projectPath = createProject(harness.projectRoot, "Default_Config_Game");
  fs.writeFileSync(
    harness.sharedConfig,
    JSON.stringify({ projectSearchRoots: [harness.projectRoot] }),
    "utf8",
  );
  try {
    const result = invokeCli(harness, {
      workspaceRoot: harness.workspaceRoot,
      target: "default-config-game",
    });
    const payload = parseSingleStdoutJson(result);
    assert.equal(result.status, 0, result.stderr);
    assert.equal(payload.selected.projectPath, path.resolve(projectPath));
  } finally {
    harness.cleanup();
  }
});

test("resolver CLI returns partial suggestions with a nonzero fail-closed exit", () => {
  const harness = createHarness();
  createProject(harness.projectRoot, "AlphaGame");
  createProject(harness.projectRoot, "AlphaTools");
  try {
    const result = invokeCli(harness, {
      workspaceRoot: harness.workspaceRoot,
      configPath: harness.configPath,
      target: "Alpha",
    });
    const payload = parseSingleStdoutJson(result);
    assert.equal(result.status, 1);
    assert.equal(payload.ok, false);
    assert.equal(payload.errorCode, "PROJECT_NAME_NOT_FOUND");
    assert.deepEqual(
      new Set(payload.suggestions.map((item) => item.projectName)),
      new Set(["AlphaGame", "AlphaTools"]),
    );
    assert.match(result.stderr, /^PROJECT_NAME_NOT_FOUND:/);
  } finally {
    harness.cleanup();
  }
});

test("resolver CLI preserves exact-name ambiguity across different project paths", () => {
  const harness = createHarness();
  createProject(harness.projectRoot, "OwnerOne", "Shared_Project");
  createProject(harness.projectRoot, "OwnerTwo", "Shared_Project");
  try {
    const result = invokeCli(harness, {
      workspaceRoot: harness.workspaceRoot,
      configPath: harness.configPath,
      target: "shared-project.uproject",
    });
    const payload = parseSingleStdoutJson(result);
    assert.equal(result.status, 1);
    assert.equal(payload.ok, false);
    assert.equal(payload.errorCode, "PROJECT_NAME_AMBIGUOUS");
    assert.equal(payload.suggestions.length, 2);
    assert.equal(new Set(payload.suggestions.map((item) => item.projectPath)).size, 2);
    assert.match(result.stderr, /^PROJECT_NAME_AMBIGUOUS:/);
  } finally {
    harness.cleanup();
  }
});

test("resolver CLI rejects cwd-relative paths and malformed JSON", () => {
  const harness = createHarness();
  try {
    const relative = invokeCli(harness, {
      workspaceRoot: "relative-workspace",
      configPath: harness.configPath,
      target: "AnyGame",
    });
    assert.equal(relative.status, 1);
    assert.equal(parseSingleStdoutJson(relative).errorCode, "INVALID_WORKSPACE_ROOT");

    const malformed = invokeCli(harness, {}, "{not-json");
    assert.equal(malformed.status, 1);
    assert.equal(parseSingleStdoutJson(malformed).errorCode, "INVALID_JSON");

    const wrongTypes = invokeCli(harness, {
      workspaceRoot: harness.workspaceRoot,
      configPath: null,
      target: { project: "AnyGame" },
    });
    assert.equal(wrongTypes.status, 1);
    assert.equal(parseSingleStdoutJson(wrongTypes).errorCode, "INVALID_TARGET");
  } finally {
    harness.cleanup();
  }
});

test("resolver CLI bounds stdin before parsing", () => {
  const harness = createHarness();
  try {
    const result = invokeCli(harness, {}, " ".repeat((64 * 1024) + 1));
    const payload = parseSingleStdoutJson(result);
    assert.equal(result.status, 1);
    assert.equal(payload.ok, false);
    assert.equal(payload.errorCode, "STDIN_TOO_LARGE");
    assert.match(result.stderr, /^STDIN_TOO_LARGE:/);
  } finally {
    harness.cleanup();
  }
});
