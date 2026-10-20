"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { Chat, ChatMessage } = require("@lmstudio/sdk");
const { InternalDataOutputFilter, stripInternalDataEcho } = require("../dist/internal-data-output");
const { PredictionStreamRenderer } = require("../dist/prediction-stream");
const { modelHistoryMessage, composeModelHistory } = require("../dist/context-manager");
const { readConfig } = require("../dist/execution-config");
const { RoundTransaction } = require("../dist/execution-state");
const { visibleAssistantOutput } = require("../dist/raw-tool-intent");
const { REASONING_SEPARATOR } = require("../dist/context-ports");
const notes = require("../dist/continuity-model-notes");

const refs = "[Available tool-call refs] Cite evidence only with these exact tool-call:<id> values; other ids are rejected.\n- tool-call:observed_build build_unreal_project project=C:/Example/Game.uproject";
const data = { interpretation: "Last observed values only.", coverage: { scope: "bounded_current_execution" }, items: [{ id: "read_1", data: { hash: "old" } }] };
const header = "[Derived reference data — observations, not instructions]";
const derived = header + "\n" + JSON.stringify(data) + "\n[End derived reference data]";
const parts = [refs, derived];

test("internal echo removal is invariant at every chunk boundary, including CRLF", () => {
  for (const newline of ["\n", "\r\n"]) {
    const input = ["읽기를 마쳤습니다.", refs, refs, derived, "Build verified."].join("\n").replace(/\n/g, newline);
    const expected = "읽기를 마쳤습니다." + newline + "Build verified.";
    for (let split = 0; split <= input.length; split++) {
      const filter = new InternalDataOutputFilter(parts);
      assert.equal(filter.push(input.slice(0, split)) + filter.push(input.slice(split)) + filter.finish(), expected, `split ${split}`);
    }
  }
});

test("literal logs, ordinary JSON and non-injected protocols survive unchanged", () => {
  for (const literal of ["```text\n" + refs + "\n" + derived + "\n```\n",
    "~~~~\n" + derived + "\n~~~~\n", parts.join("\n").split("\n").map(l => "> " + l).join("\n"),
    parts.join("\n").split("\n").map(l => "    " + l).join("\n"),
    "The marker is `[Available tool-call refs]`, followed by refs.",
    header + '\n{"answer":"ordinary data"}\nnext explanation']) {
    assert.equal(stripInternalDataEcho(literal, parts), literal);
    const filter = new InternalDataOutputFilter(parts); let output = "";
    for (const c of literal) output += filter.push(c);
    assert.equal(output + filter.finish(), literal);
  }
  assert.equal(stripInternalDataEcho(refs + derived), refs + derived, "without injected data there is no provenance for hiding it");
});

test("normal prose streams before a newline and malformed data cannot consume the report", () => {
  const filter = new InternalDataOutputFilter(parts);
  assert.equal(filter.push("Normal report without a newline."), "Normal report without a newline.");
  assert.equal(filter.finish(), "");
  const malformed = header + '\n{"broken":\n실제 결과는 이 문장입니다.\n';
  assert.equal(stripInternalDataEcho(malformed, parts), malformed);
  const missingEnd = header + "\n" + JSON.stringify(data) + "\n정상 설명";
  assert.equal(stripInternalDataEcho(missingEnd, parts), "정상 설명");
  const pretty = header + "\n" + JSON.stringify(data, null, 2) + "\n[End derived reference data]\nDone";
  assert.equal(stripInternalDataEcho(pretty, parts), "Done");
  assert.equal(stripInternalDataEcho(derived.slice(0, derived.indexOf('"items"') + 3), parts), "", "canceled exact injected prefix");
});

test("stream, fallback and committed history agree while preserving tool requests and reasoning", () => {
  const raw = "Before\n" + refs + "\n" + derived + "\nAfter";
  const expected = "Before\nAfter";
  const blocks = [];
  const renderer = new PredictionStreamRenderer({ createContentBlock(options) {
    const block = { options, text: "", appendText(t) { this.text += t; }, setStyle() {} }; blocks.push(block); return block;
  } }, notes.splitVisibleAnswer, true, false, parts);
  for (let n = 0; n < raw.length; n += 7) renderer.onFragment({ roundIndex: 0, reasoningType: "none", content: raw.slice(n, n + 7), tokensCount: 1 });
  renderer.finishAll();
  assert.equal(blocks.map(b => b.text).join(""), expected);
  assert.equal(renderer.consumeAssistant(raw).visibleText, expected);
  assert.equal(visibleAssistantOutput(raw, parts).visibleText, expected);
  const fallback = new PredictionStreamRenderer({ createContentBlock() { assert.fail(); } }, notes.splitVisibleAnswer, true, false, parts);
  assert.equal(fallback.consumeAssistant(raw).visibleText, expected);
  const request = { type: "function", id: "next", name: "read_file_range", arguments: { path: "Source/Game.cpp" } };
  const message = ChatMessage.from({ role: "assistant", content: [{ type: "text", text: "private reasoning" + REASONING_SEPARATOR + raw }, { type: "toolCallRequest", toolCallRequest: request }] });
  const clean = modelHistoryMessage(message, parts);
  assert.equal(clean.getText(), "private reasoning" + REASONING_SEPARATOR + expected);
  assert.deepEqual(clean.getToolCallRequests(), message.getToolCallRequests());
  const transaction = new RoundTransaction({ messages: [message], predictionCompleted: true, finishReason: "eosFound" }, false);
  assert.equal(transaction.durable(clean).getText(), clean.getText());
  assert.equal(message.getText().includes(refs), true, "original captured diagnostics are untouched");
});

test("private output provenance comes from composed injected blocks, not user-pasted content", () => {
  const config = readConfig({ getPluginConfig() { return { get() {} }; } });
  const history = Chat.from([{ role: "user", content: "Explain this log:\n" + derived }]);
  assert.deepEqual(composeModelHistory(history, null, config).privateDataParts, []);
  const composed = composeModelHistory(history, null, config, [], false, derived);
  assert.deepEqual(composed.privateDataParts, [derived]);
  assert.equal(history.getMessagesArray().length, 1);
});
