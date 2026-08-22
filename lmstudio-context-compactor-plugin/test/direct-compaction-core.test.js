"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const core = require("../src/direct-compaction-core.js");

function message(role, text, extra = {}) {
  return { role, text, hasFiles: false, toolRequests: [], toolResults: [], ...extra };
}

test("latest real user request stays authoritative without promoting an older objective", () => {
  const old = "Implement the old combat feature";
  const latest = "시네마틱 C++ 구조만 분석해. 파일은 수정하지 마.";
  const result = core.buildCheckpoint([
    message("system", "system"),
    message("user", old),
    message("assistant", "Old answer finished."),
    message("user", latest),
  ], { recentCompleteTurns: 0 });

  assert.equal(result.latestUserVerbatim, latest);
  assert.equal(result.memory.currentUserRequestVerbatim, latest);
  assert.equal(result.retainedIndexes.includes(3), true);
  assert.equal(result.checkpoint.includes(latest), true);
  assert.equal(result.memory.priorUserRequestsForContinuation[0].text, old);
  assert.match(result.checkpoint, /bounded inactive context/);
});

test("hard compaction preserves the prior request needed to interpret a continuation", () => {
  const objective = "Project_MJS의 시네마틱 C++ 시스템을 끝까지 분석해.";
  const continuation = "좋아, 계속 진행해.";
  const result = core.buildCheckpoint([
    message("system", "system"),
    message("user", objective),
    message("assistant", "Public/Cinematic 목록을 확인했습니다."),
    message("user", continuation),
  ], { recentCompleteTurns: 0 });

  assert.equal(result.memory.currentUserRequestVerbatim, continuation);
  assert.deepEqual(result.memory.priorUserRequestsForContinuation, [{
    messageIndex: 1,
    text: objective,
  }]);
  assert.match(result.checkpoint, /시네마틱 C\+\+ 시스템/u);
  assert.match(result.checkpoint, /explicitly refers to prior work/u);
});

test("hard compaction preserves a bounded multi-hop continuation chain", () => {
  const objective = "Analyze the Project_MJS cinematic Director C++ architecture and fix the crash in its camera handoff.";
  const result = core.buildCheckpoint([
    message("system", "system"),
    message("user", objective),
    message("assistant", "Should I continue into the handoff implementation?"),
    message("user", "Yes."),
    message("assistant", "I found the call site. Should I apply the safe fix?"),
    message("user", "Continue."),
  ], { recentCompleteTurns: 0 });

  assert.equal(result.memory.currentUserRequestVerbatim, "Continue.");
  assert.deepEqual(result.memory.priorUserRequestsForContinuation, [
    { messageIndex: 1, text: objective },
    { messageIndex: 3, text: "Yes." },
  ]);
  assert.match(result.checkpoint, /camera handoff/u);
  assert.match(result.checkpoint, /bounded inactive context/u);
  assert.deepEqual(result.retainedIndexes, [0, 5]);
});

test("hard compaction keeps one substantive anchor beyond any fixed ellipsis horizon", () => {
  const objective = "Analyze the Project_MJS cinematic Director C++ architecture and fix the crash in its camera handoff.";
  const result = core.buildCheckpoint([
    message("system", "system"),
    message("user", objective),
    message("assistant", "First question?"),
    message("user", "Yes."),
    message("assistant", "Second question?"),
    message("user", "Continue."),
    message("assistant", "Third question?"),
    message("user", "Go on."),
    message("assistant", "Apply the next diagnostic?"),
    message("user", "Continue."),
  ], { recentCompleteTurns: 0 });

  assert.equal(result.memory.currentUserRequestVerbatim, "Continue.");
  assert.deepEqual(result.memory.priorUserRequestsForContinuation.map((item) => item.text), ["Yes.", "Continue.", "Go on."]);
  assert.deepEqual(result.memory.olderContinuationAnchor, { messageIndex: 1, text: objective });
  assert.match(result.checkpoint, /camera handoff/u);
  assert.deepEqual(result.retainedIndexes, [0, 9]);
});

test("latest constraints and actually unanswered questions survive compaction", () => {
  const result = core.buildCheckpoint([
    message("user", "Never modify Generated files. 어느 프로젝트를 선택해야 해?"),
    message("assistant", "I need more context."),
    message("user", "기존 구조보다 실제 사용성을 우선하라. 빌드는 지금 가능한가?"),
  ], { recentCompleteTurns: 0 });

  assert.match(result.memory.latestUserConstraints.join("\n"), /사용성을 우선/u);
  assert.doesNotMatch(result.memory.latestUserConstraints.join("\n"), /Never modify/u);
  assert.match(JSON.stringify(result.memory.historicalUserConstraintEvidence), /Never modify/u);
  assert.match(JSON.stringify(result.memory.openQuestionEvidence), /빌드는 지금 가능한가/u);
  assert.doesNotMatch(JSON.stringify(result.memory.openQuestionEvidence), /어느 프로젝트/u);
});

test("a newer request does not promote a conflicting old constraint to active status", () => {
  const result = core.buildCheckpoint([
    message("user", "Never modify Source/Old.cpp."),
    message("assistant", "Understood; no files were changed."),
    message("user", "Now replace Source/Old.cpp exactly as requested."),
  ], { recentCompleteTurns: 0 });

  assert.deepEqual(result.memory.latestUserConstraints, []);
  assert.match(JSON.stringify(result.memory.historicalUserConstraintEvidence), /Never modify Source\/Old\.cpp/u);
  assert.equal(result.memory.currentUserRequestVerbatim, "Now replace Source/Old.cpp exactly as requested.");
});

test("older tool outcomes retain file/build facts but strip workflow control", () => {
  const result = core.buildCheckpoint([
    message("user", "old request"),
    message("assistant", "", { toolRequests: [{ name: "replace_in_file" }] }),
    message("tool", "", {
      toolResults: [{
        content: JSON.stringify({
          ok: true,
          operation: "replaced",
          path: "project://Source/Foo.cpp",
          sha256: "a".repeat(64),
          control: { requiredTool: "static_validate_project", allowedTools: ["static_validate_project"] },
          taskAuthorization: { ownerCapability: "secret" },
        }),
      }],
    }),
    message("assistant", "done"),
    message("user", "new request"),
  ], { recentCompleteTurns: 0 });

  const serialized = JSON.stringify(result.memory);
  assert.match(serialized, /project:\/\/Source\/Foo\.cpp/);
  assert.match(serialized, /replaced/);
  assert.doesNotMatch(serialized, /ownerCapability|requiredTool|allowedTools|taskAuthorization/);
});

test("malformed tool results are replaced instead of replaying truncated control", () => {
  const result = core.buildCheckpoint([
    message("user", "old request"),
    message("tool", "", {
      toolResults: [{
        content: '{"ok":true,"requiredNextTool":"read_file","allowed_tools":["read_file"],"phase":"synthesis","commitEligible":true',
      }],
    }),
    message("assistant", "done"),
    message("user", "new request"),
  ], { recentCompleteTurns: 0 });

  const serialized = JSON.stringify(result.memory);
  assert.match(serialized, /malformed tool result omitted/);
  assert.doesNotMatch(serialized, /requiredNextTool|allowed_tools|read_file|synthesis|commitEligible/i);
  assert.doesNotMatch(result.checkpoint, /requiredNextTool|allowed_tools|read_file|commitEligible/i);
});

test("retained factual strings neutralize control tokens regardless of casing or snake case", () => {
  const parsed = core.parseToolResult(JSON.stringify({
    ok: false,
    status: "failed",
    summary: "REQUIRED_NEXT_TOOL and RequiredNextTool disagree with allowed_tools",
    message: "Task_Authorization exposed PhaseState, STATE_HASH, and commit_eligible",
    errorCode: "TASK_TOOL_NOT_ACTIVE",
  }));

  const serialized = JSON.stringify(parsed);
  assert.equal(parsed.ok, false);
  assert.equal(parsed.status, "failed");
  assert.match(serialized, /control-token-omitted/);
  assert.doesNotMatch(
    serialized,
    /required_?next_?tool|allowed_?tools|task_?authorization|phase_?state|state_?hash|commit_?eligible|task_?tool_?not_?active/i,
  );
});

test("phase and state objects cannot become an unfiltered JSON fallback", () => {
  const parsed = core.parseToolResult(JSON.stringify({
    phase: "synthesis",
    phase_state: { required_next_tool: "read_file" },
    STATE: "ready",
    stateHash: "secret",
    debugBlob: { Allowed_Tools: ["read_file"] },
  }));

  assert.deepEqual(parsed, { summary: "tool result contained no retained factual fields" });
});

test("tool-result redaction never rewrites the latest real user message", () => {
  const latest = "Explain why requiredNextTool and phase appeared in the old tool result.";
  const result = core.buildCheckpoint([
    message("user", "old request"),
    message("tool", "", {
      toolResults: [{ content: JSON.stringify({ message: "requiredNextTool forced phase=synthesis" }) }],
    }),
    message("user", latest),
  ], { recentCompleteTurns: 0 });

  assert.equal(result.memory.currentUserRequestVerbatim, latest);
  assert.equal(result.latestUserVerbatim, latest);
  assert.match(result.checkpoint, /Explain why requiredNextTool and phase appeared/u);
  assert.doesNotMatch(result.memory.recentOlderToolOutcomes[0], /requiredNextTool|phase|synthesis/i);
});

test("bundle mutation results retain each modified path and hash", () => {
  const result = core.buildCheckpoint([
    message("user", "old request"),
    message("tool", "", {
      toolResults: [{
        content: JSON.stringify({
          ok: true,
          operation: "bundle_applied",
          files: [
            { path: "project://Source/A.cpp", sha256: "a".repeat(64) },
            { path: "project://Source/B.h", sha256: "b".repeat(64) },
          ],
        }),
      }],
    }),
    message("assistant", "done"),
    message("user", "new request"),
  ], { recentCompleteTurns: 0 });

  assert.deepEqual(result.memory.modifiedOrObservedFiles.map((item) => item.path), [
    "project://Source/A.cpp",
    "project://Source/B.h",
  ]);
  assert.equal(result.memory.modifiedOrObservedFiles[1].sha256, "b".repeat(64));
});

test("system messages, latest turn, and file-bearing messages are retained individually", () => {
  const result = core.buildCheckpoint([
    message("system", "system A"),
    message("user", "image evidence", { hasFiles: true }),
    message("assistant", "old answer"),
    message("user", "latest"),
  ], { recentCompleteTurns: 0 });

  assert.deepEqual(result.retainedIndexes, [0, 1, 3]);
  assert.equal(result.omittedMessageCount, 1);
});

test("an old attachment does not pin every later turn and disable compaction", () => {
  const messages = [message("system", "system"), message("user", "attachment", { hasFiles: true })];
  for (let index = 0; index < 50; index += 1) {
    messages.push(message("assistant", `old answer ${index}`));
    messages.push(message("user", `old follow-up ${index}`));
  }
  const result = core.buildCheckpoint(messages, { recentCompleteTurns: 1 });

  assert.equal(result.retainedIndexes.includes(1), true);
  assert.ok(result.tailStart > 90, result.tailStart);
  assert.ok(result.omittedMessageCount > 90, result.omittedMessageCount);
});

test("previous final response is recorded as evidence without claiming objective completion", () => {
  const result = core.buildCheckpoint([
    message("user", "first"),
    message("assistant", "complete answer"),
    message("user", "second"),
  ], { recentCompleteTurns: 0 });
  assert.deepEqual(result.memory.previousTurnFinalResponseEvidence, { present: true, messageIndex: 1 });
  assert.doesNotMatch(result.checkpoint, /commit|ack|route|planner|synthesis/i);
});

test("token pressure is the only normal compaction decision", () => {
  assert.equal(core.shouldCompact({ remainingTokens: 13999, messageCount: 3 }, { enabled: true, softRemainingTokens: 14000 }), true);
  assert.equal(core.shouldCompact({ remainingTokens: 14001, messageCount: 100 }, { enabled: true, softRemainingTokens: 14000 }), false);
  assert.equal(core.shouldCompact({ remainingTokens: 100, messageCount: 100 }, { enabled: false, softRemainingTokens: 14000 }), false);
  assert.equal(core.shouldCompact({ remainingTokens: 100, messageCount: 100 }, { enabled: true, observeOnly: true, softRemainingTokens: 14000 }), false);
});

test("fallback message threshold applies only when remaining token measurement is unavailable", () => {
  assert.equal(core.shouldCompact({ remainingTokens: Number.NaN, messageCount: 24 }, { compactAboveMessageCount: 24 }), true);
  assert.equal(core.shouldCompact({ remainingTokens: Number.NaN, messageCount: 23 }, { compactAboveMessageCount: 24 }), false);
});

test("actual Qwen E2E transcript keeps the cinematic objective through three hard compactions", () => {
  const fixture = JSON.parse(fs.readFileSync(
    path.join(__dirname, "fixtures", "qwen-direct-e2e-continuity.json"),
    "utf8",
  ));
  const fromFixture = (item) => message(item.role, item.text || "", {
    toolResults: item.toolResults || [],
  });
  const hardCompact = (messages) => {
    const result = core.buildCheckpoint(messages, {
      recentCompleteTurns: 0,
      maxCheckpointChars: 16000,
    });
    const retained = new Set(result.retainedIndexes);
    return {
      result,
      messages: [
        ...messages.filter((item, index) => item.role === "system" && retained.has(index)),
        message("system", result.checkpoint),
        ...messages.filter((item, index) => item.role !== "system" && retained.has(index)),
      ],
    };
  };

  let history = fixture.initialMessages.map(fromFixture);
  const first = hardCompact(history);
  history = [...first.messages, ...fixture.afterFirstCompaction.map(fromFixture)];
  const second = hardCompact(history);
  history = [...second.messages, ...fixture.afterSecondCompaction.map(fromFixture)];
  const third = hardCompact(history);
  const objective = "시네마틱 C++ 시스템에 대해서 더 구체적으로 분석해줘";
  const project = "C:\\Users\\sster\\Documents\\Git\\Project_MJS\\Project_MJS.uproject";

  assert.equal(first.result.memory.latestUserMessage, objective);
  assert.equal(first.result.memory.activeObjective.text, objective);
  assert.equal(first.result.memory.activeProject.descriptor, project);
  assert.equal(first.result.memory.recentRawTail.length, 4);
  for (const result of [second.result, third.result]) {
    assert.equal(result.memory.latestUserMessage, "어 진행해");
    assert.equal(result.memory.activeObjective.text, objective);
    assert.equal(result.memory.continuationAntecedent.text, objective);
    assert.equal(result.memory.activeProject.descriptor, project);
    assert.ok(result.memory.recentRawTail.length >= 4 && result.memory.recentRawTail.length <= 8);
    assert.doesNotMatch(JSON.stringify(result.memory), /requiredTool|allowedTools|taskAuthorization|synthesisLatch/iu);
  }
  assert.match(third.result.memory.currentWorkStatus.lastAssistantUpdate.text, /빌드 결과/u);
  assert.match(
    third.result.memory.completedOrArchivedObjectives.map((item) => item.text).join("\n"),
    /지금 무슨 프로젝트지/u,
  );
  assert.notEqual(third.result.memory.activeObjective.text, "지금 무슨 프로젝트지");
});

test("emergency checkpoint compaction always emits parseable continuity JSON", () => {
  const result = core.buildCheckpoint([
    message("user", `긴 요청 ${"가".repeat(30000)}`),
    message("assistant", `진행 상황 ${"나".repeat(30000)}`),
    message("user", "좋아, 계속 진행해"),
  ], { maxCheckpointChars: 2000 });
  const marker = result.checkpoint.indexOf("[Direct continuity state v1]");
  const jsonStart = result.checkpoint.indexOf("{", marker);
  const parsed = JSON.parse(result.checkpoint.slice(jsonStart));

  assert.equal(parsed.schemaVersion, 1);
  assert.equal(parsed.authority, "factual_memory_only");
  assert.equal(parsed.latestUserMessageVerbatimRetainedSeparately, true);
  assert.match(parsed.activeObjective.text, /^긴 요청/u);
});
