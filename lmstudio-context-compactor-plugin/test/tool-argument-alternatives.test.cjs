"use strict";
// Conversation 1794590083146 (turn 2, call 18): apply_edit_bundle with a second
// patches[] entry that lacked its version proof never reached the provider.
// The model saw only "params.patches.1 is not any of [subschema 0],[subschema 1]".
// Checked here against the tool schemas the Unreal MCP really publishes.
const test = require("node:test");
const assert = require("node:assert/strict");
const { alternativeViolations, describeArgumentLengthRejection } = require("../dist/tool-argument-limits");
const { toolDefinitions } = require("../../lmstudio-unreal-agent-mcp/src/direct-tool-catalog.js");

const tools = toolDefinitions().map(tool => ({ name: tool.name, parametersJsonSchema: tool.inputSchema }));
const schemaOf = name => tools.find(tool => tool.name === name).parametersJsonSchema;
const HASH = "a".repeat(64), PATH = "project://Source/Game/Enemy.cpp";
const SDK = { message: "Failed to parse arguments for tool \"apply_edit_bundle\": params.patches.1 is not any of [subschema 0],[subschema 1]" };

test("a patches[] entry without version proof is named with the fields it lacks", () => {
  const args = { patches: [
    { path: PATH, expectedHash: HASH, edits: [{ oldText: "a", newText: "b" }] },
    { path: PATH, startLine: 959, endLine: 960, expectedRangeSha256: HASH, newText: "x" } ] };
  assert.deepEqual(alternativeViolations(schemaOf("apply_edit_bundle"), args),
    [{ field: "patches[1]", conflict: false, choices: ["fileVersionReceipt", "expectedHash"] }]);
  const text = describeArgumentLengthRejection(SDK, { name: "apply_edit_bundle", arguments: args }, tools);
  assert.match(text, /^apply_edit_bundle was not run: patches\[1\] lacks fileVersionReceipt or expectedHash\./u);
  assert.match(text, /Schema check: Failed to parse arguments/u);
});

test("a patch form with a missing field lists the nearest alternative first", () => {
  const range = { path: PATH, startLine: 43, endLine: 47, expectedRangeSha256: HASH, fileVersionReceipt: "fvr1_x" };
  assert.deepEqual(alternativeViolations(schemaOf("replace_in_file"), range),
    [{ field: "", conflict: false, choices: ["newText", "edits", "oldText + newText"] }]);
  assert.match(describeArgumentLengthRejection({ message: "params is not exactly one from [subschema 0],[subschema 1],[subschema 2]" },
    { name: "replace_in_file", arguments: range }, tools), /the arguments lacks newText or edits or oldText \+ newText/u);
  const nested = alternativeViolations(schemaOf("apply_edit_bundle"), { patches: [{ ...range }] });
  assert.deepEqual(nested.map(item => item.field), ["patches[0]"]);
});

test("two patch forms at once are a conflict, and complete arguments report nothing", () => {
  const both = { path: PATH, fileVersionReceipt: "fvr1_x", oldText: "a", newText: "b", edits: [{ oldText: "a", newText: "b" }] };
  const [conflict] = alternativeViolations(schemaOf("replace_in_file"), both);
  assert.equal(conflict.conflict, true);
  assert.deepEqual(conflict.choices, ["oldText + newText", "edits"]);
  assert.match(describeArgumentLengthRejection({ message: "x" }, { name: "replace_in_file", arguments: both }, tools),
    /combines alternatives that exclude each other \(oldText \+ newText \/ edits\); send exactly one/u);
  const complete = { patches: [{ path: PATH, expectedHash: HASH, startLine: 1, endLine: 2, expectedRangeSha256: HASH, newText: "x" }] };
  assert.deepEqual(alternativeViolations(schemaOf("apply_edit_bundle"), complete), []);
  assert.equal(describeArgumentLengthRejection(SDK, { name: "apply_edit_bundle", arguments: complete }, tools), undefined);
});
