"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const test = require("node:test");

const {
  displayPath,
  resolveReadPath,
  stripProjectNamePrefix,
} = require("../src/read-path-resolver");

test("project-prefix identity folds ASCII only on Windows", () => {
  const projectDir = path.join("Projects", "MyGame");
  assert.strictEqual(
    stripProjectNamePrefix("Projects/mygame/Source/MyGame/Foo.cpp", projectDir, "win32"),
    "Source/MyGame/Foo.cpp",
  );
  assert.strictEqual(
    stripProjectNamePrefix("Projects/mygame/Source/MyGame/Foo.cpp", projectDir, "linux"),
    "Projects/mygame/Source/MyGame/Foo.cpp",
  );

  const idotProject = path.join("Projects", "\u0130Game");
  const lookalike = "Projects/i\u0307game/Source/Demo/Foo.cpp";
  assert.strictEqual(stripProjectNamePrefix(lookalike, idotProject, "win32"), lookalike);
});

test("workspace-prefixed active-project paths normalize to project:// paths", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "unreal-path-resolver-"));
  const workspaceRoot = path.join(root, "AgentWorkspace");
  const projectDir = path.join(root, "Git", "MyGame");
  const sourceFile = path.join(projectDir, "Source", "MyGame", "Foo.cpp");
  const activeProject = path.join(projectDir, "MyGame.uproject");

  fs.mkdirSync(path.dirname(sourceFile), { recursive: true });
  fs.mkdirSync(workspaceRoot, { recursive: true });
  fs.writeFileSync(sourceFile, "void Foo() {}\n", "utf8");
  fs.writeFileSync(activeProject, "{}", "utf8");

  try {
    const resolution = await resolveReadPath(
      "Git/MyGame/Source/MyGame/Foo.cpp",
      { workspaceRoot, activeProject }
    );

    assert.strictEqual(resolution.resolvedRootType, "active_project");
    assert.strictEqual(resolution.projectRelativePath, "Source/MyGame/Foo.cpp");
    assert.strictEqual(displayPath(resolution), "project://Source/MyGame/Foo.cpp");
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
