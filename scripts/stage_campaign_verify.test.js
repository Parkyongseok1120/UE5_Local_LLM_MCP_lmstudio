"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
  stripCppComments,
  scanForbidden,
  signatureMatches,
} = require("./stage_campaign_verify");

test("stripCppComments removes line and block comments", () => {
  const raw = [
    "int A = 1; // TArray<AActor*> in comment",
    "/* TArray<AActor*> also here */",
    "void F() { TArray<int32> X; }",
  ].join("\n");
  const stripped = stripCppComments(raw);
  assert.equal(/TArray\s*<\s*AActor\s*\*>/.test(stripped), false);
  assert.equal(/TArray\s*<\s*int32\s*>/.test(stripped), true);
});

test("scanForbidden ignores forbidden tokens that only appear in comments", () => {
  const text = [
    "// Find board without TArray<AActor*>.",
    "void Place() { /* not TArray<AActor*> */ }",
    "UInstancedStaticMeshComponent* Stones;",
  ].join("\n");
  const hits = scanForbidden(text, ["TArray<AActor\\*>", "625"]);
  assert.deepEqual(hits, []);
});

test("scanForbidden still flags real code matches", () => {
  const text = "TArray<AActor*> Cells; // real ownership array";
  const hits = scanForbidden(text, ["TArray<AActor\\*>"]);
  assert.deepEqual(hits, ["TArray<AActor\\*>"]);
});

test("signatureMatches supports plain and regex patterns", () => {
  assert.equal(signatureMatches("void SetupInputComponent()", "SetupInputComponent"), true);
  assert.equal(signatureMatches("UFUNCTION(Server)", "UFUNCTION\\(Server"), true);
  assert.equal(signatureMatches("client only", "UFUNCTION\\(Server"), false);
});
