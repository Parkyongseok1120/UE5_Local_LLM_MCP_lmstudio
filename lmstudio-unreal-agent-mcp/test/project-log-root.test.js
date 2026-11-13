"use strict";

// Conversation 1794590083146: WORKSPACE_ROOT pointed at one game and the active
// project was another, outside it. The build logs of the second were written
// into the first one's repository (<WORKSPACE_ROOT>/.agent/logs).
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const { createDirectRuntime } = require("../src/direct-server.js");

function fixture(t, { outside }) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "project-log-root-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const workspaceRoot = path.join(root, "WorkspaceGame");
  const projectRoot = outside ? path.join(root, "OtherGame") : path.join(workspaceRoot, "OtherGame");
  fs.mkdirSync(path.join(projectRoot, "Source", "OtherGame"), { recursive: true });
  fs.mkdirSync(workspaceRoot, { recursive: true });
  const projectFile = path.join(projectRoot, "OtherGame.uproject");
  fs.writeFileSync(projectFile, JSON.stringify({ FileVersion: 3, Modules: [{ Name: "OtherGame" }] }), "utf8");
  const seen = {};
  const build = { projectPath: projectFile, projectName: "OtherGame", target: "OtherGameEditor",
    platform: "Win64", configuration: "Development", engineRoot: path.join(root, "Engine") };
  const runtime = createDirectRuntime({
    workspaceRoot, configPath: path.join(root, "agent-mcp.json"),
    env: { AGENT_STATE_ROOT: path.join(root, "state"), ALLOW_UNREAL_BUILD: "1" },
    getActiveProject: () => projectFile,
    resolveBuildPlan: async () => ({ ok: true, build, selectionReason: "test" }),
    runStaticValidation: async () => ({ ok: true, findings: [], findingCount: 0 }),
    runUnrealBuildFromPlan: async (options) => {
      seen.logPath = options.logPath;
      fs.mkdirSync(path.dirname(options.logPath), { recursive: true });
      fs.writeFileSync(options.logPath, "Source/OtherGame/A.cpp(2): error C2065: undeclared\n", "utf8");
      return { ok: false, exitCode: 6, processStarted: true, processExited: true, terminationStatus: "process_exited",
        stdout: "Source/OtherGame/A.cpp(2): error C2065: undeclared\n", stderr: "" };
    },
  });
  return { runtime, workspaceRoot, projectRoot, seen };
}

test("a project outside the workspace keeps its build log in its own Saved/Logs, where the log reader finds it", async (t) => {
  const f = fixture(t, { outside: true });
  const built = (await f.runtime.callTool("build_unreal_project", {})).structuredContent;
  assert.equal(built.errorCode, "BUILD_FAILED");
  assert.equal(path.dirname(built.fullLogPath), path.join(f.projectRoot, "Saved", "Logs"));
  assert.equal(built.fullLogPath, f.seen.logPath);
  assert.equal(fs.existsSync(path.join(f.workspaceRoot, ".agent")), false, "nothing is written into the workspace repository");
  const log = (await f.runtime.callTool("read_unreal_logs", { fileName: path.basename(built.fullLogPath), mode: "tail" })).structuredContent;
  assert.equal(log.ok, true, JSON.stringify(log));
  assert.match(JSON.stringify(log), /error C2065/u);
});

test("a project inside the workspace keeps the workspace log folder", async (t) => {
  const f = fixture(t, { outside: false });
  const built = (await f.runtime.callTool("build_unreal_project", {})).structuredContent;
  assert.equal(path.dirname(built.fullLogPath), path.join(f.workspaceRoot, ".agent", "logs"));
});
