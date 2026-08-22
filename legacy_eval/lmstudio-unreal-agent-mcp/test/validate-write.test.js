"use strict";

// Historical validation-gate behavior; excluded from the product test suite.

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");

const {
  resolveProjectRootForFile,
  resolvePythonExe,
  resolveValidationRoot,
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

test("clean home resolves the repo-local validator without UNREAL58_ROOT", () => {
  const cleanHome = fs.mkdtempSync(path.join(os.tmpdir(), "validate-write-home-"));
  try {
    const root = resolveValidationRoot({ envRoot: "", homeDir: cleanHome });
    assert.equal(root, path.resolve(__dirname, "../.."));
    assert.equal(
      fs.existsSync(path.join(root, "scripts", "validate_project_sources.py")),
      true
    );
    assert.notEqual(root, path.join(cleanHome, ".lmstudio", "Unreal58-RAG"));
  } finally {
    fs.rmSync(cleanHome, { recursive: true, force: true });
  }
});

test("manual LM Studio template declares its repository as the validation root", () => {
  const templatePath = path.resolve(
    __dirname,
    "../config/lmstudio-mcp-unreal-agent.json.template"
  );
  const template = JSON.parse(fs.readFileSync(templatePath, "utf8"));
  assert.equal(
    template.mcpServers["unreal-agent"].env.UNREAL58_ROOT,
    "%USERPROFILE%\\.lmstudio\\UE5_Local_LLM_MCP_lmstudio"
  );
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
