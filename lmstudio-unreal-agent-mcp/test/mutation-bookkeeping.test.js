"use strict";

const assert = require("assert");
const test = require("node:test");
const { errorPayload } = require("../src/context-ux");

test("mutation bookkeeping failure payload marks write applied and blocks retry", () => {
  const payload = errorPayload("lock busy", {
    errorCode: "MUTATION_LOCK_BUSY",
    path: "Source/Foo.cpp",
    operation: "create",
    writeApplied: true,
    bookkeepingFailed: true,
    mutationGenerationNotRecorded: true,
    retryable: false,
    doNotRetry: ["write_file"],
    nextSteps: [
      "Do NOT retry write_file — the file change is already on disk.",
      "Call read_file on the same path to confirm current content.",
    ],
    agentInstruction: "Bookkeeping failed after a successful write; verify disk state before any further edits.",
  });
  assert.strictEqual(payload.writeApplied, true);
  assert.strictEqual(payload.bookkeepingFailed, true);
  assert.deepStrictEqual(payload.doNotRetry, ["write_file"]);
  assert.match(payload.nextSteps[0], /Do NOT retry write_file/);
});
