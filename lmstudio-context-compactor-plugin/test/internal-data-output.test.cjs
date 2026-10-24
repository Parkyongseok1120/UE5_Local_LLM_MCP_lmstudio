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

const budget = 'Runtime read-result budget facts: [{"field":"byteBudget","requested":8192,"effective":4096,"unit":"utf8_bytes","denial":null}]. Limits bound the full response, not source body length. Keep the requested page budget stable while advancing the returned cursor; do not subtract previously read content. Consume delivered peers before retrying a denied read in a later round.';

test("owned budget echoes are removed at every fragment boundary while actual prose survives", () => {
  for (const echo of [budget, budget.replace('8192', '4096')]) {
    const text = 'Read completed.\n' + echo + '\nActual audit report.';
    for (let at = 0; at <= text.length; at++) {
      const filter = new InternalDataOutputFilter([budget]);
      assert.equal(filter.push(text.slice(0, at)) + filter.push(text.slice(at)) + filter.finish(), 'Read completed.\nActual audit report.', `split ${at}`);
    }
  }
  assert.equal(stripInternalDataEcho(budget, [budget]), '');
  assert.equal(visibleAssistantOutput(budget, [budget]).visibleText, '');
  assert.equal(modelHistoryMessage(ChatMessage.create('assistant', budget), [budget]).getText(), '');
});

test("budget quotations, unknown logs and ordinary JSON survive", () => {
  for (const text of ['```text\n'+budget+'\n```', '> '+budget, '    '+budget,
    'The budget message says: '+budget, 'Runtime read-result budget facts: [{"unrelated":true}]'])
    assert.equal(stripInternalDataEcho(text, [budget]), text);
  assert.equal(stripInternalDataEcho(budget), budget, 'a non-injected protocol is not private');
  const config = readConfig({getPluginConfig(){return {get:()=>undefined}}});
  const ask = Chat.from([{role:'user',content:'Explain this exact message: '+budget}]);
  const composition = composeModelHistory(ask, null, config, ['scope'], false, undefined, [budget]);
  assert.ok(composition.history.toString().includes(budget));
  assert.ok(!composition.privateDataParts.includes(budget));
});

test("canceling an injected partial budget echo does not expose its unfinished JSON", () => {
  const filter = new InternalDataOutputFilter([budget]);
  assert.equal(filter.push(budget.slice(0, 95)) + filter.finish(), '');
  const normal = new InternalDataOutputFilter([budget]);
  assert.equal(normal.push('Runtime performance depends on disk speed.') + normal.finish(), 'Runtime performance depends on disk speed.');
  const canceledQuote = '```text\n' + budget.slice(0, 95);
  assert.equal(stripInternalDataEcho(canceledQuote, [budget]), canceledQuote, 'a canceled literal fence remains user content');
});

test("only owned ledger and projection metadata disappear while user quotations remain literal", () => {
  const ledger = 'Runtime read ledger (kept across compaction). delivered earlier is not verified.\n- project://Source/A.cpp sha aaaaaaaa: recorded claims: none\nNo claim recorded at this sha: A.cpp.';
  const projection = 'Runtime evidence observation: 1 earlier tool result is present only in part.\n- project://Source/A.cpp: lines 1-20';
  const injected = [ledger, projection, budget];
  const raw = 'Real progress.\n' + injected.join('\n') + '\nFinal report.';
  for (let step = 1; step < 16; step++) {
    const filter = new InternalDataOutputFilter(injected); let out = '';
    for (let at = 0; at < raw.length; at += step) out += filter.push(raw.slice(at, at + step));
    assert.equal(out + filter.finish(), 'Real progress.\nFinal report.');
  }
  for (const value of [ledger, projection]) {
    const literal = '```text\n' + value;
    assert.equal(stripInternalDataEcho(literal, injected), literal);
  }
  assert.equal(stripInternalDataEcho('Runtime evidence observation: an unrelated explanation.', injected), 'Runtime evidence observation: an unrelated explanation.');
});

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
  const echoParts = [...parts, budget];
  const raw = "Before\n" + refs + "\n" + derived + "\n" + budget + "\nAfter";
  const expected = "Before\nAfter";
  const blocks = [];
  const renderer = new PredictionStreamRenderer({ createContentBlock(options) {
    const block = { options, text: "", appendText(t) { this.text += t; }, setStyle() {} }; blocks.push(block); return block;
  } }, notes.splitVisibleAnswer, true, false, echoParts);
  for (let n = 0; n < raw.length; n += 7) renderer.onFragment({ roundIndex: 0, reasoningType: "none", content: raw.slice(n, n + 7), tokensCount: 1 });
  renderer.finishAll();
  assert.equal(blocks.map(b => b.text).join(""), expected);
  assert.equal(renderer.consumeAssistant(raw).visibleText, expected);
  assert.equal(visibleAssistantOutput(raw, echoParts).visibleText, expected);
  const fallback = new PredictionStreamRenderer({ createContentBlock() { assert.fail(); } }, notes.splitVisibleAnswer, true, false, echoParts);
  assert.equal(fallback.consumeAssistant(raw).visibleText, expected);
  const request = { type: "function", id: "next", name: "read_file_range", arguments: { path: "Source/Game.cpp" } };
  const message = ChatMessage.from({ role: "assistant", content: [{ type: "text", text: "private reasoning" + REASONING_SEPARATOR + raw }, { type: "toolCallRequest", toolCallRequest: request }] });
  const clean = modelHistoryMessage(message, echoParts);
  assert.equal(clean.getText(), "private reasoning" + REASONING_SEPARATOR + expected);
  assert.deepEqual(clean.getToolCallRequests(), message.getToolCallRequests());
  const transaction = new RoundTransaction({ messages: [message], predictionCompleted: true, finishReason: "eosFound" }, false);
  assert.equal(transaction.durable(clean).getText(), clean.getText());
  assert.equal(message.getText().includes(refs), true, "original captured diagnostics are untouched");
});

test("an internal-budget-only echo is not a delivered final report", () => {
  const { DeliveryController } = require('../dist/delivery-controller');
  const raw = ChatMessage.create('assistant', budget);
  const cleaned = modelHistoryMessage(raw, [budget]);
  const delivery = new DeliveryController();
  delivery.observe({messages:[cleaned],finishReason:'eosFound'});
  const outcome = delivery.finish();
  assert.equal(outcome.reportPresent, false);
  assert.equal(outcome.taskCompleted, false);
  assert.ok(raw.getText().includes(budget));
});

test("private output provenance comes from composed injected blocks, not user-pasted content", () => {
  const config = readConfig({ getPluginConfig() { return { get() {} }; } });
  const history = Chat.from([{ role: "user", content: "Explain this log:\n" + derived }]);
  assert.deepEqual(composeModelHistory(history, null, config).privateDataParts, []);
  const composed = composeModelHistory(history, null, config, [], false, derived);
  assert.deepEqual(composed.privateDataParts, [derived]);
  assert.equal(history.getMessagesArray().length, 1);
});
