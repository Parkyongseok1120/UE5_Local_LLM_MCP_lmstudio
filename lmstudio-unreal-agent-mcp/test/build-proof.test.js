"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { parseBuildProof } = require("../src/build-proof");

test("executor setup lines do not yield Built", () => {
  const proof = parseBuildProof(true, "Building 4 actions with 4 processes");
  assert.notEqual(proof.proofLevel, "Built");
});

test("compile actions yield Built", () => {
  const proof = parseBuildProof(true, "[1/2] Compile Demo.cpp");
  assert.equal(proof.proofLevel, "Built");
});
