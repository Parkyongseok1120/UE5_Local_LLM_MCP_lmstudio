"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const test = require("node:test");
const {
  MAX_AUTOMATION_FILTERS,
  discoverAutomationTests,
  parseAutomationOutput,
  resolveEditorCmdPaths,
  runAutomationTests,
  validateAutomationFilter,
} = require("../src/automation-executor");

function withTempProject(prefix, callback) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), prefix));
  try {
    return callback(root);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
}

function writeProjectFile(root, relative, content) {
  const target = path.join(root, ...relative.split("/"));
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, content, "utf8");
  return target;
}

test("scoped discovery emits exact declaration filters for modules sharing a prefix", () => {
  withTempProject("automation-shared-prefix-", (root) => {
    writeProjectFile(
      root,
      "Source/Alpha/Private/AlphaTests.cpp",
      'IMPLEMENT_SIMPLE_AUTOMATION_TEST(FAlpha, "Shared.Runtime.Alpha", Flags)'
    );
    writeProjectFile(
      root,
      "Source/Beta/Private/BetaTests.cpp",
      'IMPLEMENT_SIMPLE_AUTOMATION_TEST(FBeta, "Shared.Runtime.Beta", Flags)'
    );

    const scoped = discoverAutomationTests(root, {
      scopeTargets: ["Source/Alpha/Private/AlphaFeature.cpp"],
    });

    assert.deepStrictEqual(scoped.names, ["Shared.Runtime.Alpha"]);
    assert.deepStrictEqual(scoped.suggestedFilters, ["Shared.Runtime.Alpha"]);
    assert.deepStrictEqual(
      validateAutomationFilter(scoped.suggestedFilters[0], scoped.names).expectedTests,
      ["Shared.Runtime.Alpha"]
    );
    assert.strictEqual(
      validateAutomationFilter("Shared.Runtime.Beta", scoped.names).errorCode,
      "AUTOMATION_FILTER_NO_MATCH"
    );
  });
});

test("case aliases do not create duplicate scoped filters or false cap pressure", () => {
  withTempProject("automation-case-alias-", (root) => {
    writeProjectFile(
      root,
      "Source/Alpha/Private/AlphaTests.cpp",
      [
        'IMPLEMENT_SIMPLE_AUTOMATION_TEST(FUpper, "Portable.Runtime.Rule", Flags)',
        'IMPLEMENT_SIMPLE_AUTOMATION_TEST(FLower, "portable.runtime.rule", Flags)',
      ].join("\n")
    );

    const scoped = discoverAutomationTests(root, {
      scopeTargets: ["Source/Alpha/Private/AlphaFeature.cpp"],
    });
    assert.deepStrictEqual(scoped.names, ["Portable.Runtime.Rule"]);
    assert.deepStrictEqual(scoped.suggestedFilters, ["Portable.Runtime.Rule"]);
  });
});

test("a mixed mapped and unmapped slice fails closed before Automation spawn", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "automation-partial-scope-"));
  try {
    const projectPath = writeProjectFile(root, "Portable.uproject", "{}");
    writeProjectFile(
      root,
      "Source/Alpha/Private/AlphaTests.cpp",
      'IMPLEMENT_SIMPLE_AUTOMATION_TEST(FAlpha, "Portable.Alpha", Flags)'
    );
    const scopeTargets = [
      "Source/Alpha/Private/AlphaFeature.cpp",
      "Config/DefaultGame.ini",
    ];
    const discovery = discoverAutomationTests(root, { scopeTargets });
    assert.deepStrictEqual(discovery.names, ["Portable.Alpha"]);
    assert.deepStrictEqual(discovery.unmappedScopeTargets, ["Config/DefaultGame.ini"]);
    assert.strictEqual(discovery.truncated, true);

    const result = await runAutomationTests({
      engineRoot: path.join(root, "MissingEngine"),
      projectPath,
      testFilter: "Portable.Alpha",
      scopeTargets,
    });
    assert.strictEqual(result.ok, false);
    assert.strictEqual(result.errorCode, "AUTOMATION_SCOPE_UNMAPPED");
    assert.deepStrictEqual(
      result.automationCoverage.unmappedScopeTargets,
      ["Config/DefaultGame.ini"]
    );
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("syntactic module targets with no existing module root remain unmapped", () => {
  withTempProject("automation-missing-module-", (root) => {
    const discovery = discoverAutomationTests(root, {
      scopeTargets: ["Source/Missing/Private/MissingFeature.cpp"],
    });
    assert.deepStrictEqual(discovery.scopeRoots, []);
    assert.deepStrictEqual(
      discovery.unmappedScopeTargets,
      ["Source/Missing/Private/MissingFeature.cpp"]
    );
    assert.strictEqual(discovery.truncated, true);
  });
});

test("discovery recognizes DEFINE_SPEC and BEGIN_DEFINE_SPEC with TEXT spellings", () => {
  withTempProject("automation-spec-macros-", (root) => {
    writeProjectFile(
      root,
      "Source/Specs/Private/FeatureSpecs.cpp",
      [
        'DEFINE_SPEC(FDefineSpec, TEXT("Portable.Spec.Define"), Flags)',
        'BEGIN_DEFINE_SPEC(FBeginSpec, TEXT ( "Portable.Spec.Begin" ), Flags)',
        'IMPLEMENT_CUSTOM_SIMPLE_AUTOMATION_TEST(FCustom, FAutomationTestBase, TEXT("Portable.Custom.Simple"), Flags)',
        'IMPLEMENT_NETWORKED_AUTOMATION_TEST(FNetworked, "Portable.Networked", Flags)',
      ].join("\n")
    );

    const discovery = discoverAutomationTests(root);
    assert.deepStrictEqual(discovery.names, [
      "Portable.Custom.Simple",
      "Portable.Networked",
      "Portable.Spec.Begin",
      "Portable.Spec.Define",
    ]);
  });
});

test("Epic UE 5.8 CQTest declarations register as Dir.Name without standalone methods", () => {
  withTempProject("automation-cqtest-macros-", (root) => {
    writeProjectFile(
      root,
      "Source/CQTests/Private/CQTestExamples.cpp",
      [
        '#include "CQTest.h"',
        'TEST(MinimalTest, "Game.MyGame") { ASSERT_THAT(IsTrue(true)); }',
        'TEST_CLASS(MyTest, TEXT("Game.MyGame")) {',
        '  TEST_METHOD(BeforeRunTest_CallsSetup) {}',
        '};',
        'TEST_CLASS_WITH_ASSERTS(AssertFixture, "Game.Assert", FCustomAsserter) {};',
        'TEST_CLASS_WITH_BASE(BaseFixture, "Game.Base", FBaseFixture) {};',
        'TEST_CLASS_WITH_FLAGS(FlagsFixture, "Game.Flags", EAutomationTestFlags::EditorContext) {};',
        'TEST_CLASS_WITH_BASE_AND_FLAGS(BothFixture, "Game.Both", FBaseFixture, Flags) {};',
        'TEST_METHOD(StandaloneMethodMustNotRegister) {}',
        'TEST_CLASS_IMPL(InternalMacroMustNotRegister, "Game.Internal", Extra) {};',
        'TEST_CLASS_WITH_NOT_REAL(FakeVariant, "Game.Fake", Extra) {};',
        '// TEST(CommentOnly, "Game.Comment") {}',
        '/* TEST_CLASS(BlockComment, "Game.Comment") {} */',
        'const char* Example = "TEST(StringOnly, \\"Game.String\\")";',
        'const char* RawExample = R"cq(TEST(RawStringOnly, "Game.String"))cq";',
        '#define WRAPPED_EXAMPLE TEST(DefinitionOnly, "Game.Definition")',
        "#define WRAPPED_MULTILINE " + "\\",
        '  TEST(DefinitionContinuationOnly, "Game.Definition")',
      ].join("\n")
    );

    const discovery = discoverAutomationTests(root, {
      scopeTargets: ["Source/CQTests/Private/CQTestExamples.cpp"],
    });
    assert.deepStrictEqual(discovery.names, [
      "Game.Assert.AssertFixture",
      "Game.Base.BaseFixture",
      "Game.Both.BothFixture",
      "Game.Flags.FlagsFixture",
      "Game.MyGame.MinimalTest",
      "Game.MyGame.MyTest",
    ]);
    assert.deepStrictEqual(discovery.suggestedFilters, discovery.names);

    const classChild = parseAutomationOutput([
      "Automation Test Succeeded (Game.MyGame.MyTest.BeforeRunTest_CallsSetup)",
      "Automation Test Queue Empty",
    ].join("\n"), 0, {
      expectedTests: ["Game.MyGame.MyTest"],
    });
    assert.strictEqual(classChild.ok, true);
    assert.deepStrictEqual(classChild.missingTests, []);
  });
});

test("cached-read semantic anchors include CQTest roots but not TEST_METHOD as a root pattern", () => {
  const serverSource = fs.readFileSync(path.join(__dirname, "..", "src", "server.js"), "utf8");
  const anchorLine = serverSource.split(/\r?\n/).find((line) => (
    line.includes("IMPLEMENT_[A-Z0-9_]*AUTOMATION_TEST")
  ));
  assert.ok(anchorLine);
  assert.match(anchorLine, /TEST/);
  assert.match(anchorLine, /_CLASS/);
  assert.doesNotMatch(anchorLine, /TEST_METHOD/);
});

test("maxFiles reports truncation when another source file remains in the same directory", () => {
  withTempProject("automation-same-directory-limit-", (root) => {
    writeProjectFile(
      root,
      "Source/Scan/Private/AFirstTests.mm",
      'IMPLEMENT_SIMPLE_AUTOMATION_TEST(FFirst, "Scan.First", Flags)'
    );
    writeProjectFile(
      root,
      "Source/Scan/Private/BSecondTests.inl",
      'IMPLEMENT_SIMPLE_AUTOMATION_TEST(FSecond, "Scan.Second", Flags)'
    );

    const discovery = discoverAutomationTests(root, { maxFiles: 1 });
    assert.strictEqual(discovery.inspectedFileCount, 1);
    assert.strictEqual(discovery.truncated, true);
    assert.deepStrictEqual(discovery.names, ["Scan.First"]);
  });
});

test("Automation discovery scans portable C++ and Objective-C++ declaration files only", () => {
  withTempProject("automation-portable-extensions-", (root) => {
    writeProjectFile(
      root,
      "Source/Portable/Private/MacTests.mm",
      'TEST(MacFixture, "Portable.CQ") {}'
    );
    writeProjectFile(
      root,
      "Source/Portable/Public/HeaderTests.hpp",
      'IMPLEMENT_SIMPLE_AUTOMATION_TEST(FHeader, "Portable.Header.Hpp", Flags)'
    );
    writeProjectFile(
      root,
      "Source/Portable/Public/InlineTests.inl",
      'DEFINE_SPEC(FInline, "Portable.Header.Inline", Flags)'
    );
    writeProjectFile(
      root,
      "Source/Portable/Notes.txt",
      'IMPLEMENT_SIMPLE_AUTOMATION_TEST(FText, "Portable.Text.MustNotRegister", Flags)'
    );

    const discovery = discoverAutomationTests(root);
    assert.strictEqual(discovery.truncated, false);
    assert.strictEqual(discovery.inspectedFileCount, 3);
    assert.deepStrictEqual(discovery.names, [
      "Portable.CQ.MacFixture",
      "Portable.Header.Hpp",
      "Portable.Header.Inline",
    ]);
  });
});

test("Complex and Spec child coverage accepts only an exact dot boundary", () => {
  const complete = [
    "LogAutomationController: Display: Test Completed. Result={Success} Name={CaseA} Path={Suite.Complex.CaseA}",
    "LogAutomationController: Display: Test Completed. Result={Success} Name={CaseB} Path={Suite.Spec.Context.CaseB}",
    "LogAutomationCommandLine: Display: **** TEST COMPLETE. EXIT CODE: 0 ****",
  ].join("\n");
  const parsedComplete = parseAutomationOutput(complete, 0, {
    expectedTests: ["Suite.Complex", "Suite.Spec"],
  });
  assert.strictEqual(parsedComplete.ok, true);
  assert.deepStrictEqual(parsedComplete.missingTests, []);

  const sibling = [
    "LogAutomationController: Display: Test Completed. Result={Success} Name={CaseA} Path={Suite.Complexity.CaseA}",
    "LogAutomationCommandLine: Display: **** TEST COMPLETE. EXIT CODE: 0 ****",
  ].join("\n");
  const parsedSibling = parseAutomationOutput(sibling, 0, {
    expectedTests: ["Suite.Complex"],
  });
  assert.strictEqual(parsedSibling.ok, false);
  assert.strictEqual(parsedSibling.errorCode, "AUTOMATION_COVERAGE_INCOMPLETE");
  assert.deepStrictEqual(parsedSibling.missingTests, ["Suite.Complex"]);
  assert.deepStrictEqual(
    validateAutomationFilter(
      "Suite.Complex",
      ["Suite.Complex.CaseA", "Suite.Complexity.CaseA"]
    ).expectedTests,
    ["Suite.Complex.CaseA"]
  );
});

test("scoped discovery includes dependent test modules but excludes unrelated project and plugin modules", () => {
  withTempProject("automation-dependent-modules-", (root) => {
    const modules = [
      ["Source/GameRuntime/GameRuntime.Build.cs", "public class GameRuntime {}"],
      [
        "Source/GameRuntimeTests/GameRuntimeTests.Build.cs",
        'PrivateDependencyModuleNames.AddRange(new string[] { "GameRuntime" });',
      ],
      ["Source/UnrelatedTests/UnrelatedTests.Build.cs", "public class UnrelatedTests {}"],
      [
        "Plugins/ScopedTools/Source/PluginRuntimeTests/PluginRuntimeTests.Build.cs",
        'PublicDependencyModuleNames.Add("GameRuntime");',
      ],
      [
        "Plugins/OtherTools/Source/PluginUnrelated/PluginUnrelated.Build.cs",
        "public class PluginUnrelated {}",
      ],
    ];
    for (const [relative, content] of modules) writeProjectFile(root, relative, content);
    writeProjectFile(
      root,
      "Source/GameRuntimeTests/Private/GameTests.cpp",
      'IMPLEMENT_COMPLEX_AUTOMATION_TEST(FGame, "Shared.Game.Runtime", Flags)'
    );
    writeProjectFile(
      root,
      "Source/UnrelatedTests/Private/OtherTests.cpp",
      'IMPLEMENT_SIMPLE_AUTOMATION_TEST(FOther, "Shared.Other.Runtime", Flags)'
    );
    writeProjectFile(
      root,
      "Plugins/ScopedTools/Source/PluginRuntimeTests/Private/PluginTests.cpp",
      'BEGIN_DEFINE_SPEC(FPlugin, "Shared.Plugin.Runtime", Flags)'
    );
    writeProjectFile(
      root,
      "Plugins/OtherTools/Source/PluginUnrelated/Private/OtherPluginTests.cpp",
      'IMPLEMENT_SIMPLE_AUTOMATION_TEST(FOtherPlugin, "Shared.Plugin.Unrelated", Flags)'
    );

    const scoped = discoverAutomationTests(root, {
      scopeTargets: ["Source/GameRuntime/Private/GameFeature.cpp"],
    });

    assert.strictEqual(scoped.dependencyGraphComplete, true);
    assert.deepStrictEqual(scoped.scopeRoots, [
      "Plugins/ScopedTools/Source/PluginRuntimeTests",
      "Source/GameRuntime",
      "Source/GameRuntimeTests",
    ]);
    assert.deepStrictEqual(scoped.names, [
      "Shared.Game.Runtime",
      "Shared.Plugin.Runtime",
    ]);
    assert.deepStrictEqual(scoped.suggestedFilters, [
      "Shared.Game.Runtime",
      "Shared.Plugin.Runtime",
    ]);
  });
});

test("dynamic Build.cs dependencies make scoped discovery incomplete instead of silently empty", () => {
  withTempProject("automation-dynamic-dependency-", (root) => {
    writeProjectFile(root, "Source/GameRuntime/GameRuntime.Build.cs", "public class GameRuntime {}");
    writeProjectFile(
      root,
      "Source/GameRuntimeTests/GameRuntimeTests.Build.cs",
      [
        "var RuntimeDependencies = ResolveRuntimeDependencies(Target);",
        "PrivateDependencyModuleNames.AddRange(RuntimeDependencies);",
      ].join("\n")
    );
    writeProjectFile(
      root,
      "Source/GameRuntimeTests/Private/GameTests.cpp",
      'IMPLEMENT_SIMPLE_AUTOMATION_TEST(FGame, "Game.Runtime.DynamicDependency", Flags)'
    );

    const scoped = discoverAutomationTests(root, {
      scopeTargets: ["Source/GameRuntime/Private/GameFeature.cpp"],
    });

    assert.strictEqual(scoped.dependencyGraphComplete, false);
    assert.strictEqual(scoped.truncated, true);
    assert.deepStrictEqual(scoped.names, []);
    assert.deepStrictEqual(scoped.scopeRoots, ["Source/GameRuntime"]);
  });
});

test("Automation tool schema is wired to the same bounded filter maximum", () => {
  assert.strictEqual(MAX_AUTOMATION_FILTERS, 256);
  const serverSource = fs.readFileSync(path.join(__dirname, "..", "src", "server.js"), "utf8");
  assert.match(
    serverSource,
    /testFilters:\s*\{[\s\S]{0,300}?maxItems:\s*MAX_AUTOMATION_FILTERS/
  );
});

test("Automation log persistence failure resolves as a bounded failure", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "automation-log-failure-"));
  try {
    const projectPath = writeProjectFile(
      root,
      "Portable.uproject",
      'process.stdout.write("Automation Test Succeeded (Portable.Log)\\nAutomation Test Queue Empty\\n");'
    );
    writeProjectFile(
      root,
      "Source/Portable/Private/PortableTests.cpp",
      'IMPLEMENT_SIMPLE_AUTOMATION_TEST(FPortable, "Portable.Log", Flags)'
    );
    const engineRoot = path.join(root, "EngineRoot");
    const editorCmd = resolveEditorCmdPaths(engineRoot, process.platform)[0];
    fs.mkdirSync(path.dirname(editorCmd), { recursive: true });
    try {
      fs.linkSync(process.execPath, editorCmd);
    } catch {
      fs.copyFileSync(process.execPath, editorCmd);
      if (process.platform !== "win32") fs.chmodSync(editorCmd, 0o755);
    }
    const blockedParent = path.join(root, "not-a-directory");
    fs.writeFileSync(blockedParent, "blocked", "utf8");
    const logPath = path.join(blockedParent, "latest-automation.log");
    let guard;
    const guardPromise = new Promise((_, reject) => {
      guard = setTimeout(() => reject(new Error("Automation execution did not resolve")), 10000);
    });
    let result;
    try {
      result = await Promise.race([
        runAutomationTests({
          engineRoot,
          projectPath,
          testFilter: "Portable.Log",
          timeoutMs: 3000,
          logPath,
        }),
        guardPromise,
      ]);
    } finally {
      clearTimeout(guard);
    }
    assert.strictEqual(result.ok, false);
    assert.strictEqual(result.errorCode, "AUTOMATION_LOG_WRITE_FAILED");
    assert.strictEqual(result.timedOut, false);
    assert.strictEqual(result.fullLogPath, logPath);
    assert.ok(result.error);
    assert.strictEqual(result.logPersistenceError, result.error);
    assert.strictEqual(result.outputDecodeError, "");
  } finally {
    fs.rmSync(root, {
      recursive: true,
      force: true,
      maxRetries: process.platform === "win32" ? 10 : 0,
      retryDelay: 100,
    });
  }
});
