"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");

const {
  resolveProjectRootForFile,
  resolvePythonExe,
  validateReplaceOccurrences,
  isValidationInfrastructureFailure
} = require("../src/validate-write");

test("resolvePythonExe honors installer-provided PYTHON_EXE", () => {
  const original = process.env.PYTHON_EXE;
  process.env.PYTHON_EXE = "/opt/codex/python3.12";
  try {
    assert.equal(resolvePythonExe(), "/opt/codex/python3.12");
  } finally {
    if (original === undefined) delete process.env.PYTHON_EXE;
    else process.env.PYTHON_EXE = original;
  }
});

test("expectedOccurrences=1 rejects ambiguous replace", () => {
  const err = validateReplaceOccurrences("hello world hello", "hello", "hi", { expectedOccurrences: 1 });
  assert.ok(err);
  assert.match(String(err), /occurrence mismatch/i);
});

test("expectedOccurrences=1 accepts single match", () => {
  const err = validateReplaceOccurrences("hello world", "hello", "hi", { expectedOccurrences: 1 });
  assert.equal(err, null);
});

test("resolveProjectRootForFile finds game root from plugin source", async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "validate-write-"));
  const projectRoot = path.join(tmp, "MyGame");
  const pluginRoot = path.join(projectRoot, "Plugins", "MyPlugin");
  fs.mkdirSync(path.join(pluginRoot, "Source", "MyPlugin"), { recursive: true });
  const file = path.join(pluginRoot, "Source", "MyPlugin", "MyPluginModule.cpp");
  fs.writeFileSync(file, "// test\n");
  const uproject = path.join(projectRoot, "MyGame.uproject");
  fs.writeFileSync(uproject, "{}");
  const resolved = await resolveProjectRootForFile(file, () => uproject);
  assert.equal(path.normalize(resolved), path.normalize(projectRoot));
});
test("validator infrastructure failures are advisory", () => {
  for (const code of ["VALIDATOR_MISSING", "VALIDATOR_EXEC_FAILED"]) {
    assert.equal(isValidationInfrastructureFailure({
      findings: [{ severity: "error", code }]
    }), true);
  }
});

test("real source findings remain blocking", () => {
  assert.equal(isValidationInfrastructureFailure({
    findings: [{ severity: "error", code: "MOCK_FINDING" }]
  }), false);
  assert.equal(isValidationInfrastructureFailure({
    findings: [
      { severity: "error", code: "VALIDATOR_EXEC_FAILED" },
      { severity: "error", code: "CPP_DEFINITION_MISSING" }
    ]
  }), false);
});
