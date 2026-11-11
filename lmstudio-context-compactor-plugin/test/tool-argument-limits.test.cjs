"use strict";
// Conversation 1794419304890: a 1,235-char oldText was refused by the SDK
// schema check ("does not meet maximum length of 1200") before the provider
// could say which limit was hit or that oldText and newText differed on one line.
const test = require("node:test");
const assert = require("node:assert/strict");
const { Chat } = require("@lmstudio/sdk");
const { describeArgumentLengthRejection, lengthViolations, changedLineSummary } = require("../dist/tool-argument-limits");
const { runOneToolRound } = require("../dist/round-loop.js");

const patchProperties = {
  path: { type: "string" },
  oldText: { type: "string", maxLength: 50 },
  newText: { type: "string", maxLength: 80 },
  startLine: { type: "integer" }, endLine: { type: "integer" }, expectedRangeSha256: { type: "string" },
};
const replaceTool = { name: "replace_in_file", parametersJsonSchema: { type: "object", properties: {
  ...patchProperties,
  edits: { type: "array", items: { type: "object", properties: { oldText: patchProperties.oldText, newText: patchProperties.newText } } },
} } };
const bundleTool = { name: "apply_edit_bundle", parametersJsonSchema: { type: "object", properties: {
  patches: { type: "array", items: { type: "object", properties: {
    ...patchProperties,
    edits: { type: "array", items: { type: "object", properties: { oldText: patchProperties.oldText, newText: patchProperties.newText } } },
  } } },
} } };
const lines = (count, changedAt) => Array.from({ length: count }, (_, index) => index === changedAt ? `changed ${index}` : `keep ${index}`).join("\n");

test("a length rejection names the field, the allowed length and the lines that differ", () => {
  const oldText = lines(12, -1), newText = lines(12, 6);
  const message = describeArgumentLengthRejection({ message: "params.oldText does not meet maximum length of 50" },
    { name: "replace_in_file", arguments: { path: "A.cpp", oldText, newText } }, [replaceTool]);
  assert.match(message, /^replace_in_file was not run: oldText is \d+ characters; the tool schema allows 50; newText is \d+ characters; the tool schema allows 80\./u);
  assert.match(message, /Only oldText line 7 \(of 12\) differ from newText line 7 \(of 12\)/u);
  assert.match(message, /replace those file lines by range \(startLine\/endLine with the rangeSha256 of a read covering them\)/u);
  assert.match(message, /\(Schema check: params\.oldText does not meet maximum length of 50\)$/u);
});

test("nested edits[] and bundle patches are checked against their own item schema", () => {
  const edits = [{ oldText: "short", newText: "short" }, { oldText: lines(10, -1), newText: lines(10, 2) }];
  assert.deepEqual(lengthViolations(replaceTool.parametersJsonSchema, { path: "A", edits }).map(v => v.field),
    ["edits[1].oldText"], "only the field over its own limit (newText is 72 of 80)");
  const bundle = describeArgumentLengthRejection({ message: "x" },
    { name: "apply_edit_bundle", arguments: { patches: [{ path: "A", edits }] } }, [bundleTool]);
  assert.match(bundle, /patches\[0\]\.edits\[1\]\.oldText is \d+ characters; the tool schema allows 50/u);
  assert.match(bundle, /Only oldText line 3 \(of 10\) differ from newText line 3 \(of 10\)/u);
  assert.doesNotMatch(bundle, /replace those file lines by range/u, "an edits[] item has no range fields of its own");
});

test("other schema errors and unknown tools keep the SDK message", () => {
  assert.equal(describeArgumentLengthRejection({ message: "must match oneOf" },
    { name: "replace_in_file", arguments: { path: "A", oldText: "a", newText: "b" } }, [replaceTool]), undefined);
  assert.equal(describeArgumentLengthRejection({ message: "Cannot find tool" },
    { name: "missing_tool", arguments: { oldText: "x".repeat(99) } }, [replaceTool]), undefined);
  assert.equal(describeArgumentLengthRejection({ message: "m" }, { name: "replace_in_file", arguments: "not an object" }, [replaceTool]), undefined);
});

test("changed-line summary handles insertions and deletions", () => {
  assert.deepEqual(changedLineSummary("a\nb\nc", "a\nb\nNEW\nc"), { oldLines: 3, newLines: 4, changedOld: [3, 2], changedNew: [3, 3] });
  assert.deepEqual(changedLineSummary("a\nGONE\nc", "a\nc"), { oldLines: 3, newLines: 2, changedOld: [2, 2], changedNew: [2, 1] });
  assert.match(describeArgumentLengthRejection({ message: "m" },
    { name: "replace_in_file", arguments: { oldText: `${lines(10, -1)}\nmore`, newText: `${lines(10, -1)}\nNEW\nmore` } }, [replaceTool]),
  /Only oldText no lines \(of 11\) differ from newText line 11 \(of 12\)/u);
});

test("the round passes the translated reason to the model and labels the refusal apart from a failed generation", async () => {
  const { ToolCallRequestError } = require("@lmstudio/sdk");
  const kinds = [];
  let returned;
  const rejected = new ToolCallRequestError("params.oldText does not meet maximum length of 50", "{}");
  const unparsed = new ToolCallRequestError("Generation failed", undefined);
  const model = { async act(_chat, tools, options) {
    returned = await options.handleInvalidToolRequest(rejected,
      { type: "function", id: "big", name: "replace_in_file", arguments: { oldText: lines(12, -1), newText: lines(12, 6) } });
    options.onToolCallRequestFailure(0, 7, rejected);
    await options.handleInvalidToolRequest(unparsed, undefined);
    options.onToolCallRequestFailure(0, 8, unparsed);
    options.onPredictionCompleted?.({ stats: { stopReason: "eosFound" } });
  } };
  await runOneToolRound(model, Chat.empty(), [replaceTool], new AbortController().signal, {
    captureUnparsedToolRequest: true, onToolCallRequestFinalized() {}, async guardToolCall() {},
    onToolCallRequestFailure: (_round, callId, _error, kind) => kinds.push([callId, kind]),
  });
  assert.match(returned, /^replace_in_file was not run: oldText is \d+ characters/u);
  assert.deepEqual(kinds, [[7, "rejected_request"], [8, "generation_failed"]]);
});
