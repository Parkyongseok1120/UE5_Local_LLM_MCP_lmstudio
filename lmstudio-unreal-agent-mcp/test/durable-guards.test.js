"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const test = require("node:test");

const packageRoot = path.resolve(__dirname, "..");

function runIsolated(source, stateRoot) {
  const result = spawnSync(process.execPath, ["-e", source], {
    cwd: packageRoot,
    encoding: "utf8",
    env: { ...process.env, AGENT_STATE_ROOT: stateRoot },
  });
  assert.equal(result.status, 0, result.stderr || result.stdout);
  return String(result.stdout || "").trim();
}

test("workflow loop state survives a process restart and stays task isolated", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "workflow-guard-restart-"));
  const stateRoot = path.join(root, "state");
  const projectRoot = path.join(root, "Demo");
  const validation = JSON.stringify({
    findings: [{ severity: "error", code: "STATIC_ERROR", path: "Source/Foo.cpp", line: 7 }],
  });
  const common = `const g=require('./src/workflow-loop-guard');const p=${JSON.stringify(projectRoot)};`
    + `const v=${validation};const o={taskSessionId:'task-a',stateRoot:${JSON.stringify(stateRoot)}};`;
  try {
    assert.equal(runIsolated(`${common}process.stdout.write(JSON.stringify(g.recordValidationFailure(p,3,v,o)));`, stateRoot).includes('"blocked":false'), true);
    const restarted = JSON.parse(runIsolated(
      `${common}process.stdout.write(JSON.stringify(g.recordValidationFailure(p,3,v,o)));`,
      stateRoot
    ));
    assert.equal(restarted.blocked, true);
    assert.equal(restarted.reason, "same_validation_failure");

    const otherTask = JSON.parse(runIsolated(
      `const g=require('./src/workflow-loop-guard');const p=${JSON.stringify(projectRoot)};`
        + `const v=${validation};const o={taskSessionId:'task-b',stateRoot:${JSON.stringify(stateRoot)}};`
        + "process.stdout.write(JSON.stringify(g.recordValidationFailure(p,3,v,o)));",
      stateRoot
    ));
    assert.equal(otherTask.blocked, false);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("mutation history survives restart without crossing task boundaries", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "mutation-guard-restart-"));
  const stateRoot = path.join(root, "state");
  const projectRoot = path.join(root, "Demo");
  const scope = `{taskSessionId:'task-a',projectRoot:${JSON.stringify(projectRoot)},mutationGeneration:9,stateRoot:${JSON.stringify(stateRoot)}}`;
  try {
    runIsolated(
      `const g=require('./src/mutation-history');g.recordMutation('write_file',${JSON.stringify(path.join(projectRoot, "Source", "Foo.cpp"))},'body',${scope});`,
      stateRoot
    );
    const restarted = JSON.parse(runIsolated(
      `const g=require('./src/mutation-history');process.stdout.write(JSON.stringify(g.checkMutationDuplicate('write_file',${JSON.stringify(path.join(projectRoot, "Source", "Foo.cpp"))},'body',${scope})));`,
      stateRoot
    ));
    assert.equal(restarted.duplicate, true);
    assert.equal(restarted.consecutive, true);

    const isolated = JSON.parse(runIsolated(
      `const g=require('./src/mutation-history');process.stdout.write(JSON.stringify(g.checkMutationDuplicate('write_file',${JSON.stringify(path.join(projectRoot, "Source", "Foo.cpp"))},'body',{taskSessionId:'task-b',projectRoot:${JSON.stringify(projectRoot)},mutationGeneration:9,stateRoot:${JSON.stringify(stateRoot)}})));`,
      stateRoot
    ));
    assert.equal(isolated.duplicate, false);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("tool failure history survives restart and never persists authorization secrets", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "tool-guard-restart-"));
  const stateRoot = path.join(root, "state");
  const projectRoot = path.join(root, "Demo");
  const args = `{path:'Source/Foo.cpp',taskAuthorization:{taskSessionId:'task-a',authToken:'must-not-persist'}}`;
  const scope = `{taskSessionId:'task-a',projectRoot:${JSON.stringify(projectRoot)},mutationGeneration:4,stateRoot:${JSON.stringify(stateRoot)}}`;
  try {
    runIsolated(
      `const g=require('./src/tool-failure-history');g.beginToolCall(${scope});g.recordToolFailure('read_file',${args},'INTERNAL_ERROR',${scope});`,
      stateRoot
    );
    const restarted = JSON.parse(runIsolated(
      `const g=require('./src/tool-failure-history');const o=${scope};const a=${args};const s=g.beginToolCall(o);process.stdout.write(JSON.stringify(g.checkToolRepeatBlocked('read_file',a,s,o)));`,
      stateRoot
    ));
    assert.equal(restarted.blocked, true);
    assert.equal(restarted.consecutive, true);

    const files = fs.readdirSync(path.join(stateRoot, "tasks", "task-a", "guards"));
    const persisted = files.map((name) => fs.readFileSync(
      path.join(stateRoot, "tasks", "task-a", "guards", name),
      "utf8"
    )).join("\n");
    assert.equal(persisted.includes("must-not-persist"), false);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
