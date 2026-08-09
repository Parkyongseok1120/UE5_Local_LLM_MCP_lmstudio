"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const test = require("node:test");
const {
  automationArgs,
  discoverAutomationTests,
  parseAutomationOutput,
  resolveEditorCmdPaths,
} = require("../src/automation-executor");

test("UnrealEditor-Cmd path is host specific", () => {
  const root = path.resolve("UE_5.8");
  assert.ok(resolveEditorCmdPaths(root, "win32")[0].endsWith(path.join("Win64", "UnrealEditor-Cmd.exe")));
  assert.ok(resolveEditorCmdPaths(root, "darwin")[0].endsWith(path.join("Mac", "UnrealEditor-Cmd")));
  assert.ok(resolveEditorCmdPaths(root, "linux")[0].endsWith(path.join("Linux", "UnrealEditor-Cmd")));
});

test("project Automation declarations produce a natural shared filter", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "automation-discovery-"));
  try {
    const source = path.join(root, "Source", "Demo", "Private");
    fs.mkdirSync(source, { recursive: true });
    fs.writeFileSync(
      path.join(source, "DemoTests.cpp"),
      [
        'IMPLEMENT_SIMPLE_AUTOMATION_TEST(FOne, TEXT("Gomoku.Stage3.Rule"), Flags)',
        'IMPLEMENT_COMPLEX_AUTOMATION_TEST(FTwo, "Gomoku.Stage4.Items", Flags)',
        'IMPLEMENT_SIMPLE_AUTOMATION_TEST(\n  FThree,\n  TEXT( "Gomoku.Stage5.LongMatch" ),\n  Flags)',
      ].join("\n"),
      "utf8"
    );
    const result = discoverAutomationTests(root);
    assert.strictEqual(result.count, 3);
    assert.strictEqual(result.suggestedFilter, "Gomoku");
    assert.deepStrictEqual(result.names, [
      "Gomoku.Stage3.Rule",
      "Gomoku.Stage4.Items",
      "Gomoku.Stage5.LongMatch",
    ]);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("automation command uses one spawn argument per Unreal option", () => {
  const args = automationArgs("Demo.uproject", "Gomoku");
  assert.ok(args.includes("-ExecCmds=Automation RunTests Gomoku;Quit"));
  assert.ok(args.includes("-TestExit=Automation Test Queue Empty"));
  assert.ok(args.includes("-NullRHI"));
});

test("automation parser fails closed on zero tests and test failures", () => {
  assert.strictEqual(
    parseAutomationOutput("Automation Test Queue Empty", 0).errorCode,
    "NO_AUTOMATION_TESTS_EXECUTED"
  );
  assert.strictEqual(
    parseAutomationOutput("Automation Test Failed (Gomoku.Stage3.Rule)", 0).errorCode,
    "AUTOMATION_TEST_FAILED"
  );
  const passed = parseAutomationOutput(
    "Automation Test Succeeded (Gomoku.Stage3.Rule)\nAutomation Test Queue Empty",
    0
  );
  assert.strictEqual(passed.ok, true);
  assert.strictEqual(passed.succeededCount, 1);
});

test("automation parser accepts UE 5.8 AutomationController completion records", () => {
  const output = [
    "LogAutomationCommandLine: Display: Found 2 automation tests based on 'Gomoku'",
    "LogAutomationController: Display: Test Completed. Result={Success} Name={One} Path={Gomoku.One}",
    "LogAutomationController: Display: Test Completed. Result={Success} Name={Two} Path={Gomoku.Two}",
    "LogAutomationCommandLine: Display: **** TEST COMPLETE. EXIT CODE: 0 ****",
  ].join("\n");
  const parsed = parseAutomationOutput(output, 0);
  assert.strictEqual(parsed.ok, true);
  assert.strictEqual(parsed.succeededCount, 2);
  assert.strictEqual(parsed.failedCount, 0);
  assert.strictEqual(parsed.queueEmpty, true);
});

test("automation parser fails closed on non-success UE completion records", () => {
  const output = [
    "LogAutomationController: Display: Test Completed. Result={Success} Name={One} Path={Gomoku.One}",
    "LogAutomationController: Display: Test Completed. Result={Fail} Name={Two} Path={Gomoku.Two}",
    "LogAutomationCommandLine: Display: **** TEST COMPLETE. EXIT CODE: 1 ****",
  ].join("\n");
  const parsed = parseAutomationOutput(output, 0);
  assert.strictEqual(parsed.ok, false);
  assert.strictEqual(parsed.errorCode, "AUTOMATION_TEST_FAILED");
  assert.strictEqual(parsed.succeededCount, 1);
  assert.strictEqual(parsed.failedCount, 1);
});
