"use strict";

// JSON stdin/stdout adapter for the deterministic context-compactor core.
// It runs only when the headless chat client detects context pressure.

const fs = require("node:fs");
const path = require("node:path");

const core = require(path.join(
  __dirname,
  "..",
  "lmstudio-context-compactor-plugin",
  "src",
  "direct-compaction-core.js",
));

function textContent(content) {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .filter((item) => item && item.type === "text")
    .map((item) => String(item.text || ""))
    .join("\n");
}

function normalizeMessage(message, index) {
  const toolRequests = (message.tool_calls || []).map((call) => {
    const fn = call.function || {};
    let args = fn.arguments || {};
    if (typeof args === "string") {
      try {
        args = JSON.parse(args);
      } catch {
        args = { raw: args };
      }
    }
    return { id: call.id, name: fn.name, arguments: args };
  });
  const toolResults = message.role === "tool"
    ? [{ toolCallId: message.tool_call_id, content: textContent(message.content) }]
    : [];
  return {
    index,
    role: String(message.role || ""),
    text: textContent(message.content),
    hasFiles: false,
    toolRequests,
    toolResults,
  };
}

function main() {
  const input = JSON.parse(fs.readFileSync(0, "utf8"));
  const messages = Array.isArray(input.messages) ? input.messages : [];
  const options = { maxCurrentTurnMessages: 8, ...(input.options || {}) };
  const measurement = input.measurement || {};
  if (!core.shouldCompact(measurement, options)) {
    process.stdout.write(JSON.stringify({ compacted: false, messages }));
    return;
  }
  const normalized = messages.map(normalizeMessage);
  const checkpoint = core.buildCheckpoint(normalized, options);
  if (checkpoint.omittedMessageCount <= 0) {
    process.stdout.write(JSON.stringify({ compacted: false, messages }));
    return;
  }
  const retained = new Set(checkpoint.retainedIndexes);
  const compacted = [];
  const systemText = [
    ...normalized.filter(message => message.role === "system")
      .map(message => core.invariantSystemText(message)).filter(Boolean),
    checkpoint.checkpoint,
  ].filter(Boolean).join("\n\n");
  if (systemText) compacted.push({ role: "system", content: systemText });
  // Keep model assertions and untrusted change text out of system instructions.
  for (const content of [checkpoint.assistantCheckpoint, checkpoint.changeDataCheckpoint])
    if (content) compacted.push({ role: "assistant", content });
  messages.forEach((message, index) => {
    if (message.role !== "system" && retained.has(index)) compacted.push(message);
  });
  process.stdout.write(JSON.stringify({
    compacted: true,
    messages: compacted,
    omittedMessageCount: checkpoint.omittedMessageCount,
  }));
}

main();
