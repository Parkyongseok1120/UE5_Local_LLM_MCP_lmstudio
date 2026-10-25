"use strict";

// A review states findings across many rounds. The checkpoint kept only the
// last assistant update, so after each compaction of the 2026-10-25 run the
// model lost "AddDeath sets bIsDead (P0 rejected)" and similar conclusions and
// re-read files to verify them again.

const test = require("node:test");
const assert = require("node:assert/strict");
const { Chat, ChatMessage } = require("@lmstudio/sdk");
const { buildCompactedHistory } = require("../dist/context-manager");
const { readConfig } = require("../dist/execution-config");
const { ASSISTANT_MARKER, currentTurnFindings } = require("../src/continuity-assistant-evidence.js");

const config = readConfig({ getPluginConfig: () => ({ get: () => undefined }) });

function exchange(history, id, progress) {
  if (progress) history.append(ChatMessage.from({ role: "assistant", content: progress }));
  history.append(ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest",
    toolCallRequest: { type: "function", id, name: "read_file", arguments: { path: `Source/${id}.cpp` } } }] }));
  history.append(ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: id,
    content: JSON.stringify({ ok: true, path: `Source/${id}.cpp`, content: `int ${id};\n`.repeat(200) }) }] }));
}

function evidenceOf(history) {
  const message = history.getMessagesArray().find(item => item.isAssistantMessage() && item.getText().startsWith(ASSISTANT_MARKER));
  return message ? JSON.parse(message.getText().slice(ASSISTANT_MARKER.length + 1)) : null;
}

test("findings stated in earlier rounds survive repeated compaction of the same turn", () => {
  const history = Chat.from([{ role: "user", content: "previous request" }, { role: "assistant", content: "previous answer" },
    { role: "user", content: "Audit the character RPC contracts" }]);
  exchange(history, "r1", "프로젝트 구조부터 파악하겠습니다.");
  exchange(history, "r2", "`AddDeath()`가 `bIsDead = true`를 설정함을 확인했습니다 (P0 가설 기각). 이제 GameState를 읽습니다.");
  exchange(history, "r3", "- project://Source/GameMode.cpp sha 9c130e06: delivered lines 1-146 (complete); visible now: none (archived)\n"
    + "PlayerState.h:25 `DeathCount` uses `ReplicatedUsing = OnRep_KillCount` (copy-paste error).");
  for (let i = 4; i < 9; i++) exchange(history, `r${i}`, `Reading file ${i}.`);
  const first = buildCompactedHistory(history, 0, config, { maxCurrentTurnMessages: 4, checkpointPolicy: "bounded" }).history;
  const findings = evidenceOf(first)?.turnFindings?.items || [];
  assert.ok(findings.some(item => /AddDeath\(\)/.test(item)), JSON.stringify(findings));
  assert.ok(findings.some(item => /DeathCount/.test(item)));
  assert.ok(!findings.some(item => /project:\/\/|visible now/.test(item)), "echoed runtime rows are not findings");
  assert.ok(!findings.some(item => /구조부터|Reading file/.test(item)), "plans without code are not findings");

  // The next compaction no longer sees those rounds; the earlier checkpoint carries them.
  for (let i = 9; i < 14; i++) exchange(history, `r${i}`, `Reading file ${i}.`);
  const again = Chat.from(first);
  for (const message of history.getMessagesArray().slice(-15)) again.append(message);
  const second = buildCompactedHistory(again, 0, config, { maxCurrentTurnMessages: 4, checkpointPolicy: "bounded" }).history;
  const carried = evidenceOf(second)?.turnFindings?.items || [];
  assert.ok(carried.some(item => /AddDeath\(\)/.test(item)), JSON.stringify(carried));
  assert.ok(carried.some(item => /DeathCount/.test(item)));
});

test("findings of another user turn are not carried", () => {
  const messages = [{ role: "user", text: "new task", index: 0 }];
  const carried = { forUserTurn: "a".repeat(64), items: ["`Old()` was broken"] };
  assert.deepEqual(currentTurnFindings(messages, 0, "b".repeat(64), carried).items, []);
  assert.deepEqual(currentTurnFindings(messages, 0, "a".repeat(64), carried).items, ["`Old()` was broken"]);
});
