"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const { createDirectRuntime } = require("../src/direct-server.js");
const { BuildChangeLedger } = require("../src/direct-build-change-ledger.js");
const { automationTestSuggestion, isDefiniteStaticError } = require("../src/direct-build-precheck.js");

function payloadOf(result) {
  assert.deepStrictEqual(JSON.parse(result.content[0].text), result.structuredContent);
  return result.structuredContent;
}

const SOURCE = "Source/BuildFixture/BuildFixture.cpp";

// A project with one Source file, one Config file and scripted static/UBT
// stand-ins: no Python validator and no Unreal build ever run.
function fixture(t, { validation, buildOk = true, env = {} } = {}) {
  const workspaceRoot = fs.mkdtempSync(path.join(os.tmpdir(), "unreal-build-precheck-"));
  const projectRoot = path.join(workspaceRoot, "BuildFixture");
  const projectFile = path.join(projectRoot, "BuildFixture.uproject");
  fs.mkdirSync(path.join(projectRoot, "Source", "BuildFixture"), { recursive: true });
  fs.mkdirSync(path.join(projectRoot, "Config"), { recursive: true });
  fs.writeFileSync(projectFile, JSON.stringify({ FileVersion: 3, Modules: [{ Name: "BuildFixture" }] }), "utf8");
  fs.writeFileSync(path.join(projectRoot, SOURCE), "#include \"BuildFixture.h\"\nvoid Run() {}\n", "utf8");
  fs.writeFileSync(path.join(projectRoot, "Config", "Game.ini"), "[Game]\nValue=1\n", "utf8");
  t.after(() => fs.rmSync(workspaceRoot, { recursive: true, force: true }));

  const calls = { validation: [], build: 0 };
  const build = { projectPath: projectFile, projectName: "BuildFixture", target: "BuildFixtureEditor",
    platform: "Win64", configuration: "Development", engineRoot: path.join(workspaceRoot, "Engine") };
  const runtime = createDirectRuntime({
    workspaceRoot,
    configPath: path.join(workspaceRoot, "agent-mcp.json"),
    env: { AGENT_STATE_ROOT: path.join(workspaceRoot, "state"), ALLOW_WRITE: "1", ALLOW_UNREAL_BUILD: "1", ...env },
    getActiveProject: () => projectFile,
    resolveBuildPlan: async () => ({ ok: true, build, selectionReason: "test" }),
    runStaticValidation: async (root, options) => {
      calls.validation.push({ root, scopeTargets: options.scopeTargets });
      return typeof validation === "function" ? validation(options) : validation || { ok: true, findings: [], findingCount: 0 };
    },
    runUnrealBuildFromPlan: async () => {
      calls.build += 1;
      const lifecycle = { processStarted: true, processExited: true, terminationStatus: "process_exited" };
      return { ...lifecycle, ...(buildOk
        ? { ok: true, exitCode: 0, stdout: "[1/1] Compile BuildFixture.cpp\nTotal time in Parallel executor: 1 seconds\n", stderr: "" }
        : { ok: false, exitCode: 6, stdout: "Source/BuildFixture/BuildFixture.cpp(2): error C2065: undeclared\n", stderr: "" }) };
    },
  });
  return { runtime, projectRoot, projectFile, calls };
}

async function editSource(runtime, newText = "void Run() { int Value = 1; }") {
  const read = payloadOf(await runtime.callTool("read_file_range", { path: `project://${SOURCE}`, startLine: 2, endLine: 2 }));
  const edited = payloadOf(await runtime.callTool("replace_in_file", {
    path: `project://${SOURCE}`, startLine: 2, endLine: 2, expectedRangeSha256: read.rangeSha256,
    newText, fileVersionReceipt: read.fileVersionReceipt,
  }));
  assert.equal(edited.ok, true, JSON.stringify(edited));
  return edited;
}

const includeError = { ok: false, findingCount: 2, elapsedMs: 40, analysis: { analysisCoverage: { status: "complete" } },
  findings: [
    { severity: "warning", code: "TIMER_SET_WITHOUT_CLEAR", path: SOURCE, line: 2, message: "timer", blocking: false },
    { severity: "error", code: "INCLUDE_PATH_NOT_FOUND", path: SOURCE, line: 1, message: "BuildFixture.h not found", blocking: true },
  ] };

test("definite static errors in changed Source files skip UBT with the findings", async (t) => {
  const f = fixture(t, { validation: includeError });
  await editSource(f.runtime);
  const result = await f.runtime.callTool("build_unreal_project", {});
  const payload = payloadOf(result);
  assert.equal(result.isError, true);
  assert.equal(payload.ok, false);
  assert.equal(payload.errorCode, "BUILD_SKIPPED_STATIC_ERRORS");
  assert.equal(payload.buildSkipped, "static_errors");
  assert.equal(f.calls.build, 0, "UBT must not start");
  assert.deepEqual(f.calls.validation.map((call) => call.scopeTargets), [[SOURCE]]);
  assert.equal(payload.staticPrecheck.status, "definite_errors");
  assert.equal(payload.staticPrecheck.definiteErrorCount, 1);
  assert.deepEqual(payload.staticPrecheck.scopeTargets, [SOURCE]);
  assert.equal(payload.findings[0].code, "INCLUDE_PATH_NOT_FOUND", "definite errors are listed first");
  assert.equal(payload.findings[0].definite, true);
  assert.match(payload.message, /force=true/);
  assert.deepEqual(payload.retry, { allowed: true, mode: "after_state_change" });
});

test("force=true runs UBT despite definite static errors and reports the precheck", async (t) => {
  const f = fixture(t, { validation: includeError });
  await editSource(f.runtime);
  const payload = payloadOf(await f.runtime.callTool("build_unreal_project", { force: true }));
  assert.equal(payload.ok, true, JSON.stringify(payload));
  assert.equal(f.calls.build, 1);
  assert.equal(payload.staticPrecheck.status, "definite_errors");
  assert.equal(payload.staticPrecheck.forced, true);
  assert.equal(payload.buildSkipped, undefined);
});

test("heuristic static errors stay advisory and never skip the build", async (t) => {
  const f = fixture(t, { validation: { ok: false, findingCount: 1, findings: [
    { severity: "error", code: "CPP_FUNCTION_SIGNATURE_MISMATCH", path: SOURCE, line: 2, message: "maybe", blocking: true },
  ] } });
  await editSource(f.runtime);
  const payload = payloadOf(await f.runtime.callTool("build_unreal_project", {}));
  assert.equal(payload.ok, true);
  assert.equal(f.calls.build, 1);
  assert.equal(payload.staticPrecheck.status, "advisory_errors");
  assert.equal(payload.staticPrecheck.definiteErrorCount, 0);
  assert.equal(payload.staticPrecheck.findings[0].definite, false);
});

test("an unavailable validator never skips the build", async (t) => {
  const f = fixture(t, { validation: { ok: false, reason: "validator script missing: x", findingCount: 1,
    findings: [{ severity: "error", code: "VALIDATOR_MISSING", path: "", line: 0, message: "missing" }] } });
  await editSource(f.runtime);
  const payload = payloadOf(await f.runtime.callTool("build_unreal_project", {}));
  assert.equal(payload.ok, true);
  assert.equal(f.calls.build, 1);
  assert.equal(payload.staticPrecheck.status, "unavailable");
  assert.match(payload.staticPrecheck.reason, /validator script missing/);
});

test("the precheck covers only Source changed since the last successful build", async (t) => {
  const f = fixture(t);
  const runtime = f.runtime;
  // No recorded Source change: nothing to scope, so the validator is not run.
  const first = payloadOf(await runtime.callTool("build_unreal_project", {}));
  assert.equal(first.staticPrecheck.status, "skipped");
  assert.equal(first.staticPrecheck.reason, "no_recorded_source_changes");
  assert.equal(f.calls.validation.length, 0);

  // Config edits are not Source and are never prechecked.
  const config = payloadOf(await runtime.callTool("read_file", { path: "project://Config/Game.ini" }));
  payloadOf(await runtime.callTool("replace_in_file", { path: "project://Config/Game.ini", oldText: "Value=1",
    newText: "Value=2", expectedOccurrences: 1, expectedHash: config.sha256 }));
  await editSource(runtime);
  const clean = payloadOf(await runtime.callTool("build_unreal_project", {}));
  assert.equal(clean.staticPrecheck.status, "clean");
  assert.deepEqual(f.calls.validation.at(-1).scopeTargets, [SOURCE]);

  // The successful build settled the change; the next build has nothing pending.
  const after = payloadOf(await runtime.callTool("build_unreal_project", {}));
  assert.equal(after.staticPrecheck.status, "skipped");
  assert.equal(f.calls.validation.length, 1);
});

test("a failed build keeps its changes pending for the next precheck", async (t) => {
  const f = fixture(t, { buildOk: false });
  await editSource(f.runtime);
  const failed = payloadOf(await f.runtime.callTool("build_unreal_project", {}));
  assert.equal(failed.ok, false);
  assert.equal(failed.staticPrecheck.status, "clean");
  assert.ok(JSON.stringify(failed).length <= 3_584 + 512, "failure stays near the long-running failure budget");
  await f.runtime.callTool("build_unreal_project", {});
  assert.deepEqual(f.calls.validation.map((call) => call.scopeTargets), [[SOURCE], [SOURCE]]);
});

test("BUILD_STATIC_PRECHECK=0 disables the precheck", async (t) => {
  const f = fixture(t, { validation: includeError, env: { BUILD_STATIC_PRECHECK: "0" } });
  await editSource(f.runtime);
  const payload = payloadOf(await f.runtime.callTool("build_unreal_project", {}));
  assert.equal(payload.ok, true);
  assert.equal(payload.staticPrecheck.status, "disabled");
  assert.equal(f.calls.validation.length, 0);
});

test("a successful build suggests the project Automation tests of the changed modules", async (t) => {
  const f = fixture(t);
  const testsDir = path.join(f.projectRoot, "Source", "BuildFixture", "Tests");
  fs.mkdirSync(testsDir, { recursive: true });
  fs.writeFileSync(path.join(testsDir, "CombatTests.cpp"), [
    'IMPLEMENT_SIMPLE_AUTOMATION_TEST(FHit, "BuildFixture.Combat.Hit", Flags)',
    'IMPLEMENT_SIMPLE_AUTOMATION_TEST(FBlock, "BuildFixture.Combat.Block", Flags)',
  ].join("\n"), "utf8");
  await editSource(f.runtime);
  const payload = payloadOf(await f.runtime.callTool("build_unreal_project", {}));
  assert.equal(payload.ok, true);
  assert.equal(payload.suggestion.tool, "run_unreal_automation_tests");
  assert.equal(payload.suggestion.args.testFilter, "BuildFixture.Combat");
  assert.deepEqual(payload.suggestion.args.scopeTargets, [SOURCE]);
  assert.deepEqual(payload.relatedAutomationTests,
    { advisory: true, count: 2, matchedByFilter: 2, names: ["BuildFixture.Combat.Block", "BuildFixture.Combat.Hit"] });

  // No change, no test suggestion.
  const again = payloadOf(await f.runtime.callTool("build_unreal_project", {}));
  assert.equal(again.suggestion, undefined);
});

test("no related Automation tests means no suggestion", async (t) => {
  const f = fixture(t);
  await editSource(f.runtime);
  const payload = payloadOf(await f.runtime.callTool("build_unreal_project", {}));
  assert.equal(payload.ok, true);
  assert.equal(payload.suggestion, undefined);
});

test("automation suggestion picks the root covering most tests when no prefix is shared", () => {
  const discover = () => ({ count: 3, truncated: false, names: ["A.One", "B.Two", "B.Three"] });
  const { suggestion, relatedAutomationTests } = automationTestSuggestion("/p", ["Source/M/X.cpp"], "Proj", discover);
  assert.equal(suggestion.args.testFilter, "B");
  assert.equal(suggestion.args.project, "Proj");
  assert.equal(relatedAutomationTests.matchedByFilter, 2);
  assert.equal(automationTestSuggestion("/p", ["Source/M/X.cpp"], "", () => ({ count: 2, truncated: true, names: ["A", "B"] })), undefined);
});

test("definite static errors are a fixed structural set", () => {
  for (const code of ["INCLUDE_PATH_NOT_FOUND", "GENERATED_H_MISSING", "GENERATED_H_NOT_LAST", "UHT_MACRO_IN_CONDITIONAL_BLOCK"]) {
    assert.equal(isDefiniteStaticError({ severity: "error", code, blocking: true }), true, code);
  }
  assert.equal(isDefiniteStaticError({ severity: "warning", code: "INCLUDE_PATH_NOT_FOUND" }), false);
  assert.equal(isDefiniteStaticError({ severity: "error", code: "INCLUDE_PATH_NOT_FOUND", blocking: false }), false,
    "a pre-existing error outside the changed files does not skip the build");
  assert.equal(isDefiniteStaticError({ severity: "error", code: "CPP_FUNCTION_SIGNATURE_MISMATCH" }), false);
});

test("change ledger keeps edits made during a build pending and ignores non-Source paths", () => {
  const ledger = new BuildChangeLedger();
  const project = path.join(os.tmpdir(), "Ledger", "Ledger.uproject");
  ledger.record(project, ["Source/Ledger/A.cpp", "Config/Game.ini", "project://Plugins/P/Source/PM/B.h"]);
  const started = ledger.pending(project);
  assert.deepEqual(started.paths, ["Source/Ledger/A.cpp", "Plugins/P/Source/PM/B.h"]);
  ledger.record(project, ["Source/Ledger/C.cpp"]);
  ledger.settle(project, started.sequence);
  assert.deepEqual(ledger.pending(project).paths, ["Source/Ledger/C.cpp"]);
  const small = new BuildChangeLedger({ maxPaths: 2 });
  small.record(project, ["Source/L/1.cpp", "Source/L/2.cpp", "Source/L/3.cpp"]);
  assert.deepEqual(small.pending(project), { paths: ["Source/L/2.cpp", "Source/L/3.cpp"], sequence: 3, overflowed: true });
  const cutoff = small.pending(project).sequence;
  small.record(project, ["Source/L/4.cpp", "Source/L/5.cpp", "Source/L/6.cpp"]);
  small.settle(project, cutoff);
  assert.equal(small.pending(project).overflowed, true);
  small.settle(project, small.pending(project).sequence);
  assert.deepEqual(small.pending(project).paths, []);
  assert.equal(small.pending(project).overflowed, false);
});
