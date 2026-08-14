"use strict";

const assert = require("assert");
const test = require("node:test");
const {
  applyBuildRecoveryScopeBinding,
  buildResponsePayload,
  buildToolDisposition,
  extractLikelyCompileErrors,
  compactCompilerDiagnostic,
  defaultUnrealPlatform,
  firstErrorCluster,
} = require("../src/context-ux");

test("build response platform defaults follow the host Unreal layout", () => {
  assert.strictEqual(defaultUnrealPlatform("win32"), "Win64");
  assert.strictEqual(defaultUnrealPlatform("darwin"), "Mac");
  assert.strictEqual(defaultUnrealPlatform("linux"), "Linux");
});

test("out-of-slice build recovery stops instead of authorizing ownership expansion", () => {
  const payload = buildResponsePayload({
    result: {
      ok: false,
      exitCode: 6,
      stdout: 'Module.Demo.gen.cpp.obj : error LNK2019: "public: bool __cdecl ADemoGameMode::SetPlayerReady(class APlayerController *,bool)"',
      stderr: "",
      error: "",
    },
    build: { target: "DemoEditor", platform: "Win64", configuration: "Development" },
    planResult: { ok: true },
    projectPath: "C:\\Game\\Demo.uproject",
    command: "UnrealBuildTool.exe DemoEditor Win64 Development",
    logPath: "C:\\Game\\.agent\\logs\\latest-build.log",
    verbose: false,
  });

  applyBuildRecoveryScopeBinding(payload, {
    scopeDisposition: "out_of_slice",
    activeSliceId: "local_input",
    activeSliceFiles: [
      "Source/Demo/DemoPlayerController.h",
      "Source/Demo/DemoPlayerController.cpp",
    ],
  });
  const disposition = buildToolDisposition(payload);

  assert.strictEqual(payload.errorCode, "BUILD_FAILURE_OUTSIDE_ACTIVE_SLICE");
  assert.strictEqual(payload.stopCurrentWorkflow, true);
  assert.strictEqual(payload.requiredNextTool, null);
  assert.strictEqual(payload.recovery.scopeStrategy, "out_of_slice_blocker");
  assert.deepStrictEqual(payload.suggestedToolCalls, []);
  assert.strictEqual(disposition.recoverable, false);
  assert.strictEqual(disposition.mcpIsError, true);
});

test("compiler failure is a recoverable build outcome, not an MCP tool error", () => {
  const payload = buildResponsePayload({
    result: {
      ok: false,
      exitCode: 6,
      stdout: "C:\\Game\\Foo.cpp(12,3): error C2039: 'StreamLevel': 'UGameplayStatics'",
      stderr: "",
      error: "",
    },
    build: { target: "GameEditor", platform: "Win64", configuration: "Development" },
    planResult: { ok: true },
    projectPath: "C:\\Game\\Game.uproject",
    command: "Build.bat GameEditor Win64 Development",
    logPath: "C:\\Game\\.agent\\logs\\latest-build.log",
    verbose: false,
  });
  const disposition = buildToolDisposition(payload);

  assert.strictEqual(payload.ok, false);
  assert.strictEqual(payload.buildOutcome, "compile_failed");
  assert.strictEqual(payload.toolExecutionSucceeded, true);
  assert.strictEqual(payload.recoverable, true);
  assert.strictEqual(disposition.mcpIsError, false);
  assert.strictEqual(payload.requiredNextTool, "unreal_symbol_lookup");
  assert.deepStrictEqual(payload.requiredNextToolArgs, {
    query: "StreamLevel", top_k: 8, detailLevel: "compact",
  });
});

test("compact compiler diagnostics remove machine path and mojibake tail", () => {
  const raw = "D:\\BuildAgent\\Game\\Source\\StaminaComponent.cpp(93,28): error C2039: 'Empty': 'FGameplayTagContainer'??좎룞??源⑥쭊 ?ㅻ챸";
  const compact = compactCompilerDiagnostic(raw);
  assert.strictEqual(
    compact,
    "Source/StaminaComponent.cpp(93,28): error C2039: 'Empty': 'FGameplayTagContainer'"
  );

  const payload = buildResponsePayload({
    result: { ok: false, exitCode: 6, stdout: raw, stderr: "", error: "" },
    build: { target: "GameEditor", platform: "Win64", configuration: "Development" },
    planResult: { ok: true },
    projectPath: "C:\\Game\\Game.uproject",
    command: "Build.bat GameEditor",
    logPath: "C:\\Game\\.agent\\logs\\latest-build.log",
    verbose: false,
  });
  assert.deepStrictEqual(payload.likelyErrors, [compact]);
  assert.strictEqual(payload.suggestedToolCalls[0].tool, "unreal_symbol_lookup");
  assert.strictEqual(payload.suggestedToolCalls[0].args.query, "Empty");
  assert.strictEqual(payload.recovery.owner, "FGameplayTagContainer");
  assert.ok(!payload.summary.includes("D:\\BuildAgent"));
});

test("clang diagnostics keep project-relative coordinates and route to the failing range", () => {
  const raw = "/tmp/example/Game/Source/Demo/GomokuGameState.cpp:109:17: error: too few arguments to function call, single argument 'bForceEnd' was not specified";
  const payload = buildResponsePayload({
    result: { ok: false, exitCode: 6, stdout: raw, stderr: "", error: "" },
    build: { target: "GameEditor", platform: "Mac", configuration: "Development" },
    planResult: { ok: true },
    projectPath: "/tmp/example/Game/Game.uproject",
    command: "Build.sh GameEditor Mac Development",
    logPath: "/tmp/example/Game/.agent/logs/latest-build.log",
    verbose: false,
  });

  assert.strictEqual(
    payload.likelyErrors[0],
    "Source/Demo/GomokuGameState.cpp:109:17: error: too few arguments to function call, single argument 'bForceEnd' was not specified"
  );
  assert.strictEqual(payload.requiredNextTool, "read_file_range");
  assert.deepStrictEqual(payload.requiredNextToolArgs, {
    path: "Source/Demo/GomokuGameState.cpp",
    startLine: 94,
    endLine: 124,
    detailLevel: "compact",
  });
  assert.deepStrictEqual(payload.recovery.requiredSequence, [
    "read_file_range",
    "unreal_code_sketch_claim_validate",
    "replace_in_file",
    "static_validate_project",
    "build_unreal_project",
  ]);
  assert.strictEqual(payload.recovery.rebindTargetBeforeMutation, true);
  assert.ok(!payload.nextSteps.some((step) => step.includes("No actionable")));
});

test("two compiler failures keep deterministic recovery before a successful rebuild", () => {
  const makePayload = (result) => buildResponsePayload({
    result,
    build: { target: "GameEditor", platform: "Win64", configuration: "Development" },
    planResult: { ok: true },
    projectPath: "C:\\Game\\Game.uproject",
    command: "Build.bat GameEditor",
    logPath: "C:\\Game\\.agent\\logs\\latest-build.log",
    verbose: false,
  });

  const first = makePayload({
    ok: false,
    exitCode: 6,
    stdout: "C:\\Game\\CinematicDirectorSubsystem.cpp(693,22): error C2039: 'StreamLevel': 'UGameplayStatics'",
    stderr: "",
    error: "",
  });
  const second = makePayload({
    ok: false,
    exitCode: 6,
    stdout: "C:\\Game\\CinematicDirectorSubsystem.cpp(693,22): error C2660: 'UGameplayStatics::LoadStreamLevel': function does not take 5 arguments",
    stderr: "",
    error: "",
  });
  const success = makePayload({
    ok: true,
    exitCode: 0,
    stdout: "[1/2] Compile CinematicDirectorSubsystem.cpp\n[2/2] Link GameEditor\nResult: Succeeded",
    stderr: "",
    error: "",
  });

  assert.deepStrictEqual(
    [first.buildOutcome, second.buildOutcome, success.buildOutcome],
    ["compile_failed", "compile_failed", "succeeded"]
  );
  assert.strictEqual(buildToolDisposition(first).mcpIsError, false);
  assert.strictEqual(buildToolDisposition(second).mcpIsError, false);
  assert.strictEqual(buildToolDisposition(success).mcpIsError, false);
  assert.strictEqual(first.requiredNextTool, "unreal_symbol_lookup");
  assert.strictEqual(first.requiredNextToolArgs.query, "StreamLevel");
  assert.strictEqual(second.requiredNextTool, "unreal_symbol_lookup");
  assert.strictEqual(second.requiredNextToolArgs.query, "LoadStreamLevel");
});

test("clang linker blocks preserve undefined symbols and route to the missing definition", () => {
  const output = [
    "[2/2] Link [Apple] libUnrealEditor-Demo.dylib",
    "Undefined symbols for architecture arm64:",
    '  "AGomokuGameState::SetHoveredCell(UE::Math::TIntPoint<int> const&)", referenced from:',
    "      AGomokuGameState::execSetHoveredCell(UObject*, FFrame&, void*) in Module.Demo.gen.cpp.o",
    '  "AGomokuGameState::Tick(float)", referenced from:',
    "      vtable for AGomokuGameState in GomokuGameState.cpp.o",
    "ld: symbol(s) not found for architecture arm64",
    "clang++: error: linker command failed with exit code 1 (use -v to see invocation)",
    "Result: Failed (OtherCompilationError)",
  ].join("\n");

  const payload = buildResponsePayload({
    result: { ok: false, exitCode: 6, stdout: output, stderr: "", error: "" },
    build: { target: "DemoEditor", platform: "Mac", configuration: "Development" },
    planResult: { ok: true },
    projectPath: "/tmp/example/Demo/Demo.uproject",
    command: "Build.sh DemoEditor Mac Development",
    logPath: "/tmp/example/Demo/.agent/logs/latest-build.log",
    verbose: false,
  });

  assert.strictEqual(
    payload.likelyErrors[0],
    "Undefined symbol: AGomokuGameState::SetHoveredCell(UE::Math::TIntPoint<int> const&)"
  );
  assert.strictEqual(payload.recovery.category, "linker_missing_definition");
  assert.strictEqual(payload.requiredNextTool, "unreal_symbol_lookup");
  assert.strictEqual(payload.requiredNextToolArgs.query, "SetHoveredCell");
  assert.strictEqual(payload.recovery.ownerSymbol, "AGomokuGameState");
  assert.strictEqual(payload.recovery.missingSymbol, "SetHoveredCell");
  assert.strictEqual(payload.recovery.semanticEvidenceRequired, true);
  assert.ok(payload.recovery.requiredSequence.includes("unreal_agent_plan"));
  assert.ok(payload.recovery.requiredSequence.includes("unreal_code_sketch_claim_validate"));
  assert.ok(!payload.nextSteps.some((step) => step.includes("No actionable")));
});

test("MSVC LNK2019 routes the quoted unresolved method to symbol lookup", () => {
  const output = [
    "[6/7] Link [x64] UnrealEditor-O_Mock.dll [NoUba]",
    'Module.O_Mock.gen.cpp.obj : error LNK2019: "public: bool __cdecl AGomokuGameMode::SetPlayerReady(class APlayerController *,bool)" (?SetPlayerReady@AGomokuGameMode@@QEAA_NPEAVAPlayerController@@_N@Z) "public: static void __cdecl AGomokuGameMode::execSetPlayerReady(class UObject *,struct FFrame &,void * const)"',
    "C:\\Game\\Binaries\\Win64\\UnrealEditor-O_Mock.dll : fatal error LNK1120: 1",
    "Result: Failed (OtherCompilationError)",
  ].join("\n");

  const payload = buildResponsePayload({
    result: { ok: false, exitCode: 6, stdout: output, stderr: "", error: "" },
    build: { target: "O_MockEditor", platform: "Win64", configuration: "Development" },
    planResult: { ok: true },
    projectPath: "C:\\Game\\O_Mock.uproject",
    command: "UnrealBuildTool.exe O_MockEditor Win64 Development",
    logPath: "C:\\Game\\.agent\\logs\\latest-build.log",
    verbose: false,
  });

  assert.strictEqual(payload.recovery.category, "linker_missing_definition");
  assert.strictEqual(payload.requiredNextTool, "unreal_symbol_lookup");
  assert.strictEqual(payload.requiredNextToolArgs.query, "SetPlayerReady");
  assert.strictEqual(payload.recovery.ownerSymbol, "AGomokuGameMode");
  assert.strictEqual(payload.recovery.missingSymbol, "SetPlayerReady");
  assert.strictEqual(payload.recovery.semanticEvidenceRequired, true);
  assert.strictEqual(payload.recovery.mutationPermittedWithoutSemanticEvidence, false);
  assert.ok(payload.recovery.requiredSequence.includes("unreal_code_sketch_claim_validate"));
  assert.ok(payload.nextSteps.some((step) => step.includes("do not invent persistent state")));
});

test("same-file incomplete type recovery includes the source preamble when bounded", () => {
  const output = [
    "/tmp/example/Demo/Source/Demo/Board.cpp:92:51: error: cannot initialize a parameter of type 'const APlayerController *' with an rvalue of type 'ABoard *'",
    "/tmp/example/Demo/Source/Demo/Board.cpp:100:29: error: member access into incomplete type 'ADemoGameState'",
    "/tmp/example/Demo/Source/Demo/Board.cpp:108:12: error: member access into incomplete type 'ADemoGameState'",
  ].join("\n");

  const payload = buildResponsePayload({
    result: { ok: false, exitCode: 6, stdout: output, stderr: "", error: "" },
    build: { target: "DemoEditor", platform: "Mac", configuration: "Development" },
    planResult: { ok: true },
    projectPath: "/tmp/example/Demo/Demo.uproject",
    command: "Build.sh DemoEditor Mac Development",
    logPath: "/tmp/example/Demo/.agent/logs/latest-build.log",
    verbose: false,
  });

  assert.strictEqual(payload.recovery.includesSourcePreamble, true);
  assert.deepStrictEqual(payload.requiredNextToolArgs, {
    path: "Source/Demo/Board.cpp",
    startLine: 1,
    endLine: 123,
    detailLevel: "compact",
  });
});

test("build infrastructure failures still set the MCP error disposition", () => {
  const disposition = buildToolDisposition({
    ok: false,
    phase: "failed",
    timedOut: true,
    errorCode: "BUILD_TIMEOUT",
    error: "Build timed out",
    likelyErrors: [],
  });

  assert.strictEqual(disposition.buildOutcome, "tool_failed");
  assert.strictEqual(disposition.toolExecutionSucceeded, false);
  assert.strictEqual(disposition.recoverable, false);
  assert.strictEqual(disposition.mcpIsError, true);
});

test("UHT warnings-as-errors are returned as actionable build errors", () => {
  const output = [
    'Running Internal UnrealHeaderTool "C:\\Game\\Game.uproject" -WarningsAsErrors',
    'C:\\Game\\Source\\Game\\Public\\Status.h(75): Warning: Property has a Category set but is not exposed to the editor or Blueprints.',
    "Unhandled 1 aggregate exceptions",
    "Result: Failed (OtherCompilationError)",
  ].join("\n");

  const errors = extractLikelyCompileErrors(output, "");

  assert.match(errors[0], /Status\.h\(75\): Warning:/);
  assert.ok(errors.some((line) => line.includes("OtherCompilationError")));
});

test("error clustering finds UHT failure before a long build tail", () => {
  const lines = [
    ...Array.from({ length: 80 }, (_, index) => `setup ${index}`),
    'C:\\Game\\Status.h(75): Warning: Property has a Category set but is not exposed.',
    "Unhandled 1 aggregate exceptions",
    "Result: Failed (OtherCompilationError)",
    ...Array.from({ length: 100 }, (_, index) => `timeline ${index}`),
  ];

  const cluster = firstErrorCluster(lines, 4, 30);

  assert.ok(cluster.some((line) => line.includes("Status.h(75): Warning:")));
  assert.ok(cluster.some((line) => line.includes("OtherCompilationError")));
  assert.ok(!cluster.some((line) => line === "timeline 99"));
});

test("error clustering recognizes UE Automation failure records", () => {
  for (const failure of [
    "Automation Test Failed (Project.Runtime.Rule)",
    "LogAutomationController: Display: Test Completed. Result={Fail} Name={Rule} Path={Project.Runtime.Rule}",
  ]) {
    const cluster = firstErrorCluster(["setup", failure, "tail"], 1, 10);
    assert.ok(cluster.includes(failure));
  }
});
