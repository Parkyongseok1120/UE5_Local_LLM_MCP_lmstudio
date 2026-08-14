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
  resolveEditorCmd,
  resolveEditorCmdPaths,
  runAutomationTests,
  validateAutomationFilter,
} = require("../src/automation-executor");

test("UnrealEditor-Cmd path is host specific", () => {
  const root = path.resolve("UE_5.8");
  assert.deepStrictEqual(resolveEditorCmdPaths(root, "win32"), [
    path.join(root, "Engine", "Binaries", "Win64", "UnrealEditor-Cmd.exe"),
  ]);
  assert.deepStrictEqual(resolveEditorCmdPaths(root, "darwin"), [
    path.join(root, "Engine", "Binaries", "Mac", "UnrealEditor-Cmd"),
    path.join(root, "Engine", "Binaries", "Mac", "UnrealEditor.app", "Contents", "MacOS", "UnrealEditor"),
  ]);
  assert.deepStrictEqual(resolveEditorCmdPaths(root, "linux"), [
    path.join(root, "Engine", "Binaries", "Linux", "UnrealEditor-Cmd"),
    path.join(root, "Engine", "Binaries", "Linux", "UnrealEditor"),
  ]);
});

for (const fixture of [
  { host: "win32", selectedIndex: 0 },
  { host: "darwin", selectedIndex: 1 },
  { host: "linux", selectedIndex: 1 },
]) {
  test(`Unreal editor resolution uses the supported ${fixture.host} candidate order`, () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), `automation-editor-${fixture.host}-`));
    try {
      const candidates = resolveEditorCmdPaths(root, fixture.host);
      const selected = candidates[fixture.selectedIndex];
      if (fixture.selectedIndex > 0) {
        fs.mkdirSync(candidates[0], { recursive: true });
      }
      fs.mkdirSync(path.dirname(selected), { recursive: true });
      fs.writeFileSync(selected, "", "utf8");
      assert.strictEqual(resolveEditorCmd(root, fixture.host), selected);
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });
}

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
    assert.deepStrictEqual(result.suggestedFilters, ["Gomoku"]);
    assert.deepStrictEqual(result.names, [
      "Gomoku.Stage3.Rule",
      "Gomoku.Stage4.Items",
      "Gomoku.Stage5.LongMatch",
    ]);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("Automation discovery maps generic source and plugin scope targets to module roots", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "automation-scoped-discovery-"));
  try {
    const declarations = [
      ["Source/Alpha/Private/AlphaTests.cpp", 'IMPLEMENT_SIMPLE_AUTOMATION_TEST(FAlpha, "Alpha.Runtime.Rule", Flags)'],
      ["Source/Beta/Private/BetaTests.cpp", 'IMPLEMENT_SIMPLE_AUTOMATION_TEST(FBeta, "Beta.Runtime.Rule", Flags)'],
      ["Plugins/BoardTools/Source/BoardRuntime/Private/BoardTests.cpp", 'BEGIN_DEFINE_SPEC(FBoard, "Board.Runtime.Rule", Flags)'],
    ];
    for (const [relative, declaration] of declarations) {
      const target = path.join(root, ...relative.split("/"));
      fs.mkdirSync(path.dirname(target), { recursive: true });
      fs.writeFileSync(target, declaration, "utf8");
    }

    const scoped = discoverAutomationTests(root, {
      scopeTargets: [
        "project://Source/Alpha/Private/AlphaActor.cpp",
        "Plugins/BoardTools/Source/BoardRuntime/Public/BoardActor.h",
      ],
    });
    assert.strictEqual(scoped.scopeBound, true);
    assert.deepStrictEqual(scoped.scopeRoots, [
      "Plugins/BoardTools/Source/BoardRuntime",
      "Source/Alpha",
    ]);
    assert.deepStrictEqual(scoped.names, ["Alpha.Runtime.Rule", "Board.Runtime.Rule"]);
    assert.deepStrictEqual(scoped.suggestedFilters, [
      "Alpha.Runtime.Rule",
      "Board.Runtime.Rule",
    ]);
    assert.strictEqual(scoped.suggestedFilter, "");

    const unbound = discoverAutomationTests(root);
    assert.strictEqual(unbound.scopeBound, false);
    assert.deepStrictEqual(unbound.names, [
      "Alpha.Runtime.Rule",
      "Beta.Runtime.Rule",
      "Board.Runtime.Rule",
    ]);
    assert.deepStrictEqual(unbound.suggestedFilters, ["Alpha", "Beta", "Board"]);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("supplied non-module scope targets fail closed instead of widening to a full audit", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "automation-unmapped-scope-"));
  try {
    const source = path.join(root, "Source", "Alpha", "Private");
    fs.mkdirSync(source, { recursive: true });
    fs.writeFileSync(
      path.join(source, "AlphaTests.cpp"),
      'IMPLEMENT_SIMPLE_AUTOMATION_TEST(FAlpha, "Alpha.Runtime.Rule", Flags)',
      "utf8"
    );
    const scoped = discoverAutomationTests(root, {
      scopeTargets: ["Config/DefaultGame.ini"],
    });
    assert.strictEqual(scoped.scopeBound, true);
    assert.deepStrictEqual(scoped.scopeRoots, []);
    assert.deepStrictEqual(scoped.unmappedScopeTargets, ["Config/DefaultGame.ini"]);
    assert.deepStrictEqual(scoped.names, []);
    assert.strictEqual(scoped.truncated, true);
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

test("Automation filters must be safe and match discovered test names", () => {
  const discoveredNames = ["Alpha.Runtime.Rule", "Alpha.Editor.Rule", "Beta.Runtime.Rule"];
  assert.deepStrictEqual(
    validateAutomationFilter("Alpha", discoveredNames).expectedTests,
    ["Alpha.Editor.Rule", "Alpha.Runtime.Rule"]
  );
  assert.strictEqual(
    validateAutomationFilter("Gamma", discoveredNames).errorCode,
    "AUTOMATION_FILTER_NO_MATCH"
  );
  for (const unsafe of [
    "Alpha;Quit",
    "Alpha,Beta",
    "Alpha\nBeta",
    "Alpha\rBeta",
    "Alpha\n",
    "Alpha\u2028Beta",
  ]) {
    assert.strictEqual(
      validateAutomationFilter(unsafe, discoveredNames).errorCode,
      "AUTOMATION_FILTER_UNSAFE"
    );
    assert.throws(
      () => automationArgs("Demo.uproject", unsafe, { discoveredNames }),
      (error) => error.errorCode === "AUTOMATION_FILTER_UNSAFE"
    );
  }
  assert.throws(
    () => automationArgs("Demo.uproject", "Gamma", { discoveredNames }),
    (error) => error.errorCode === "AUTOMATION_FILTER_NO_MATCH"
  );
});

test("Automation execution validates filters against scoped discovery before spawning", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "automation-scoped-run-"));
  try {
    const projectPath = path.join(root, "Demo.uproject");
    fs.writeFileSync(projectPath, "{}", "utf8");
    for (const [moduleName, testName] of [["Alpha", "Alpha.Runtime.Rule"], ["Beta", "Beta.Runtime.Rule"]]) {
      const source = path.join(root, "Source", moduleName, "Private");
      fs.mkdirSync(source, { recursive: true });
      fs.writeFileSync(
        path.join(source, `${moduleName}Tests.cpp`),
        `IMPLEMENT_SIMPLE_AUTOMATION_TEST(F${moduleName}, "${testName}", Flags)`,
        "utf8"
      );
    }
    const result = await runAutomationTests({
      engineRoot: path.join(root, "MissingEngine"),
      projectPath,
      testFilter: "Beta",
      scopeTargets: ["Source/Alpha/Private/AlphaActor.cpp"],
    });
    assert.strictEqual(result.ok, false);
    assert.strictEqual(result.errorCode, "AUTOMATION_FILTER_NO_MATCH");
    assert.deepStrictEqual(result.automationCoverage.names, ["Alpha.Runtime.Rule"]);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
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

test("automation parser requires terminal queue completion", () => {
  const parsed = parseAutomationOutput("Automation Test Succeeded (Gomoku.Stage3.Rule)", 0);
  assert.strictEqual(parsed.ok, false);
  assert.strictEqual(parsed.errorCode, "AUTOMATION_INCOMPLETE");
  assert.strictEqual(parsed.terminalComplete, false);
});

test("automation parser requires expected discovered-test coverage", () => {
  const partial = [
    "LogAutomationController: Display: Test Completed. Result={Success} Name={One} Path={Gomoku.One}",
    "LogAutomationCommandLine: Display: **** TEST COMPLETE. EXIT CODE: 0 ****",
  ].join("\n");
  const parsedPartial = parseAutomationOutput(partial, 0, {
    expectedTests: ["Gomoku.One", "Gomoku.Two"],
  });
  assert.strictEqual(parsedPartial.ok, false);
  assert.strictEqual(parsedPartial.errorCode, "AUTOMATION_COVERAGE_INCOMPLETE");
  assert.deepStrictEqual(parsedPartial.missingTests, ["Gomoku.Two"]);

  const complete = [
    "LogAutomationController: Display: Test Completed. Result={Success} Name={Two} Path={Gomoku.Two}",
    "LogAutomationController: Display: Test Completed. Result={Success} Name={One} Path={Gomoku.One}",
    "LogAutomationCommandLine: Display: **** TEST COMPLETE. EXIT CODE: 0 ****",
  ].join("\n");
  const parsedComplete = parseAutomationOutput(complete, 0, {
    expectedTests: ["Gomoku.One", "Gomoku.Two"],
  });
  assert.strictEqual(parsedComplete.ok, true);
  assert.deepStrictEqual(parsedComplete.missingTests, []);
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
