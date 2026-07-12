"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { parseBuildProof } = require("../src/build-proof");

test("executor setup with compile yields Built", () => {
  const proof = parseBuildProof(
    true,
    "Building 4 actions with 4 processes\n[1/4] Compile Demo.cpp",
  );
  assert.equal(proof.proofLevel, "Built");
  assert.equal(proof.compileLineCount, 1);
  assert.equal(proof.executorOnly, false);
});

test("executor setup lines do not yield Built", () => {
  const proof = parseBuildProof(true, "Building 4 actions with 4 processes");
  assert.notEqual(proof.proofLevel, "Built");
  assert.equal(proof.executorOnly, true);
});

test("compile actions yield Built", () => {
  const proof = parseBuildProof(true, "[1/2] Compile Demo.cpp");
  assert.equal(proof.proofLevel, "Built");
  assert.equal(proof.compileLineCount, 1);
});

test("large action denominator keeps compileLineCount at 1", () => {
  const proof = parseBuildProof(true, "[1/100] Compile Demo.cpp");
  assert.equal(proof.compileLineCount, 1);
  assert.equal(proof.declaredTotalActions, 100);
  assert.equal(proof.proofLevel, "Built");
});
