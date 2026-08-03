"use strict";

const fs = require("fs");
const path = require("path");
const { Chat } = require("@lmstudio/sdk");
const core = require("../lmstudio-context-compactor-plugin/src/compaction-core.js");

async function main() {
  // Simulate rebuild after many tool pairs then a new user goal.
  const messages = [
    { role: "system", content: [{ type: "text", text: "You are an Unreal agent." }] },
    { role: "user", content: [{ type: "text", text: "현재 프로젝트 찾고 코드 구조 전체 적으로 확인해줘" }] },
  ];
  for (let i = 0; i < 20; i += 1) {
    const id = `c${i}`;
    messages.push({
      role: "assistant",
      content: [{
        type: "toolCallRequest",
        toolCallRequest: { id, type: "function", name: "list_directory", arguments: { path: `P${i}` } },
      }],
    });
    messages.push({
      role: "tool",
      content: [{ type: "toolCallResult", toolCallId: id, content: JSON.stringify({ entries: [`F${i}`] }) }],
    });
  }
  messages.push({ role: "assistant", content: [{ type: "text", text: "structure overview complete" }] });
  messages.push({ role: "user", content: [{ type: "text", text: "지금 버그있는거 찾기만하고 수정은 하지마." }] });

  const chat = Chat.from({ messages });
  const arr = chat.getMessagesArray();
  const dump = arr.map((m, index) => ({
    index,
    role: m.getRole(),
    textLen: String(m.getText() || "").length,
    text: String(m.getText() || "").slice(0, 80),
    toolCalls: (m.getToolCallRequests?.() || []).length,
    toolResults: (m.getToolCallResults?.() || []).length,
  }));
  console.log(JSON.stringify({ dumpTail: dump.slice(-5), users: dump.filter((d) => d.role === "user") }, null, 2));

  // Mimic buildCompactedChat selection
  let latestUserIndex = -1;
  let latestUserText = "";
  for (let i = 0; i < arr.length; i += 1) {
    const text = String(arr[i].getText() || "");
    if (arr[i].getRole() === "user" && text.trim() && !core.isMetaUserMessage(text)) {
      latestUserIndex = i;
      latestUserText = text;
    }
  }
  const result = Chat.empty();
  result.append("system", "checkpoint");
  result.append(arr[latestUserIndex]);
  const after = result.getMessagesArray().map((m) => ({
    role: m.getRole(),
    textLen: String(m.getText() || "").length,
    text: String(m.getText() || "").slice(0, 80),
  }));
  console.log(JSON.stringify({ latestUserIndex, latestUserText, afterAppend: after }, null, 2));

  fs.appendFileSync(
    path.join(__dirname, "..", "debug-49b048.log"),
    `${JSON.stringify({
      sessionId: "49b048",
      runId: "chat-roundtrip",
      hypothesisId: "H12",
      location: "scripts/debug_chat_roundtrip.js",
      message: "chat from+append user retention",
      data: { latestUserIndex, latestUserText, afterAppend: after, userCount: dump.filter((d) => d.role === "user").length },
      timestamp: Date.now(),
    })}\n`,
  );
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
