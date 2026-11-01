"use strict";
// Conversation 1793526507401 showed review-register rows in the answer: the model
// wrote "- [found] <multi-word label> (reasoning, round N): …" lines with their
// "  ↳ …" evidence lines. The row shape accepted only a one-token label, and a
// streamed indented evidence line passed before it could be classified.
const test = require("node:test");
const assert = require("node:assert/strict");
const { REVIEW_REGISTER_HEADER, REVIEW_REGISTER_GUIDANCE } = require("../dist/review-register");
const { InternalDataOutputFilter } = require("../dist/internal-data-output");

const register = [REVIEW_REGISTER_HEADER, "- [found] Earlier row (reasoning, round 3): x", REVIEW_REGISTER_GUIDANCE].join("\n");
const leaked = [
  "- [found] AActor::TakeDamage engine body (reasoning, round 7): Key findings so far: the body does not touch the health component.",
  "  ↳ engine://Engine/Source/Runtime/Engine/Private/Actor.cpp:3390 @bf47164a · code: float AActor::TakeDamage(...)",
  "- [needs_recheck] TakeDamage flow (visible, round 9): reads CombatCharacter.cpp:161",
  "  ↳ project://Source/Game/Private/CombatCharacter.cpp:161 @86d29ef0 · code: float ACombatCharacter::TakeDamage(",
  "",
  "엔진 `AActor::TakeDamage`는 체력 컴포넌트를 직접 건드리지 않습니다.",
].join("\n");

function run(parts, text, size) {
  const filter = new InternalDataOutputFilter(parts);
  let output = "";
  for (let i = 0; i < text.length; i += size) output += filter.push(text.slice(i, i + size));
  return output + filter.finish();
}

for (const size of [1, 2, 9, 5000]) test(`model-written register rows and evidence lines stay hidden (fragments=${size})`, () => {
  const output = run([register], leaked, size);
  assert.doesNotMatch(output, /\[(?:found|needs_recheck)\]/u);
  assert.doesNotMatch(output, /↳/u);
  assert.match(output, /엔진 `AActor::TakeDamage`는 체력 컴포넌트를 직접 건드리지 않습니다\./u);
  // Ordinary indented text and list items are user content.
  const ordinary = "  indented code\n- list item (see round 2): fine\n";
  assert.equal(run([register], ordinary, size), ordinary);
  // Without an injected register the same shapes are not hidden.
  assert.equal(run([], leaked, size), leaked);
});
