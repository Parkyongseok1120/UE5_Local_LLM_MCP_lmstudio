"use strict";

// A short "continue" turn must keep the prior objective; a substantive request
// that merely contains "계속" must not be hidden as a continuation.
const test = require("node:test");
const assert = require("node:assert/strict");
const { ChatMessage } = require("@lmstudio/sdk");
const { classifyContinuation, looksElliptical } = require("../src/continuity-text.js");
const { buildObjectiveContinuity } = require("../src/continuity-objectives.js");
const notes = require("../src/continuity-model-notes.js");

const CONTINUATIONS = [
  "계속", "계속해", "계속해줘", "계속 진행해", "계속 진행해줘", "계속해서 진행해", "계속해서 진행해줘",
  "이어서 해줘", "이어서 진행", "마저 해줘", "다시 진행해", "이제 계속 진행해줘", "응 계속해", "진행해줘",
  "하던 거 마저 해줘", "중단된 작업 계속해줘", "이어서 분석 진행해줘", "그거 해줘", "해줘", "네",
  "continue please", "go ahead", "go on", "keep going", "resume", "continue where you left off",
];
const SUBSTANTIVE = [
  "계속 실패하는 원인 찾아줘", "계속해서 Source/Foo.cpp 읽어줘", "계속 할까?", "이제 빌드 오류를 고쳐줘",
  "다음은 UI를 수정해줘", "계속 에러가 나", "go ahead and delete Saved folder", "read_file 다시 호출해",
];

test("continuation phrases are classified as continuations", () => {
  for (const text of CONTINUATIONS) assert.equal(classifyContinuation(text).continuation, true, text);
  for (const text of CONTINUATIONS) assert.equal(looksElliptical(text), true, text);
});

test("substantive requests containing a continuation word stay substantive", () => {
  for (const text of SUBSTANTIVE) assert.equal(classifyContinuation(text).continuation, false, text);
});

test("a continuation turn keeps the objective fingerprint and antecedent", () => {
  const goal = "Source/Foo.cpp의 수명 관리 문제를 분석해줘";
  const base = [ChatMessage.create("user", goal)];
  for (const phrase of ["계속해서 진행해줘", "이어서 해줘", "마저 해줘", "이제 계속 진행해줘"]) {
    const resumed = [...base, ChatMessage.create("assistant", "절반 분석했습니다."), ChatMessage.create("user", phrase)];
    assert.equal(notes.objectiveFingerprint(resumed), notes.objectiveFingerprint(base), phrase);
    const continuity = buildObjectiveContinuity(resumed.map((message, index) => ({
      role: message.getRole(), text: message.getText(), index, toolRequests: [] })));
    assert.equal(continuity.activeObjective.text, goal, phrase);
    assert.equal(continuity.continuationAntecedent.text, goal, phrase);
  }
});

test("a continuation that starts with 이제 does not supersede a numbered selection", () => {
  const messages = [
    { role: "user", text: "문제를 찾아줘", index: 0, toolRequests: [] },
    { role: "assistant", text: "1. 첫 문제\n2. 둘째 문제", index: 1, toolRequests: [] },
    { role: "user", text: "1번 수정해줘", index: 2, toolRequests: [] },
    { role: "user", text: "이제 계속 진행해줘", index: 3, toolRequests: [] },
  ];
  assert.equal(buildObjectiveContinuity(messages).taskSelection.status, "active");
});
