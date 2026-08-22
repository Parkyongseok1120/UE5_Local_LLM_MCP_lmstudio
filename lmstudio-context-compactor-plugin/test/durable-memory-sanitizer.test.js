"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const {
  sanitizeDurableText,
  sanitizeDurableValue,
} = require("../src/durable-memory-sanitizer.js");

test("durable sanitizer is recursive, non-mutating, and idempotent", () => {
  const input = {
    fileVersionReceipt: "fvr1_top_level",
    snapshot_version: 77,
    snapshotOwner: "opaque_receipt",
    nested: [{
      path: "project://Source/Foo.cpp",
      message: "Use receipt fvr1_nested_now for the next edit.",
      receipt: "fvr1_generic_receipt_key",
    }],
    serialized: JSON.stringify({
      fileVersionReceipt: "fvr1_inside_json_string",
      snapshotVersion: 78,
      summary: "The current valid receipt is fvr1_json_action.",
    }),
  };
  const before = structuredClone(input);
  const once = sanitizeDurableValue(input);
  const twice = sanitizeDurableValue(once);
  const serialized = JSON.stringify(once);

  assert.deepEqual(input, before);
  assert.deepEqual(twice, once);
  assert.doesNotMatch(serialized, /fvr1_|fileVersionReceipt|snapshotVersion|snapshot_version|snapshotOwner/iu);
  assert.match(serialized, /fresh file snapshot required before mutation/iu);
  assert.deepEqual(JSON.parse(once.serialized), {
    summary: "fresh file snapshot required before mutation.",
  });
});

test("English and Korean executable receipt prose becomes a fresh-read fact", () => {
  for (const value of [
    "Retry with the previous receipt.",
    "The receipt for this file is still valid.",
    "이 영수증으로 수정해.",
    "receipt로 다시 시도해.",
    "현재 유효한 리시트를 사용해.",
    "현재 receipt로 계속 진행해.",
    "Keep using the receipt you already have.",
    "The previous receipt remains valid.",
    "The current receipt still works.",
  ]) {
    const sanitized = sanitizeDurableText(value);
    assert.match(sanitized, /fresh file snapshot required before mutation/iu);
    assert.doesNotMatch(sanitized, /fvr1_|previous receipt|receipt for this file|영수증|리시트/iu);
  }
});

test("a descriptive anti-persistence requirement remains understandable", () => {
  const sanitized = sanitizeDurableText(
    "fileVersionReceipt를 durable memory에 저장하지 마라. snapshotVersion은 파일 변경 증거가 아니다.",
  );

  assert.match(sanitized, /ephemeral file-mutation capability/iu);
  assert.match(sanitized, /durable memory에 저장하지 마라/u);
  assert.match(sanitized, /registry observation counter은 파일 변경 증거가 아니다/iu);
  assert.doesNotMatch(sanitized, /fileVersionReceipt|snapshotVersion/iu);
});

test("receipt-safety objectives keep their prohibition, diagnosis, and clone scope", () => {
  for (const objective of [
    "Never reuse the receipt from Clone A; inspect Clone B instead.",
    "Explain how receipt reuse is prevented without changing the server/controller.",
    "Diagnose why receipt reuse crosses same-name clones and fix canonical project/path association.",
    "Receipt reuse is blocked after compaction and rejected by a new runtime.",
    "Never keep using the previous receipt.",
    "Verify whether the previous receipt remains valid after restart.",
    "The previous receipt is no longer valid.",
  ]) {
    assert.equal(sanitizeDurableText(objective), objective);
  }
});

test("mixed descriptive clauses cannot whitelist a later receipt instruction", () => {
  for (const value of [
    "Do not take another snapshot and use the previous receipt now.",
    "Without a fresh read, reuse the previous receipt now.",
    "Ensure you use the previous receipt for the next mutation.",
    "Explain the risk later and reuse the current receipt now.",
    "Diagnose the failure, then retry with the previous receipt.",
    "Never reuse the old receipt or use the previous receipt now.",
    "Do not reuse the old receipt — use the current receipt for the mutation.",
    "Never reuse old receipt: use the current receipt instead.",
    "Explain why reuse of receipts is unsafe or use the previous receipt now.",
    "apply_edit_bundle: duplicate patches[] paths are not allowed; use one focused region per file and continue with the returned receipt in the next prediction round",
    "설명하지 말고 현재 리시트를 재사용해.",
    "분석은 나중에 하고 receipt를 재사용해.",
  ]) {
    const sanitized = sanitizeDurableText(value);
    assert.match(sanitized, /fresh file snapshot required before mutation/iu);
    assert.notEqual(sanitized, value);
    assert.doesNotMatch(sanitized, /(?:and|or|then|—|:|,)\s*(?:use|reuse|retry)[^.!?]{0,80}receipt/iu);
    assert.doesNotMatch(sanitized, /(?:말고|하고|하거나|또는)\s*(?:현재\s*)?(?:리시트[^.!?]{0,80}재사용|receipt[^.!?]{0,80}재사용)/iu);
  }
  assert.equal(
    sanitizeDurableText("Never reuse the receipt from Clone A."),
    "Never reuse the receipt from Clone A.",
  );
  assert.equal(
    sanitizeDurableText("현재 receipt를 재사용하지 마라."),
    "현재 receipt를 재사용하지 마라.",
  );
});

test("the production bundle retry message cannot become durable capability guidance", () => {
  const source = "apply_edit_bundle: duplicate patches[] paths are not allowed; use one focused region per file and continue with the returned receipt in the next prediction round";
  const sanitized = sanitizeDurableText(source);

  assert.doesNotMatch(sanitized, /continue with the returned receipt/iu);
  assert.match(sanitized, /fresh file snapshot required before mutation/iu);
});

test("Markdown and identifier adjacency cannot hide forbidden capability literals", () => {
  const sanitized = sanitizeDurableText(
    "_fvr1_AbC-123_ prefix_fvr1_XYZ _fileVersionReceipt_ _snapshotVersion_",
  );

  assert.doesNotMatch(sanitized, /fvr1_|fileVersionReceipt|snapshotVersion/iu);
  assert.match(sanitized, /ephemeral file capability omitted/iu);
  assert.match(sanitized, /ephemeral file-mutation capability/iu);
  assert.match(sanitized, /registry observation counter/iu);
});

test("raw receipt tokens cannot leave executable reuse prose behind", () => {
  for (const source of [
    "Use fvr1_AbC123 for the next edit.",
    "I will reuse fvr1_AbC123 now.",
    "Pass fvr1_AbC123 to replace_in_file.",
  ]) {
    const sanitized = sanitizeDurableText(source);
    assert.match(sanitized, /fresh file snapshot required before mutation/iu);
    assert.doesNotMatch(sanitized, /\b(?:use|reuse|pass)\b[^.!?]{0,100}ephemeral file capability omitted/iu);
  }
});

test("meta-level safety objectives keep their prohibition without replaying quoted guidance", () => {
  assert.equal(
    sanitizeDurableText('The phrase "continue with the returned receipt" must never be retained.'),
    'The phrase "receipt-reuse wording" must never be retained.',
  );
  assert.equal(
    sanitizeDurableText("Block instructions to continue with the returned receipt."),
    "Block receipt-reuse instructions.",
  );
  assert.equal(
    sanitizeDurableText('Ensure "continue with the returned receipt" is rejected.'),
    'Ensure "receipt-reuse wording" is rejected.',
  );
  assert.equal(
    sanitizeDurableText('"현재 리시트를 재사용해"라는 지시는 저장하지 마라.'),
    '"receipt-reuse wording"라는 지시는 저장하지 마라.',
  );
  assert.equal(
    sanitizeDurableText('"현재 receipt로 계속 진행해"라는 지시는 저장하지 마라.'),
    '"receipt-reuse wording"라는 지시는 저장하지 마라.',
  );
  for (const [source, expected] of [
    ["Prevent using the previous receipt.", "Prevent receipt-reuse instructions."],
    ["Block use of the previous receipt.", "Block receipt-reuse instructions."],
    ["Reject attempts to supply the prior receipt.", "Reject receipt-reuse instructions."],
  ]) {
    assert.equal(sanitizeDurableText(source), expected);
  }
});

test("JSON Unicode escapes cannot reconstruct a raw file capability", () => {
  for (const source of [
    String.raw`Supply \u0066vr1_live_capability to replace_in_file now.`,
    String.raw`Use \u0066vr1_live_capability for the next edit.`,
    String.raw`Pass f\u0076r1_live_capability to replace_in_file.`,
    String.raw`Use \u005cu0066vr1_nested_capability for the next edit.`,
  ]) {
    const sanitized = sanitizeDurableText(source);
    const oneJsonDecode = JSON.parse(`"${sanitized.replace(/"/gu, '\\"')}"`);
    assert.doesNotMatch(sanitized, /fvr1_|\\u00(?:66|76|72|31|5f)|fileVersionReceipt/iu);
    assert.doesNotMatch(oneJsonDecode, /fvr1_/iu);
    assert.doesNotMatch(sanitized, /\b(?:supply|use|pass)\b[^.!?]{0,120}(?:capability omitted|receipt)/iu);
  }
});

test("sanitizer bounds inherited arrays and recursive depth", () => {
  const many = Array.from({ length: 120 }, (_, index) => ({ index }));
  let deep = { value: "safe" };
  for (let index = 0; index < 30; index += 1) deep = { child: deep };
  const sanitized = sanitizeDurableValue({ many, deep });

  assert.equal(sanitized.many.length, 80);
  assert.match(JSON.stringify(sanitized.deep), /depth limited/u);
});
