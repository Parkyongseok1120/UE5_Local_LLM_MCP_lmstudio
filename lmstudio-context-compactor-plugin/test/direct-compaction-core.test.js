"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const core = require("../src/direct-compaction-core.js");

function message(role, text, extra = {}) {
  return { role, text, hasFiles: false, toolRequests: [], toolResults: [], ...extra };
}

const EPHEMERAL_KEY_TOKENS = new Set([
  "fileversionreceipt",
  "mutationreceipt",
  "receiptexpiresat",
  "receiptowner",
  "registryobservationversion",
  "snapshotexpiresat",
  "snapshotowner",
  "snapshotreceipt",
  "snapshotversion",
]);

function assertNoExactEphemeralKeys(value) {
  if (Array.isArray(value)) {
    for (const item of value) assertNoExactEphemeralKeys(item);
    return;
  }
  if (!value || typeof value !== "object") return;
  for (const [key, item] of Object.entries(value)) {
    const normalized = key.replace(/[^A-Za-z0-9]/gu, "").toLowerCase();
    assert.equal(EPHEMERAL_KEY_TOKENS.has(normalized), false, `ephemeral key survived: ${key}`);
    assertNoExactEphemeralKeys(item);
  }
}

function assertNoDurableFileCapability(result) {
  const durable = `${JSON.stringify(result.memory)}\n${result.checkpoint}`;
  assert.doesNotMatch(durable, /fvr1_[A-Za-z0-9_-]+/iu);
  assertNoExactEphemeralKeys(result.memory);
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
          absolutePath: "C:\\Work\\Game\\Source\\Foo.cpp",
          activeProject: "C:\\Work\\Game\\Game.uproject",
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
          activeProject: "C:\\Work\\BundleGame\\BundleGame.uproject",
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
  assert.equal(result.memory.modifiedOrObservedFiles[1].sha256AtObservation, "b".repeat(64));
  assert.equal(result.memory.modifiedOrObservedFiles[1].mutationSnapshotState, "fresh_read_required");
});

test("hard compaction drops a top-level file receipt and keeps only non-actionable file facts", () => {
  const project = "C:\\Projects\\DirectTest\\Project_MJS\\Project_MJS.uproject";
  const absolutePath = "C:\\Projects\\DirectTest\\Project_MJS\\Source\\Project_MJS\\Private\\Character\\SharedComponent\\HealthComponent.cpp";
  const result = core.buildCheckpoint([
    message("user", "Project_MJS의 사망 및 부활 파이프라인 구현을 계속해."),
    message("tool", "", { toolResults: [{ content: JSON.stringify({
      ok: true,
      operation: "replaced",
      path: "project://Source/Project_MJS/Private/Character/SharedComponent/HealthComponent.cpp",
      absolutePath,
      activeProject: project,
      sha256: "a".repeat(64),
      fileVersionReceipt: "fvr1_health_cpp_live_capability",
      snapshotVersion: 12,
      snapshotCapturedAt: "2026-08-22T01:23:45.000Z",
    }) }] }),
    message("assistant", "HealthComponent.cpp was updated. Use receipt fvr1_health_cpp_live_capability for the next edit."),
    message("user", "좋아, 계속 진행해."),
  ], { recentCompleteTurns: 0 });

  assertNoDurableFileCapability(result);
  assert.deepEqual(result.memory.modifiedOrObservedFiles, [{
    canonicalProject: project,
    canonicalProjectRoot: "C:\\Projects\\DirectTest\\Project_MJS",
    canonicalPath: absolutePath,
    path: "project://Source/Project_MJS/Private/Character/SharedComponent/HealthComponent.cpp",
    observationState: "modified",
    sha256AtObservation: "a".repeat(64),
    lastObservedAt: "2026-08-22T01:23:45.000Z",
    mutationSnapshotState: "fresh_read_required",
  }]);
});

test("nested bundle results cannot preserve receipts or registry ordering counters", () => {
  const project = "C:\\Work\\CloneA\\Game.uproject";
  const result = core.buildCheckpoint([
    message("user", "두 파일 변경을 이어서 완료해."),
    message("tool", "", { toolResults: [{ content: JSON.stringify({
      ok: true,
      operation: "bundle_applied",
      activeProject: project,
      files: [
        {
          path: "project://Source/A.cpp",
          sha256: "a".repeat(64),
          fileVersionReceipt: "fvr1_bundle_a",
          snapshotVersion: 31,
          snapshotCapturedAt: "2026-08-22T02:00:00.000Z",
        },
        {
          path: "project://Source/B.h",
          sha256: "b".repeat(64),
          FILE_VERSION_RECEIPT: "fvr1_bundle_b",
          snapshot_version: 32,
        },
      ],
    }) }] }),
    message("user", "계속해."),
  ], { recentCompleteTurns: 0 });

  assertNoDurableFileCapability(result);
  assert.equal(result.memory.modifiedOrObservedFiles.length, 2);
  assert.deepEqual(
    result.memory.modifiedOrObservedFiles.map((item) => item.mutationSnapshotState),
    ["fresh_read_required", "fresh_read_required"],
  );
});

test("assistant progress text loses executable receipt instructions without losing the completed fact", () => {
  const result = core.buildCheckpoint([
    message("user", "Implement the death pipeline."),
    message("assistant", "HealthComponent.cpp is implemented; retry with this receipt fvr1_retry_me for the header. The build has not run yet."),
    message("user", "Continue."),
  ], { recentCompleteTurns: 0 });

  assertNoDurableFileCapability(result);
  const update = result.memory.currentWorkStatus.lastAssistantUpdate.text;
  assert.match(update, /HealthComponent\.cpp is implemented/iu);
  assert.match(update, /fresh file snapshot required before mutation/iu);
  assert.match(update, /build has not run yet/iu);
});

test("mixed assistant prose cannot carry a receipt instruction across hard compaction", () => {
  const result = core.buildCheckpoint([
    message("user", "Implement the death pipeline."),
    message("assistant", "Diagnose the failure, then retry with the previous receipt for the header."),
    message("user", "Continue."),
  ], { recentCompleteTurns: 0 });
  const durable = JSON.stringify(result.memory);

  assert.match(durable, /fresh file snapshot required before mutation/iu);
  assert.doesNotMatch(durable, /retry with the previous receipt/iu);
  assertNoDurableFileCapability(result);
});

test("a prior raw tail is sanitized before it is inherited", () => {
  const prior = {
    schemaVersion: 1,
    authority: "factual_memory_only",
    latestUserMessage: "Implement the death pipeline.",
    activeObjective: { kind: "user_request", status: "active", text: "Implement the death pipeline.", source: "current_history" },
    currentWorkStatus: {},
    unresolvedItems: [],
    recentRawTail: [{ role: "assistant", text: "The current valid receipt is fvr1_prior_tail. Use it now." }],
  };
  const result = core.buildCheckpoint([
    message("system", `[Direct continuity state v1]\n${JSON.stringify(prior)}`),
    message("user", "Continue."),
  ], { recentCompleteTurns: 0 });

  assertNoDurableFileCapability(result);
  assert.match(JSON.stringify(result.memory.recentRawTail), /fresh file snapshot required before mutation/iu);
  assert.equal(result.memory.schemaVersion, 2);
  assert.match(result.checkpoint, /\[Direct continuity state v2\]/u);
});

test("prior unresolved items and assistant pending evidence cannot replay a receipt", () => {
  const prior = {
    schemaVersion: 1,
    authority: "factual_memory_only",
    latestUserMessage: "Implement the death pipeline.",
    activeObjective: { kind: "user_request", status: "active", text: "Implement the death pipeline.", source: "current_history" },
    currentWorkStatus: {
      lastAssistantUpdate: { text: "Use fvr1_prior_update in the next mutation.", source: "assistant_history" },
    },
    unresolvedItems: [{ kind: "assistant_progress_evidence", text: "Need to retry with this receipt fvr1_prior_pending." }],
    recentRawTail: [],
  };
  const result = core.buildCheckpoint([
    message("system", `[Direct continuity state v1]\n${JSON.stringify(prior)}`),
    message("user", "Continue."),
  ], { recentCompleteTurns: 0 });

  assertNoDurableFileCapability(result);
  assert.match(JSON.stringify(result.memory.unresolvedItems), /fresh file snapshot required before mutation/iu);
});

test("three hard compactions preserve the objective while never reviving an old receipt", () => {
  const objective = "Project_MJS의 사망, 피격, 부활 파이프라인을 끝까지 구현하고 빌드해.";
  const compact = (history) => {
    const result = core.buildCheckpoint(history, { recentCompleteTurns: 0, maxCheckpointChars: 16000 });
    const retained = new Set(result.retainedIndexes);
    return {
      result,
      history: [
        ...history.filter((item, index) => item.role === "system" && retained.has(index)),
        message("system", result.checkpoint),
        ...history.filter((item, index) => item.role !== "system" && retained.has(index)),
      ],
    };
  };

  let history = [
    message("user", objective),
    message("tool", "", { toolResults: [{ content: JSON.stringify({
      ok: true,
      path: "project://Source/HealthComponent.h",
      operation: "observed",
      sha256: "c".repeat(64),
      fileVersionReceipt: "fvr1_health_header_v1",
      snapshotVersion: 1,
    }) }] }),
    message("assistant", "I will use fvr1_health_header_v1 for HealthComponent.h next."),
    message("user", "계속 진행해."),
  ];
  const first = compact(history);
  assertNoDurableFileCapability(first.result);
  assert.doesNotMatch(JSON.stringify(first.result.memory), /\b(?:use|reuse|pass)\b[^.!?]{0,100}ephemeral file capability omitted/iu);
  assert.match(JSON.stringify(first.result.memory), /fresh file snapshot required before mutation/iu);
  history = [...first.history, message("assistant", "The receipt for this file is fvr1_after_first; retry with this receipt."), message("user", "계속해.")];
  const second = compact(history);
  assertNoDurableFileCapability(second.result);
  history = [...second.history, message("assistant", "Current valid receipt: fvr1_after_second."), message("user", "진행해.")];
  const third = compact(history);
  assertNoDurableFileCapability(third.result);

  for (const round of [first, second, third]) {
    assert.equal(round.result.memory.activeObjective.text, objective);
  }
});

test("emergency-size checkpoint also removes receipts from every compressed surface", () => {
  const result = core.buildCheckpoint([
    message("user", `Implement the combat pipeline ${"goal ".repeat(5000)}`),
    message("assistant", `Use receipt fvr1_emergency_capability for this file. ${"progress ".repeat(5000)}`),
    message("user", "Continue."),
  ], { recentCompleteTurns: 0, maxCheckpointChars: 2000 });

  assertNoDurableFileCapability(result);
  const marker = result.checkpoint.indexOf("[Direct continuity state v2]");
  assert.doesNotThrow(() => JSON.parse(result.checkpoint.slice(result.checkpoint.indexOf("{", marker))));
});

test("receipt-safety diagnosis remains the active objective through an emergency continuation", () => {
  const objective = [
    "Diagnose why receipt reuse crosses same-name clones and fix canonical project/path association.",
    "Retain the exact project facts while preserving the current objective through hard compaction.",
    "CRITICAL: preserve canonical file observations and never redesign the server/controller.",
  ].join(" ");
  const result = core.buildCheckpoint([
    message("user", objective),
    message("assistant", `Evidence gathered. ${"progress ".repeat(5000)}`),
    message("user", "Continue."),
  ], { recentCompleteTurns: 0, maxCheckpointChars: 2000 });
  const marker = result.checkpoint.indexOf("[Direct continuity state v2]");
  const parsed = JSON.parse(result.checkpoint.slice(result.checkpoint.indexOf("{", marker)));

  assert.deepEqual(result.retainedIndexes, [2]);
  assert.equal(result.memory.activeObjective.text, objective);
  assert.equal(parsed.activeObjective.text, objective);
  assert.match(parsed.activeObjective.text, /CRITICAL: preserve canonical file observations/u);
  assert.ok(result.checkpoint.length <= 2000, result.checkpoint.length);
});

test("emergency rendering budgets canonical observations instead of imposing a two-file cap", () => {
  const project = "C:\\Work\\Emergency\\Emergency.uproject";
  const reads = Array.from({ length: 6 }, (_, index) => message("tool", "", {
    toolResults: [{ content: JSON.stringify({
      ok: true,
      activeProject: project,
      path: `project://Source/File${index}.cpp`,
      absolutePath: `C:\\Work\\Emergency\\Source\\File${index}.cpp`,
      sha256: String(index).repeat(64),
      fileVersionReceipt: `fvr1_emergency_${index}`,
      snapshotVersion: index + 1,
    }) }],
  }));
  const result = core.buildCheckpoint([
    message("user", "Keep the active objective and as many exact canonical file observations as fit."),
    ...reads,
    message("assistant", `Analysis complete. ${"large progress ".repeat(5000)}`),
    message("user", "Continue."),
  ], { recentCompleteTurns: 0, maxCheckpointChars: 4000 });
  const marker = result.checkpoint.indexOf("[Direct continuity state v2]");
  const parsed = JSON.parse(result.checkpoint.slice(result.checkpoint.indexOf("{", marker)));

  assert.ok(parsed.currentWorkStatus.modifiedOrObservedFiles.length > 2);
  assert.ok(parsed.currentWorkStatus.modifiedOrObservedFiles.every((item) => (
    item.canonicalProject === project && item.mutationSnapshotState === "fresh_read_required"
  )));
  for (const legacyMirror of [
    "recentOlderToolOutcomes",
    "modifiedOrObservedFiles",
    "recentBuildOrTestState",
  ]) {
    assert.equal(Object.hasOwn(parsed, legacyMirror), false);
  }
  assert.ok(result.checkpoint.length <= 4000, result.checkpoint.length);
});

test("emergency rendering keeps its hard size bound with an oversized inherited project descriptor", () => {
  const descriptor = `C:\\${"VeryLongProjectSegment\\".repeat(120)}Game.uproject`;
  const prior = {
    schemaVersion: 2,
    authority: "factual_memory_only",
    latestUserMessage: "Inspect the project.",
    activeObjective: { kind: "user_request", status: "active", text: "Inspect the project.", source: "prior_checkpoint" },
    activeProject: { descriptor, root: descriptor.slice(0, -"Game.uproject".length), source: "tool_result_fact" },
    currentWorkStatus: { recentToolOutcomes: [], modifiedOrObservedFiles: [], recentBuildOrTestState: [] },
    unresolvedItems: [],
    completedOrArchivedObjectives: [],
    recentRawTail: [],
  };
  const result = core.buildCheckpoint([
    message("system", `[Direct continuity state v2]\n${JSON.stringify(prior)}`),
    message("assistant", "Continue the exact objective."),
    message("user", "Continue."),
  ], { recentCompleteTurns: 0, maxCheckpointChars: 2000 });
  const marker = result.checkpoint.indexOf("[Direct continuity state v2]");
  const parsed = JSON.parse(result.checkpoint.slice(result.checkpoint.indexOf("{", marker)));

  assert.ok(result.checkpoint.length <= 2000, result.checkpoint.length);
  assert.equal(parsed.activeObjective.text, "Inspect the project.");
  assert.equal(parsed.activeProject, null);
});

test("same-name clones remain distinct factual observations and neither is mutation-authoritative", () => {
  const gitProject = "C:\\Projects\\GitClone\\Project_MJS\\Project_MJS.uproject";
  const githubProject = "C:\\Projects\\GithubClone\\Project_MJS\\Project_MJS.uproject";
  const readResult = (activeProject, absolutePath, receipt, hash) => message("tool", "", {
    toolResults: [{ content: JSON.stringify({
      ok: true,
      path: "project://Source/Project_MJS/Public/Character/SharedComponent/HealthComponent.h",
      absolutePath,
      activeProject,
      sha256: hash,
      fileVersionReceipt: receipt,
      snapshotVersion: 1,
      snapshotCapturedAt: "2026-08-22T03:00:00.000Z",
    }) }],
  });
  const result = core.buildCheckpoint([
    message("user", "두 checkout의 관찰 사실을 구분해."),
    readResult(githubProject, "C:\\Projects\\GithubClone\\Project_MJS\\Source\\Project_MJS\\Public\\Character\\SharedComponent\\HealthComponent.h", "fvr1_github", "a".repeat(64)),
    readResult(gitProject, "C:\\Projects\\GitClone\\Project_MJS\\Source\\Project_MJS\\Public\\Character\\SharedComponent\\HealthComponent.h", "fvr1_git", "b".repeat(64)),
    message("user", "Continue with the proven facts only."),
  ], { recentCompleteTurns: 0 });

  assertNoDurableFileCapability(result);
  assert.equal(result.memory.modifiedOrObservedFiles.length, 2);
  assert.deepEqual(
    new Set(result.memory.modifiedOrObservedFiles.map((item) => item.canonicalProject)),
    new Set([githubProject, gitProject]),
  );
  assert.ok(result.memory.modifiedOrObservedFiles.every((item) => item.mutationSnapshotState === "fresh_read_required"));
  assert.equal(result.memory.activeProject.root, "C:\\Projects\\GitClone\\Project_MJS");
});

test("a per-call exact project keeps a write on that clone without changing the active clone", () => {
  const cloneA = "C:\\Work\\CloneA\\Game.uproject";
  const cloneB = "C:\\Work\\CloneB\\Game.uproject";
  const result = core.buildCheckpoint([
    message("user", "Create one file in Clone B while Clone A remains active."),
    message("tool", "", { toolResults: [{ content: JSON.stringify({
      ok: true,
      activeProject: cloneA,
      path: "project://Source/Existing.cpp",
      absolutePath: "C:\\Work\\CloneA\\Source\\Existing.cpp",
      sha256: "a".repeat(64),
    }) }] }),
    message("assistant", "", { toolRequests: [{
      id: "write-clone-b",
      type: "function",
      name: "write_file",
      arguments: { project: cloneB, path: "Source/New.cpp", content: "// new" },
    }] }),
    message("tool", "", { toolResults: [{
      toolCallId: "write-clone-b",
      content: JSON.stringify({
        ok: true,
        operation: "created",
        path: "project://Source/New.cpp",
        sha256: "b".repeat(64),
      }),
    }] }),
    message("user", "Continue with the proven facts only."),
  ], { recentCompleteTurns: 0 });

  const created = result.memory.modifiedOrObservedFiles.find((item) => item.path.endsWith("New.cpp"));
  assert.equal(result.memory.activeProject.descriptor, cloneA);
  assert.equal(created.canonicalProject, cloneB);
  assert.equal(created.canonicalPath, "C:\\Work\\CloneB\\Source\\New.cpp");
  assert.equal(created.mutationSnapshotState, "fresh_read_required");
});

test("a per-call read cannot change active project or retarget the next unscoped write", () => {
  const cloneA = "C:\\Work\\CloneA\\Game.uproject";
  const cloneB = "C:\\Work\\CloneB\\Game.uproject";
  const result = core.buildCheckpoint([
    message("user", "Read Clone B once, then create the next file in still-active Clone A."),
    message("tool", "", { toolResults: [{ content: JSON.stringify({ ok: true, activeProject: cloneA }) }] }),
    message("assistant", "", { toolRequests: [{
      id: "read-clone-b",
      type: "function",
      name: "read_file",
      arguments: { project: cloneB, path: "project://Source/B.cpp" },
    }] }),
    message("tool", "", { toolResults: [{
      toolCallId: "read-clone-b",
      content: JSON.stringify({
        ok: true,
        activeProject: cloneB,
        resolvedRootType: "active_project",
        path: "project://Source/B.cpp",
        absolutePath: "C:\\Work\\CloneB\\Source\\B.cpp",
        sha256: "b".repeat(64),
      }),
    }] }),
    message("assistant", "", { toolRequests: [{
      id: "write-active-a",
      type: "function",
      name: "write_file",
      arguments: { path: "Source/NewA.cpp", content: "// active clone" },
    }] }),
    message("tool", "", { toolResults: [{
      toolCallId: "write-active-a",
      content: JSON.stringify({
        ok: true,
        operation: "created",
        path: "project://Source/NewA.cpp",
        sha256: "a".repeat(64),
      }),
    }] }),
    message("user", "Continue with the proven facts only."),
  ], { recentCompleteTurns: 0 });

  const byPath = new Map(result.memory.modifiedOrObservedFiles.map((item) => [item.path, item]));
  assert.equal(result.memory.activeProject.descriptor, cloneA);
  assert.equal(byPath.get("project://Source/B.cpp").canonicalProject, cloneB);
  assert.equal(byPath.get("project://Source/NewA.cpp").canonicalProject, cloneA);
  assert.equal(byPath.get("project://Source/NewA.cpp").canonicalPath, "C:\\Work\\CloneA\\Source\\NewA.cpp");
});

test("set_active_project remains the explicit owner of a durable active-project transition", () => {
  const cloneA = "C:\\Work\\CloneA\\Game.uproject";
  const cloneB = "C:\\Work\\CloneB\\Game.uproject";
  const result = core.buildCheckpoint([
    message("user", "Switch the selected project to Clone B."),
    message("tool", "", { toolResults: [{ content: JSON.stringify({ ok: true, activeProject: cloneA }) }] }),
    message("assistant", "", { toolRequests: [{
      id: "select-clone-b",
      type: "function",
      name: "set_active_project",
      arguments: { projectPath: cloneB },
    }] }),
    message("tool", "", { toolResults: [{
      toolCallId: "select-clone-b",
      content: JSON.stringify({ ok: true, activeProject: cloneB, selected: true }),
    }] }),
  ], { recentCompleteTurns: 0 });

  assert.equal(result.memory.activeProject.descriptor, cloneB);
  assert.equal(result.memory.activeProject.root, "C:\\Work\\CloneB");
});

test("a successful explicit project clear cannot revive the previous active project", () => {
  const cloneA = "C:\\Work\\CloneA\\Game.uproject";
  const result = core.buildCheckpoint([
    message("user", "Clear the selected Unreal project."),
    message("tool", "", { toolResults: [{ content: JSON.stringify({ ok: true, activeProject: cloneA }) }] }),
    message("assistant", "", { toolRequests: [{
      id: "clear-active-project",
      type: "function",
      name: "set_active_project",
      arguments: { clear: true },
    }] }),
    message("tool", "", { toolResults: [{
      toolCallId: "clear-active-project",
      content: JSON.stringify({ ok: true, activeProject: null, selected: false }),
    }] }),
  ], { recentCompleteTurns: 0 });

  assert.equal(result.memory.activeProject, null);
});

test("an unresolved or mismatched per-call project is never guessed from the active clone", () => {
  const active = "C:\\Work\\CloneA\\Game.uproject";
  const base = message("tool", "", { toolResults: [{ content: JSON.stringify({
    ok: true,
    activeProject: active,
    path: "project://Source/Existing.cpp",
    absolutePath: "C:\\Work\\CloneA\\Source\\Existing.cpp",
    sha256: "a".repeat(64),
  }) }] });
  const request = message("assistant", "", { toolRequests: [{
    id: "named-project-call",
    type: "function",
    name: "write_file",
    arguments: { project: "Game", path: "Source/Ambiguous.cpp", content: "// ambiguous" },
  }] });
  const result = core.buildCheckpoint([
    message("user", "Do not guess a clone for unresolved request evidence."),
    base,
    request,
    message("tool", "", { toolResults: [{
      toolCallId: "different-call-id",
      content: JSON.stringify({
        ok: true,
        operation: "created",
        path: "project://Source/Ambiguous.cpp",
        sha256: "b".repeat(64),
      }),
    }] }),
    message("assistant", "", { toolRequests: [{
      id: "named-project-call-2",
      type: "function",
      name: "write_file",
      arguments: { project: "Game", path: "Source/Ambiguous2.cpp", content: "// ambiguous" },
    }] }),
    message("tool", "", { toolResults: [{
      toolCallId: "named-project-call-2",
      content: JSON.stringify({
        ok: true,
        operation: "created",
        path: "project://Source/Ambiguous2.cpp",
        sha256: "c".repeat(64),
      }),
    }] }),
  ], { recentCompleteTurns: 0 });

  assert.equal(result.memory.activeProject.descriptor, active);
  assert.equal(
    result.memory.modifiedOrObservedFiles.some((item) => item.path.endsWith("Ambiguous.cpp")),
    false,
  );
  assert.equal(
    result.memory.modifiedOrObservedFiles.some((item) => item.path.endsWith("Ambiguous2.cpp")),
    false,
  );
});

test("an unmatched request cannot scope a later ID-less write to another clone", () => {
  const cloneA = "C:\\Work\\CloneA\\Game.uproject";
  const cloneB = "C:\\Work\\CloneB\\Game.uproject";
  const result = core.buildCheckpoint([
    message("user", "Edit the active clone safely."),
    message("tool", "", { toolResults: [{ content: JSON.stringify({ ok: true, activeProject: cloneA }) }] }),
    message("assistant", "", { toolRequests: [{
      id: "read-b",
      name: "read_file",
      arguments: { project: cloneB, path: "project://Source/B.cpp" },
    }] }),
    message("tool", "", { toolResults: [{
      toolCallId: "wrong-id",
      content: JSON.stringify({
        ok: true,
        path: "project://Source/B.cpp",
        absolutePath: "C:\\Work\\CloneB\\Source\\B.cpp",
        sha256: "b".repeat(64),
      }),
    }] }),
    message("assistant", "", { toolRequests: [{
      id: "write-a",
      name: "write_file",
      arguments: { path: "Source/New.cpp", content: "x" },
    }] }),
    message("tool", "", { toolResults: [{ content: JSON.stringify({
      ok: true,
      operation: "created",
      path: "project://Source/New.cpp",
      sha256: "c".repeat(64),
    }) }] }),
    message("user", "Continue with the proven facts only."),
  ], { recentCompleteTurns: 0 });

  const created = result.memory.modifiedOrObservedFiles.find((item) => item.path.endsWith("New.cpp"));
  assert.equal(result.memory.activeProject.descriptor, cloneA);
  assert.equal(created.canonicalProject, cloneA);
  assert.equal(created.canonicalPath, "C:\\Work\\CloneA\\Source\\New.cpp");
  assert.equal(
    result.memory.modifiedOrObservedFiles.some((item) => item.path.endsWith("B.cpp")),
    false,
  );
});

test("duplicate tool-call IDs cannot choose one of two clone scopes", () => {
  const cloneA = "C:\\Work\\CloneA\\Game.uproject";
  const cloneB = "C:\\Work\\CloneB\\Game.uproject";
  const result = core.buildCheckpoint([
    message("user", "Do not guess between duplicate request IDs."),
    message("assistant", "", { toolRequests: [
      { id: "duplicate", name: "write_file", arguments: { project: cloneA, path: "Source/A.cpp" } },
      { id: "duplicate", name: "write_file", arguments: { project: cloneB, path: "Source/B.cpp" } },
    ] }),
    message("tool", "", { toolResults: [{
      toolCallId: "duplicate",
      content: JSON.stringify({
        ok: true,
        operation: "created",
        path: "project://Source/A.cpp",
        sha256: "a".repeat(64),
      }),
    }] }),
  ], { recentCompleteTurns: 0 });

  assert.deepEqual(result.memory.modifiedOrObservedFiles, []);
  assert.equal(result.memory.activeProject, null);
});

test("reverse ID-less results from different clone scopes are omitted as ambiguous", () => {
  const cloneA = "C:\\Work\\CloneA\\Game.uproject";
  const cloneB = "C:\\Work\\CloneB\\Game.uproject";
  const result = core.buildCheckpoint([
    message("user", "Do not infer ordering for optional tool-call IDs."),
    message("assistant", "", { toolRequests: [
      { id: "write-a", name: "write_file", arguments: { project: cloneA, path: "Source/A.cpp" } },
      { id: "write-b", name: "write_file", arguments: { project: cloneB, path: "Source/B.cpp" } },
    ] }),
    message("tool", "", { toolResults: [
      { content: JSON.stringify({ ok: true, operation: "created", path: "project://Source/B.cpp", sha256: "b".repeat(64) }) },
      { content: JSON.stringify({ ok: true, operation: "created", path: "project://Source/A.cpp", sha256: "a".repeat(64) }) },
    ] }),
  ], { recentCompleteTurns: 0 });

  assert.deepEqual(result.memory.modifiedOrObservedFiles, []);
  assert.equal(result.memory.activeProject, null);
});

test("unique exact IDs remain correlatable when one request batch returns in multiple tool messages", () => {
  const cloneA = "C:\\Work\\CloneA\\Game.uproject";
  const cloneB = "C:\\Work\\CloneB\\Game.uproject";
  const result = core.buildCheckpoint([
    message("user", "Observe both exact clones."),
    message("assistant", "", { toolRequests: [
      { id: "read-a", name: "read_file", arguments: { project: cloneA, path: "Source/A.cpp" } },
      { id: "read-b", name: "read_file", arguments: { project: cloneB, path: "Source/B.cpp" } },
    ] }),
    message("tool", "", { toolResults: [{
      toolCallId: "read-a",
      content: JSON.stringify({ ok: true, path: "project://Source/A.cpp", sha256: "a".repeat(64) }),
    }] }),
    message("tool", "", { toolResults: [{
      toolCallId: "read-b",
      content: JSON.stringify({ ok: true, path: "project://Source/B.cpp", sha256: "b".repeat(64) }),
    }] }),
    message("user", "Continue with the proven facts only."),
  ], { recentCompleteTurns: 0 });

  assert.deepEqual(
    result.memory.modifiedOrObservedFiles.map((item) => item.canonicalProject),
    [cloneA, cloneB],
  );
});

test("an ambiguous ID-less result does not prevent a later exact ID from retaining its own scope", () => {
  const cloneA = "C:\\Work\\CloneA\\Game.uproject";
  const cloneB = "C:\\Work\\CloneB\\Game.uproject";
  const result = core.buildCheckpoint([
    message("user", "Retain only results with proven correlation."),
    message("assistant", "", { toolRequests: [
      { id: "read-a", name: "read_file", arguments: { project: cloneA, path: "Source/A.cpp" } },
      { id: "read-b", name: "read_file", arguments: { project: cloneB, path: "Source/B.cpp" } },
    ] }),
    message("tool", "", { toolResults: [{
      content: JSON.stringify({ ok: true, path: "project://Source/Unknown.cpp", sha256: "0".repeat(64) }),
    }] }),
    message("tool", "", { toolResults: [{
      toolCallId: "read-b",
      content: JSON.stringify({ ok: true, path: "project://Source/B.cpp", sha256: "b".repeat(64) }),
    }] }),
    message("user", "Continue with the proven facts only."),
  ], { recentCompleteTurns: 0 });

  assert.deepEqual(result.memory.modifiedOrObservedFiles.map((item) => item.path), ["project://Source/B.cpp"]);
  assert.equal(result.memory.modifiedOrObservedFiles[0].canonicalProject, cloneB);
});

test("a conflicting per-call result project is omitted instead of crossing clone scope", () => {
  const cloneA = "C:\\Work\\CloneA\\Game.uproject";
  const cloneB = "C:\\Work\\CloneB\\Game.uproject";
  const result = core.buildCheckpoint([
    message("user", "Keep exact per-call clone scope."),
    message("assistant", "", { toolRequests: [{
      id: "read-b",
      name: "read_file",
      arguments: { project: cloneB, path: "Source/B.cpp" },
    }] }),
    message("tool", "", { toolResults: [{
      toolCallId: "read-b",
      content: JSON.stringify({
        ok: true,
        activeProject: cloneA,
        path: "project://Source/B.cpp",
        absolutePath: "C:\\Work\\CloneA\\Source\\B.cpp",
        sha256: "b".repeat(64),
      }),
    }] }),
    message("user", "Continue with the proven facts only."),
  ], { recentCompleteTurns: 0 });

  assert.deepEqual(result.memory.modifiedOrObservedFiles, []);
  assert.equal(result.memory.activeProject, null);
  assert.match(JSON.stringify(result.memory.currentWorkStatus.recentToolOutcomes), /omitted_inconsistent_project_scope/u);
});

test("bundle bare relative paths become canonical only under the proven exact project", () => {
  const project = "C:\\Work\\Game\\Game.uproject";
  const result = core.buildCheckpoint([
    message("user", "Apply the focused header and implementation bundle."),
    message("assistant", "", { toolRequests: [{
      id: "bundle-call",
      type: "function",
      name: "apply_edit_bundle",
      arguments: { project, patches: [] },
    }] }),
    message("tool", "", { toolResults: [{
      toolCallId: "bundle-call",
      content: JSON.stringify({
        ok: true,
        operation: "bundle_applied",
        files: [
          { path: "Source/A.cpp", previousSha256: "1".repeat(64), sha256: "2".repeat(64) },
          { path: "Plugins/P/Source/P/B.cpp", previousSha256: "3".repeat(64), sha256: "4".repeat(64) },
        ],
      }),
    }] }),
    message("user", "Continue with the proven facts only."),
  ], { recentCompleteTurns: 0 });

  assert.deepEqual(
    result.memory.modifiedOrObservedFiles.map((item) => item.canonicalPath),
    ["C:\\Work\\Game\\Source\\A.cpp", "C:\\Work\\Game\\Plugins\\P\\Source\\P\\B.cpp"],
  );
  assert.ok(result.memory.modifiedOrObservedFiles.every((item) => item.canonicalProject === project));
});

test("workspace and outside absolute paths are never attached to an active project observation", () => {
  const project = "C:\\Work\\Game\\Game.uproject";
  const result = core.buildCheckpoint([
    message("user", "Keep only canonical project-contained file observations."),
    message("tool", "", { toolResults: [{ content: JSON.stringify({
      ok: true,
      activeProject: project,
      resolvedRootType: "workspace",
      path: "workspace://README.md",
      absolutePath: "C:\\Repo\\README.md",
      workspaceRelativePath: "README.md",
      sha256: "1".repeat(64),
    }) }] }),
    message("tool", "", { toolResults: [{ content: JSON.stringify({
      ok: true,
      activeProject: project,
      resolvedRootType: "active_project",
      path: "project://../CloneB/Bad.cpp",
      absolutePath: "C:\\Work\\CloneB\\Bad.cpp",
      sha256: "2".repeat(64),
    }) }] }),
    message("user", "Continue with the proven facts only."),
  ], { recentCompleteTurns: 0 });
  const durableOutcomes = JSON.stringify(result.memory.currentWorkStatus.recentToolOutcomes);

  assert.equal(result.memory.activeProject.descriptor, project);
  assert.deepEqual(result.memory.modifiedOrObservedFiles, []);
  assert.doesNotMatch(durableOutcomes, /workspace:\/\/README|C:\\\\Repo\\\\README|CloneB\\\\Bad/iu);
  assert.match(durableOutcomes, /omitted_non_project_scope/u);
});

test("workspace paths are removed from durable tool outcomes even without an active project", () => {
  const result = core.buildCheckpoint([
    message("user", "Inspect only exact project files."),
    message("tool", "", { toolResults: [{ content: JSON.stringify({
      ok: true,
      resolvedRootType: "workspace",
      path: "workspace://README.md",
      absolutePath: "C:\\Repo\\README.md",
      sha256: "1".repeat(64),
    }) }] }),
    message("user", "Continue with the proven facts only."),
  ], { recentCompleteTurns: 0 });
  const durableOutcomes = JSON.stringify(result.memory.currentWorkStatus.recentToolOutcomes);

  assert.deepEqual(result.memory.modifiedOrObservedFiles, []);
  assert.doesNotMatch(durableOutcomes, /workspace:\/\/README|C:\\\\Repo\\\\README|"sha256"/iu);
  assert.match(durableOutcomes, /omitted_non_project_scope/u);
});

test("path-only workspace errors cannot survive as rootless durable file identity", () => {
  const result = core.buildCheckpoint([
    message("user", "Inspect only canonical project files."),
    message("tool", "", { toolResults: [{ content: JSON.stringify({
      ok: false,
      errorCode: "NOT_FOUND",
      resolvedRootType: "workspace",
      path: "workspace://README.md",
    }) }] }),
    message("user", "Continue with the proven facts only."),
  ], { recentCompleteTurns: 0 });
  const durableOutcomes = JSON.stringify(result.memory.currentWorkStatus.recentToolOutcomes);

  assert.deepEqual(result.memory.modifiedOrObservedFiles, []);
  assert.doesNotMatch(durableOutcomes, /workspace:\/\/README/iu);
  assert.match(durableOutcomes, /omitted_non_project_scope/u);
});

test("bounded tool display never discards extracted bundle file facts", () => {
  const project = "C:\\LongProjectSegment\\Game\\Game.uproject";
  const result = core.buildCheckpoint([
    message("user", "Apply bundle."),
    message("assistant", "", { toolRequests: [{
      id: "bundle",
      name: "apply_edit_bundle",
      arguments: { project, patches: [] },
    }] }),
    message("tool", "", { toolResults: [{
      toolCallId: "bundle",
      content: JSON.stringify({
        ok: true,
        operation: "bundle_applied",
        files: [
          {
            path: "Source/VeryLongModuleName/Private/Subsystem/AComponent.cpp",
            previousSha256: "1".repeat(64),
            sha256: "2".repeat(64),
          },
          {
            path: "Plugins/VeryLongPluginName/Source/VeryLongPluginName/Private/BComponent.cpp",
            previousSha256: "3".repeat(64),
            sha256: "4".repeat(64),
          },
        ],
      }),
    }] }),
    message("user", "Continue with the proven facts only."),
  ], { recentCompleteTurns: 0, maxToolResultChars: 1200 });

  assert.equal(result.memory.modifiedOrObservedFiles.length, 2);
  assert.ok(result.memory.modifiedOrObservedFiles.every((item) => item.canonicalProject === project));
  assert.ok(result.memory.currentWorkStatus.recentToolOutcomes.every((outcome) => {
    assert.ok(outcome.length <= 1200, outcome.length);
    return Boolean(JSON.parse(outcome));
  }));
});

test("production receipt continuation prose is absent from every durable surface", () => {
  const guidance = "apply_edit_bundle: duplicate patches[] paths are not allowed; use one focused region per file and continue with the returned receipt in the next prediction round";
  const result = core.buildCheckpoint([
    message("user", "Fix both regions safely."),
    message("tool", "", { toolResults: [{ content: JSON.stringify({
      ok: false,
      errorCode: "INVALID_ARGUMENT",
      message: guidance,
    }) }] }),
    message("assistant", "I still need to fix the second region."),
    message("user", "Continue."),
  ], { recentCompleteTurns: 0 });
  const durable = `${JSON.stringify(result.memory)}\n${result.checkpoint}`;

  assert.doesNotMatch(durable, /continue with the returned receipt/iu);
  assert.match(durable, /fresh file snapshot required before mutation/iu);
});

test("a legacy observation stays with its prior exact clone when another clone becomes active", () => {
  const oldProject = "C:\\Work\\Git\\Project_MJS\\Project_MJS.uproject";
  const newProject = "C:\\Work\\Github\\Project_MJS\\Project_MJS.uproject";
  const prior = {
    schemaVersion: 1,
    authority: "factual_memory_only",
    latestUserMessage: "Inspect the health component.",
    activeObjective: { kind: "user_request", status: "active", text: "Inspect the health component.", source: "current_history" },
    activeProject: { descriptor: oldProject, source: "tool_result_fact" },
    currentWorkStatus: {
      modifiedOrObservedFiles: [{
        path: "project://Source/HealthComponent.h",
        activeProject: oldProject,
        sha256: "1".repeat(64),
        fileVersionReceipt: "fvr1_legacy_rootless",
        snapshotVersion: 4,
      }],
    },
    modifiedOrObservedFiles: [{
      path: "project://Source/HealthComponent.h",
      activeProject: oldProject,
      sha256: "1".repeat(64),
      fileVersionReceipt: "fvr1_legacy_rootless_top",
    }],
    unresolvedItems: [],
    recentRawTail: [],
  };
  const result = core.buildCheckpoint([
    message("system", `[Direct continuity state v1]\n${JSON.stringify(prior)}`),
    message("tool", "", { toolResults: [{ content: JSON.stringify({
      ok: true,
      activeProject: newProject,
      path: "project://Source/HealthComponent.h",
      absolutePath: "C:\\Work\\Github\\Project_MJS\\Source\\HealthComponent.h",
      sha256: "2".repeat(64),
      fileVersionReceipt: "fvr1_new_clone_live",
      snapshotVersion: 1,
    }) }] }),
    message("user", "Continue."),
  ], { recentCompleteTurns: 0 });

  assertNoDurableFileCapability(result);
  assert.deepEqual(
    result.memory.modifiedOrObservedFiles.map((item) => item.canonicalProject),
    [oldProject, newProject],
  );
  assert.deepEqual(
    result.memory.currentWorkStatus.modifiedOrObservedFiles.map((item) => item.canonicalProject),
    [oldProject, newProject],
  );
  assert.equal(result.memory.modifiedOrObservedFiles[0].sha256AtObservation, "1".repeat(64));
  assert.equal(result.memory.modifiedOrObservedFiles[1].sha256AtObservation, "2".repeat(64));
});

test("an unambiguous v1 project-relative observation migrates to canonical v2 facts", () => {
  const project = "C:\\Work\\Game\\Game.uproject";
  const legacyFile = {
    path: "project://Source/A.cpp",
    activeProject: project,
    operation: "observed",
    sha256: "5".repeat(64),
    fileVersionReceipt: "fvr1_legacy_only",
    snapshotVersion: 19,
  };
  const prior = {
    schemaVersion: 1,
    authority: "factual_memory_only",
    latestUserMessage: "Inspect A.cpp.",
    activeObjective: { kind: "user_request", status: "active", text: "Inspect A.cpp.", source: "current_history" },
    activeProject: { descriptor: project, source: "tool_result_fact" },
    currentWorkStatus: { modifiedOrObservedFiles: [legacyFile] },
    modifiedOrObservedFiles: [legacyFile],
    unresolvedItems: [],
    recentRawTail: [],
  };
  const result = core.buildCheckpoint([
    message("system", `[Direct continuity state v1]\n${JSON.stringify(prior)}`),
    message("user", "Continue."),
  ], { recentCompleteTurns: 0 });

  assertNoDurableFileCapability(result);
  assert.equal(result.memory.schemaVersion, 2);
  assert.equal(result.memory.modifiedOrObservedFiles[0].canonicalProject, project);
  assert.equal(result.memory.modifiedOrObservedFiles[0].canonicalPath, "C:\\Work\\Game\\Source\\A.cpp");
  assert.equal(result.memory.modifiedOrObservedFiles[0].sha256AtObservation, "5".repeat(64));
  assert.equal(result.memory.currentWorkStatus.modifiedOrObservedFiles[0].canonicalPath, "C:\\Work\\Game\\Source\\A.cpp");
});

test("a rootless v1 observation is omitted when the final active clone cannot prove its origin", () => {
  const cloneB = "C:\\Work\\CloneB\\Game.uproject";
  const rootless = { path: "project://Source/Legacy.cpp", sha256: "1".repeat(64) };
  const prior = {
    schemaVersion: 1,
    authority: "factual_memory_only",
    latestUserMessage: "Continue.",
    activeObjective: { kind: "user_request", status: "active", text: "Continue.", source: "current_history" },
    activeProject: { descriptor: cloneB, source: "tool_result_fact" },
    currentWorkStatus: { modifiedOrObservedFiles: [rootless] },
    modifiedOrObservedFiles: [rootless],
  };
  const result = core.buildCheckpoint([
    message("system", `[Direct continuity state v1]\n${JSON.stringify(prior)}`),
    message("user", "Continue."),
  ], { recentCompleteTurns: 0 });

  assert.deepEqual(result.memory.modifiedOrObservedFiles, []);
  assert.deepEqual(result.memory.currentWorkStatus.modifiedOrObservedFiles, []);
});

test("nested workspace files inherit parent scope and cannot become project observations", () => {
  const project = "C:\\Work\\CloneA\\Game.uproject";
  const result = core.buildCheckpoint([
    message("user", "Keep workspace files outside project continuity."),
    message("tool", "", { toolResults: [{ content: JSON.stringify({
      ok: true,
      activeProject: project,
      resolvedRootType: "workspace",
      path: "workspace://",
      absolutePath: "C:\\Repo",
      operation: "observed",
      files: [{
        path: "README.md",
        absolutePath: "C:\\Repo\\README.md",
        sha256: "1".repeat(64),
      }],
    }) }] }),
  ], { recentCompleteTurns: 0 });

  assert.deepEqual(result.memory.modifiedOrObservedFiles, []);
  assert.doesNotMatch(JSON.stringify(result.memory.currentWorkStatus.recentToolOutcomes), /README\.md|C:\\\\Repo/iu);
});

test("mutation-context receipt synonyms become a fresh-snapshot fact", () => {
  for (const text of [
    "Supply the previous receipt to replace_in_file for the next mutation.",
    "Submit the current receipt with the next edit.",
    "Provide the previous receipt to replace_in_file.",
    "Set fileVersionReceipt to the prior value and mutate the file.",
  ]) {
    const result = core.buildCheckpoint([
      message("user", "Continue safely."),
      message("assistant", text),
      message("user", "Continue."),
    ], { recentCompleteTurns: 0 });
    const durable = `${JSON.stringify(result.memory)}\n${result.checkpoint}`;
    assert.match(durable, /fresh file snapshot required before mutation/iu);
    assert.doesNotMatch(durable, /\b(?:supply|submit|provide|set)\b[^.!?]{0,120}(?:receipt|file-mutation capability)/iu);
  }
});

test("JSON-escaped receipt capabilities cannot survive hard compaction", () => {
  const result = core.buildCheckpoint([
    message("user", "Edit safely."),
    message("assistant", String.raw`Supply \u0066vr1_live_capability to replace_in_file now.`),
    message("user", "Continue."),
  ], { recentCompleteTurns: 0 });
  const durable = `${JSON.stringify(result.memory)}\n${result.checkpoint}`;

  assert.doesNotMatch(durable, /fvr1_|\\u0066vr1|u0066vr1/iu);
  assert.doesNotMatch(durable, /\bsupply\b[^.!?]{0,120}(?:receipt|capability omitted)/iu);
  assert.match(durable, /fresh file snapshot required before mutation/iu);
});

test("registry registration order is discarded even when an unchanged hash is observed repeatedly", () => {
  const project = "C:\\Work\\Game\\Game.uproject";
  const file = "C:\\Work\\Game\\Source\\HealthComponent.h";
  const tool = (version) => message("tool", "", { toolResults: [{ content: JSON.stringify({
    ok: true,
    path: "project://Source/HealthComponent.h",
    absolutePath: file,
    activeProject: project,
    sha256: "d".repeat(64),
    fileVersionReceipt: `fvr1_same_hash_${version}`,
    snapshotVersion: version,
  }) }] });
  const result = core.buildCheckpoint([
    message("user", "파일 변경 여부를 관찰 SHA로만 기억해."),
    tool(1),
    tool(8),
    tool(24),
    message("user", "Continue with the proven facts only."),
  ], { recentCompleteTurns: 0 });

  assertNoDurableFileCapability(result);
  assert.equal(result.memory.modifiedOrObservedFiles.length, 1);
  assert.equal(result.memory.modifiedOrObservedFiles[0].sha256AtObservation, "d".repeat(64));
  assert.equal(result.memory.modifiedOrObservedFiles[0].mutationSnapshotState, "fresh_read_required");
});

test("partial independent mutation outcomes stay factual without preserving the reused capability", () => {
  const result = core.buildCheckpoint([
    message("user", "HealthComponent 선언과 구현을 일치시켜."),
    message("tool", "", { toolResults: [{ content: JSON.stringify({
      ok: false,
      errorCode: "FILE_SNAPSHOT_SCOPE_MISMATCH",
      message: "fileVersionReceipt belongs to a different path: fvr1_wrong_mapping",
      path: "project://Source/HealthComponent.h",
      activeProject: "C:\\Work\\Project_MJS\\Project_MJS.uproject",
    }) }] }),
    message("tool", "", { toolResults: [{ content: JSON.stringify({
      ok: true,
      operation: "replaced",
      path: "project://Source/HealthComponent.cpp",
      activeProject: "C:\\Work\\Project_MJS\\Project_MJS.uproject",
      previousSha256: "e".repeat(64),
      sha256: "f".repeat(64),
      fileVersionReceipt: "fvr1_wrong_mapping",
      snapshotVersion: 13,
    }) }] }),
    message("user", "계속해."),
  ], { recentCompleteTurns: 0 });

  assertNoDurableFileCapability(result);
  assert.match(JSON.stringify(result.memory.recentOlderToolOutcomes), /FILE_SNAPSHOT_SCOPE_MISMATCH/u);
  assert.equal(result.memory.modifiedOrObservedFiles[0].observationState, "modified");
  assert.equal(result.memory.modifiedOrObservedFiles[0].mutationSnapshotState, "fresh_read_required");
});

test("the durable user copy strips raw capability payloads but keeps diagnostic identifiers", () => {
  const latest = "Diagnose fvr1_user_supplied but never persist fileVersionReceipt in durable memory.";
  const result = core.buildCheckpoint([message("user", latest)], { recentCompleteTurns: 0 });

  assert.equal(result.latestUserVerbatim, latest);
  assertNoDurableFileCapability(result);
  assert.match(result.memory.currentUserRequestVerbatim, /ephemeral file capability omitted/iu);
  assert.match(result.memory.currentUserRequestVerbatim, /never persist fileVersionReceipt/iu);
});

test("a retained file result stays raw and becomes durable only after it is omitted", () => {
  const receipt = "fvr1_recent_uncompressed_round";
  const messages = [
    message("user", "Implement the first file."),
    message("assistant", "The first edit is complete."),
    message("user", "Read the next exact region and edit it."),
    message("tool", "", { toolResults: [{ content: JSON.stringify({
      ok: true,
      path: "project://Source/Next.cpp",
      activeProject: "C:\\Work\\Recent\\Recent.uproject",
      absolutePath: "C:\\Work\\Recent\\Source\\Next.cpp",
      sha256: "9".repeat(64),
      fileVersionReceipt: receipt,
      snapshotVersion: 41,
    }) }] }),
  ];
  const result = core.buildCheckpoint(messages, { recentCompleteTurns: 1 });

  assert.equal(result.retainedIndexes.includes(3), true);
  assert.match(messages[3].toolResults[0].content, new RegExp(receipt, "u"));
  assertNoDurableFileCapability(result);
  assert.deepEqual(result.memory.modifiedOrObservedFiles, []);
  assert.doesNotMatch(result.checkpoint, /Next\.cpp|fresh_read_required|9999999999999999/u);

  const retained = new Set(result.retainedIndexes);
  const agedResult = core.buildCheckpoint([
    message("system", result.checkpoint),
    ...messages.filter((item, index) => item.role !== "system" && retained.has(index)),
    message("user", "Continue with the proven facts only."),
  ], { recentCompleteTurns: 0 });

  assertNoDurableFileCapability(agedResult);
  assert.equal(agedResult.memory.modifiedOrObservedFiles.length, 1);
  assert.equal(agedResult.memory.modifiedOrObservedFiles[0].path, "project://Source/Next.cpp");
  assert.equal(agedResult.memory.modifiedOrObservedFiles[0].sha256AtObservation, "9".repeat(64));
  assert.equal(agedResult.memory.modifiedOrObservedFiles[0].mutationSnapshotState, "fresh_read_required");
  const agedCheckpoint = JSON.parse(agedResult.checkpoint.slice(agedResult.checkpoint.indexOf("{")));
  assert.equal(Object.hasOwn(agedCheckpoint, "modifiedOrObservedFiles"), false);
  assert.equal(agedCheckpoint.currentWorkStatus.modifiedOrObservedFiles.length, 1);
  const agedOutcomes = agedResult.memory.currentWorkStatus.recentToolOutcomes;
  assert.doesNotMatch(JSON.stringify(agedOutcomes), /Next\.cpp|9999999999999999/u);
  assert.ok(agedOutcomes.every((outcome) => (
    !Object.hasOwn(JSON.parse(outcome), "canonicalProjectRoot")
  )));
});

test("a simulated MCP restart inherits facts and objective but no prior runtime receipt", () => {
  const firstRuntime = core.buildCheckpoint([
    message("user", "Implement and build the death pipeline."),
    message("tool", "", { toolResults: [{ content: JSON.stringify({
      ok: true,
      activeProject: "C:\\Work\\Project_MJS\\Project_MJS.uproject",
      absolutePath: "C:\\Work\\Project_MJS\\Source\\HealthComponent.h",
      path: "project://Source/HealthComponent.h",
      sha256: "7".repeat(64),
      fileVersionReceipt: "fvr1_runtime_one_only",
      snapshotVersion: 3,
    }) }] }),
    message("user", "Continue."),
  ], { recentCompleteTurns: 0 });
  const secondRuntime = core.buildCheckpoint([
    message("system", firstRuntime.checkpoint),
    message("tool", "", { toolResults: [{ content: JSON.stringify({
      ok: false,
      errorCode: "FILE_SNAPSHOT_INVALID",
      message: "fileVersionReceipt was not issued by this runtime: fvr1_runtime_one_only",
    }) }] }),
    message("user", "Continue."),
  ], { recentCompleteTurns: 0 });

  assertNoDurableFileCapability(firstRuntime);
  assertNoDurableFileCapability(secondRuntime);
  assert.equal(secondRuntime.memory.activeObjective.text, "Implement and build the death pipeline.");
  assert.equal(secondRuntime.memory.modifiedOrObservedFiles[0].mutationSnapshotState, "fresh_read_required");
  assert.match(JSON.stringify(secondRuntime.memory.recentOlderToolOutcomes), /FILE_SNAPSHOT_INVALID/u);
});

test("the minimized 5,692-line Qwen transcript replay keeps canonical facts through three compactions", () => {
  const fixture = JSON.parse(fs.readFileSync(
    path.join(__dirname, "fixtures", "qwen-receipt-path-confusion.json"),
    "utf8",
  ));
  const fixtureProjectRoot = `${path.win32.dirname(fixture.canonicalProject)}\\`;
  const fromFixture = (item) => message(item.role, item.text || "", { toolResults: item.toolResults || [] });
  const compact = (history) => {
    const result = core.buildCheckpoint(history, { recentCompleteTurns: 0, maxCheckpointChars: 16000 });
    const retained = new Set(result.retainedIndexes);
    return {
      result,
      history: [
        ...history.filter((item, index) => item.role === "system" && retained.has(index)),
        message("system", result.checkpoint),
        ...history.filter((item, index) => item.role !== "system" && retained.has(index)),
      ],
    };
  };

  const first = compact(fixture.initialMessages.map(fromFixture));
  const second = compact([...first.history, ...fixture.afterFirstCompaction.map(fromFixture)]);
  const third = compact([...second.history, ...fixture.afterSecondCompaction.map(fromFixture)]);

  for (const round of [first, second, third]) {
    assertNoDurableFileCapability(round.result);
    assert.equal(round.result.memory.activeObjective.text, fixture.objective);
    assert.equal(round.result.memory.activeProject.descriptor, fixture.canonicalProject);
    assert.ok(round.result.memory.modifiedOrObservedFiles.every((item) => (
      item.canonicalProject === fixture.canonicalProject
      && item.canonicalPath.startsWith(fixtureProjectRoot)
      && item.mutationSnapshotState === "fresh_read_required"
    )));
  }
  const finalFiles = third.result.memory.modifiedOrObservedFiles;
  assert.match(finalFiles.map((item) => item.canonicalPath).join("\n"), /Public\\Animation\\CPlayerCharacterAnimInstance\.h/u);
  assert.equal(
    finalFiles.find((item) => item.path.endsWith("HealthComponent.cpp")).sha256AtObservation,
    "2a47".padEnd(64, "0"),
  );
  assert.doesNotMatch(
    finalFiles.map((item) => item.canonicalPath).join("\n"),
    /Character\\Player\\AnimInstance/u,
  );
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

test("bounded current-turn retention keeps the newest unread tool exchange complete", () => {
  const result = core.buildCheckpoint([
    message("system", "system"),
    message("user", "inspect the project and report"),
    message("assistant", "reading the older file", {
      toolRequests: [{ id: "old-read", type: "function", name: "read_file", arguments: { path: "Old.cpp" } }],
    }),
    message("tool", "", {
      toolResults: [{
        toolCallId: "old-read",
        content: JSON.stringify({ ok: true, noise: `${"OLD_ALREADY_READ ".repeat(2000)}OLD_ALREADY_READ_END` }),
      }],
    }),
    message("assistant", "reading the newest file", {
      toolRequests: [{ id: "new-read", type: "function", name: "read_file", arguments: { path: "New.cpp" } }],
    }),
    message("tool", "", {
      toolResults: [{
        toolCallId: "new-read",
        content: JSON.stringify({ ok: true, noise: `${"NEW_UNREAD ".repeat(2000)}NEW_UNREAD_END` }),
      }],
    }),
    message("tool", "", {
      toolResults: [{
        toolCallId: "orphan-result",
        content: JSON.stringify({ ok: true, noise: "ORPHAN_RESULT_MUST_NOT_SURVIVE" }),
      }],
    }),
  ], {
    recentCompleteTurns: 0,
    maxCurrentTurnMessages: 2,
  });

  assert.deepEqual(result.retainedIndexes, [0, 1, 4, 5]);
  assert.equal(result.omittedMessageCount, 3);
  assert.equal(result.memory.latestUserMessage, "inspect the project and report");
  assert.equal(result.memory.activeObjective.text, "inspect the project and report");
  assert.doesNotMatch(result.checkpoint, /OLD_ALREADY_READ_END|NEW_UNREAD_END|ORPHAN_RESULT_MUST_NOT_SURVIVE/);
  assertNoDurableFileCapability(result);
});

test("successive bounded compactions serialize each tool outcome exactly once", () => {
  let history = [message("user", "Inspect every file, then report.")];
  let lastResult = null;
  const labels = ["OUTCOME_A", "OUTCOME_B", "OUTCOME_C", "OUTCOME_D", "OUTCOME_E"];

  for (const [index, label] of labels.entries()) {
    const requestId = `read-${index}`;
    history.push(
      message("assistant", `Reading file ${index}.`, {
        toolRequests: [{ id: requestId, type: "function", name: "read_file", arguments: { path: `File${index}.cpp` } }],
      }),
      message("tool", "", {
        toolResults: [{
          toolCallId: requestId,
          content: JSON.stringify({
            ok: true,
            summary: label,
            exitCode: 0,
            proofLevel: "TestVerified",
            upToDate: true,
            fullLogPath: "C:\\Logs\\shared-build.log",
          }),
        }],
      }),
    );
    lastResult = core.buildCheckpoint(history, {
      recentCompleteTurns: 0,
      maxCurrentTurnMessages: 2,
    });
    const retained = new Set(lastResult.retainedIndexes);
    history = [
      ...history.filter((item, messageIndex) => item.role === "system" && retained.has(messageIndex)),
      message("system", lastResult.checkpoint),
      ...history.filter((item, messageIndex) => item.role !== "system" && retained.has(messageIndex)),
    ];
  }

  assert.ok(lastResult);
  const modelFacing = history.map((item) => (
    `${item.text}\n${item.toolResults.map((result) => result.content).join("\n")}`
  )).join("\n");
  for (const label of labels) {
    assert.equal(modelFacing.split(label).length - 1, 1, `${label} should appear exactly once`);
  }
  const durableOutcomes = lastResult.memory.currentWorkStatus.recentToolOutcomes;
  assert.equal(durableOutcomes.length, 4);
  assert.equal(new Set(durableOutcomes).size, durableOutcomes.length);
  assert.equal(lastResult.memory.currentWorkStatus.recentBuildOrTestState.length, 1);
  assert.equal(
    lastResult.memory.currentWorkStatus.recentBuildOrTestState[0].fullLogPath,
    "C:\\Logs\\shared-build.log",
  );
  assert.ok(durableOutcomes.every((outcome) => (
    !outcome.includes("fullLogPath")
    && !outcome.includes("proofLevel")
    && !outcome.includes("exitCode")
    && !outcome.includes("upToDate")
  )));
  assert.doesNotMatch(lastResult.checkpoint, /OUTCOME_E/u);
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

test("token pressure is the only normal compaction decision once the handler is active", () => {
  assert.equal(core.shouldCompact({ remainingTokens: 100, messageCount: 100 }), true);
  assert.equal(core.shouldCompact({ remainingTokens: 13999, messageCount: 3 }, { softRemainingTokens: 14000 }), true);
  assert.equal(core.shouldCompact({ remainingTokens: 14001, messageCount: 100 }, { softRemainingTokens: 14000 }), false);
  assert.equal(core.shouldCompact({ remainingTokens: 100, messageCount: 100 }, { enabled: false, softRemainingTokens: 14000 }), true);
  assert.equal(core.shouldCompact({ remainingTokens: 100, messageCount: 100 }, { observeOnly: true, softRemainingTokens: 14000 }), false);
});

test("fallback message threshold applies only when remaining token measurement is unavailable", () => {
  assert.equal(core.shouldCompact({ remainingTokens: Number.NaN, messageCount: 24 }, { compactAboveMessageCount: 24 }), true);
  assert.equal(core.shouldCompact({ remainingTokens: Number.NaN, messageCount: 23 }, { compactAboveMessageCount: 24 }), false);
  assert.equal(core.shouldCompact({ exact: false, remainingTokens: 27000, messageCount: 24 }, { compactAboveMessageCount: 24 }), true);
  assert.equal(core.shouldCompact({ exact: false, remainingTokens: 27000, messageCount: 23 }, { compactAboveMessageCount: 24 }), false);
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
  const projectObservation = fixture.initialMessages.find((item) => (
    item.role === "tool" && Array.isArray(item.toolResults) && item.toolResults.length > 0
  ));
  const project = JSON.parse(projectObservation.toolResults[0].content).activeProject;

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
  const marker = result.checkpoint.indexOf("[Direct continuity state v2]");
  const jsonStart = result.checkpoint.indexOf("{", marker);
  const parsed = JSON.parse(result.checkpoint.slice(jsonStart));

  assert.equal(parsed.schemaVersion, 2);
  assert.equal(parsed.authority, "factual_memory_only");
  assert.equal(parsed.latestUserMessageVerbatimRetainedSeparately, true);
  assert.match(parsed.activeObjective.text, /^긴 요청/u);
});

test("escape-heavy emergency text cannot exceed the hard checkpoint bound", () => {
  const result = core.buildCheckpoint([
    message("user", `Implement ${String.fromCharCode(0).repeat(30000)}`),
  ], { recentCompleteTurns: 0, maxCheckpointChars: 2000 });
  const marker = result.checkpoint.indexOf("[Direct continuity state v2]");
  const jsonStart = result.checkpoint.indexOf("{", marker);
  const parsed = JSON.parse(result.checkpoint.slice(jsonStart));

  assert.equal(parsed.schemaVersion, 2);
  assert.ok(result.checkpoint.length <= 2000, result.checkpoint.length);
});

test("Korean payment-receipt objective is preserved across hard compaction", () => {
  const objective = "결제 영수증 데이터를 사용해 구매 내역 UI를 구현해.";
  const result = core.buildCheckpoint([
    message("user", objective),
  ], { recentCompleteTurns: 0 });

  assert.equal(result.memory.latestUserMessage, objective);
  assert.equal(result.memory.activeObjective.text, objective);
  assert.equal(result.memory.currentUserRequestVerbatim, objective);
  assert.equal(result.memory.recentRawTail[0].text, objective);
  assert.doesNotMatch(result.checkpoint, /fresh file snapshot required before mutation/iu);
});

test("English payment-receipt objective is preserved across hard compaction", () => {
  const objective = "Implement a payment receipt parser and display the receipt history.";
  const result = core.buildCheckpoint([
    message("user", objective),
  ], { recentCompleteTurns: 0 });

  assert.equal(result.memory.latestUserMessage, objective);
  assert.equal(result.memory.activeObjective.text, objective);
  assert.match(result.checkpoint, /payment receipt parser/u);
});

test("receipt-domain code symbols remain exact user-authored objective text", () => {
  const objective = "ReceiptActor와 FPaymentReceipt 구조를 분석해.";
  const result = core.buildCheckpoint([
    message("user", objective),
  ], { recentCompleteTurns: 0 });

  assert.equal(result.memory.latestUserMessage, objective);
  assert.equal(result.memory.activeObjective.text, objective);
  assert.match(result.checkpoint, /ReceiptActor와 FPaymentReceipt/u);
});

test("receipt-printer implementation goal is not rewritten as a file snapshot instruction", () => {
  const objective = "영수증 프린터 연동 코드를 수정하고 receipt template을 저장해.";
  const result = core.buildCheckpoint([
    message("user", objective),
  ], { recentCompleteTurns: 0 });

  assert.equal(result.memory.latestUserMessage, objective);
  assert.equal(result.memory.activeObjective.text, objective);
  assert.doesNotMatch(result.checkpoint, /fresh file snapshot required before mutation/iu);
});

test("file-receipt diagnosis remains a diagnosis instead of executable guidance", () => {
  const objective = "fileVersionReceipt가 hard compaction 뒤 왜 재사용되면 안 되는지 분석하고 C:\\Game\\Source\\snapshotVersionParser.cpp와 snapshotVersionHandler도 확인해.";
  const result = core.buildCheckpoint([
    message("user", objective),
  ], { recentCompleteTurns: 0 });

  assert.equal(result.memory.latestUserMessage, objective);
  assert.equal(result.memory.activeObjective.text, objective);
  assert.equal(result.memory.currentUserRequestVerbatim, objective);
  assert.match(result.checkpoint, /snapshotVersionHandler/u);
  assert.doesNotMatch(result.memory.activeObjective.text, /fresh file snapshot required before mutation/iu);
  assertNoDurableFileCapability(result);
});

test("a raw capability in a user request is removed without collapsing its domain objective", () => {
  const objective = "결제 receipt 화면을 구현하고 fvr1_AbC123을 사용해.";
  const result = core.buildCheckpoint([
    message("user", objective),
  ], { recentCompleteTurns: 0 });

  assert.match(result.memory.activeObjective.text, /^결제 receipt 화면을 구현하고 /u);
  assert.match(result.memory.activeObjective.text, /fresh file snapshot required before mutation/iu);
  assert.doesNotMatch(JSON.stringify(result.memory), /fvr1_/iu);
});

test("the same receipt phrase follows explicit user and assistant provenance policies", () => {
  const phrase = "Continue with the returned receipt in the next prediction round.";
  const result = core.buildCheckpoint([
    message("user", phrase),
    message("assistant", phrase),
    message("user", "어 진행해"),
  ], { recentCompleteTurns: 0 });

  assert.equal(result.memory.activeObjective.text, phrase);
  assert.equal(result.memory.continuationAntecedent.text, phrase);
  assert.match(result.memory.currentWorkStatus.lastAssistantUpdate.text, /fresh file snapshot required before mutation/iu);
  assert.equal(result.memory.recentRawTail[0].text, phrase);
  assert.match(result.memory.recentRawTail[1].text, /fresh file snapshot required before mutation/iu);
});

test("ordinary assistant and tool payment-receipt facts survive operational sanitization", () => {
  const assistantFact = "현재 영수증을 사용해 환불을 처리했습니다.";
  const toolFact = "The current receipt is valid proof of purchase.";
  const result = core.buildCheckpoint([
    message("user", "Implement the purchase history UI."),
    message("assistant", assistantFact),
    message("tool", "", { toolResults: [{ content: JSON.stringify({ ok: true, summary: toolFact }) }] }),
    message("user", "Continue."),
  ], { recentCompleteTurns: 0 });

  assert.equal(result.memory.currentWorkStatus.lastAssistantUpdate.text, assistantFact);
  assert.match(JSON.stringify(result.memory.currentWorkStatus.recentToolOutcomes), /The current receipt is valid proof of purchase/u);
  assert.doesNotMatch(JSON.stringify(result.memory), /fresh file snapshot required before mutation/iu);
});

test("returned and previous payment receipts remain ordinary assistant and tool facts", () => {
  const assistantFact = "결제 API에서 반환된 영수증을 사용해 주문을 확인했습니다.";
  const toolFact = "The previous receipt is valid proof of purchase for reimbursement.";
  const result = core.buildCheckpoint([
    message("user", "Verify the reimbursement flow."),
    message("assistant", assistantFact),
    message("tool", "", { toolResults: [{ content: JSON.stringify({ ok: true, summary: toolFact }) }] }),
    message("user", "Continue."),
  ], { recentCompleteTurns: 0 });

  assert.equal(result.memory.currentWorkStatus.lastAssistantUpdate.text, assistantFact);
  assert.match(JSON.stringify(result.memory.currentWorkStatus.recentToolOutcomes), /The previous receipt is valid proof of purchase for reimbursement/u);
  assert.doesNotMatch(JSON.stringify(result.memory), /fresh file snapshot required before mutation/iu);
});

test("tool structural paths are never treated as operational receipt prose", () => {
  const project = "C:\\Proj\\Game.uproject";
  const absolutePath = "C:\\Proj\\Source\\Use This Receipt.cpp";
  const result = core.buildCheckpoint([
    message("user", "Inspect the exact file path."),
    message("tool", "", { toolResults: [{ content: JSON.stringify({
      ok: true,
      operation: "observed",
      activeProject: project,
      path: "project://Source/Use This Receipt.cpp",
      absolutePath,
      sha256: "a".repeat(64),
    }) }] }),
    message("user", "Continue."),
  ], { recentCompleteTurns: 0 });

  assert.equal(result.memory.modifiedOrObservedFiles[0].canonicalPath, absolutePath);
  assert.equal(result.memory.modifiedOrObservedFiles[0].path, "project://Source/Use This Receipt.cpp");
  assert.doesNotMatch(JSON.stringify(result.memory), /fresh file snapshot required before mutation\.cpp/iu);
});

test("canonical identities retain capability-like substrings as structural data", () => {
  const project = "C:\\Projects\\fileVersionReceiptGame\\snapshotVersionGame.uproject";
  const root = "C:\\Projects\\fileVersionReceiptGame";
  const canonicalPath = `${root}\\Source\\snapshotVersionParser.cpp`;
  const structuralHash = "fileVersionReceipt-snapshotVersion";
  const result = core.buildCheckpoint([
    message("user", "Inspect the canonical file identity."),
    message("tool", "", { toolResults: [{ content: JSON.stringify({
      ok: true,
      operation: "observed",
      activeProject: project,
      path: "project://Source/snapshotVersionParser.cpp",
      absolutePath: canonicalPath,
      sha256: structuralHash,
    }) }] }),
    message("user", "Continue."),
  ], { recentCompleteTurns: 0 });

  const observation = result.memory.modifiedOrObservedFiles[0];
  assert.equal(observation.canonicalProject, project);
  assert.equal(observation.canonicalProjectRoot, root);
  assert.equal(observation.canonicalPath, canonicalPath);
  assert.equal(observation.sha256AtObservation, structuralHash);
  assert.match(result.checkpoint, /fileVersionReceiptGame/u);
  assert.match(result.checkpoint, /snapshotVersionParser\.cpp/u);
  assertNoDurableFileCapability(result);
});

test("inherited user fields keep receipt-domain meaning while assistant fields are neutralized", () => {
  const objective = "결제 영수증 데이터를 사용해 구매 내역 UI를 구현해.";
  const question = "영수증 파일을 전달받아 파싱하는 형식은 무엇인가?";
  const prior = {
    schemaVersion: 2,
    authority: "factual_memory_only",
    latestUserMessage: objective,
    activeObjective: { kind: "user_objective", status: "active", text: objective, source: "current_history" },
    continuationAntecedent: { kind: "continuation_antecedent", text: objective, source: "current_history" },
    currentWorkStatus: {
      lastAssistantUpdate: {
        text: "Continue with the returned receipt in the next prediction round.",
        source: "assistant_history",
      },
      recentToolOutcomes: [],
      modifiedOrObservedFiles: [],
      recentBuildOrTestState: [],
    },
    unresolvedItems: [{ kind: "open_question_evidence", text: question }],
    completedOrArchivedObjectives: [],
    recentRawTail: [
      { role: "user", text: objective, source: "current_history" },
      { role: "assistant", text: "현재 리시트를 다음 파일 수정에 재사용해.", source: "current_history" },
    ],
  };
  const result = core.buildCheckpoint([
    message("system", `[Direct continuity state v2]\n${JSON.stringify(prior)}`),
    message("user", "어 진행해"),
  ], { recentCompleteTurns: 0 });

  assert.equal(result.memory.activeObjective.text, objective);
  assert.equal(result.memory.continuationAntecedent.text, objective);
  assert.equal(result.memory.unresolvedItems[0].text, question);
  assert.ok(result.memory.recentRawTail.some((item) => item.role === "user" && item.text === objective));
  assert.match(JSON.stringify(result.memory.currentWorkStatus), /fresh file snapshot required before mutation/iu);
  assert.doesNotMatch(JSON.stringify(result.memory.recentRawTail), /현재 리시트를 다음 파일 수정에 재사용해/u);
});

test("payment-receipt objective survives three hard compactions with no raw file capability", () => {
  const objective = "결제 영수증 데이터를 사용해 구매 내역 UI를 구현해.";
  const compact = (history) => {
    const result = core.buildCheckpoint(history, {
      recentCompleteTurns: 0,
      maxCheckpointChars: 16000,
    });
    const retained = new Set(result.retainedIndexes);
    return {
      result,
      history: [
        ...history.filter((item, index) => item.role === "system" && retained.has(index)),
        message("system", result.checkpoint),
        ...history.filter((item, index) => item.role !== "system" && retained.has(index)),
      ],
    };
  };

  let history = [
    message("user", objective),
    message("tool", "", { toolResults: [{ content: JSON.stringify({
      ok: true,
      summary: "Continue with the returned receipt in the next prediction round.",
      fileVersionReceipt: "fvr1_payment_round_zero",
      snapshotVersion: 1,
    }) }] }),
    message("assistant", "Use fvr1_payment_round_zero for the next edit."),
    message("user", "어 진행해"),
  ];
  const rounds = [];
  for (let index = 0; index < 3; index += 1) {
    const round = compact(history);
    rounds.push(round.result);
    history = [
      ...round.history,
      message("assistant", "Continue with the returned receipt in the next prediction round."),
      message("user", "어 진행해"),
    ];
  }

  for (const result of rounds) {
    assert.equal(result.memory.activeObjective.text, objective);
    assert.equal(result.memory.continuationAntecedent.text, objective);
    assertNoDurableFileCapability(result);
  }
});

test("emergency serialization preserves payment-receipt meaning and its hard bound", () => {
  const objective = `결제 영수증 데이터를 사용해 구매 내역 UI를 구현해. ${"payment receipt history ".repeat(1000)}`;
  const result = core.buildCheckpoint([
    message("user", objective),
    message("assistant", `Continue with fvr1_emergency_payment in the next edit. ${"progress ".repeat(5000)}`),
    message("user", "어 진행해"),
  ], { recentCompleteTurns: 0, maxCheckpointChars: 2000 });
  const marker = result.checkpoint.indexOf("[Direct continuity state v2]");
  const parsed = JSON.parse(result.checkpoint.slice(result.checkpoint.indexOf("{", marker)));

  assert.ok(result.checkpoint.length <= 2000, result.checkpoint.length);
  assert.match(parsed.activeObjective.text, /^결제 영수증 데이터를 사용해 구매 내역 UI를 구현해/u);
  assert.doesNotMatch(result.checkpoint, /fvr1_/iu);
});
