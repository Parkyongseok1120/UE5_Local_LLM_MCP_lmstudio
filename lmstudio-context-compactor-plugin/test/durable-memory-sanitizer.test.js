"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const {
  sanitizeDerivedOperationalRecord,
  sanitizeDerivedOperationalText,
  sanitizeDerivedOperationalValue,
  sanitizeDurableText,
  sanitizeDurableValue,
  sanitizeStructuredDurableValue,
  sanitizeUserAuthoredText,
} = require("../src/durable-memory-sanitizer.js");

test("user-authored receipt-domain language is byte-for-byte preserved", () => {
  for (const value of [
    "결제 영수증 데이터를 사용해 구매 내역 UI를 구현해.",
    "Implement a payment receipt parser and display the receipt history.",
    "ReceiptActor와 FPaymentReceipt 구조를 분석해.",
    "영수증 프린터 연동 코드를 수정하고 receipt template을 저장해.",
    "영수증 파일을 전달받아 파싱해.",
  ]) {
    assert.equal(sanitizeUserAuthoredText(value), value);
  }
});

test("structured raw-capability policy removes exact fields without erasing receipt-domain data", () => {
  const source = {
    latestUserMessage: "결제 영수증 데이터를 사용해 구매 내역 UI를 구현해.",
    receipt: "payment receipt #42",
    fileVersionReceipt: "fvr1_live_top",
    nested: {
      snapshotReceipt: "fvr1_live_nested",
      summary: "ReceiptActor와 FPaymentReceipt 구조를 분석해.",
    },
    serialized: JSON.stringify({
      mutationReceipt: "fvr1_live_json",
      objective: "영수증 프린터 연동 코드를 수정해.",
    }),
  };
  const sanitized = sanitizeStructuredDurableValue(source);

  assert.equal(sanitized.latestUserMessage, source.latestUserMessage);
  assert.equal(sanitized.receipt, source.receipt);
  assert.equal(sanitized.nested.summary, source.nested.summary);
  assert.deepEqual(JSON.parse(sanitized.serialized), {
    objective: "영수증 프린터 연동 코드를 수정해.",
  });
  assert.doesNotMatch(JSON.stringify(sanitized), /fvr1_|fileVersionReceipt|snapshotReceipt|mutationReceipt/iu);
});

test("structured policy preserves capability-like substrings in identity and hash values", () => {
  const source = {
    canonicalProject: "C:\\Projects\\fileVersionReceiptGame\\snapshotVersionGame.uproject",
    canonicalProjectRoot: "C:\\Projects\\fileVersionReceiptGame",
    canonicalPath: "C:\\Projects\\fileVersionReceiptGame\\Source\\snapshotVersionParser.cpp",
    sha256: "fileVersionReceipt-snapshotVersion",
  };

  assert.deepEqual(sanitizeStructuredDurableValue(source), source);
});

test("exact ephemeral keys do not erase longer diagnostic schema keys", () => {
  const sanitized = sanitizeStructuredDurableValue({
    fileVersionReceipt: "fvr1_live",
    fileVersionReceiptPolicy: "diagnostic documentation",
    fileVersionReceiptHistory: "never persisted",
    paymentReceiptPolicy: "keep",
  });

  assert.deepEqual(sanitized, {
    fileVersionReceiptPolicy: "diagnostic documentation",
    fileVersionReceiptHistory: "never persisted",
    paymentReceiptPolicy: "keep",
  });
});

test("origin policies separate user domain prose from derived receipt-reuse guidance", () => {
  const domain = "영수증 프린터 연동 코드를 수정하고 receipt template을 저장해.";
  const operational = "Continue with the returned receipt in the next prediction round.";

  assert.equal(sanitizeUserAuthoredText(domain), domain);
  assert.match(sanitizeDerivedOperationalText(operational), /fresh file snapshot required before mutation/iu);
  assert.doesNotMatch(sanitizeDerivedOperationalText(operational), /returned receipt/iu);
});

test("derived assistant and tool prose preserves ordinary receipt-domain facts", () => {
  for (const value of [
    "We used the payment receipt data to render the purchase UI.",
    "The payment receipt parser stored the receipt history.",
    "영수증 프린터 연동 코드를 수정하고 receipt template을 저장했습니다.",
    "결제 영수증 데이터를 사용해 구매 내역 UI를 렌더링했습니다.",
    "이 영수증 데이터를 사용해 UI를 구현했습니다.",
    "현재 영수증 데이터를 사용해 구매 내역 UI를 구현했습니다.",
    "Use the current receipt data to render purchase history.",
    "현재 영수증을 사용해 환불을 처리했습니다.",
    "The current receipt is valid proof of purchase.",
    "Retry with this receipt.",
    "Use this receipt to edit the refund amount.",
    "현재 영수증을 사용해 환불 내역을 수정했습니다.",
    "결제 API에서 반환된 영수증을 사용해 주문을 확인했습니다.",
    "The previous receipt is valid proof of purchase for reimbursement.",
    "Retry with the previous receipt.",
    "반환된 영수증으로 수정해.",
    "The previous receipt remains valid.",
    "The current receipt still works.",
    "현재 receipt로 계속 진행해.",
    "receipt로 다시 시도해.",
  ]) {
    assert.equal(sanitizeDerivedOperationalText(value), value);
  }
});

test("user diagnostic identifiers and capability-like symbols remain literal", () => {
  for (const source of [
    "fileVersionReceipt가 hard compaction 뒤 왜 재사용되면 안 되는지 분석해.",
    "C:\\Game\\Source\\snapshotVersionParser.cpp와 snapshotVersionHandler를 분석해.",
  ]) {
    assert.equal(sanitizeUserAuthoredText(source), source);
  }
});

test("raw capability removal preserves Korean diagnostic intent", () => {
  for (const [source, expected] of [
    [
      "fvr1_AbC123을 왜 사용하면 안 되는지 분석해.",
      "[ephemeral file capability omitted]을 왜 사용하면 안 되는지 분석해.",
    ],
    [
      "왜 fvr1_AbC123을 재사용하면 위험한지 설명해.",
      "왜 [ephemeral file capability omitted]을 재사용하면 위험한지 설명해.",
    ],
  ]) {
    assert.equal(sanitizeUserAuthoredText(source), expected);
  }
  assert.equal(
    sanitizeUserAuthoredText("Using fvr1_ABC after compaction is unsafe."),
    "Using [ephemeral file capability omitted] after compaction is unsafe.",
  );
  assert.equal(
    sanitizeUserAuthoredText("Explain why fvr1_ is the forbidden capability prefix."),
    "Explain why fvr1_ is the forbidden capability prefix.",
  );
});

test("derived records sanitize operational prose without rewriting structural fields", () => {
  const source = {
    path: "project://Source/Use This Receipt.cpp",
    canonicalPath: "C:\\Proj\\Source\\Use This Receipt.cpp",
    summary: "Continue with the returned receipt in the next prediction round.",
    nested: {
      message: "The current receipt is for order 42.",
      path: "project://Content/Payment Receipt.uasset",
    },
    structuralPaths: [
      "project://Source/fileVersionReceiptParser.cpp",
      "C:\\Proj\\Source\\snapshotVersionParser.cpp",
    ],
    structuralHashes: ["fileVersionReceipt-snapshotVersion"],
    likelyErrors: ["Continue with the returned receipt in the next prediction round."],
  };
  const sanitized = sanitizeDerivedOperationalRecord(source);

  assert.equal(sanitized.path, source.path);
  assert.equal(sanitized.canonicalPath, source.canonicalPath);
  assert.match(sanitized.summary, /fresh file snapshot required before mutation/iu);
  assert.equal(sanitized.nested.message, source.nested.message);
  assert.equal(sanitized.nested.path, source.nested.path);
  assert.deepEqual(sanitized.structuralPaths, source.structuralPaths);
  assert.deepEqual(sanitized.structuralHashes, source.structuralHashes);
  assert.match(sanitized.likelyErrors[0], /fresh file snapshot required before mutation/iu);
  assert.deepEqual(sanitizeDerivedOperationalRecord(sanitized), sanitized);

  const serialized = sanitizeDerivedOperationalRecord(JSON.stringify(source));
  assert.deepEqual(JSON.parse(serialized), sanitized);
});

test("unrelated literal JSON Unicode escapes remain byte-for-byte unchanged", () => {
  for (const value of [
    String.raw`영수증 JSON에서 \u005c escape를 그대로 보존해.`,
    String.raw`Payment receipt field must contain literal \u0024.`,
    String.raw`ReceiptActor should render the literal \u0066 character sequence.`,
  ]) {
    assert.equal(sanitizeUserAuthoredText(value), value);
    assert.equal(sanitizeStructuredDurableValue(value), value);
  }
});

test("encoded capability removal does not decode unrelated escapes in the same string", () => {
  const source = String.raw`Keep literal \u0066 here; use \u0066vr1_live_capability for the next edit.`;
  const expected = String.raw`Keep literal \u0066 here; fresh file snapshot required before mutation.`;

  assert.equal(sanitizeUserAuthoredText(source), expected);
  assert.equal(sanitizeDerivedOperationalText(source), expected);
});

test("encoded space and dot delimiters stop the capability span", () => {
  for (const [source, expected] of [
    [
      String.raw`Observed \u0066vr1_ABC\u0020payment receipt text survives.`,
      String.raw`Observed [ephemeral file capability omitted]\u0020payment receipt text survives.`,
    ],
    [
      String.raw`Observed \u0066vr1_ABC\u002e Payment receipt history survives.`,
      String.raw`Observed [ephemeral file capability omitted]\u002e Payment receipt history survives.`,
    ],
  ]) {
    assert.equal(sanitizeUserAuthoredText(source), expected);
    assert.equal(sanitizeStructuredDurableValue(source), expected);
    assert.equal(sanitizeDerivedOperationalText(source), expected);
  }
});

test("a mixed user objective loses only its raw capability clause", () => {
  const source = "결제 receipt 화면을 구현하고 fvr1_AbC123을 사용해.";
  const sanitized = sanitizeUserAuthoredText(source);

  assert.match(sanitized, /^결제 receipt 화면을 구현하고 /u);
  assert.match(sanitized, /fresh file snapshot required before mutation/iu);
  assert.doesNotMatch(sanitized, /fvr1_|use\s+\[ephemeral|사용해/u);
});

test("both origin policies remain idempotent", () => {
  const userValue = {
    objective: "결제 receipt 화면을 구현하고 fvr1_AbC123을 사용해.",
    snapshotVersion: 3,
  };
  const derivedValue = {
    summary: "Continue with the returned receipt in the next prediction round.",
    fileVersionReceipt: "fvr1_AbC123",
  };
  const onceUser = sanitizeStructuredDurableValue(userValue);
  const onceDerived = sanitizeDerivedOperationalValue(derivedValue);

  assert.deepEqual(sanitizeStructuredDurableValue(onceUser), onceUser);
  assert.deepEqual(sanitizeDerivedOperationalValue(onceDerived), onceDerived);
});

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
    "Retry the next file edit with the previous receipt.",
    "The receipt for this file is still valid.",
    "반환된 영수증으로 파일을 수정해.",
    "반환된 receipt로 다음 파일 편집을 다시 시도해.",
    "현재 유효한 리시트를 다음 파일 수정에 사용해.",
    "현재 receipt로 다음 파일 수정을 계속 진행해.",
    "Keep using the receipt you already have.",
    "Retry the next file edit with this receipt.",
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
    "Do not take another snapshot and use the previous receipt for the next file edit.",
    "Without a fresh read, reuse the previous receipt for the next mutation.",
    "Ensure you use the previous receipt for the next mutation.",
    "Explain the risk later and reuse the current receipt for the next file edit.",
    "Diagnose the failure, then retry the file edit with the previous receipt.",
    "Never reuse the old receipt or use the previous receipt for the next file edit.",
    "Do not reuse the old receipt — use the current receipt for the mutation.",
    "Never reuse old receipt: use the current receipt for the next mutation instead.",
    "Explain why reuse of receipts is unsafe or use the previous receipt for the next mutation.",
    "apply_edit_bundle: duplicate patches[] paths are not allowed; use one focused region per file and continue with the returned receipt in the next prediction round",
    "설명하지 말고 현재 리시트를 다음 파일 수정에 재사용해.",
    "분석은 나중에 하고 반환된 receipt를 다음 파일 수정에 재사용해.",
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
    sanitizeDurableText('The phrase "continue the next file edit with the returned receipt" must never be retained.'),
    'The phrase "receipt-reuse wording" must never be retained.',
  );
  assert.equal(
    sanitizeDurableText("Block instructions to continue the next file edit with the returned receipt."),
    "Block receipt-reuse instructions.",
  );
  assert.equal(
    sanitizeDurableText('Ensure "continue the next file edit with the returned receipt" is rejected.'),
    'Ensure "receipt-reuse wording" is rejected.',
  );
  assert.equal(
    sanitizeDurableText('"현재 리시트를 파일 수정에 재사용해"라는 지시는 저장하지 마라.'),
    '"receipt-reuse wording"라는 지시는 저장하지 마라.',
  );
  assert.equal(
    sanitizeDurableText('"현재 receipt로 다음 파일 수정을 계속 진행해"라는 지시는 저장하지 마라.'),
    '"receipt-reuse wording"라는 지시는 저장하지 마라.',
  );
  for (const [source, expected] of [
    ["Prevent using the previous receipt for the next file edit.", "Prevent receipt-reuse instructions for the next file edit."],
    ["Block use of the previous receipt for the next mutation.", "Block receipt-reuse instructions for the next mutation."],
    ["Reject attempts to supply the prior receipt to replace_in_file.", "Reject receipt-reuse instructions to replace_in_file."],
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
