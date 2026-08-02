"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const test = require("node:test");

process.env.UNREAL58_ROOT = path.resolve(__dirname, "../..");

const {
  validateMutationSemanticText,
  probeMutationSemanticGuard,
} = require("../src/mutation-semantic-guard");

test("prospective mutation rejects reverse turn clamped on a later line", () => {
  const result = validateMutationSemanticText(`
int32 URuleEngine::AdvanceTurnIndex(int32 CurrentIndex, int32 Direction) const
{
  const int32 ActiveCount = ActivePlayerIndices.Num();
  int64 Next = static_cast<int64>(CurrentIndex) + static_cast<int64>(Direction);
  Next = FMath::Max(Next, 0LL);
  return static_cast<int32>(Next % ActiveCount);
}`);

  assert.strictEqual(result.ok, false);
  assert.ok(result.hits.some((hit) => hit.term === "turn_direction_clamped_instead_of_wrapped"));
});

test("prospective mutation accepts positive modulo traversal", () => {
  const result = validateMutationSemanticText(`
int32 URuleEngine::AdvanceTurnIndex(int32 CurrentIndex, int32 Direction) const
{
  const int32 ActiveCount = ActivePlayerIndices.Num();
  const int32 Next = ((CurrentIndex + Direction) % ActiveCount + ActiveCount) % ActiveCount;
  return Next;
}`);

  assert.strictEqual(result.ok, true);
  assert.deepStrictEqual(result.hits, []);
});

test("missing guard script fails closed", () => {
  const previous = process.env.UNREAL58_ROOT;
  const fakeRoot = fs.mkdtempSync(path.join(os.tmpdir(), "mutation-guard-missing-"));
  try {
    process.env.UNREAL58_ROOT = fakeRoot;
    const result = validateMutationSemanticText("int32 Next = FMath::Max(Next, 0LL);");
    assert.strictEqual(result.ok, false);
    assert.strictEqual(result.infrastructureError, true);
    assert.match(String(result.reason || ""), /missing/i);
  } finally {
    process.env.UNREAL58_ROOT = previous;
    fs.rmSync(fakeRoot, { recursive: true, force: true });
  }
});

test("startup probe reports healthy guard under repo UNREAL58_ROOT", () => {
  const health = probeMutationSemanticGuard();
  assert.strictEqual(health.ok, true);
  assert.strictEqual(health.present, true);
  assert.strictEqual(health.importable, true);
  assert.strictEqual(health.pythonProbe, true);
});
