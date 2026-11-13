"use strict";

// Conversation 1794469194476: git_read_file on a file the model had just
// created failed as INTERNAL_ERROR with retry "none", leaving no next step.
const test = require("node:test");
const assert = require("node:assert/strict");
const { errorFromException } = require("../src/direct-runtime-shared");

test("a path git cannot read as a committed file names the reader that can", () => {
  const error = Object.assign(new Error("Path is not one regular committed file"), { code: "not_file", status: "not_applied" });
  const result = errorFromException(error);
  assert.equal(result.errorCode, "GIT_PATH_NOT_COMMITTED_FILE");
  assert.equal(result.retry.allowed, true);
  assert.match(result.nextStep, /read_file \(project:\/\/ path\)/u);
  const other = errorFromException(Object.assign(new Error("boom"), { code: "something_else" }));
  assert.equal(other.errorCode, "INTERNAL_ERROR", "other failures keep their mapping");
});
