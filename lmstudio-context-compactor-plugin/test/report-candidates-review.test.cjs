"use strict";
// Revised plan 2026-11-07 §F3/§C/H5. Conversation 1793984074550 ended with a
// partial report of zero findings while the register held several; a forced
// final report could also say "no major issue" without the omission review.
const test = require("node:test");
const assert = require("node:assert/strict");
const { Chat, ChatMessage } = require("@lmstudio/sdk");
const { ReviewRegister, reviewCandidateLines } = require("../dist/review-register");
const { DeliveryController } = require("../dist/delivery-controller");
const { cleanVerdictScopeGap } = require("../dist/report-coverage");
const { reportReviewObligations } = require("../dist/report-scope");
const { fakeController, handlePredictionLoop } = require("./helpers/prediction-loop-fixture.cjs");

const say = text => [ChatMessage.create("assistant", text)];

test("a retraction reaches the claim it names and nothing else", () => {
  const one = new ReviewRegister();
  one.observe(say("`TakeDamage` bug at Source/Combat.cpp:10; it misses the damage update."), 1);
  one.observe(say("`TakeDamage` is not a bug. The caller already applies the damage."), 2);
  assert.deepEqual(one.candidates(), [], "the single file-qualified claim is cleared");
  const two = new ReviewRegister();
  two.observe(say("`TakeDamage` bug at Source/Combat.cpp:10; it misses the damage update."), 1);
  two.observe(say("`TakeDamage` bug at Source/Other.cpp:5; it double-applies damage."), 2);
  two.observe(say("`TakeDamage` is not a bug."), 3);
  assert.deepEqual(two.candidates().map(row => row.status), ["needs_recheck", "needs_recheck"], "an ambiguous retraction asks for a recheck");
  const line = new ReviewRegister();
  line.observe(say("`TakeDamage` bug: missing damage. Later verification: `TakeDamage` is not a bug."), 1);
  assert.deepEqual(line.candidates(), [], "a later sentence in the same line decides");
  const other = new ReviewRegister();
  other.observe(say("`ServerFire` bug at Source/Weapon.cpp:3; results never reach remote clients."), 1);
  other.observe(say("`TakeDamage` is not a bug."), 2);
  assert.deepEqual(other.candidates().map(row => row.identifier), ["ServerFire"]);
});

test("candidate lines are bounded, newest first, and never called confirmed", () => {
  assert.deepEqual(reviewCandidateLines([]), []);
  const register = new ReviewRegister();
  for (let i = 0; i < 20; i++) register.observe(say(`\`Subject${String.fromCharCode(65 + i)}Thing\` bug at Source/F${i}.cpp:${i + 1}; wrong.`), i + 1);
  const lines = reviewCandidateLines(register.candidates());
  assert.match(lines[0], /미검증 검토 후보 \(작업 메모이며 확인된 결함이 아님\)/u);
  assert.equal(lines.filter(line => line.startsWith("- Subject")).length, 12);
  assert.match(lines.at(-2), /외 8개는 길이 제한으로 생략/u);
  assert.match(lines[1], /SubjectTThing/u, "newest first");
});

test("the delivery owner exists before initialization and puts candidates before evidence once bound", () => {
  const owner = new DeliveryController();
  assert.equal(owner.finish().reportDelivered, false, "an unbound owner can finish an initialization failure");
  const register = new ReviewRegister();
  register.observe(say("`ServerFire` bug at Source/Weapon.cpp:3; results never reach remote clients."), 1);
  owner.bindExecution([], { kind: "review", requiredEffects: new Set(["read"]), mutationState: "none" }, () => register.candidates());
  const report = owner.partialReport(Chat.from([{ role: "user", content: "Review" }]), "research_round_limit");
  const text = report.text;
  assert.ok(text.indexOf("ServerFire") > 0 && text.indexOf("ServerFire") < text.indexOf("확인된 근거:"));
});

const inventory = (files, directories = [{ path: "project://Source/Game", complete: true, subdirectories: [], files: files.length }], overflow = false) =>
  ({ listed: { overflow, directories, files: files.map(([name, coverage]) => ({ path: `project://Source/Game/${name}`, sourceKey: name, coverage })) } });

test("a clean verdict is limited to the files actually read", () => {
  const report = "## 리뷰\n- A는 정상.\n\n## 결론\n**P0/P1 결함 없음.** 서버 권위 계약이 일관됩니다.";
  const gap = cleanVerdictScopeGap(report, inventory([["A.cpp", "complete"], ["B.cpp", "unread"], ["C.cpp", "partial"]]));
  assert.equal(gap.kind, "clean_verdict_scope");
  assert.match(gap.message, /P0\/P1 없음 결론이 읽은 범위를 넘습니다/u);
  assert.match(gap.message, /Game\/B\.cpp\(미열람\)/u);
  assert.match(gap.message, /Game\/C\.cpp\(일부만 읽음\)/u);
  assert.match(gap.notice, /\[확인 범위\]/u);
  assert.equal(cleanVerdictScopeGap(report, inventory([["A.cpp", "complete"], ["B.cpp", "complete"]])), null, "everything listed was read");
  assert.equal(cleanVerdictScopeGap("## 결론\n읽은 범위 내에서는 결함 없음.", inventory([["B.cpp", "unread"]])), null, "already scoped");
  assert.equal(cleanVerdictScopeGap("## 표\n| 항목 | ✅ 문제 없음 |\n\n## 결론\nP1 두 건을 발견했습니다.", inventory([["B.cpp", "unread"]])), null,
    "a table row of a non-issue is not a verdict");
  assert.equal(cleanVerdictScopeGap(report, { listed: null }), null, "no listing, no scope claim");
  assert.equal(cleanVerdictScopeGap(`${report}\n\n## 검토 보완\n- 미확인 범위: B.cpp는 읽지 않았습니다.`, inventory([["B.cpp", "unread"]])), null,
    "a supplement that names the unread file resolves it");
  const sub = cleanVerdictScopeGap(report, inventory([["A.cpp", "complete"]],
    [{ path: "project://Source/Game", complete: true, subdirectories: ["Components"], files: 1 }]));
  assert.match(sub.message, /열어 보지 않은 하위 디렉터리 1개: project:\/\/Source\/Game\/Components/u);
  assert.match(cleanVerdictScopeGap(report, inventory([["A.cpp", "complete"]], undefined, true)).message, /상한/u);
});

test("the scope gap is an obligation for review and explain, not for a change request", () => {
  const register = new ReviewRegister();
  const history = Chat.from([{ role: "user", content: "Review" }]);
  const answer = "## 결론\n결함 없음.";
  const source = { files: [], contractIds: new Set(), scope: "execution", ...inventory([["B.cpp", "unread"]]) };
  for (const kind of ["review", "explain"]) {
    const review = reportReviewObligations(history, answer, source, "unreal", kind, register);
    assert.equal(review.needed, true, kind);
    assert.ok(review.telemetry.scopeGaps.includes("clean_verdict_scope"));
  }
  assert.equal(reportReviewObligations(history, answer, source, "unreal", "mutate", register).telemetry.scopeGaps.includes("clean_verdict_scope"), false);
});

const read = { name: "read_file", pluginIdentifier: "mcp/unreal-agent", description: "Read source",
  parametersJsonSchema: { type: "object", properties: { path: { type: "string" } } } };
async function emitRead(opts, index, claim) {
  const request = { type: "function", id: `cand-${index}`, name: "read_file", arguments: { path: `project://Source/Test${index}.cpp` } };
  let effective;
  await opts.guardToolCall(0, index, { toolCallRequest: request, allow() { effective = request.arguments; },
    allowAndOverrideParameters(args) { effective = args; }, deny(reason) { throw new Error(reason); } });
  opts.onPredictionCompleted({ stats: { stopReason: "toolCalls" } });
  if (claim) opts.onMessage(ChatMessage.create("assistant", claim));
  opts.onMessage(ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: { ...request, arguments: effective } }] }));
  opts.onMessage(ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: request.id,
    content: JSON.stringify({ ok: true, kind: "workspace_file_observation", path: request.arguments.path, canonicalProject: "C:/Game/Game.uproject",
      sha256: String(index).repeat(64), startLine: 1, endLine: 1, totalLines: 1, content: "void TakeDamage() {}" }) }] }));
  opts.onRoundEnd();
  throw opts.signal.reason;
}
const prompt = async (chat, options = {}) => JSON.stringify({ text: chat.toString(), tools: (options.toolDefinitions || []).map(tool => tool.function.name) });
function forcedModel(answers) {
  const finals = [];
  let tools = 0;
  return { finals, model: { identifier: "forced-review", async getContextLength() { return 65536; }, applyPromptTemplate: prompt,
    async countTokens() { return 1000; },
    async act(chat, toolList, opts) {
      if (toolList.length) { tools++; return emitRead(opts, tools, "`TakeDamage` bug at Source/Test1.cpp:1; damage is never applied."); }
      finals.push(chat.toString());
      opts.onPredictionCompleted({ stats: { stopReason: "eosFound" } });
      opts.onMessage(ChatMessage.create("assistant", answers[finals.length - 1] ?? "done"));
    } } };
}

test("a forced final report that omits an open candidate gets the one review round", async () => {
  const { finals, model } = forcedModel(["The requested review is complete. No major issue was found.",
    "## 검토 보완\n- `TakeDamage`: 미검증 후보로 남깁니다."]);
  const ctl = fakeController(Chat.from([{ role: "user", content: "Review the damage implementation." }]), model,
    { maxResearchRounds: 1, toolStagnationAction: "off", reportReviewMode: "on" }, [read]);
  await handlePredictionLoop(ctl);
  const scheduled = ctl.debugValues.filter(event => event.event === "report_review_scheduled");
  assert.equal(scheduled.length, 1);
  assert.equal(scheduled[0].afterFinalReport, true);
  assert.equal(finals.length, 2, "the final report and one review, no more");
  assert.match(finals[1], /TakeDamage/u, "the review input names the omitted candidate");
});

test("with the review switched off a forced final report is not reopened", async () => {
  const { finals, model } = forcedModel(["No major issue was found."]);
  const ctl = fakeController(Chat.from([{ role: "user", content: "Review the damage implementation." }]), model,
    { maxResearchRounds: 1, toolStagnationAction: "off", reportReviewMode: "off" }, [read]);
  await handlePredictionLoop(ctl);
  assert.equal(finals.length, 1);
  assert.equal(ctl.debugValues.filter(event => event.event === "report_review_scheduled").length, 0);
});

test("review fixes: wording, partial naming and bounded audits", async () => {
  const listed = inventory([["A.cpp", "unread"], ["B.cpp", "unread"], ["C.cpp", "unread"], ["D.cpp", "unread"], ["E.cpp", "unread"], ["F.cpp", "unread"]]);
  assert.equal(cleanVerdictScopeGap("## 결론\n빌드는 문제없이 통과했고 P1 두 건을 고쳐야 합니다.", listed), null,
    "\"문제없이\" describes how something ran, not a verdict");
  const report = "## 결론\n결함 없음.";
  // Conversation 1794306949131's own wording, with the severity in parentheses.
  const parenthesized = cleanVerdictScopeGap("**결론**: 코어 아키텍처는 잘 되어 있고 구조적 결함(P0/P1)은 발견되지 않았습니다.", listed);
  assert.match(parenthesized.message, /P0\/P1 없음 결론/u);
  assert.ok(cleanVerdictScopeGap(`${report}\n\n## 검토 보완\n- 미확인 범위: A.cpp`, listed), "naming one of six unread files does not scope the verdict");
  assert.equal(cleanVerdictScopeGap(`${report}\n\n## 검토 보완\n- 미확인 범위: A.cpp, B.cpp, C.cpp, D.cpp, E.cpp 외 1개`, listed), null);
  // A bounded audit keeps its one final attempt; the omission goes to the terminal notice.
  const { finals, model } = forcedModel(["No major issue was found."]);
  const ctl = fakeController(Chat.from([{ role: "user", content: "Review the damage implementation." }]), model,
    { maxResearchRounds: 1, toolStagnationAction: "off", reportReviewMode: "on", auditCompletionMode: "bounded", auditResearchRounds: 1 }, [read]);
  await handlePredictionLoop(ctl);
  assert.equal(finals.length, 1);
  assert.equal(ctl.debugValues.filter(event => event.event === "report_review_scheduled").length, 0);
});

// Conversation 1794306949131, round 52: the review round printed tool-call text
// instead of a supplement. The harness replanned, reopened 38 tools and the
// model wrote the whole report a second time.
test("a review round that asks for tools neither reopens research nor adds a partial report", async () => {
  const surfaces = [];
  const model = { identifier: "review-raw-intent", async getContextLength() { return 65536; }, applyPromptTemplate: prompt,
    async countTokens() { return 1000; },
    async act(chat, toolList, opts) {
      surfaces.push(toolList.length);
      if (surfaces.length === 1) return emitRead(opts, 1, "`TakeDamage` bug at Source/Test1.cpp:1; damage is never applied.");
      opts.onPredictionCompleted({ stats: { stopReason: "eosFound" } });
      opts.onMessage(ChatMessage.create("assistant", surfaces.length === 2
        ? "## 결론\nThe weapon path is consistent within the files read."
        : "마지막으로 무기 경로를 확인하겠습니다.\n<tool_call><function=read_file><parameter=path>project://Source/Weapon.cpp</parameter></function></tool_call>"));
    } };
  const ctl = fakeController(Chat.from([{ role: "user", content: "Review the damage implementation." }]), model,
    { toolStagnationAction: "off", reportReviewMode: "on" }, [read]);
  await handlePredictionLoop(ctl);
  const count = name => ctl.debugValues.filter(event => event.event === name).length;
  assert.equal(count("report_review_scheduled"), 1);
  assert.deepEqual(surfaces, [1, 1, 0], "one read, the report, one tool-free review; nothing after it");
  assert.equal(count("fresh_tool_planning_retry_scheduled"), 0);
  assert.equal(count("read_recovery_resumed_original"), 0);
  assert.equal(count("partial_report_delivery"), 0);
  assert.equal(ctl.blocks.some(block => /부분 조사 보고/u.test(block.text)), false);
  const outcome = ctl.debugValues.filter(event => event.event === "execution_outcome").at(-1);
  assert.equal(outcome.executionOutcome, "completed");
  assert.equal(outcome.reportCompleted, true);
  assert.ok(ctl.statuses.some(status => /검토 보완 단계에서는 도구를 쓸 수 없어/u.test(status.state?.text || "")));
  // The omitted candidate still reaches the user through the terminal notice.
  assert.ok(ctl.blocks.some(block => /\[보고 검토 · 누락\]/u.test(block.text) && /TakeDamage/u.test(block.text)));
});
