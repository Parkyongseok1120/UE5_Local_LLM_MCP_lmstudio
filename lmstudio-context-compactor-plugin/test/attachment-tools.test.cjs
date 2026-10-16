"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const { Chat } = require("@lmstudio/sdk");
const { createAttachmentContext } = require("../dist/attachment-tools.js");
const { parseToolResult } = require("../dist/direct-compaction-core.js");

function attachedHistory(files) {
  return Chat.from({
    messages: [{
      role: "user",
      content: [
        { type: "text", text: "Review the attached guide." },
        ...files.map((file) => ({ type: "file", ...file })),
      ],
    }],
  });
}

test("attachment tool returns only the requested parsed-text range with source metadata", async () => {
  let parseCount = 0;
  const client = { files: { async parseDocument() {
    parseCount += 1;
    return { content: "0123456789".repeat(1000), parser: { library: "test", version: "1" } };
  } } };
  const history = attachedHistory([{
    name: "guide.pdf",
    identifier: "attachment-1",
    sizeBytes: 12345,
    fileType: "application/pdf",
  }]);
  const context = createAttachmentContext(history, client);

  assert.equal(context.attachmentCount, 1);
  assert.equal(context.tools.length, 1);
  assert.match(context.instruction, /guide\.pdf/u);
  assert.match(context.instruction, /does not prove that its complete contents were read/u);
  const result = await context.tools[0].implementation(
    { attachment: "attachment-1", startOffset: 100, maxChars: 500 },
    { status() {}, warn() {}, signal: new AbortController().signal, callId: 1 },
  );
  assert.equal(result.attachmentId, "attachment-1");
  assert.equal(result.attachmentName, "guide.pdf");
  assert.equal(result.startOffset, 100);
  assert.equal(result.endOffset, 600);
  assert.equal(result.totalChars, 10000);
  assert.equal(result.content.length, 500);
  assert.equal(result.hasMore, true);

  await context.tools[0].implementation(
    { attachment: "guide.pdf", startOffset: 600, maxChars: 500 },
    { status() {}, warn() {}, signal: new AbortController().signal, callId: 2 },
  );
  assert.equal(parseCount, 1, "one prediction turn caches the parsed document without another model call");
});

test("attachment observations keep source and range but omit document content from durable memory", () => {
  const parsed = parseToolResult(JSON.stringify({
    ok: true,
    observation: "attached_document_character_range",
    attachmentId: "attachment-1",
    attachmentName: "guide.pdf",
    attachmentType: "application/pdf",
    parser: { library: "test", version: "1" },
    startOffset: 1200,
    endOffset: 7200,
    totalChars: 20000,
    hasMore: true,
    content: "large document excerpt that must not enter durable memory",
  }));

  assert.equal(parsed.attachmentId, "attachment-1");
  assert.equal(parsed.attachmentName, "guide.pdf");
  assert.equal(parsed.startOffset, 1200);
  assert.equal(parsed.endOffset, 7200);
  assert.equal(parsed.totalChars, 20000);
  assert.equal(parsed.hasMore, true);
  assert.equal(Object.prototype.hasOwnProperty.call(parsed, "content"), false);
});

