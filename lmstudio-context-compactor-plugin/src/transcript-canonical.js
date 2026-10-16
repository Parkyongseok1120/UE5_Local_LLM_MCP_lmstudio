"use strict";

const { visibleAssistantText } = require("./continuity-text.js");

// The host stores the visible transcript in its own block/message layout,
// which need not match how this plugin recorded it (one SDK message per
// content block). Persisted prefix identities hash this layout-independent
// form so a host that merges or splits blocks still matches the same turn.
// rawEnds[i] is the last raw message index covered by canonical entry i.
function canonicalTranscript(messages) {
  const entries = [], rawEnds = [];
  messages.forEach((message, rawIndex) => {
    if (message.hasFiles()) throw new Error("typed_files_not_serializable");
    const role = message.getRole();
    const rawText = role === "assistant" ? visibleAssistantText(message.getText()) : message.getText();
    const text = String(rawText || "").replace(/\s+$/u, "");
    const requests = message.getToolCallRequests().map(request => ({
      id: String(request.id || ""), name: String(request.name || ""), arguments: request.arguments || {} }));
    const results = message.getToolCallResults().map(result => ({ toolCallId: String(result.toolCallId || ""),
      content: typeof result.content === "string" ? result.content : JSON.stringify(result.content) }));
    if (!text && !requests.length && !results.length) return;
    const previous = entries.at(-1);
    if (previous && previous.role === role && (role === "assistant" || role === "tool")) {
      previous.text = [previous.text, text].filter(Boolean).join("\n");
      previous.requests.push(...requests); previous.results.push(...results);
      rawEnds[rawEnds.length - 1] = rawIndex;
      return;
    }
    entries.push({ role, text, requests, results });
    rawEnds.push(rawIndex);
  });
  return { entries, rawEnds };
}

module.exports = { canonicalTranscript };
