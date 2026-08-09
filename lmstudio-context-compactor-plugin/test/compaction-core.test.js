"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const core = require("../src/compaction-core");

test("budget gate reserves output, tool schema, and build result space", () => {
  const soft = core.budgetDecision({
    contextLength: 32_000,
    inputTokens: 8_000,
    nextToolName: "build_unreal_project",
    toolSchemaTokens: 2_000,
  });
  assert.equal(soft.action, "soft_compact");
  assert.equal(soft.remainingTokens, 8_880);
  assert.equal(soft.reservedTokens, 15_120);

  const hard = core.budgetDecision({
    contextLength: 32_000,
    inputTokens: 19_000,
    nextToolName: "read_file_range",
    toolSchemaTokens: 1_000,
  });
  assert.equal(hard.action, "hard_compact");
});

test("checkpoint preserves required next tool and exact signature contract", () => {
  const messages = [
    { role: "user", content: "Fix the compile error" },
    { role: "tool", content: JSON.stringify({
      requiredNextTool: "unreal_symbol_lookup",
      requiredNextToolArgs: { query: "LoadStreamLevel" },
      signatureContract: { name: "LoadStreamLevel", parameterCount: 5 },
      path: "project://Source/Game/Foo.cpp",
    }) },
  ];
  const checkpoint = core.buildCheckpoint(messages);
  assert.equal(checkpoint.requiredNextTool.name, "unreal_symbol_lookup");
  assert.deepEqual(checkpoint.requiredNextTool.args, { query: "LoadStreamLevel" });
  assert.equal(checkpoint.exactSignatureContracts[0].parameterCount, 5);
  assert.ok(checkpoint.modifiedFiles.includes("project://Source/Game/Foo.cpp"));
  assert.equal(core.validateCheckpoint(checkpoint), true);
});

test("checkpoint recovery nextAction outranks its post-checkpoint requiredNextAction", () => {
  const checkpoint = core.buildCheckpoint([
    { role: "user", content: "continue" },
    { role: "tool", content: JSON.stringify({
      ok: false,
      errorCode: "TASK_PHASE_TOOL_BUDGET_EXHAUSTED",
      nextAction: "unreal_task_checkpoint",
      nextActionArgs: {
        action: "record",
        requiredNextAction: "read_file",
      },
    }) },
  ]);

  assert.equal(checkpoint.requiredNextTool?.name, "unreal_task_checkpoint");
  assert.equal(checkpoint.requiredNextTool?.args?.requiredNextAction, "read_file");
});

test("checkpoint validation rejects malformed pending tool state", () => {
  assert.equal(core.validateCheckpoint({
    schemaVersion: 1,
    checkpointGeneration: 1,
    completedToolCallIds: [],
    pendingToolCalls: [{ id: "pending-1" }],
  }), false);
  assert.equal(core.validateCheckpoint({
    schemaVersion: 1,
    checkpointGeneration: 1,
    completedToolCallIds: [42],
  }), false);
});

test("latest user message replaces sticky first-turn objective", () => {
  const messages = [
    { role: "user", content: "현재 프로젝트 찾고 코드 구조 전체 적으로 확인해줘" },
    { role: "assistant", content: "structure overview..." },
    { role: "user", content: "지금 버그있는거 찾기만하고 수정은 하지마." },
  ];
  const checkpoint = core.buildCheckpoint(messages);
  assert.match(checkpoint.objective, /버그있는거 찾기만/);
  assert.doesNotMatch(checkpoint.objective, /코드 구조 전체/);
  assert.ok(checkpoint.constraints.some((item) => String(item).startsWith("read_only_findings_only:")));
});

test("compaction pins latest user goal instead of first user turn", () => {
  const messages = [
    { role: "system", content: "rules" },
    { role: "user", content: "구조 전체 확인" },
    { role: "assistant", content: "overview" },
    { role: "user", content: "지금 버그있는거 찾기만하고 수정은 하지마." },
    { role: "assistant", content: "scanning" },
  ];
  const checkpoint = core.buildCheckpoint(messages);
  const compacted = core.compactSnapshots(messages, checkpoint, { recentCompleteTurns: 0 });
  const pinnedUsers = compacted.filter((message) => message.role === "user").map((message) => message.text);
  assert.deepEqual(pinnedUsers, ["지금 버그있는거 찾기만하고 수정은 하지마."]);
  assert.equal(compacted[0].role, "system");
  assert.equal(compacted.filter((message) => message.role === "system").length, 1);
  assert.match(compacted[0].text, /Conversation checkpoint/);
  assert.match(compacted[0].text, /rules/);
  assert.match(checkpoint.objective, /버그있는거 찾기만/);
});

test("compaction emits a single leading system message for chat-template safety", () => {
  const messages = [
    { role: "system", content: "base rules" },
    { role: "system", content: "extra rules" },
    { role: "user", content: "구조 전체 확인" },
    { role: "assistant", content: "overview" },
    { role: "user", content: "시네마틱 관련해서 구현된것들 더 구체적으로 알려줘." },
  ];
  const checkpoint = core.buildCheckpoint(messages);
  const compacted = core.compactSnapshots(messages, checkpoint, { recentCompleteTurns: 0 });
  const systems = compacted.filter((message) => message.role === "system");
  assert.equal(systems.length, 1);
  assert.match(systems[0].text, /base rules/);
  assert.match(systems[0].text, /extra rules/);
  assert.match(systems[0].text, /Conversation checkpoint/);
  assert.match(systems[0].text, /objective=/);
  assert.equal(compacted.filter((message) => message.role === "user").at(-1).text.includes("시네마틱"), true);
});

test("LM Studio title prompt does not replace the real objective", () => {
  const titlePrompt = (
    "Based on the conversation above, can you please come up with a 2-5 word title "
    + "for this conversation? Put your answer in <title> tags, like this: <title>Your Title Here</title>.\n\n"
    + "Do not explain anything. Just return the title in the specified format."
  );
  const messages = [
    { role: "user", content: "현재 프로젝트 찾고 코드 구조 전체 적으로 확인해줘" },
    { role: "assistant", content: "", toolCalls: [{ id: "a", name: "list_directory", arguments: { path: "Source" } }] },
    { role: "tool", content: "entries", toolResults: [{ toolCallId: "a", name: "list_directory", content: "Project_MJS" }] },
    { role: "user", content: titlePrompt },
  ];
  const checkpoint = core.buildCheckpoint(messages);
  assert.match(checkpoint.objective, /코드 구조/);
  assert.equal(core.isMetaUserMessage(titlePrompt), true);
  assert.doesNotMatch(checkpoint.objective, /2-5 word title/);
});

test("title meta prompt is passed through at the end of compacted chat", () => {
  const titlePrompt = (
    "Based on the conversation above, can you please come up with a 2-5 word title "
    + "for this conversation? Put your answer in <title> tags."
  );
  const messages = [
    { role: "user", content: "코드 구조 확인해줘" },
    { role: "assistant", content: "", toolCalls: [{ id: "a", name: "list_directory", arguments: { path: "Source" } }] },
    { role: "tool", content: "entries", toolResults: [{ toolCallId: "a", name: "list_directory", content: "Project_MJS" }] },
    { role: "user", content: titlePrompt },
  ];
  const checkpoint = core.buildCheckpoint(messages);
  const compacted = core.compactSnapshots(messages, checkpoint, {
    recentCompleteTurns: 0,
    trailingMetaUser: { role: "user", text: titlePrompt, toolCalls: [], toolResults: [] },
  });
  assert.equal(compacted[compacted.length - 1].role, "user");
  assert.match(compacted[compacted.length - 1].text, /2-5 word title/);
  assert.equal(compacted.filter((message) => message.role === "user").length, 2);
  assert.match(checkpoint.objective, /코드 구조/);
});

test("current-turn overflow trim drops oldest tool pairs only", () => {
  const messages = [
    { role: "system", content: "rules" },
    { role: "user", content: "scan deep" },
    { role: "assistant", content: "", toolCalls: [{ id: "a", name: "list_directory", arguments: { path: "A" } }] },
    { role: "tool", content: "a", toolResults: [{ toolCallId: "a", name: "list_directory", content: "A" }] },
    { role: "assistant", content: "", toolCalls: [{ id: "b", name: "list_directory", arguments: { path: "B" } }] },
    { role: "tool", content: "b", toolResults: [{ toolCallId: "b", name: "list_directory", content: "B" }] },
    { role: "assistant", content: "", toolCalls: [{ id: "c", name: "list_directory", arguments: { path: "C" } }] },
    { role: "tool", content: "c", toolResults: [{ toolCallId: "c", name: "list_directory", content: "C" }] },
  ];
  const compacted = core.compactSnapshots(messages, core.buildCheckpoint(messages), {
    recentCompleteTurns: 0,
    maxCurrentTurnMessages: 2,
  });
  const toolResults = compacted.flatMap((message) => message.toolResults || []);
  assert.equal(toolResults.some((result) => result.toolCallId === "a"), false);
  assert.equal(toolResults.some((result) => result.toolCallId === "b"), false);
  assert.equal(toolResults.some((result) => result.toolCallId === "c"), true);
  assert.equal(core.isCompleteToolPair(compacted), true);
});

test("soft compaction keeps the full current turn tool evidence", () => {
  const messages = [
    { role: "system", content: "rules" },
    { role: "user", content: "old goal" },
    { role: "assistant", content: "old-" + "x".repeat(200) },
    { role: "user", content: "현재 프로젝트 코드 구조 확인해줘" },
    { role: "assistant", content: "", toolCalls: [{ id: "a", name: "list_directory", arguments: { path: "Source" } }] },
    { role: "tool", content: "source", toolResults: [{ toolCallId: "a", name: "list_directory", content: "Project_MJS" }] },
    { role: "assistant", content: "", toolCalls: [{ id: "b", name: "list_directory", arguments: { path: "Source/Project_MJS/Public" } }] },
    { role: "tool", content: "public", toolResults: [{ toolCallId: "b", name: "list_directory", content: "Character" }] },
  ];
  const compacted = core.compactSnapshots(messages, core.buildCheckpoint(messages), { recentCompleteTurns: 1 });
  const toolResults = compacted.flatMap((message) => message.toolResults || []);
  assert.equal(toolResults.some((result) => result.toolCallId === "a"), true);
  assert.equal(toolResults.some((result) => result.toolCallId === "b"), true);
  assert.equal(core.isCompleteToolPair(compacted), true);
});

test("compaction never leaves an orphan tool call in the retained tail", () => {
  const messages = [
    { role: "system", content: "rules" },
    { role: "user", content: "objective" },
    { role: "assistant", content: "", toolCalls: [{ id: "a", name: "read_file", arguments: {} }] },
    { role: "tool", content: "result", toolResults: [{ toolCallId: "a", name: "read_file", content: "result" }] },
    { role: "user", content: "continue" },
    { role: "assistant", content: "done" },
  ];
  const compacted = core.compactSnapshots(messages, core.buildCheckpoint(messages), { recentCompleteTurns: 1 });
  assert.equal(core.isCompleteToolPair(compacted), true);
});

test("compaction is deterministic for the same checkpoint and messages", () => {
  const messages = [
    { role: "system", content: "rules" },
    { role: "user", content: "objective" },
    { role: "assistant", content: "first" },
    { role: "user", content: "second" },
    { role: "assistant", content: "third" },
  ];
  const checkpoint = core.buildCheckpoint(messages);
  const a = core.compactSnapshots(messages, checkpoint, { recentCompleteTurns: 1 });
  const b = core.compactSnapshots(messages, checkpoint, { recentCompleteTurns: 1 });
  assert.deepEqual(a, b);
});

test("tail expansion includes the request when a retained tool result would be orphaned", () => {
  const messages = [
    { role: "system", content: "rules" },
    { role: "user", content: "objective" },
    { role: "assistant", content: "", toolCalls: [{ id: "pair-1", name: "read_file", arguments: {} }] },
    { role: "tool", content: "result", toolResults: [{ toolCallId: "pair-1", content: "result" }] },
    { role: "user", content: "continue" },
  ];
  const compacted = core.compactSnapshots(messages, core.buildCheckpoint(messages), { recentCompleteTurns: 1 });
  assert.equal(core.isCompleteToolPair(compacted), true);
  assert.ok(compacted.some((message) => message.toolCalls?.some((call) => call.id === "pair-1")));
});

test("rebuilding an unchanged checkpoint does not double count mutations", () => {
  const messages = [
    { role: "user", content: "fix" },
    { role: "assistant", content: "", toolCalls: [{ id: "write-1", name: "mcp_unreal_agent_write_file", arguments: {} }] },
    { role: "tool", content: "ok", toolResults: [{ toolCallId: "write-1", content: "ok" }] },
  ];
  const first = core.buildCheckpoint(messages);
  const second = core.buildCheckpoint(messages, first);
  assert.equal(first.mutationGeneration, 1);
  assert.equal(second.mutationGeneration, 1);
});

test("zero retained turns keeps only the minimum recent tail", () => {
  const messages = [
    { role: "system", content: "rules" },
    { role: "user", content: "objective" },
    { role: "assistant", content: "old answer" },
    { role: "user", content: "latest request" },
  ];
  const compacted = core.compactSnapshots(messages, core.buildCheckpoint(messages), { recentCompleteTurns: 0 });
  const users = compacted.filter((message) => message.role === "user").map((message) => message.text);
  assert.deepEqual(users, ["latest request"]);
  assert.equal(compacted.some((message) => message.text === "old answer"), false);
  assert.equal(compacted.some((message) => message.text === "objective"), false);
});

test("session fingerprint salt separates identical prompts in different workspaces", () => {
  const messages = [{ role: "user", content: "same request" }];
  assert.notEqual(core.sessionFingerprint(messages, "A"), core.sessionFingerprint(messages, "B"));
});

test("session fingerprint remains stable as later turns are appended", () => {
  const initial = [
    { role: "system", content: "rules" },
    { role: "user", content: "same request" },
  ];
  const later = [
    ...initial,
    { role: "assistant", content: "answer" },
    { role: "user", content: "follow-up" },
  ];
  assert.equal(core.sessionFingerprint(initial, "workspace"), core.sessionFingerprint(later, "workspace"));
});

test("session markers isolate identical first prompts across chats", () => {
  const chatA = [
    { role: "system", content: "rules\n<!-- ucc-session:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa -->" },
    { role: "user", content: "현재 프로젝트 구조 분석해줘" },
  ];
  const chatB = [
    { role: "system", content: "rules\n<!-- ucc-session:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb -->" },
    { role: "user", content: "현재 프로젝트 구조 분석해줘" },
  ];
  assert.notEqual(
    core.sessionFingerprint(chatA, "workspace\nmodel"),
    core.sessionFingerprint(chatB, "workspace\nmodel"),
  );
  assert.equal(core.extractSessionMarker(chatA), "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
});

test("session markers are idempotent session identities", () => {
  const marker = "abcdef0123456789abcdef0123456789";
  const messages = [
    { role: "system", content: `rules\n<!-- ucc-session:${marker} -->` },
    { role: "user", content: "continue" },
  ];
  assert.equal(core.sessionFingerprint(messages, "workspace\nmodel"), marker);
  assert.equal(
    core.sessionFingerprint(messages, "workspace\nmodel", { sessionMarker: marker }),
    marker,
  );
});

test("LM Studio conversation directories provide cross-platform stable session identities", () => {
  const windowsA = "C:\\Users\\USERNAME\\.lmstudio\\working-directories\\1786265188981";
  const windowsSame = "c:/users/USERNAME/.lmstudio/working-directories/1786265188981/";
  const windowsB = "C:\\Users\\USERNAME\\.lmstudio\\working-directories\\1786265188982";
  const posix = "/Users/USERNAME/.lmstudio/working-directories/1786265188981";
  const a = core.lmStudioConversationSessionFingerprint(windowsA, "qwen-model");

  assert.match(a, /^[a-f0-9]{32}$/);
  assert.equal(a, core.lmStudioConversationSessionFingerprint(windowsSame, "qwen-model"));
  assert.notEqual(a, core.lmStudioConversationSessionFingerprint(windowsB, "qwen-model"));
  assert.notEqual(a, core.lmStudioConversationSessionFingerprint(windowsA, "other-model"));
  assert.match(
    core.lmStudioConversationSessionFingerprint(posix, "qwen-model"),
    /^[a-f0-9]{32}$/,
  );
  assert.equal(
    core.lmStudioConversationSessionFingerprint("C:\\Projects\\O-Mock", "qwen-model"),
    "",
  );
});

test("lineageContinues matches growing chats but not sibling chats", () => {
  const first = core.messageLineageFingerprints([
    { role: "system", content: "rules" },
    { role: "user", content: "analyze" },
  ]);
  const grew = core.messageLineageFingerprints([
    { role: "system", content: "rules" },
    { role: "user", content: "analyze" },
    { role: "assistant", content: "ok" },
  ]);
  const sibling = core.messageLineageFingerprints([
    { role: "system", content: "rules" },
    { role: "user", content: "analyze" },
  ]);
  assert.equal(core.lineageContinues(first, grew), true);
  assert.equal(core.lineageContinues(grew, sibling), false);
});

test("isMajorGoalChange ignores minor follow-ups but catches mode flips", () => {
  assert.equal(
    core.isMajorGoalChange("프로젝트 구조 분석해줘", "프로젝트 구조에서 Source 폴더만 더 자세히 봐줘"),
    false,
  );
  assert.equal(
    core.isMajorGoalChange("프로젝트 구조 분석해줘", "버그 찾기만하고 수정은 하지마"),
    true,
  );
});

test("required next tool clears only after its matching successful result", () => {
  const prior = core.buildCheckpoint([
    { role: "user", content: "fix" },
    { role: "tool", content: JSON.stringify({ requiredNextTool: "unreal_symbol_lookup" }) },
  ]);
  const next = core.buildCheckpoint([
    { role: "user", content: "fix" },
    { role: "tool", content: JSON.stringify({ requiredNextTool: "unreal_symbol_lookup" }) },
    { role: "assistant", content: "", toolCalls: [{ id: "lookup-1", name: "mcp_unreal_symbol_lookup" }] },
    { role: "tool", content: JSON.stringify({ ok: true }), toolResults: [{ toolCallId: "lookup-1", content: "{}" }] },
  ], prior);
  assert.equal(next.requiredNextTool, null);
});

test("required next tool remains pending after call dispatch without a result", () => {
  const prior = core.buildCheckpoint([
    { role: "user", content: "fix" },
    { role: "tool", content: JSON.stringify({ requiredNextTool: "unreal_symbol_lookup" }) },
  ]);
  const next = core.buildCheckpoint([
    { role: "user", content: "fix" },
    { role: "tool", content: JSON.stringify({ requiredNextTool: "unreal_symbol_lookup" }) },
    { role: "assistant", content: "", toolCalls: [{ id: "lookup-1", name: "mcp_unreal_symbol_lookup" }] },
  ], prior);
  assert.equal(next.requiredNextTool?.name, "unreal_symbol_lookup");
});

test("failed matching tool result does not clear required next tool", () => {
  const prior = core.buildCheckpoint([
    { role: "user", content: "fix" },
    { role: "tool", content: JSON.stringify({ requiredNextTool: "unreal_symbol_lookup" }) },
  ]);
  const next = core.buildCheckpoint([
    { role: "user", content: "fix" },
    { role: "tool", content: JSON.stringify({ requiredNextTool: "unreal_symbol_lookup" }) },
    { role: "assistant", content: "", toolCalls: [{ id: "lookup-1", name: "mcp_unreal_symbol_lookup" }] },
    { role: "tool", content: JSON.stringify({ ok: false, errorCode: "LOOKUP_FAILED" }), toolResults: [{ toolCallId: "lookup-1", content: "{\"ok\":false}" }] },
  ], prior);
  assert.equal(next.requiredNextTool?.name, "unreal_symbol_lookup");
});

test("checkpoint preserves slice, invariants, coverage, pending Automation, and sanitized failures", () => {
  const payload = {
    requiredNextTool: "run_unreal_automation_tests",
    requiredNextToolArgs: { testFilter: "Gomoku" },
    toolRoute: {
      routeHash: "route-automation",
      phase: "verifier",
      activeTools: ["run_unreal_automation_tests", "read_file"],
      selectedSlice: { sliceId: "network", files: ["Source/Demo/Network.cpp"] },
    },
    sliceProgress: {
      activeSliceId: "network",
      completedSlices: ["rules"],
      pendingSlices: ["network"],
    },
    buildVerification: {
      status: "pending_automation",
      mutationGeneration: 4,
      testFilter: "Gomoku",
    },
    invariants: ["server owns move acceptance", "clients request only"],
    automationCoverage: { count: 30, suggestedFilter: "Gomoku" },
  };
  const messages = [
    { role: "user", content: "finish the active implementation" },
    { role: "tool", content: JSON.stringify(payload) },
    { role: "assistant", content: "", toolCalls: [{ id: "auto-1", name: "run_unreal_automation_tests" }] },
    {
      role: "tool",
      content: JSON.stringify({ ok: false, errorCode: "AUTOMATION_TEST_FAILED", error: "one test failed", taskAuthorization: { authToken: "secret" } }),
      toolResults: [{ toolCallId: "auto-1", content: JSON.stringify({ ok: false, errorCode: "AUTOMATION_TEST_FAILED", error: "one test failed", taskAuthorization: { authToken: "secret" } }) }],
    },
  ];

  const checkpoint = core.buildCheckpoint(messages);
  const summary = core.summarizeOldMessages(messages, checkpoint);

  assert.equal(checkpoint.requiredNextTool?.name, "run_unreal_automation_tests");
  assert.equal(checkpoint.sliceProgress.activeSliceId, "network");
  assert.equal(checkpoint.buildVerification.status, "pending_automation");
  assert.deepEqual(checkpoint.invariants, ["server owns move acceptance", "clients request only"]);
  assert.equal(checkpoint.coverageEvidence.at(-1).automationCoverage.count, 30);
  assert.deepEqual(checkpoint.failedToolResults.at(-1), {
    tool: "run_unreal_automation_tests",
    errorCode: "AUTOMATION_TEST_FAILED",
    detail: "one test failed",
  });
  assert.match(summary, /pending_automation/);
  assert.match(summary, /AUTOMATION_TEST_FAILED/);
  assert.doesNotMatch(summary, /secret/);
});

test("checkpoint preserves compact route ownership without exposing authToken", () => {
  const messages = [
    { role: "user", content: "continue the active task" },
    {
      role: "tool",
      content: JSON.stringify({
        toolRoute: { routeHash: "route-1", phase: "executor", activeTools: ["unreal_symbol_lookup"] },
        taskAuthorization: {
          taskSessionId: "task-1",
          authToken: "must-not-survive",
          ownerCapability: "owner-capability-1",
          routeHash: "route-1",
          routePhase: "executor",
        },
      }),
    },
  ];
  const checkpoint = core.buildCheckpoint(messages);
  const summary = core.summarizeOldMessages(messages, checkpoint);
  assert.deepEqual(checkpoint.taskRouteOwnership, {
    taskSessionId: "task-1",
    ownerCapability: "owner-capability-1",
  });
  assert.match(summary, /owner-capability-1/);
  assert.doesNotMatch(summary, /must-not-survive/);
  assert.match(summary, /Do not recover, cancel, or replace/);
});

test("legacy active-route checkpoint rescans history to recover compact ownership", () => {
  const messages = [
    { role: "user", content: "continue" },
    {
      role: "tool",
      content: JSON.stringify({
        toolRoute: { routeHash: "route-legacy", phase: "executor" },
        taskAuthorization: { taskSessionId: "task-legacy", ownerCapability: "owner-legacy" },
      }),
    },
  ];
  const prior = core.buildCheckpoint(messages);
  delete prior.taskRouteOwnership;
  const next = core.buildCheckpoint([...messages, { role: "user", content: "look up the symbol" }], prior);
  assert.deepEqual(next.taskRouteOwnership, {
    taskSessionId: "task-legacy",
    ownerCapability: "owner-legacy",
  });
});

test("checkpoint normalizes LM Studio content blocks and preserves negative discovery evidence", () => {
  const activePayload = {
    activeProject: "C:\\Projects\\O-Mock\\O_Mock.uproject",
    details: { projectName: "O_Mock", projectDir: "C:\\Projects\\O-Mock" },
  };
  const searchPayload = {
    path: { displayPath: "project://Source" },
    results: [],
    fileNameResults: [],
    filesSeen: 33,
    searchComplete: true,
  };
  const messages = [
    { role: "user", content: "finish stages zero through thirteen" },
    { role: "assistant", toolCalls: [{ id: "active-1", name: "unreal_get_active_project", arguments: {} }] },
    {
      role: "tool",
      getToolCallResults() {
        return [{
          toolCallId: "active-1",
          name: "unreal_get_active_project",
          content: JSON.stringify([{ type: "text", text: JSON.stringify(activePayload) }]),
        }];
      },
    },
    {
      role: "assistant",
      toolCalls: [{
        id: "search-1",
        name: "search_files",
        arguments: { query: "GomokuMinigameSubsystem.h", path: "project://Source", matchFileNames: true },
      }],
    },
    {
      role: "tool",
      getToolCallResults() {
        return [{
          toolCallId: "search-1",
          name: "search_files",
          content: JSON.stringify([{ type: "text", text: JSON.stringify(searchPayload) }]),
        }];
      },
    },
  ];

  const checkpoint = core.buildCheckpoint(messages);
  const summary = core.summarizeOldMessages(messages, checkpoint);

  assert.equal(checkpoint.activeProject, "C:\\Projects\\O-Mock\\O_Mock.uproject");
  assert.equal(checkpoint.activeProjectName, "O_Mock");
  const search = checkpoint.evidenceFacts.find((fact) => fact.tool === "search_files");
  assert.deepEqual(search, {
    tool: "search_files",
    query: "GomokuMinigameSubsystem.h",
    path: "project://Source",
    resultCount: 0,
    fileNameResultCount: 0,
    searchComplete: true,
    matchedFiles: [],
  });
  assert.match(summary, /GomokuMinigameSubsystem\.h/);
  assert.match(summary, /resultCount":0/);
});

test("checkpoint retains bounded semantic anchors from LM Studio read_file source results", () => {
  const source = `[path-metadata: {"projectRelativePath":"Source/O_Mock/GomokuGameState.h"}]\n`
    + `[line-endings: CRLF]\n`
    + `#pragma once\n`
    + `UCLASS()\n`
    + `class AGomokuGameState : public AGameStateBase\n`
    + `{\npublic:\n`
    + `UPROPERTY(ReplicatedUsing=OnRep_Board)\n`
    + `TArray<int32> Board;\n`
    + `UFUNCTION(Server, Reliable)\n`
    + `void ServerPlaceStone(int32 X, int32 Y);\n`
    + `void OnRep_Board();\n`
    + `};\n`;
  const messages = [
    { role: "user", content: "audit the networking code" },
    {
      role: "assistant",
      toolCalls: [{ id: "read-1", name: "read_file", arguments: { path: "Source/O_Mock/GomokuGameState.h" } }],
    },
    {
      role: "tool",
      getToolCallResults() {
        return [{
          toolCallId: "read-1",
          name: "read_file",
          content: JSON.stringify([{ type: "text", text: source }]),
        }];
      },
    },
  ];

  const checkpoint = core.buildCheckpoint(messages);
  const read = checkpoint.evidenceFacts.find((fact) => fact.tool === "read_file");
  assert.equal(read.path, "Source/O_Mock/GomokuGameState.h");
  assert.ok(read.lineCount >= 12);
  assert.match(read.evidenceHash, /^[a-f0-9]{64}$/);
  assert.ok(read.semanticAnchors.some((line) => line.includes("UFUNCTION(Server, Reliable)")));
  assert.ok(read.semanticAnchors.some((line) => line.includes("ServerPlaceStone")));
  assert.match(core.summarizeOldMessages(messages, checkpoint), /ServerPlaceStone/);
});

test("cached repeat reads keep semantic anchors and emit an explicit no-reread ledger", () => {
  const path = "Source/O_Mock/GomokuGameState.cpp";
  const source = "AGomokuGameState::AGomokuGameState()\n{\n}\nvoid AGomokuGameState::OnRep_Board()\n{\n}\n";
  const repeatPayload = {
    ok: true,
    cached: true,
    repeatDetected: true,
    doNotRepeatRead: true,
    errorCode: "READ_REPEAT_DETECTED",
    content: source,
    readAttempts: 2,
  };
  const messages = [
    { role: "user", content: "audit networking" },
    { role: "assistant", toolCalls: [{ id: "read-1", name: "read_file", arguments: { path } }] },
    {
      role: "tool",
      toolResults: [{
        toolCallId: "read-1",
        name: "read_file",
        content: JSON.stringify([{ type: "text", text: source }]),
      }],
    },
    { role: "assistant", toolCalls: [{ id: "read-2", name: "read_file", arguments: { path } }] },
    {
      role: "tool",
      toolResults: [{
        toolCallId: "read-2",
        name: "read_file",
        content: JSON.stringify([{ type: "text", text: JSON.stringify(repeatPayload) }]),
      }],
    },
  ];

  const checkpoint = core.buildCheckpoint(messages);
  const reads = checkpoint.evidenceFacts.filter((fact) => fact.path === path);
  assert.equal(reads.length, 1);
  assert.equal(reads[0].repeatDetected, true);
  assert.equal(reads[0].readAttempts, 2);
  assert.ok(reads[0].semanticAnchors.some((line) => line.includes("OnRep_Board")));
  const summary = core.summarizeOldMessages(messages, checkpoint);
  assert.match(summary, /discoveryLedger=already-read unchanged files/);
  assert.match(summary, /Do not re-read these paths merely to remember them/);
});

test("unrelated complete payload does not clear required next tool", () => {
  const prior = core.buildCheckpoint([
    { role: "user", content: "fix" },
    { role: "tool", content: JSON.stringify({ requiredNextTool: "unreal_symbol_lookup" }) },
  ]);
  const next = core.buildCheckpoint([
    { role: "user", content: "fix" },
    { role: "tool", content: JSON.stringify({ requiredNextTool: "unreal_symbol_lookup" }) },
    { role: "assistant", content: "", toolCalls: [{ id: "other-1", name: "mcp_read_file" }] },
    { role: "tool", content: JSON.stringify({ ok: true, phase: "complete" }), toolResults: [{ toolCallId: "other-1", name: "mcp_read_file", content: "{\"ok\":true,\"phase\":\"complete\"}" }] },
  ], prior);
  assert.equal(next.requiredNextTool?.name, "unreal_symbol_lookup");
});

test("anonymous tool result compaction retains its matching call", () => {
  const messages = [
    { role: "system", content: "rules" },
    { role: "user", content: "objective" },
    { role: "assistant", content: "", toolCalls: [{ name: "read_file", arguments: {} }] },
    { role: "tool", content: "result", toolResults: [{ name: "read_file", content: "result" }] },
    { role: "user", content: "continue" },
  ];
  const compacted = core.compactSnapshots(messages, core.buildCheckpoint(messages), { recentCompleteTurns: 1 });
  assert.equal(core.isCompleteToolPair(compacted), true);
  assert.ok(compacted.some((message) => message.toolCalls?.some((call) => call.name === "read_file")));
});

test("anonymous result before call expands the retained tail", () => {
  const snapshots = [
    { role: "assistant", content: "", toolCalls: [{ name: "read_file", arguments: {} }] },
    { role: "tool", content: "result", toolResults: [{ name: "read_file", content: "result" }] },
  ];
  assert.equal(core.completeTailStart(snapshots, 1), 0);
});

test("explicit null required next tool clears stale state", () => {
  const prior = core.buildCheckpoint([
    { role: "user", content: "fix" },
    { role: "tool", content: JSON.stringify({ requiredNextTool: "build_unreal_project" }) },
  ]);
  const next = core.buildCheckpoint([
    { role: "user", content: "fix" },
    { role: "tool", content: JSON.stringify({ requiredNextTool: "build_unreal_project" }) },
    { role: "tool", content: JSON.stringify({ requiredNextTool: null }) },
  ], prior);
  assert.equal(next.requiredNextTool, null);
});

test("active-route sentinel clears an exact tool gate instead of becoming a fake tool", () => {
  const prior = core.buildCheckpoint([
    { role: "user", content: "build the project" },
    { role: "tool", content: JSON.stringify({ requiredNextTool: "build_unreal_project" }) },
  ]);
  const next = core.buildCheckpoint([
    { role: "user", content: "build the project" },
    { role: "tool", content: JSON.stringify({ requiredNextTool: "build_unreal_project" }) },
    {
      role: "tool",
      content: JSON.stringify({
        nextAction: "use_active_route_tool",
        nextActionArgs: { ignored: true },
      }),
    },
  ], prior);
  assert.equal(next.requiredNextTool, null);
});

test("resuming a legacy checkpoint drops a persisted active-route sentinel", () => {
  const messages = [{ role: "user", content: "build the project" }];
  const prior = core.buildCheckpoint(messages);
  prior.requiredNextTool = {
    name: "use_active_route_tool",
    reference: { sourceField: "nextAction", value: "use_active_route_tool" },
    args: { stale: true },
  };
  const next = core.buildCheckpoint([
    ...messages,
    { role: "user", content: "retry the exact build" },
  ], prior);
  assert.equal(next.requiredNextTool, null);
});

test("legacy authorization recovery sentinels do not become exact tool gates", () => {
  for (const nextAction of [
    "continue_with_current_tool_route",
    "request_fresh_authorization_or_replan",
    "retry_same_tool_with_returned_taskAuthorization",
    "start_agent_edit_task_to_apply_changes",
    "replan_autonomous_strategy",
    "quarantine_corrupt_task",
  ]) {
    const checkpoint = core.buildCheckpoint([
      { role: "user", content: "continue" },
      { role: "tool", content: JSON.stringify({ nextAction }) },
    ]);
    assert.equal(checkpoint.requiredNextTool, null, nextAction);
  }
});

test("protocol marker clears arbitrary instructional next actions", () => {
  const checkpoint = core.buildCheckpoint([
    { role: "user", content: "continue" },
    {
      role: "tool",
      content: JSON.stringify({
        nextAction: "future_server_instruction",
        nextActionIsTool: false,
      }),
    },
  ]);
  assert.equal(checkpoint.requiredNextTool, null);
});

test("architecture portfolio and repair instructions are not treated as tool names", () => {
  for (const action of [
    "collect_source_evidence_for_owner_choice",
    "resolve_ambiguous_candidates_with_rationale",
    "review_ranked_candidates_and_select",
    "resolve_architecture_contract_issues",
    "submit_exact_architecture_repairs",
    "submit_full_architecture_proposal",
    "revise_architecture_proposal",
  ]) {
    assert.equal(core.isNonToolNextAction(action), true, action);
  }
});

test("architecture repair continuity survives hard compaction without repeating a patch", () => {
  const repairRequirements = [
    {
      jsonPath: "networking.requestPath",
      constraint: "requestPath must contain three concrete source-backed hops",
    },
    {
      jsonPath: "stateInventory",
      constraint: "participant roster must reconcile AGameStateBase::PlayerArray",
    },
  ];
  const proposalPatch = {
    networking: {
      rpcOwner: "APlayerController",
      requestPath: ["client", "rpc"],
    },
    stateInventory: [{ state: "Lobby membership", owner: "AGameMode", source: "new" }],
  };
  const messages = [
    { role: "user", content: "validate the lobby architecture" },
    {
      role: "assistant",
      toolCalls: [{
        id: "arch-1",
        name: "unreal_architecture_reasoning",
        arguments: { baseProposalRevision: "r1", proposalPatch },
      }],
    },
    {
      role: "tool",
      toolResults: [{
        toolCallId: "arch-1",
        name: "unreal_architecture_reasoning",
        content: JSON.stringify({
          ok: false,
          errorCode: "ARCHITECTURE_PROPOSAL_INVALID",
          proposalRevision: "r2",
          proposalPatchApplied: true,
          proposalValidation: { ok: false, repairRequirements },
          requiredNextAction: "revise_architecture_proposal",
          nextActionIsTool: false,
        }),
      }],
    },
    {
      role: "assistant",
      toolCalls: [{
        id: "arch-2",
        name: "unreal_architecture_reasoning",
        arguments: { baseProposalRevision: "r2", proposalPatch },
      }],
    },
    {
      role: "tool",
      toolResults: [{
        toolCallId: "arch-2",
        name: "unreal_architecture_reasoning",
        content: JSON.stringify({
          ok: false,
          errorCode: "ARCHITECTURE_PROPOSAL_UNCHANGED",
          proposalRevision: "r2",
          repairSubmission: {
            mode: "proposalRepairs",
            requiredJsonPaths: ["networking.requestPath", "stateInventory"],
          },
          requiredNextAction: "revise_architecture_proposal",
          nextActionIsTool: false,
        }),
      }],
    },
  ];

  const checkpoint = core.buildCheckpoint(messages);
  assert.equal(checkpoint.architectureProposal.revision, "r2");
  assert.deepEqual(checkpoint.architectureProposal.repairRequirements, repairRequirements);
  assert.deepEqual(checkpoint.architectureProposal.lastPatchFields, ["networking", "stateInventory"]);
  assert.equal(checkpoint.architectureProposal.unchangedPatchAttempts, 1);
  assert.equal(checkpoint.architectureProposal.repairMode, "proposalRepairs");
  assert.deepEqual(
    checkpoint.architectureProposal.requiredRepairPaths,
    ["networking.requestPath", "stateInventory"],
  );
  assert.equal(checkpoint.architectureProposal.lastPatchPreview.networking.requestPath.length, 2);
  const summary = core.summarizeOldMessages(messages, checkpoint);
  assert.match(summary, /architectureProposalContinuation=/);
  assert.match(summary, /AGameStateBase::PlayerArray/);
  assert.match(summary, /never resubmit the same patch digest/);
  assert.match(summary, /one \{jsonPath,value\} entry per requiredRepairPaths item/);
});

test("architecture exact-path repairs survive hard compaction", () => {
  const proposalRepairs = [
    {
      jsonPath: "networking.requestPath",
      value: ["client input", "owned controller RPC", "server authority"],
    },
    { jsonPath: "migrationPlan", value: ["add compatible request path"] },
  ];
  const checkpoint = core.buildCheckpoint([
    { role: "user", content: "continue architecture repair" },
    {
      role: "assistant",
      toolCalls: [{
        id: "repair-1",
        name: "unreal_architecture_reasoning",
        arguments: { baseProposalRevision: "r2", proposalRepairs },
      }],
    },
    {
      role: "tool",
      toolResults: [{
        toolCallId: "repair-1",
        name: "unreal_architecture_reasoning",
        content: JSON.stringify({
          ok: false,
          errorCode: "ARCHITECTURE_PROPOSAL_INVALID",
          proposalRevision: "r3",
          proposalRepairsApplied: true,
          proposalValidation: {
            ok: false,
            repairRequirements: [{
              jsonPath: "stateInventory",
              constraint: "remove the duplicate truth source",
            }],
          },
          repairSubmission: {
            mode: "proposalRepairs",
            requiredJsonPaths: ["stateInventory"],
          },
        }),
      }],
    },
  ]);

  assert.deepEqual(
    checkpoint.architectureProposal.lastPatchFields,
    ["networking.requestPath", "migrationPlan"],
  );
  assert.equal(
    checkpoint.architectureProposal.lastPatchPreview[0].jsonPath,
    "networking.requestPath",
  );
  assert.equal(checkpoint.architectureProposal.repairMode, "proposalRepairs");
  assert.deepEqual(checkpoint.architectureProposal.requiredRepairPaths, ["stateInventory"]);
});

test("architecture full replan survives hard compaction without patch instructions", () => {
  const messages = [
    { role: "user", content: "blind lobby architecture validation" },
    {
      role: "assistant",
      toolCalls: [{
        id: "replan-1",
        name: "unreal_architecture_reasoning",
        arguments: { proposal: { decision: "put all lobby state in GameState" } },
      }],
    },
    {
      role: "tool",
      toolResults: [{
        toolCallId: "replan-1",
        name: "unreal_architecture_reasoning",
        content: JSON.stringify({
          ok: false,
          errorCode: "ARCHITECTURE_PROPOSAL_INVALID",
          proposalRevision: "r-full",
          graphEvidence: { sourceSnapshotFingerprint: "source-fingerprint" },
          proposalValidation: {
            ok: false,
            repairStrategy: "full_replan",
            designContract: { requiresFullReplan: true },
            repairRequirements: [{ jsonPath: "proposal", constraint: "replan" }],
          },
          repairSubmission: { mode: "fullProposal", requiredJsonPaths: [] },
          requiredNextAction: "submit_full_architecture_proposal",
        }),
      }],
    },
  ];

  const checkpoint = core.buildCheckpoint(messages);
  assert.equal(checkpoint.architectureProposal.repairStrategy, "full_replan");
  assert.equal(checkpoint.architectureProposal.requiresFullReplan, true);
  assert.equal(checkpoint.architectureProposal.repairMode, "fullProposal");
  assert.equal(checkpoint.architectureProposal.sourceSnapshotFingerprint, "source-fingerprint");
  const summary = core.summarizeOldMessages(messages, checkpoint);
  assert.match(summary, /submit one complete independently derived proposal/i);
  assert.match(summary, /Reuse retained direct-source evidence while sourceSnapshotFingerprint is unchanged/);
  assert.match(summary, /Re-read only when source changed, required evidence is missing/);
  assert.match(summary, /Do not use proposalPatch\/proposalRepairs/);
  assert.doesNotMatch(summary, /one \{jsonPath,value\} entry/);
});
